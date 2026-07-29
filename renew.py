#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import time
import re
import requests
import io
from datetime import datetime
import pytz
from seleniumbase import SB
import speech_recognition as sr

# ==================== 环境变量 ====================
RENEW_URL = "https://host2play.gratis/server/renew?i=51b0dc2e-b901-46bf-b47a-20f5e6051459"
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
PROXY = os.getenv("PROXY")
CAPTCHA_API_KEY = os.getenv("CAPTCHA_API_KEY")  # 此处不再需要，保留兼容

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

# ==================== 音频验证核心 ====================
def solve_audio_captcha(sb):
    """
    处理 reCAPTCHA 音频验证
    返回 True/False 表示验证是否成功
    """
    print("🔊 切换到音频验证...")
    try:
        # 1. 点击音频按钮（通常在 reCAPTCHA 图像挑战界面右下角）
        # 查找音频按钮选择器（可能是耳机图标或 "Audio" 按钮）
        audio_btn_selectors = [
            '.rc-audiochallenge-control',
            'button[aria-label="Audio challenge"]',
            'button:contains("Audio")',
            '.rc-button-audio',
            'button[title="Audio challenge"]'
        ]
        clicked = False
        for selector in audio_btn_selectors:
            try:
                sb.click(selector, timeout=3)
                print(f"✅ 点击音频按钮 (selector: {selector})")
                clicked = True
                break
            except:
                continue
        if not clicked:
            print("❌ 未找到音频按钮")
            return False

        # 2. 等待音频加载
        time.sleep(5)
        screenshot_step(sb, "audio_loaded")

        # 3. 获取音频文件 URL
        audio_url = sb.execute_script("""
            var audio = document.querySelector('audio');
            if (audio) return audio.src;
            var source = document.querySelector('audio source');
            if (source) return source.src;
            return null;
        """)
        if not audio_url:
            print("❌ 无法获取音频 URL")
            return False
        print(f"🔗 音频 URL: {audio_url}")

        # 4. 下载音频
        print("⬇️ 下载音频...")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        }
        if PROXY:
            proxies = {"http": PROXY, "https": PROXY}
            resp = requests.get(audio_url, headers=headers, proxies=proxies, timeout=30)
        else:
            resp = requests.get(audio_url, headers=headers, timeout=30)
        if resp.status_code != 200:
            print(f"❌ 下载失败，状态码: {resp.status_code}")
            return False
        audio_data = resp.content

        # 5. 保存为临时 WAV 文件（speech_recognition 要求 WAV）
        with open("captcha_audio.wav", "wb") as f:
            f.write(audio_data)

        # 6. 语音识别
        print("🎤 正在识别音频...")
        recognizer = sr.Recognizer()
        with sr.AudioFile("captcha_audio.wav") as source:
            audio = recognizer.record(source)
        try:
            # 使用 Google Speech Recognition（免费，无需 API Key）
            text = recognizer.recognize_google(audio, language='en-US')
            print(f"📝 识别结果: {text}")
        except Exception as e:
            print(f"❌ 语音识别失败: {e}")
            # 备用：尝试 Vosk（如果模型存在）
            return False

        # 7. 填入答案
        print("✏️ 填入答案...")
        try:
            # 找到输入框（可能是 input 或 textarea）
            input_selectors = [
                '#audio-response',
                'input[aria-label="Type the numbers you hear"]',
                'input.rc-audiochallenge-input',
                'input[type="text"]'
            ]
            for selector in input_selectors:
                try:
                    sb.type(selector, text, timeout=2)
                    print(f"✅ 填入答案到 {selector}")
                    break
                except:
                    continue
            else:
                print("❌ 未找到输入框")
                return False

            # 8. 点击验证按钮（通常是 "Verify" 或 "Submit"）
            time.sleep(1)
            try:
                sb.click('button:contains("Verify")', timeout=3)
                print("✅ 点击 Verify 按钮")
            except:
                try:
                    sb.click('button:contains("Submit")', timeout=3)
                    print("✅ 点击 Submit 按钮")
                except:
                    print("⚠️ 未找到提交按钮，尝试直接提交")
                    sb.execute_script("""
                        var inputs = document.querySelectorAll('input[type="submit"]');
                        if (inputs.length) inputs[0].click();
                    """)

            # 9. 等待验证完成
            time.sleep(5)
            return True
        except Exception as e:
            print(f"❌ 填入答案失败: {e}")
            return False
    except Exception as e:
        print(f"❌ 音频验证流程异常: {e}")
        return False

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
        "headless": False,
        "xvfb": True,
        "page_load_strategy": "eager"
    }
    if PROXY:
        sb_kwargs["proxy"] = PROXY
        print(f"🔗 使用代理: {PROXY}")
    else:
        print("ℹ️ 未使用代理")

    with SB(**sb_kwargs) as sb:
        try:
            sb.driver.execute_cdp_cmd('Network.setUserAgentOverride', {
                "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            })
            print("✅ UA 已设置")
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
        except Exception as e:
            print(f"⚠️ 注入失败: {e}")

        print("🌐 打开续期页面...")
        sb.open(RENEW_URL)
        sb.wait_for_ready_state_complete()
        sb.sleep(8)

        title = sb.get_title()
        if "Just a moment" in title or "524" in title:
            error_msg = "Cloudflare 拦截"
            screenshot_step(sb, "blocked")
            return False, None, error_msg, server_name
        else:
            print("✅ 页面正常")

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
                    break
                except:
                    continue
            else:
                print("ℹ️ 无 Consent")
        except Exception as e:
            print(f"⚠️ Consent 处理失败: {e}")

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
                    return True
                except:
                    continue
            try:
                sb.execute_script("renew();")
                print("✅ 通过 JS 点击 Renew")
                return True
            except:
                pass
            return False

        print("🔘 第一次点击 Renew...")
        if not click_renew():
            error_msg = "点击 Renew 失败"
            screenshot_step(sb, "renew_click_failed")
            return False, None, error_msg, server_name
        time.sleep(3)

        # ========== 2. 勾选复选框 ==========
        print("🔘 手动勾选 reCAPTCHA 复选框...")
        checkbox_clicked = False
        try:
            iframes = sb.find_elements('iframe')
            for iframe in iframes:
                sb.switch_to_frame(iframe)
                try:
                    anchor = sb.find_element('#recaptcha-anchor', timeout=1)
                    if anchor:
                        sb.click('#recaptcha-anchor')
                        print("✅ 已勾选复选框")
                        checkbox_clicked = True
                        sb.switch_to_default_content()
                        break
                except:
                    sb.switch_to_default_content()
                    continue
        except Exception as e:
            print(f"⚠️ 遍历 iframe 勾选失败: {e}")
            sb.switch_to_default_content()

        if not checkbox_clicked:
            try:
                sb.switch_to_frame('iframe[src*="recaptcha"]')
                sb.wait_for_element('#recaptcha-anchor', timeout=5)
                sb.click('#recaptcha-anchor')
                print("✅ 通过 src 选择器勾选成功")
                checkbox_clicked = True
                sb.switch_to_default_content()
            except Exception as e:
                print(f"⚠️ src 选择器勾选失败: {e}")
                sb.switch_to_default_content()

        if not checkbox_clicked:
            print("❌ 无法勾选复选框，尝试继续（可能已经勾选）")
        else:
            time.sleep(3)

        # ========== 3. 处理验证码（音频方案） ==========
        # 尝试处理图像验证（如果有 Verify 按钮）
        print("⏳ 尝试处理验证码...")
        time.sleep(5)

        # 先检查是否有音频按钮，如果有则使用音频方案
        audio_available = sb.execute_script("""
            var audioBtn = document.querySelector('.rc-audiochallenge-control');
            if (audioBtn) return true;
            var btns = document.querySelectorAll('button');
            for (var i = 0; i < btns.length; i++) {
                if (btns[i].innerText.toLowerCase().includes('audio')) return true;
            }
            return false;
        """)

        if audio_available:
            print("🎧 检测到音频选项，使用音频验证...")
            captcha_success = solve_audio_captcha(sb)
        else:
            print("ℹ️ 未检测到音频选项，跳过（可能已通过验证）")
            captcha_success = True  # 假设已通过

        if not captcha_success:
            error_msg = "音频验证失败"
            screenshot_step(sb, "audio_failed")
            return False, None, error_msg, server_name

        # 等待验证完成
        print("⏳ 等待验证完成...")
        time.sleep(5)
        screenshot_step(sb, "after_captcha")

        # ========== 4. 提交续期 ==========
        print("🔘 第二次点击 Renew（提交续期）...")
        if not click_renew():
            error_msg = "第二次点击失败"
            screenshot_step(sb, "second_renew_failed")
            return False, None, error_msg, server_name

        screenshot_step(sb, "after_submit")
        print("⏳ 等待续期处理（15秒）...")
        time.sleep(15)

        # ---- 刷新页面 ----
        print("🔄 刷新页面...")
        sb.open(RENEW_URL)
        sb.wait_for_ready_state_complete()
        sb.sleep(5)
        screenshot_step(sb, "after_reload")

        # ========== 5. 提取新的到期时间 ==========
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

        # 如果未获取到，尝试再次提交
        if not new_expiry_str:
            print("⚠️ 首次提交未获取到新到期时间，尝试再次提交...")
            click_renew()
            time.sleep(15)
            sb.open(RENEW_URL)
            sb.wait_for_ready_state_complete()
            sb.sleep(5)
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
            print(f"📅 二次尝试后新到期时间: {new_expiry_str}")

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
    print("🚀 Starting Host2Play renewal (Audio CAPTCHA solution)")
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
