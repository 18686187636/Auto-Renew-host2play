#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Host2Play 自动续期脚本
触发频率：每 470 分钟（7小时50分）由 cron-job.org 调用
功能：
- 访问续期页面，提取 reCAPTCHA sitekey
- 通过 2captcha 打码获取验证令牌
- 提交续期请求，解析新的到期时间
- 通过 Telegram 通知结果（使用北京时间 UTC+8，并附带续期链接）
- 自动管理 cron-job.org 间隔任务（若 Job ID 不存在则新建）
- 将到期时间写入 expiry.txt 并提交到仓库
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

# ==================== 配置（从环境变量读取） ====================
RENEW_URL = "https://host2play.gratis/server/renew?i=d78082ca-90f1-4d7c-afe4-8196a1d6e101"
EXPIRY_FILE = "expiry.txt"

# 从 GitHub Secrets 注入
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
CRONJOB_API_KEY = os.getenv("CRONJOB_API_KEY")
CRONJOB_JOB_ID = os.getenv("CRONJOB_JOB_ID")          # 若为空则新建
CAPTCHA_API_KEY = os.getenv("CAPTCHA_API_KEY")
GH_TOKEN = os.getenv("GH_TOKEN")                      # GitHub Token（需具备 workflow 权限）
REPO_OWNER = os.getenv("REPO_OWNER")
REPO_NAME = os.getenv("REPO_NAME")
WORKFLOW_FILE = os.getenv("WORKFLOW_FILE", "renew.yml")
BRANCH = os.getenv("BRANCH", "main")

# ==================== Telegram 通知 ====================
def send_tg_message(text):
    """发送 Telegram 消息"""
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
    """从文件读取上次记录的到期时间（ISO格式）"""
    if os.path.exists(EXPIRY_FILE):
        with open(EXPIRY_FILE, 'r') as f:
            date_str = f.read().strip()
            if date_str:
                return datetime.fromisoformat(date_str)
    return None

def write_expiry(dt):
    """写入新的到期时间（ISO格式）"""
    with open(EXPIRY_FILE, 'w') as f:
        f.write(dt.isoformat())

def commit_expiry_file():
    """提交 expiry.txt 到仓库（使用 [skip ci] 避免循环触发）"""
    os.system('git config user.name "github-actions[bot]"')
    os.system('git config user.email "github-actions[bot]@users.noreply.github.com"')
    os.system('git add expiry.txt')
    os.system('git commit -m "Update expiry date [skip ci]" || echo "No changes to commit"')
    os.system('git push')

# ==================== reCAPTCHA 处理 ====================
def get_recaptcha_sitekey(page_url):
    """从续期页面提取 reCAPTCHA sitekey"""
    resp = requests.get(page_url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, 'html.parser')

    # 常见 sitekey 位置：script 中的 sitekey 字段或 data-sitekey 属性
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

    raise Exception("Unable to find reCAPTCHA sitekey on page")

def solve_captcha_with_2captcha(sitekey, page_url):
    """使用 2captcha 解决 reCAPTCHA v2，返回验证令牌"""
    if not CAPTCHA_API_KEY:
        raise Exception("CAPTCHA_API_KEY environment variable missing")

    # 提交任务
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

    # 轮询结果
    poll_url = "http://2captcha.com/res.php"
    for _ in range(60):  # 最多等待 60 * 5 = 300 秒
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

# ==================== 续期核心操作 ====================
def perform_renewal():
    """
    执行续期流程，返回新的到期时间（datetime 对象）
    注意：需要根据实际网站抓包调整请求 URL、参数和响应解析
    """
    # 1. 获取 sitekey
    sitekey = get_recaptcha_sitekey(RENEW_URL)
    print(f"Got sitekey: {sitekey}")

    # 2. 打码
    token = solve_captcha_with_2captcha(sitekey, RENEW_URL)
    print("Got captcha token")

    # 3. 模拟点击 Renew 按钮
    # 请根据浏览器开发者工具抓包确认实际的 API 地址和参数
    renew_api = "https://host2play.gratis/server/renew"   # 示例地址，请核实
    payload = {
        "i": "d78082ca-90f1-4d7c-afe4-8196a1d6e101",      # 示例参数，请核实
        "g-recaptcha-response": token,
        # 可能还需要 CSRF token 等其他字段，请从页面提取
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": RENEW_URL,
    }
    resp = requests.post(renew_api, data=payload, headers=headers, timeout=30)
    if resp.status_code != 200:
        raise Exception(f"Renewal request failed with status {resp.status_code}: {resp.text[:200]}")

    # 4. 解析新的到期时间
    # 假设响应为 JSON 包含 "expiry" 字段，若为 HTML 则尝试从页面提取
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

    # 解析为 datetime，支持多种格式
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

    # 如果未指定时区，假设为 UTC
    if new_expiry.tzinfo is None:
        new_expiry = new_expiry.replace(tzinfo=pytz.UTC)

    return new_expiry

# ==================== cron-job.org 管理 ====================
def ensure_cronjob():
    """
    创建或更新 cron-job.org 任务，使其每隔 470 分钟触发一次当前工作流。
    若 CRONJOB_JOB_ID 存在则更新，否则新建。
    使用符合 GitHub API 规范的请求头和正文。
    """
    if not CRONJOB_API_KEY:
        print("CRONJOB_API_KEY missing, skip cronjob setup.")
        return
    if not GH_TOKEN:
        print("GH_TOKEN missing, cannot set up cron-job trigger.")
        return

    # GitHub Actions workflow_dispatch 触发 URL
    trigger_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    # 必须包含的请求头（根据 GitHub API 要求）
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GH_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json"
    }
    body = {"ref": BRANCH}

    # 构建 interval 任务数据（每 470 分钟）
    job_data = {
        "name": "Host2Play Renewal (470min)",
        "url": trigger_url,
        "request_method": "POST",
        "request_headers": headers,
        "request_body": json.dumps(body),   # 必须是 JSON 字符串
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
                print("Please save this ID as CRONJOB_JOB_ID secret for future updates.")
            else:
                print("Cron-job created, but no ID returned.")
        else:
            print("Cron-job updated successfully.")
    except Exception as e:
        print(f"Failed to manage cron-job: {e}")
        # 不抛出异常，以免影响续期主流程

# ==================== 主入口 ====================
def main():
    try:
        new_expiry = perform_renewal()
        print(f"Renewal successful, new expiry: {new_expiry}")

        # 写入到期时间并提交到仓库
        write_expiry(new_expiry)
        commit_expiry_file()

        # ---------- 使用北京时间（UTC+8）发送通知 ----------
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

        # 确保 cron-job 存在（首次运行或更新配置）
        ensure_cronjob()

    except Exception as e:
        error_msg = f"❌ 续期失败：{str(e)}\n页面链接：{RENEW_URL}"
        print(error_msg)
        send_tg_message(error_msg)
        sys.exit(1)

if __name__ == "__main__":
    main()
