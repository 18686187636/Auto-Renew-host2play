#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import time
import re
import requests
from datetime import datetime
import pytz
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
CAPTCHA_API_KEY = os.getenv("CAPTCHA_API_KEY")  # Ace Data Cloud Token

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

# ==================== Ace Data Cloud Token API ====================
TOKEN_API_URL = "https://api.acedata.cloud/captcha/token/recaptcha2"

def get_recaptcha_token(sitekey, page_url, proxy=None):
    """
    调用 Ace Data Cloud Token API 获取 reCAPTCHA v2 token
    """
    if not CAPTCHA_API_KEY:
        raise Exception("CAPTCHA_API_KEY not set")
    headers = {
        "accept": "application/json",
        "authorization": f"Bearer {CAPTCHA_API_KEY}",
        "content-type": "application/json"
    }
    payload = {
        "website_key": sitekey,
        "website_url": page_url,
        "async": False
    }
    if proxy:
        payload["proxy"] = proxy
    resp = requests.post(TOKEN_API_URL, json=payload, headers=headers, timeout=120)
    if resp.status_code != 200:
        error_info = resp.json().get("error", {})
        raise Exception(f"Token API 返回错误: {error_info.get('code')} - {error_info.get('message')}")
    result = resp.json()
    token = result.get("token")
    if not token:
        raise Exception("Token API 响应中没有 token 字段")
    return token

# ==================== 核心续期流程 ====================
def perform_renewal_with_browser():
    expiry_dt = None
    error_msg = None
    success = False
    server_name = "Unknown"

    print("🌍 正在获取当前出口 IP...")
    ip = get_current_ip(PROXY if PROXY else None)
    print(f"📍 当前出口 IP: {ip}")

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
            print("✅ 自定义 User-Agent 已设置")
        except Exception as e:
            print(f"⚠️ 设置 User-Agent 失败: {e}")

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
            print("✅ 反检测脚本已注入")
        except Exception as e:
            print(f"⚠️ CDP 注入失败: {e}")

        # ---- 加载页面 ----
        print("🌐 Opening renewal page...")
        sb.open(RENEW_URL)
        sb.wait_for_ready_state_complete()
        sb.sleep(8)
        screenshot_step(sb, "page_loaded")

        # ---- 检测 Cloudflare 拦截 ----
        title = sb.get_title()
        if "Just a moment" in title or "524" in title:
            error_msg = "Cloudflare 拦截，请更换代理"
            screenshot_step(sb, "blocked")
            return False, None, error_msg, server_name
        else:
            print("✅ 页面正常加载")

        # ========== 1. 点击 Consent ==========
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
                    sb.sleep(1)
                    break
                except:
                    continue
            else:
                print("ℹ️ 未发现 Consent 按钮，跳过")
        except Exception as e:
            print(f"⚠️ 处理 Consent 时出错（忽略）: {e}")

        # ---------- 定义 Renew 按钮点击函数 ----------
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
                    print(f"✅ 点击 Renew 按钮 (selector: {selector})")
                    return True
                except:
                    continue
            try:
                sb.execute_script("renew();")
                print("✅ 通过 JavaScript 点击 Renew 按钮")
                return True
            except:
                pass
            return False

        # ========== 2. 获取 sitekey ==========
        sitekey = sb.execute_script("""
            var elem = document.querySelector('.g-recaptcha');
            if (elem) return elem.getAttribute('data-sitekey');
            var scripts = document.getElementsByTagName('script');
            for (var i=0; i<scripts.length; i++) {
                var src = scripts[i].src || '';
                if (src.indexOf('recaptcha/api.js') !== -1) {
                    var match = src.match(/render=([^&]+)/);
                    if (match) return match[1];
                }
                var html = scripts[i].innerHTML || '';
                var m = html.match(/sitekey['"]?\\s*[:=]\\s*['"]([^'"]+)['"]/);
                if (m) return m[1];
            }
            return null;
        """)
        if not sitekey:
            error_msg = "无法获取 sitekey"
            screenshot_step(sb, "sitekey_failed")
            return False, None, error_msg, server_name
        print(f"🔑 获取到 sitekey: {sitekey}")

        # ========== 3. 第一次点击 Renew（触发 reCAPTCHA） ==========
        print("🔘 第一次点击 Renew server 按钮...")
        if not click_renew():
            error_msg = "无法点击 Renew 按钮"
            screenshot_step(sb, "renew_click_failed")
            return False, None, error_msg, server_name
        time.sleep(3)

        # ========== 4. 手动勾选 reCAPTCHA 复选框 ==========
        print("🔘 正在手动勾选 reCAPTCHA 复选框...")
        try:
            sb.wait_for_element('iframe[src*="recaptcha"]', timeout=10)
            sb.switch_to_frame('iframe[src*="recaptcha"]')
            sb.wait_for_element('#recaptcha-anchor', timeout=5)
            sb.click('#recaptcha-anchor')
            print("✅ 已勾选 I'm not a robot 复选框")
            sb.switch_to_default_content()
            time.sleep(3)
        except Exception as e:
            print(f"⚠️ 手动勾选复选框失败: {e}")
            sb.switch_to_default_content()
            # 如果失败，继续尝试，可能验证已自动通过

        # ========== 5. 获取 reCAPTCHA token（通过 Token API） ==========
        print("🔄 正在请求 reCAPTCHA token...")
        try:
            token = get_recaptcha_token(sitekey, RENEW_URL, PROXY)
            print(f"✅ 获取 token 成功: {token[:20]}...")
        except Exception as e:
            error_msg = f"获取 token 失败: {e}"
            screenshot_step(sb, "token_failed")
            return False, None, error_msg, server_name

        # ========== 6. 注入 token 并触发验证完成 ==========
        print("💉 注入 token...")
        inject_script = f"""
            (function() {{
                // 1. 填充 textarea
                var textarea = document.getElementById('g-recaptcha-response');
                if (textarea) {{
                    textarea.value = '{token}';
                    textarea.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    textarea.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}

                // 2. 触发回调
                if (typeof verifyCallback === 'function') {{
                    verifyCallback('{token}');
                }}
                if (typeof onSuccess === 'function') {{
                    onSuccess('{token}');
                }}

                // 3. 如果存在 grecaptcha 回调
                if (window.grecaptcha && grecaptcha.getResponse) {{
                    // 某些页面会定期检查 getResponse，所以无需额外操作
                }}

                // 4. 触发容器事件
                var recaptchaElem = document.querySelector('.g-recaptcha');
                if (recaptchaElem) {{
                    recaptchaElem.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
                return true;
            }})();
        """
        sb.execute_script(inject_script)
        print("✅ token 注入完成")
        time.sleep(2)

        # ---- 验证 token 是否填充 ----
        token_filled = sb.execute_script("""
            var textarea = document.getElementById('g-recaptcha-response');
            return textarea ? textarea.value.length > 0 : false;
        """)
        if not token_filled:
            print("⚠️ token 未填充，强制写入")
            sb.execute_script(f"""
                document.getElementById('g-recaptcha-response').value = '{token}';
            """)
            time.sleep(1)

        screenshot_step(sb, "after_inject")

        # ========== 7. 第二次点击 Renew（提交续期） ==========
        print("🔘 第二次点击 Renew server 按钮（提交续期）...")
        if not click_renew():
            error_msg = "第二次点击 Renew 失败"
            screenshot_step(sb, "second_renew_failed")
            return False, None, error_msg, server_name

        screenshot_step(sb, "after_submit")
        print("⏳ 等待续期处理...")
        time.sleep(10)

        # ---- 刷新页面获取新过期时间 ----
        print("🔄 刷新页面获取更新...")
        sb.open(RENEW_URL)
        sb.wait_for_ready_state_complete()
        sb.sleep(5)
        screenshot_step(sb, "after_reload")

        # ========== 8. 提取过期日期 ==========
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
        print(f"📅 New expiry (raw): {new_expiry_str}")

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
                    error_msg = f"无法解析日期格式: {new_expiry_str}"
            except Exception as e:
                error_msg = f"日期解析错误: {e}"
        else:
            error_msg = "未能获取新的过期日期"

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
    print("🚀 Starting Host2Play renewal with Ace Data Cloud Token API (manual checkbox click)")
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
