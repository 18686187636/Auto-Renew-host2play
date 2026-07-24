#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Host2Play 自动续期脚本（增强版）
- 使用 Session 保持 Cookies
- 模拟完整浏览器请求头
- 自动提取 CSRF Token（如需要）
- 支持重试机制
"""

import os
import sys
import json
import time
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import pytz

# ==================== 配置 ====================
RENEW_URL = "https://host2play.gratis/server/renew?i=d78082ca-90f1-4d7c-afe4-8196a1d6e101"
EXPIRY_FILE = "expiry.txt"

# 环境变量
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
CRONJOB_API_KEY = os.getenv("CRONJOB_API_KEY")
CRONJOB_JOB_ID = os.getenv("CRONJOB_JOB_ID")
CAPTCHA_API_KEY = os.getenv("CAPTCHA_API_KEY")
GH_TOKEN = os.getenv("GH_TOKEN")
REPO_OWNER = os.getenv("REPO_OWNER")
REPO_NAME = os.getenv("REPO_NAME")
WORKFLOW_FILE = os.getenv("WORKFLOW_FILE", "renew.yml")
BRANCH = os.getenv("BRANCH", "main")

# ==================== 会话管理 ====================
def create_session():
    """创建带有完整浏览器请求头的 Session"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    })
    return session

# ==================== Telegram 通知 ====================
def send_tg_message(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("Telegram credentials missing, skip notification.")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": text}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"Failed to send TG message: {e}")

# ==================== 到期时间读写与提交 ====================
def read_expiry():
    if os.path.exists(EXPIRY_FILE):
        with open(EXPIRY_FILE, 'r') as f:
            date_str = f.read().strip()
            if date_str:
                return datetime.fromisoformat(date_str)
    return None

def write_expiry(dt):
    with open(EXPIRY_FILE, 'w') as f:
        f.write(dt.isoformat())

def commit_expiry_file():
    os.system('git config user.name "github-actions[bot]"')
    os.system('git config user.email "github-actions[bot]@users.noreply.github.com"')
    os.system('git add expiry.txt')
    os.system('git commit -m "Update expiry date [skip ci]" || echo "No changes to commit"')
    os.system('git push')

# ==================== reCAPTCHA 处理 ====================
def get_recaptcha_sitekey(session, page_url):
    """使用 session 获取页面并提取 sitekey"""
    resp = session.get(page_url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, 'html.parser')
    for script in soup.find_all('script'):
        if script.string and 'sitekey' in script.string:
            match = re.search(r'sitekey\s*:\s*"([^"]+)"', script.string)
            if match:
                return match.group(1)
    elem = soup.find(attrs={"data-sitekey": True})
    if elem:
        return elem['data-sitekey']
    input_tag = soup.find('input', {'name': 'g-recaptcha-response'})
    if input_tag and input_tag.get('data-sitekey'):
        return input_tag['data-sitekey']
    raise Exception("Unable to find reCAPTCHA sitekey")

def solve_captcha_with_2captcha(sitekey, page_url):
    if not CAPTCHA_API_KEY:
        raise Exception("CAPTCHA_API_KEY missing")
    submit_url = "http://2captcha.com/in.php"
    params = {
        "key": CAPTCHA_API_KEY,
        "method": "userrecaptcha",
        "googlekey": sitekey,
        "pageurl": page_url,
        "json": 1
    }
    resp = requests.post(submit_url, data=params, timeout=20)
    result = resp.json()
    if result.get("status") != 1:
        raise Exception(f"2captcha submit error: {result}")
    captcha_id = result.get("request")
    if not captcha_id:
        raise Exception("No captcha ID returned")
    poll_url = "http://2captcha.com/res.php"
    for _ in range(60):
        time.sleep(5)
        poll_params = {
            "key": CAPTCHA_API_KEY,
            "action": "get",
            "id": captcha_id,
            "json": 1
        }
        resp = requests.get(poll_url, params=poll_params, timeout=10)
        data = resp.json()
        if data.get("status") == 1:
            return data.get("request")
        if data.get("request") == "CAPCHA_NOT_READY":
            continue
        raise Exception(f"2captcha error: {data}")
    raise Exception("2captcha polling timeout")

# ==================== 续期核心操作（增强版） ====================
def perform_renewal():
    """使用 Session 执行完整续期流程"""
    session = create_session()

    # 1. 首次 GET 页面（获取 Cookies 和 sitekey）
    print("Fetching renewal page...")
    sitekey = get_recaptcha_sitekey(session, RENEW_URL)
    print(f"Got sitekey: {sitekey}")

    # 2. 打码
    token = solve_captcha_with_2captcha(sitekey, RENEW_URL)
    print("Got captcha token")

    # 3. 提取 CSRF Token（若有）—— 从页面中查找 name="csrf_token" 的 input
    # 先再次 GET 页面（确保最新），或直接从之前响应的 soup 中提取，我们重新请求一次以获取最新 token
    resp = session.get(RENEW_URL, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, 'html.parser')
    csrf_token = None
    csrf_input = soup.find('input', {'name': 'csrf_token'}) or soup.find('input', {'name': '_token'})
    if csrf_input:
        csrf_token = csrf_input.get('value')
        print(f"Found CSRF token: {csrf_token}")

    # 4. 构建续期请求（需根据实际抓包调整）
    renew_api = "https://host2play.gratis/server/renew"   # 请核实实际 API 地址
    payload = {
        "i": "d78082ca-90f1-4d7c-afe4-8196a1d6e101",
        "g-recaptcha-response": token,
    }
    if csrf_token:
        payload["csrf_token"] = csrf_token   # 或 "_token"

    # 设置 POST 请求头（保持 session 中的通用头，增加 Referer）
    headers = {
        "Referer": RENEW_URL,
        "Origin": "https://host2play.gratis",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    # 使用 session.post 自动带上 cookies
    print("Sending renewal request...")
    resp = session.post(renew_api, data=payload, headers=headers, timeout=30)

    if resp.status_code != 200:
        # 尝试重试一次（可能因 token 过期）
        print(f"Renewal failed with status {resp.status_code}, retrying after 5s...")
        time.sleep(5)
        # 重新获取 CSRF token 和 sitekey（可能变化）
        sitekey = get_recaptcha_sitekey(session, RENEW_URL)
        token = solve_captcha_with_2captcha(sitekey, RENEW_URL)
        resp = session.post(renew_api, data=payload, headers=headers, timeout=30)
        if resp.status_code != 200:
            raise Exception(f"Renewal request failed after retry: {resp.status_code} - {resp.text[:200]}")

    # 5. 解析新的到期时间
    try:
        data = resp.json()
        expiry_str = data.get("expiry") or data.get("new_expiry") or data.get("expires")
        if not expiry_str:
            raise Exception("No expiry field in JSON response")
    except json.JSONDecodeError:
        soup = BeautifulSoup(resp.text, 'html.parser')
        text = soup.get_text()
        match = re.search(r'Expires? on:?\s*(\d{4}-\d{2}-\d{2})', text, re.IGNORECASE)
        if not match:
            match = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', text)
        if match:
            expiry_str = match.group(1)
        else:
            raise Exception("Could not parse expiry date from response")

    try:
        new_expiry = datetime.fromisoformat(expiry_str)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                new_expiry = datetime.strptime(expiry_str, fmt)
                break
            except ValueError:
                continue
        else:
            raise Exception(f"Unrecognized date format: {expiry_str}")

    if new_expiry.tzinfo is None:
        new_expiry = new_expiry.replace(tzinfo=pytz.UTC)
    return new_expiry

# ==================== cron-job.org 管理（不变） ====================
def ensure_cronjob():
    if not CRONJOB_API_KEY:
        print("CRONJOB_API_KEY missing, skip cronjob setup.")
        return
    if not GH_TOKEN:
        print("GH_TOKEN missing, cannot set up cron-job trigger.")
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
                print("Cron-job created, but no ID returned.")
        else:
            print("Cron-job updated successfully.")
    except Exception as e:
        print(f"Failed to manage cron-job: {e}")

# ==================== 主入口 ====================
def main():
    try:
        new_expiry = perform_renewal()
        print(f"Renewal successful, new expiry: {new_expiry}")

        write_expiry(new_expiry)
        commit_expiry_file()

        beijing_tz = pytz.timezone('Asia/Shanghai')
        now_beijing = datetime.now(beijing_tz)
        expiry_beijing = new_expiry.astimezone(beijing_tz)

        msg = (
            f"✅ 服务器续期成功\n"
            f"续期时间：{now_beijing.strftime('%Y-%m-%d %H:%M:%S')} (北京时间)\n"
            f"新到期时间：{expiry_beijing.strftime('%Y-%m-%d %H:%M:%S')} (北京时间)\n"
            f"续期链接：{RENEW_URL}"
        )
        send_tg_message(msg)

        ensure_cronjob()

    except Exception as e:
        error_msg = f"❌ 续期失败：{str(e)}\n页面链接：{RENEW_URL}"
        print(error_msg)
        send_tg_message(error_msg)
        sys.exit(1)

if __name__ == "__main__":
    main()
