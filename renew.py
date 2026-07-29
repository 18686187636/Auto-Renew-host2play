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
PROXY = os.getenv("PROXY")  # socks5://127.0.0.1:1080
CAPTCHA_API_KEY = os.getenv("CAPTCHA_API_KEY")

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
    filename = f"step_{name}_{ts}.png"
    sb.save_screenshot(filename)
    print(f"📸 Screenshot: {filename}")

def get_current_ip(proxy=None):
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

# ==================== Ace Data Cloud Recognition API ====================
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
        error_info = resp.json().get("error", {})
        raise Exception(f"API error: {error_info.get('code')} - {error_info.get('message')}")
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

    print("🌍 获取当前出口 IP...")
    ip = get_current_ip(PROXY if PROXY else None)
    print(f"📍 出口 IP: {ip}")

    sb_kwargs = {
        "uc": True,
        "headless": True,
        "page_load_strategy": "eager"
    }
    if PROXY:
        sb_kwargs["proxy"] = PROXY
        print(f"🔗 使用代理: {PROXY}")
    else:
        print("ℹ️ 未使用代理")

    with SB(**sb_kwargs) as sb:
        # ---- CDP 设置 ----
        try:
            sb.driver.execute_cdp_cmd('Network.setUserAgentOverride', {
                "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            })
            print("✅ UA 已设置")
            screenshot_step(sb, "ua_set")
        except Exception as e:
            print(f"⚠️ UA 设置失败: {e}")

        try:
            sb.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": """
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                    Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh']});
                    window.chrome = { runtime: {} };
                    window.navigator.permissions = { query: () => Promise.resolve({ state: 'prompt' }) };
                """
            })
            print("✅ 反检测注入")
            screenshot_step(sb, "anti_detect")
        except Exception as e:
            print(f"⚠️ 注入失败: {e}")

        # ---- 加载页面 ----
        print("🌐 打开续期页面...")
        sb.open(RENEW_URL)
        sb.wait_for_ready_state_complete()
        sb.sleep(8)
        screenshot_step(sb, "page_loaded")

        # ---- 检测 Cloudflare ----
        title = sb.get_title()
        if "Just a moment" in title or "524" in title:
            error_msg = "Cloudflare 拦截"
            screenshot_step(sb, "blocked")
            return False, None, error_msg, server_name
        else:
            print("✅ 页面正常")
            screenshot_step(sb, "page_normal")

        # ========== 1. Consent ==========
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
                    print("✅ 已点击 Consent")
                    sb.sleep(1)
                    screenshot_step(sb, "consent_clicked")
                    break
                except:
                    continue
            else:
                print("ℹ️ 无 Consent")
                screenshot_step(sb, "no_consent")
        except Exception as e:
            print(f"⚠️ Consent 处理失败: {e}")
            screenshot_step(sb, "consent_error")

        # ---------- 点击 Renew 函数 ----------
        def click_renew():
            btn_selectors = [
                'button.btn-primary:contains("Renew")',
                'button:contains("Renew server")',
                'button:contains("Renew")',
                'button[onclick*="renew()"]',
                '.btn-primary:contains("Renew")'
            ]
            for selector in btn_selectors:
                try:
                    sb.uc_click(selector, timeout=3)
                    print(f"✅ 点击 Renew (selector: {selector})")
                    screenshot_step(sb, "renew_clicked")
                    return True
                except:
                    continue
            try:
                sb.execute_script("renew();")
                print("✅ 通过 JS 点击 Renew")
                screenshot_step(sb, "renew_js")
                return True
            except:
                pass
            return False

        # ========== 2. 获取 sitekey（仅用于显示） ==========
        sitekey = sb.execute_script("""
            var elem = document.querySelector('.g-recaptcha');
            if (elem) return elem.getAttribute('data-sitekey');
            return null;
        """)
        print(f"🔑 sitekey: {sitekey}")
        screenshot_step(sb, "sitekey")

        # ========== 3. 第一次点击 Renew（触发验证） ==========
        print("🔘 第一次点击 Renew...")
        if not click_renew():
            error_msg = "点击 Renew 失败"
            screenshot_step(sb, "renew_click_failed")
            return False, None, error_msg, server_name
        time.sleep(3)
        screenshot_step(sb, "after_first_renew")

        # ========== 4. 手动勾选复选框 ==========
        print("🔘 手动勾选 reCAPTCHA 复选框...")
        try:
            sb.wait_for_element('iframe[src*="recaptcha"]', timeout=10)
            sb.switch_to_frame('iframe[src*="recaptcha"]')
            sb.wait_for_element('#recaptcha-anchor', timeout=5)
            sb.click('#recaptcha-anchor')
            print("✅ 已勾选")
            sb.switch_to_default_content()
            time.sleep(3)
            screenshot_step(sb, "checkbox_checked")
        except Exception as e:
            print(f"⚠️ 勾选失败: {e}")
            sb.switch_to_default_content()
            screenshot_step(sb, "checkbox_error")

        # ========== 5. 等待图像验证出现 ==========
        print("⏳ 等待图像验证加载...")
        image_loaded = False
        for attempt in range(12):
            time.sleep(2)
            try:
                sb.switch_to_frame('iframe[src*="recaptcha"]')
                img = sb.find_element('img', timeout=1)
                if img:
                    print("✅ 图像验证已加载")
                    image_loaded = True
                    sb.switch_to_default_content()
                    break
            except:
                sb.switch_to_default_content()
                continue

        if not image_loaded:
            print("⚠️ 图像验证未自动出现，尝试重新触发...")
            click_renew()
            time.sleep(5)
            for attempt in range(6):
                time.sleep(2)
                try:
                    sb.switch_to_frame('iframe[src*="recaptcha"]')
                    img = sb.find_element('img', timeout=1)
                    if img:
                        print("✅ 重新触发后图像验证加载")
                        image_loaded = True
                        sb.switch_to_default_content()
                        break
                except:
                    sb.switch_to_default_content()
                    continue

        if not image_loaded:
            error_msg = "图像验证未能加载，可能被拦截或网络问题"
            screenshot_step(sb, "image_not_loaded")
            return False, None, error_msg, server_name

        screenshot_step(sb, "image_loaded")

        # ========== 6. 提取问题文本 ==========
        try:
            question_code = extract_question_from_page(sb)
            print(f"🧩 Question code: {question_code}")
            screenshot_step(sb, "question_extracted")
        except Exception as e:
            error_msg = f"提取问题失败: {e}"
            screenshot_step(sb, "question_failed")
            return False, None, error_msg, server_name

        # ========== 7. 截取验证图像 ==========
        try:
            captcha_img = capture_recaptcha_image(sb)
            print("📸 图像已截取")
            screenshot_step(sb, "capture_done")
        except Exception as e:
            error_msg = f"截取图像失败: {e}"
            screenshot_step(sb, "capture_failed")
            return False, None, error_msg, server_name

        # ========== 8. 调用 Ace Data Cloud 识别 ==========
        try:
            objects, grid_size = solve_recaptcha_via_acedata(captcha_img, question_code)
            print(f"🧩 需要点击的索引: {objects}")
            screenshot_step(sb, "api_success")
        except Exception as e:
            error_msg = f"识别失败: {e}"
            screenshot_step(sb, "api_failed")
            return False, None, error_msg, server_name

        # ========== 9. 点击网格 ==========
        try:
            click_recaptcha_grid(sb, objects, grid_size)
            print("✅ 网格点击完成")
            time.sleep(3)
            screenshot_step(sb, "grid_clicked")
        except Exception as e:
            error_msg = f"点击网格失败: {e}"
            screenshot_step(sb, "click_grid_failed")
            return False, None, error_msg, server_name

        # ========== 10. 第二次点击 Renew（提交） ==========
        print("🔘 第二次点击 Renew（提交续期）...")
        if not click_renew():
            error_msg = "第二次点击失败"
            screenshot_step(sb, "second_renew_failed")
            return False, None, error_msg, server_name

        screenshot_step(sb, "after_submit")
        print("⏳ 等待续期处理...")
        time.sleep(10)

        # ---- 刷新页面获取新到期时间 ----
        print("🔄 刷新页面...")
        sb.open(RENEW_URL)
        sb.wait_for_ready_state_complete()
        sb.sleep(5)
        screenshot_step(sb, "after_reload")

        # ========== 11. 提取新的到期时间 ==========
        new_expiry_str = None
        expiry_selectors = ['#expireDate', '.expiry-date', 'span:contains("Expires")', 'div:contains("Expires")']
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
        print(f"📅 新到期时间: {new_expiry_str}")
        screenshot_step(sb, "expiry_found")

        if new_expiry_str:
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
                    error_msg = f"日期格式无法解析: {new_expiry_str}"
            except Exception as e:
                error_msg = f"日期解析错误: {e}"
        else:
            error_msg = "未能获取新的到期时间"

        if not success and not error_msg:
            error_msg = "续期失败（未知原因）"

        if not success:
            screenshot_step(sb, "renewal_failed")

    return success, expiry_dt, error_msg, server_name

# ==================== cron-job.org 调度 ====================
def ensure_cronjob():
    if not CRONJOB_API_KEY or not GH_TOKEN:
        print("缺少 CRONJOB_API_KEY 或 GH_TOKEN，跳过 cron-job 设置")
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
        print(f"更新 cron-job {CRONJOB_JOB_ID}")
    else:
        url = f"{api_base}/jobs"
        method = "POST"
        print("创建新 cron-job")
    auth = {"Authorization": f"Bearer {CRONJOB_API_KEY}"}
    try:
        r = requests.request(method, url, json=job_data, headers=auth, timeout=20)
        r.raise_for_status()
        result = r.json()
        if not CRONJOB_JOB_ID:
            new_id = result.get("id") or result.get("job_id")
            if new_id:
                print(f"已创建 cron-job，ID: {new_id}")
                print("请将此 ID 保存为 CRONJOB_JOB_ID 环境变量")
        else:
            print("cron-job 已更新")
    except Exception as e:
        print(f"管理 cron-job 失败: {e}")

# ==================== 主入口 ====================
def main():
    print("🚀 Starting Host2Play renewal (Image Recognition with Full Screenshots)")
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
