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

# ==================== 配置 ====================
RENEW_URL = "https://host2play.gratis/server/renew?i=d78082ca-90f1-4d7c-afe4-8196a1d6e101"
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

# ==================== 核心续期（修复返回值 + 通用选择器） ====================
def perform_renewal_with_browser():
    """
    使用 SeleniumBase 打开页面并尝试续期。
    返回 (success, expiry_datetime, error_message, server_name)
    """
    expiry_dt = None
    error_msg = None
    success = False
    server_name = "Unknown"

    with SB(uc=True, headless=True, page_load_strategy='eager') as sb:
        print("🌐 Opening renewal page...")
        sb.open(RENEW_URL)
        sb.wait_for_ready_state_complete()
        sb.sleep(5)  # 等待 Cloudflare 和动态内容
        screenshot_step(sb, "page_loaded")

        # ---- 调试输出页面信息 ----
        print(f"📄 Page title: {sb.get_title()}")
        page_source = sb.get_page_source()
        # 截取部分源码以便分析（仅当调试）
        if len(page_source) > 200:
            print(f"📄 Source snippet: {page_source[:200]}...")

        # ---- 获取服务器名称（如果存在） ----
        try:
            # 尝试多种选择器
            for sel in ['#serverName', '.server-name', 'h3:contains("Server")', 'div:contains("Server")']:
                elem = sb.find_element(sel, timeout=1)
                if elem:
                    server_name = elem.text.strip()
                    break
        except:
            pass

        # ---- 获取当前到期时间 ----
        old_expiry_str = None
        try:
            # 可能的选择器
            for sel in ['#expireDate', '.expiry-date', 'span:contains("Expires")', 'div:contains("Expires")']:
                elem = sb.find_element(sel, timeout=1)
                if elem:
                    text = elem.text.strip()
                    # 提取日期
                    match = re.search(r'(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)', text)
                    if match:
                        old_expiry_str = match.group(1)
                        break
        except:
            pass
        print(f"📅 Current expiry (raw): {old_expiry_str}")

        # ---- 点击 Renew server 按钮 ----
        print("🔘 Clicking Renew server button...")
        clicked = False
        try:
            # 更通用的选择器列表
            btn_selectors = [
                'button.btn-primary:contains("Renew")',
                'button:contains("Renew server")',
                'button:contains("Renew")',
                'a.btn-primary:contains("Renew")',
                'a:contains("Renew server")',
                'button[onclick*="renew()"]',
                'input[value="Renew"]',
                # 尝试通过 class 和文本
                '.btn-primary:contains("Renew")',
                'button.btn-primary'
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
                # 最后尝试：使用 JavaScript 执行 renew() 函数（如果存在）
                try:
                    sb.execute_script("renew();")
                    clicked = True
                    print("✅ Clicked via JavaScript renew()")
                except:
                    pass

            if not clicked:
                # 尝试查找所有按钮并点击第一个包含 "Renew" 的
                buttons = sb.find_elements('button')
                for btn in buttons:
                    if 'renew' in btn.text.lower():
                        sb.driver.execute_script("arguments[0].click();", btn)
                        clicked = True
                        print("✅ Clicked via JavaScript on button with text containing 'renew'")
                        break

            if not clicked:
                raise Exception("Could not find any clickable Renew button")
        except Exception as e:
            error_msg = f"Click Renew button failed: {e}"
            screenshot_step(sb, "click_failed")
            # 统一返回 4 个值
            return False, None, error_msg, server_name

        screenshot_step(sb, "after_click")

        # ---- 等待续期完成 ----
        print("⏳ Waiting for renewal to complete...")
        time.sleep(10)  # 给足时间

        # ---- 刷新页面并获取新的到期时间 ----
        print("🔄 Refreshing page to get updated expiry...")
        sb.open(RENEW_URL)
        sb.wait_for_ready_state_complete()
        sb.sleep(5)
        screenshot_step(sb, "after_reload")

        # ---- 解析新到期时间 ----
        new_expiry_str = None
        try:
            for sel in ['#expireDate', '.expiry-date', 'span:contains("Expires")', 'div:contains("Expires")']:
                elem = sb.find_element(sel, timeout=2)
                if elem:
                    text = elem.text.strip()
                    match = re.search(r'(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)', text)
                    if match:
                        new_expiry_str = match.group(1)
                        break
        except:
            pass

        print(f"📅 New expiry (raw): {new_expiry_str}")

        # ---- 判断续期是否成功 ----
        if new_expiry_str and new_expiry_str != old_expiry_str:
            try:
                # 尝试多种格式
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

    # 统一返回 4 个值
    return success, expiry_dt, error_msg, server_name

# ==================== cron-job.org 管理 ====================
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
    print("🚀 Starting Host2Play renewal with SeleniumBase")
    beijing_time = get_beijing_time()

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
