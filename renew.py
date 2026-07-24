#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Host2Play 自动续期脚本（SeleniumBase 修正版）
基于 oyz8/Host2Play 项目的页面元素选择器修正
参考: https://github.com/oyz8/Host2Play
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

# ==================== 续期核心（基于实际页面结构） ====================
def perform_renewal_with_browser():
    """
    使用 SeleniumBase 浏览器自动化完成续期
    参考 oyz8/Host2Play 项目的页面元素选择器
    """
    expiry_dt = None
    error_msg = None
    success = False
    server_name = "未知服务器"

    with SB(uc=True, headless=True, page_load_strategy='eager') as sb:
        print("🌐 Opening renewal page...")
        sb.open(RENEW_URL)
        sb.wait_for_ready_state_complete()
        sb.sleep(3)
        screenshot_step(sb, "page_loaded")

        # 1. 获取服务器名称和当前到期时间（参考 DrissionPage 选择器）
        try:
            # 服务器名称: #serverName
            name_elem = sb.find_element('#serverName', timeout=3)
            if name_elem:
                server_name = name_elem.text.strip()
                print(f"📛 Server name: {server_name}")
        except Exception as e:
            print(f"⚠️ Could not get server name: {e}")

        # 获取到期时间: #expireDate 或 "Expires in:" / "Deletes on:" 文本
        old_expire_str = None
        try:
            # 尝试 #expireDate
            exp_elem = sb.find_element('#expireDate', timeout=2)
            if exp_elem:
                old_expire_str = exp_elem.text.strip()
        except:
            pass
        
        if not old_expire_str:
            # 回退: 查找包含 "Expires in:" 或 "Deletes on:" 的元素
            try:
                for text_pattern in ['Expires in:', 'Deletes on:']:
                    try:
                        elem = sb.find_element(f'text:{text_pattern}', timeout=1)
                        if elem:
                            text = elem.text.strip()
                            if ':' in text:
                                old_expire_str = text.split(':', 1)[1].strip()
                            else:
                                old_expire_str = text
                            break
                    except:
                        continue
            except Exception as e:
                print(f"⚠️ Could not get expiry via text: {e}")

        print(f"📅 Current expiry (raw): {old_expire_str}")

        # 2. 点击 Renew server 按钮
        print("🔘 Clicking Renew server button...")
        clicked = False
        try:
            # 尝试多种选择器
            btn_selectors = [
                'button.btn-primary:contains("Renew server")',
                'button:contains("Renew server")',
                'button[onclick*="renew()"]',
                '.btn-primary:contains("Renew")',
                'button:contains("Renew")'
            ]
            for sel in btn_selectors:
                try:
                    sb.uc_click(sel, timeout=5)
                    clicked = True
                    print(f"✅ Clicked using selector: {sel}")
                    break
                except:
                    continue
            
            if not clicked:
                # 尝试通过 JavaScript 查找并点击
                btn = sb.find_element('button:contains("Renew")', timeout=5)
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

        # 3. 等待续期处理
        print("⏳ Waiting for renewal to complete...")
        time.sleep(8)

        # 4. 检查续期结果并获取新到期时间
        print("🔄 Refreshing page to get updated expiry...")
        sb.open(RENEW_URL)
        sb.wait_for_ready_state_complete()
        sb.sleep(3)
        screenshot_step(sb, "after_reload")

        # 获取新的到期时间
        new_expiry_str = None
        try:
            exp_elem = sb.find_element('#expireDate', timeout=3)
            if exp_elem:
                new_expiry_str = exp_elem.text.strip()
        except:
            pass

        if not new_expiry_str:
            try:
                for text_pattern in ['Expires in:', 'Deletes on:']:
                    try:
                        elem = sb.find_element(f'text:{text_pattern}', timeout=1)
                        if elem:
                            text = elem.text.strip()
                            if ':' in text:
                                new_expiry_str = text.split(':', 1)[1].strip()
                            else:
                                new_expiry_str = text
                            break
                    except:
                        continue
            except Exception as e:
                print(f"⚠️ Could not get new expiry via text: {e}")

        print(f"📅 New expiry (raw): {new_expiry_str}")

        # 5. 解析到期时间
        if new_expiry_str and new_expiry_str != old_expire_str:
            try:
                # 尝试解析 "2026-07-25 12:00" 或 "2026-07-25" 格式
                for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                    try:
                        expiry_dt = datetime.strptime(new_expiry_str, fmt)
                        expiry_dt = expiry_dt.replace(tzinfo=pytz.UTC)
                        success = True
                        break
                    except ValueError:
                        continue
                if success:
                    print(f"✅ Renewal successful, new expiry: {expiry_dt}")
                else:
                    error_msg = f"Unrecognized date format: {new_expiry_str}"
            except Exception as e:
                error_msg = f"Error parsing expiry: {e}"
        else:
            if new_expiry_str == old_expire_str:
                error_msg = "Expiry date unchanged - renewal may have failed"
            else:
                error_msg = "Could not find new expiry date after renewal"

        if not success and not error_msg:
            error_msg = "Renewal failed (no expiry update detected)"

        if not success:
            screenshot_step(sb, "renewal_failed")

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

# ==================== 主流程 ====================
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
