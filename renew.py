#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import time
import re
import base64
import io
import requests
from datetime import datetime
import pytz
from PIL import Image
from seleniumbase import SB

# ==================== 环境变量 ====================
RENEW_URL = "https://host2play.gratis/server/renew?i=459cc4c7-29c8-4fb9-90ca-7860eaeea74d"
EXPIRY_FILE = "expiry.txt"

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
CRONJOB_API_KEY = os.getenv("CRONJOB_API_KEY")
CRONJOB_JOB_ID = os.getenv("CRONJOB_JOB_ID")
GH_TOKEN = os.getenv("GH_TOKEN")
REPO_OWNER = os.getenv("REPO_OWNER")
REPO_NAME = os.getenv("REPO_NAME")
WORKFLOW_FILE = os.getenv("WORKFLOW_FILE", "renew.yml")
BRANCH = os.getenv("BRANCH", "main")
PROXY = os.getenv("PROXY")                          # socks5://127.0.0.1:1080
CAPTCHA_API_KEY = os.getenv("CAPTCHA_API_KEY")      # Ace Data Cloud Token

# ==================== 辅助函数 ====================
def send_tg_message(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def get_beijing_time():
    return datetime.now(pytz.timezone('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")

def write_expiry(dt):
    with open(EXPIRY_FILE, 'w') as f:
        f.write(dt.isoformat())

def commit_expiry_file():
    os.system('git config user.name "github-actions[bot]"')
    os.system('git config user.email "github-actions[bot]@users.noreply.github.com"')
    os.system('git add expiry.txt')
    os.system('git commit -m "Update expiry date [skip ci]" || echo "No changes"')
    os.system('git push')

def screenshot_step(sb, name):
    ts = int(time.time() * 1000)
    sb.save_screenshot(f"step_{name}_{ts}.png")
    print(f"📸 Screenshot: step_{name}_{ts}.png")

def get_current_ip(proxy=None):
    """通过代理获取当前出口 IP"""
    proxies = None
    if proxy:
        proxies = {"http": proxy, "https": proxy}
    try:
        resp = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
        if resp.status_code == 200:
            return resp.text.strip()
        return "获取失败"
    except Exception as e:
        return f"获取失败: {e}"

# ==================== Ace Data Cloud 打码集成 ====================
CAPTCHA_API_URL = "https://api.acedata.cloud/captcha/recognition/recaptcha2"

def solve_recaptcha_via_acedata(image_data, question_code):
    if not CAPTCHA_API_KEY:
        raise Exception("CAPTCHA_API_KEY not set")
    buffered = io.BytesIO()
    image_data.save(buffered, format="PNG")
    img_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
    headers = {
        "accept": "application/json",
        "authorization": f"Bearer {CAPTCHA_API_KEY}",
        "content-type": "application/json"
    }
    payload = {
        "question": question_code,
        "image": img_base64
    }
    resp = requests.post(CAPTCHA_API_URL, json=payload, headers=headers, timeout=60)
    if resp.status_code != 200:
        raise Exception(f"API request failed: {resp.status_code} - {resp.text}")
    result = resp.json()
    if not result.get("success"):
        error = result.get("error", {})
        raise Exception(f"API error: {error.get('code')} - {error.get('message')}")
    solution = result.get("solution", {})
    objects = solution.get("objects", [])
    if not objects:
        raise Exception("No objects to click returned by API")
    return objects, solution.get("size", 300)

def click_recaptcha_grid(sb, objects, grid_size=300):
    try:
        iframes = sb.find_elements('iframe[src*="recaptcha"]')
        for iframe in iframes:
            sb.switch_to_frame(iframe)
            break
        else:
            raise Exception("No reCAPTCHA iframe found")
    except Exception as e:
        raise Exception(f"Failed to switch to reCAPTCHA iframe: {e}")
    img_elem = None
    try:
        img_elem = sb.find_element('img', timeout=3)
    except:
        try:
            img_elem = sb.find_element('canvas', timeout=3)
        except:
            pass
    if not img_elem:
        raise Exception("Could not find reCAPTCHA image element")
    location = img_elem.location
    size = img_elem.size
    left = location['x']
    top = location['y']
    width = size['width']
    height = size['height']
    cols = 3
    rows = 3
    cell_w = width / cols
    cell_h = height / rows
    actions = sb.driver.action_chains
    for idx in objects:
        row = idx // cols
        col = idx % cols
        x = left + col * cell_w + cell_w / 2
        y = top + row * cell_h + cell_h / 2
        print(f"🔘 Clicking index {idx} at ({x:.0f}, {y:.0f})")
        actions.move_by_offset(x, y).click().perform()
        time.sleep(0.5)
    sb.switch_to_default_content()

def extract_question_from_page(sb):
    question_text = None
    try:
        elem = sb.find_element('.rc-imageselect-instructions', timeout=3)
        if elem:
            question_text = elem.text.strip()
    except:
        pass
    if not question_text:
        try:
            iframes = sb.find_elements('iframe[src*="recaptcha"]')
            for iframe in iframes:
                sb.switch_to_frame(iframe)
                try:
                    elem = sb.find_element('.rc-imageselect-instructions', timeout=2)
                    if elem:
                        question_text = elem.text.strip()
                        break
                except:
                    continue
            sb.switch_to_default_content()
        except:
            pass
    if not question_text:
        raise Exception("Could not find reCAPTCHA question text")
    question_map = {
        "出租车": "/m/0pg52",
        "巴士": "/m/01bjv",
        "校车": "/m/02yvhj",
        "摩托车": "/m/04_sv",
        "拖拉机": "/m/013xlm",
        "烟囱": "/m/01jk_4",
        "人行横道": "/m/014xcs",
        "红绿灯": "/m/015qff",
        "自行车": "/m/0199g",
        "停车计价表": "/m/015qbp",
        "汽车": "/m/0k4j",
        "桥": "/m/015kr",
        "船": "/m/019jd",
        "棕榈树": "/m/0cdl1",
        "山": "/m/09d_r",
        "消防栓": "/m/01pns0",
        "楼梯": "/m/01lynh"
    }
    for cn, code in question_map.items():
        if cn in question_text:
            return code
    match = re.search(r'/m/[a-z0-9]+', question_text)
    if match:
        return match.group(0)
    raise Exception(f"Unrecognized question: {question_text}")

def capture_recaptcha_image(sb):
    try:
        iframes = sb.find_elements('iframe[src*="recaptcha"]')
        for iframe in iframes:
            sb.switch_to_frame(iframe)
            break
        else:
            raise Exception("No reCAPTCHA iframe found")
    except Exception as e:
        raise Exception(f"Failed to switch to reCAPTCHA iframe: {e}")
    img_elem = None
    try:
        img_elem = sb.find_element('img', timeout=3)
    except:
        try:
            img_elem = sb.find_element('canvas', timeout=3)
        except:
            pass
    if not img_elem:
        raise Exception("Could not find reCAPTCHA image element")
    location = img_elem.location
    size = img_elem.size
    left = location['x']
    top = location['y']
    width = size['width']
    height = size['height']
    png_data = sb.driver.get_screenshot_as_png()
    img = Image.open(io.BytesIO(png_data))
    cropped = img.crop((left, top, left + width, top + height))
    resized = cropped.resize((300, 300), Image.LANCZOS)
    sb.switch_to_default_content()
    return resized

# ==================== 核心续期流程 ====================
def perform_renewal_with_browser():
    expiry_dt = None
    error_msg = None
    success = False
    server_name = "Unknown"

    print("🌍 正在获取当前出口 IP...")
    proxy_for_ip = PROXY if PROXY else None
    ip = get_current_ip(proxy_for_ip)
    print(f"📍 当前出口 IP: {ip}")

    # 构建 SeleniumBase 参数（移除了 user_agent，兼容新版）
    sb_kwargs = {
        "uc": True,                  # 使用 undetected-chromedriver
        "headless": True,
        "page_load_strategy": "eager"
    }
    if PROXY:
        sb_kwargs["proxy"] = PROXY
        print(f"🔗 使用代理: {PROXY}")
    else:
        print("ℹ️ 未使用代理")

    with SB(**sb_kwargs) as sb:
        # ---- 使用 CDP 注入反检测脚本（在导航之前） ----
        try:
            # 通过 CDP 设置自定义 User-Agent
            sb.driver.execute_cdp_cmd('Network.setUserAgentOverride', {
                "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            })
            print("✅ 自定义 User-Agent 已设置")
        except Exception as e:
            print(f"⚠️ 设置 User-Agent 失败（非关键）: {e}")

        try:
            sb.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": """
                    // 隐藏 webdriver 特征
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    // 伪造 plugins
                    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                    // 伪造 languages
                    Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh']});
                    // 伪造 chrome 对象
                    window.chrome = { runtime: {} };
                    // 伪造 permissions
                    window.navigator.permissions = { query: () => Promise.resolve({ state: 'prompt' }) };
                """
            })
            print("✅ 反检测脚本已注入")
        except Exception as e:
            print(f"⚠️ CDP 注入失败（继续尝试）: {e}")

        print("🌐 Opening renewal page...")
        max_retries = 3
        for attempt in range(max_retries):
            sb.open(RENEW_URL)
            sb.wait_for_ready_state_complete()
            wait_time = 20 if attempt == 0 else 15
            print(f"⏳ 等待 {wait_time} 秒（尝试 {attempt+1}/{max_retries}）...")
            sb.sleep(wait_time)
            screenshot_step(sb, f"page_loaded_{attempt+1}")

            title = sb.get_title()
            page_source = sb.get_page_source()
            if "524" in title or "cloudflare" in page_source.lower():
                print(f"⚠️ Cloudflare 拦截 (尝试 {attempt+1}/{max_retries})")
                if attempt < max_retries - 1:
                    sb.refresh()
                    continue
                else:
                    error_msg = "Cloudflare 拦截或超时，请更换代理节点（当前出口IP可能被封锁）"
                    screenshot_step(sb, "blocked")
                    return False, None, error_msg, server_name
            else:
                print("✅ 页面正常加载")
                break
        else:
            error_msg = "页面加载失败"
            return False, None, error_msg, server_name

        # ========== 新增：处理 Consent 按钮 ==========
        try:
            consent_selectors = [
                'button:contains("Consent")',
                'button:contains("Accept")',
                'button:contains("同意")',
                '[role="button"]:contains("Consent")',
                'a:contains("Consent")',
                '#consent-button',
                '.consent-btn'
            ]
            for selector in consent_selectors:
                try:
                    sb.click(selector, timeout=2)
                    print("✅ 已点击 Consent 按钮")
                    sb.sleep(1)   # 等待弹窗消失
                    break
                except:
                    continue
            else:
                print("ℹ️ 未发现 Consent 按钮，跳过")
        except Exception as e:
            print(f"⚠️ 处理 Consent 时出错（忽略）: {e}")

        # 获取服务器名称
        try:
            name_elem = sb.find_element('#serverName', timeout=2)
            if name_elem:
                server_name = name_elem.text.strip()
        except:
            pass

        old_expiry_str = None
        expiry_selectors = ['#expireDate', '.expiry-date', 'span:contains("Expires")', 'div:contains("Expires")']
        for sel in expiry_selectors:
            try:
                elem = sb.find_element(sel, timeout=1)
                if elem:
                    text = elem.text.strip()
                    match = re.search(r'(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)', text)
                    if match:
                        old_expiry_str = match.group(1)
                        break
            except:
                continue
        print(f"📅 Current expiry (raw): {old_expiry_str}")

        # 触发 reCAPTCHA
        try:
            recaptcha_checkbox = sb.find_element('.g-recaptcha', timeout=5)
            if recaptcha_checkbox:
                sb.uc_click('.g-recaptcha')
                print("✅ Clicked reCAPTCHA checkbox")
                time.sleep(5)
        except:
            pass

        print("⏳ 等待 reCAPTCHA 图像加载...")
        time.sleep(8)

        try:
            question_code = extract_question_from_page(sb)
            print(f"🧩 Question code: {question_code}")
        except Exception as e:
            error_msg = f"Failed to extract question: {e}"
            screenshot_step(sb, "question_failed")
            return False, None, error_msg, server_name

        try:
            captcha_img = capture_recaptcha_image(sb)
            print("📸 reCAPTCHA image captured and resized to 300x300")
        except Exception as e:
            error_msg = f"Failed to capture reCAPTCHA image: {e}"
            screenshot_step(sb, "capture_failed")
            return False, None, error_msg, server_name

        try:
            objects, grid_size = solve_recaptcha_via_acedata(captcha_img, question_code)
            print(f"🧩 Objects to click: {objects}")
        except Exception as e:
            error_msg = f"Ace Data Cloud error: {e}"
            screenshot_step(sb, "api_failed")
            return False, None, error_msg, server_name

        try:
            click_recaptcha_grid(sb, objects, grid_size)
            print("✅ reCAPTCHA grid clicked")
            time.sleep(2)
        except Exception as e:
            error_msg = f"Failed to click grid: {e}"
            screenshot_step(sb, "click_grid_failed")
            return False, None, error_msg, server_name

        print("🔘 Clicking Renew server button...")
        clicked = False
        btn_selectors = [
            'button.btn-primary:contains("Renew")',
            'button:contains("Renew server")',
            'button:contains("Renew")',
            'button[onclick*="renew()"]',
            '.btn-primary:contains("Renew")'
        ]
        for sel in btn_selectors:
            try:
                sb.uc_click(sel, timeout=3)
                clicked = True
                print(f"✅ Clicked using selector: {sel}")
                break
            except:
                continue
        if not clicked:
            try:
                sb.execute_script("renew();")
                clicked = True
                print("✅ Clicked via JavaScript renew()")
            except:
                pass
        if not clicked:
            error_msg = "Could not click Renew button"
            screenshot_step(sb, "click_failed")
            return False, None, error_msg, server_name

        screenshot_step(sb, "after_click")
        print("⏳ 等待续期完成...")
        time.sleep(10)

        print("🔄 Refreshing page...")
        sb.open(RENEW_URL)
        sb.wait_for_ready_state_complete()
        sb.sleep(5)
        screenshot_step(sb, "after_reload")

        new_expiry_str = None
        for sel in expiry_selectors:
            try:
                elem = sb.find_element(sel, timeout=2)
                if elem:
                    text = elem.text.strip()
                    match = re.search(r'(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)', text)
                    if match:
                        new_expiry_str = match.group(1)
                        break
            except:
                continue
        print(f"📅 New expiry (raw): {new_expiry_str}")

        if new_expiry_str and new_expiry_str != old_expiry_str:
            try:
                for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                    try:
                        expiry_dt = datetime.strptime(new_expiry_str, fmt)
                        expiry_dt = expiry_dt.replace(tzinfo=pytz.UTC)
                        success = True
                        break
                    except ValueError:
                        continue
                if not success:
                    error_msg = f"Unrecognized date format: {new_expiry_str}"
            except Exception as e:
                error_msg = f"Date parsing error: {e}"
        else:
            if new_expiry_str == old_expiry_str:
                error_msg = "Expiry date unchanged – renewal might have failed"
            else:
                error_msg = "Could not find expiry date after renewal"

        if not success and not error_msg:
            error_msg = "Renewal failed (unknown reason)"

        if not success:
            screenshot_step(sb, "renewal_failed")

    return success, expiry_dt, error_msg, server_name

# ==================== cron-job.org 调度 ====================
def ensure_cronjob():
    if not CRONJOB_API_KEY or not GH_TOKEN:
        print("Missing CRONJOB_API_KEY or GH_TOKEN, skip cronjob setup.")
        return
    trigger_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GH_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json"
    }
    body = {"ref": BRANCH}
    job_data = {
        "name": "Host2Play Renewal (470min)",
        "url": trigger_url,
        "request_method": "POST",
        "request_headers": headers,
        "request_body": json.dumps(body),
        "type": "interval",
        "interval_value": 470,
        "interval_unit": "minutes",
        "enabled": True
    }
    api_base = "https://cron-job.org/api/v1"
    if CRONJOB_JOB_ID:
        url = f"{api_base}/jobs/{CRONJOB_JOB_ID}"
        method = "PUT"
        print(f"Updating cron-job {CRONJOB_JOB_ID}")
    else:
        url = f"{api_base}/jobs"
        method = "POST"
        print("Creating new cron-job")
    auth = {"Authorization": f"Bearer {CRONJOB_API_KEY}"}
    try:
        r = requests.request(method, url, json=job_data, headers=auth, timeout=20)
        r.raise_for_status()
        result = r.json()
        if not CRONJOB_JOB_ID:
            new_id = result.get("id") or result.get("job_id")
            if new_id:
                print(f"Created cron-job with ID {new_id}")
                print("Please save this ID as CRONJOB_JOB_ID secret.")
        else:
            print("Cron-job updated successfully.")
    except Exception as e:
        print(f"Failed to manage cron-job: {e}")

# ==================== 主入口 ====================
def main():
    print("🚀 Starting Host2Play renewal with Ace Data Cloud reCAPTCHA solver")
    success, new_expiry, error, server_name = perform_renewal_with_browser()

    if success and new_expiry:
        write_expiry(new_expiry)
        commit_expiry_file()

        beijing_tz = pytz.timezone('Asia/Shanghai')
        expiry_beijing = new_expiry.astimezone(beijing_tz)
        msg = (
            f"✅ 续期成功\n"
            f"服务器: {server_name}\n"
            f"新到期时间: {expiry_beijing.strftime('%Y-%m-%d %H:%M:%S')} (北京时间)\n"
            f"续期链接: {RENEW_URL}"
        )
        send_tg_message(msg)
        print(msg)

        ensure_cronjob()
    else:
        err_msg = f"❌ 续期失败\n服务器: {server_name}\n错误信息: {error or '未知错误'}\n续期链接: {RENEW_URL}"
        send_tg_message(err_msg)
        print(err_msg)
        sys.exit(1)

if __name__ == "__main__":
    main()
