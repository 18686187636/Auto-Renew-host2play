#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Host2Play 自动续期脚本（SeleniumBase 版本）
- 使用真实浏览器绕过 Cloudflare / reCAPTCHA
- 自动点击 Renew server 按钮
- 解析最新到期时间
- 发送北京时间 Telegram 通知
- 管理 cron-job.org 间隔任务（470 分钟）
- 将到期时间写入 expiry.txt 并提交
"""

import os
import sys
import json
import time
import re
import requests
from datetime import datetime, timedelta
import pytz
from seleniumbase import SB

# ==================== 配置 ====================
RENEW_URL = "https://host2play.gratis/server/renew?i=d78082ca-90f1-4d7c-afe4-8196a1d6e101"
EXPIRY_FILE = "expiry.txt"

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
CRONJOB_API_KEY = os.getenv("CRONJOB_API_KEY")
CRONJOB_JOB_ID = os.getenv("CRONJOB_JOB_ID")
CAPTCHA_API_KEY = os.getenv("CAPTCHA_API_KEY")   # 备用
GH_TOKEN = os.getenv("GH_TOKEN")
REPO_OWNER = os.getenv("REPO_OWNER")
REPO_NAME = os.getenv("REPO_NAME")
WORKFLOW_FILE = os.getenv("WORKFLOW_FILE", "renew.yml")
BRANCH = os.getenv("BRANCH", "main")

# ==================== 辅助函数 ====================
def send_tg_message(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TG_CHAT_ID, "text": text}, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"Telegram send error: {e}")

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

# ==================== 续期核心（SeleniumBase） ====================
def perform_renewal_with_browser():
    """
    使用 SeleniumBase 浏览器自动化完成续期
    返回 (成功标志, 新的到期时间 datetime 对象或 None, 错误信息)
    """
    expiry_dt = None
    error_msg = None
    success = False

    with SB(uc=True, headless=True, page_load_strategy='eager') as sb:
        print("🌐 Opening renewal page...")
        sb.open(RENEW_URL)
        sb.wait_for_ready_state_complete()
        sb.sleep(3)
        screenshot_step(sb, "page_loaded")

        # 1. 尝试获取当前到期时间（用于对比）
        current_expiry_str = None
        try:
            # 根据实际页面结构调整选择器，这里假设时间显示在某个元素中
            # 常见模式：<span id="expiry">2026-07-25</span> 或类似
            expiry_elem = sb.find_element('span:contains("Expires")', timeout=5)
            if expiry_elem:
                text = expiry_elem.text
                match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
                if match:
                    current_expiry_str = match.group(1)
                    print(f"📅 Current expiry (raw): {current_expiry_str}")
        except Exception as e:
            print(f"⚠️ Could not read current expiry: {e}")

        # 2. 点击 Renew server 按钮
        print("🔘 Clicking Renew server button...")
        try:
            # 多种选择器备选
            btn_selectors = [
                'button.btn-primary:contains("Renew server")',
                'button:contains("Renew server")',
                'button[onclick*="renew()"]'
            ]
            clicked = False
            for sel in btn_selectors:
                try:
                    sb.uc_click(sel, timeout=5)
                    clicked = True
                    print(f"✅ Clicked using selector: {sel}")
                    break
                except:
                    continue
            if not clicked:
                # 尝试 JavaScript 点击
                btn = sb.find_element('button:contains("Renew server")', timeout=5)
                sb.driver.execute_script("arguments[0].click();", btn)
                clicked = True
                print("✅ Clicked via JavaScript")
            if not clicked:
                raise Exception("No clickable Renew button found")
        except Exception as e:
            error_msg = f"Click Renew button failed: {e}"
            screenshot_step(sb, "click_failed")
            return False, None, error_msg

        screenshot_step(sb, "after_click")

        # 3. 等待续期完成（可能需要几秒）
        print("⏳ Waiting for renewal to complete...")
        time.sleep(5)   # 等待 AJAX 处理

        # 4. 检查续期是否成功（看是否有成功提示或到期时间变化）
        # 尝试查找成功消息
        success_msg = None
        try:
            alert = sb.find_element('.alert-success', timeout=3)
            if alert:
                success_msg = alert.text
                print(f"✅ Success alert: {success_msg}")
        except:
            pass

        # 若没有成功消息，则尝试重新获取到期时间，看是否更新
        # 刷新页面或直接在当前页面找新时间
        # 重新加载页面以获取最新数据（续期后可能页面刷新）
        print("🔄 Refreshing page to get updated expiry...")
        sb.open(RENEW_URL)   # 重新打开页面
        sb.wait_for_ready_state_complete()
        sb.sleep(2)
        screenshot_step(sb, "after_reload")

        # 提取新的到期时间
        new_expiry_str = None
        try:
            # 尝试多种选择器
            selectors = [
                'span:contains("Expires")',
                'div:contains("Expires")',
                'span.fw-bold',
                '.expiry-date',
                '#expiry'
            ]
            for sel in selectors:
                try:
                    elem = sb.find_element(sel, timeout=3)
                    if elem:
                        text = elem.text
                        match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
                        if match:
                            new_expiry_str = match.group(1)
                            break
                except:
                    continue
            if not new_expiry_str:
                # 也许在页面源代码中
                page_source = sb.get_page_source()
                match = re.search(r'Expires? on:?\s*(\d{4}-\d{2}-\d{2})', page_source, re.IGNORECASE)
                if match:
                    new_expiry_str = match.group(1)
            if new_expiry_str:
                print(f"📅 New expiry (raw): {new_expiry_str}")
                # 解析为 datetime (假设 UTC)
                try:
                    expiry_dt = datetime.strptime(new_expiry_str, "%Y-%m-%d")
                    expiry_dt = expiry_dt.replace(tzinfo=pytz.UTC)
                    success = True
                except ValueError:
                    error_msg = f"Invalid expiry format: {new_expiry_str}"
            else:
                error_msg = "Could not find new expiry date after renewal"
        except Exception as e:
            error_msg = f"Error parsing expiry: {e}"

        if success:
            print(f"✅ Renewal successful, new expiry: {expiry_dt}")
            # 如果续期成功但没有成功消息，可以补充
            if not success_msg:
                success_msg = "Renewal completed"
        else:
            if not error_msg:
                error_msg = "Renewal failed (no expiry update detected)"
            # 尝试获取错误提示
            try:
                err = sb.find_element('.alert-danger', timeout=2)
                if err:
                    error_msg = err.text
            except:
                pass
            screenshot_step(sb, "renewal_failed")

    return success, expiry_dt, error_msg

# ==================== cron-job.org 管理（同前） ====================
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

# ==================== 主流程 ====================
def main():
    print("🚀 Starting Host2Play renewal with SeleniumBase")
    beijing_time = get_beijing_time()
    base_msg = (
        f"🔄 Host2Play 续期\n"
        f"🕐 北京时间: {beijing_time}\n"
        f"🔗 {RENEW_URL}"
    )

    success, new_expiry, error = perform_renewal_with_browser()

    if success and new_expiry:
        # 写入并提交
        write_expiry(new_expiry)
        commit_expiry_file()

        # 发送成功通知（北京时间）
        beijing_tz = pytz.timezone('Asia/Shanghai')
        expiry_beijing = new_expiry.astimezone(beijing_tz)
        msg = (
            f"✅ 续期成功\n"
            f"新到期时间: {expiry_beijing.strftime('%Y-%m-%d %H:%M:%S')} (北京时间)\n"
            f"续期链接: {RENEW_URL}"
        )
        send_tg_message(msg)
        print(msg)

        # 管理 cron-job
        ensure_cronjob()
    else:
        # 失败通知
        err_msg = f"❌ 续期失败\n错误信息: {error or '未知错误'}\n续期链接: {RENEW_URL}"
        send_tg_message(err_msg)
        print(err_msg)
        sys.exit(1)

if __name__ == "__main__":
    main()
