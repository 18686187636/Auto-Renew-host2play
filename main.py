#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import random
import html
import json
import requests
import tempfile
import subprocess
from datetime import datetime, timezone, timedelta
from xvfbwrapper import Xvfb
from DrissionPage import ChromiumPage, ChromiumOptions

try:
    import speech_recognition as sr
    from pydub import AudioSegment
except ImportError:
    pass

# ==================== 配置区域 ====================
RENEW_URLS = [
    "https://host2play.gratis/server/renew?i=62c3c856-9400-4220-9ec0-c3cd6348d5f8",
    # 添加更多链接
]

MAX_CAPTCHA = 3
MAX_RENEW_RETRIES_PER_URL = 20

# ==================== 环境变量读取 ====================
CAPTCHA_API_KEY = os.getenv("CAPTCHA_API_KEY")
CRONJOB_API_KEY = os.getenv("CRONJOB_API_KEY")
CRONJOB_JOB_ID = os.getenv("CRONJOB_JOB_ID")          # 保留但不再使用
GH_TOKEN = os.getenv("GH_TOKEN")
REPO_OWNER = os.getenv("REPO_OWNER")
REPO_NAME = os.getenv("REPO_NAME")
WORKFLOW_FILE = os.getenv("WORKFLOW_FILE", "renew.yml")
BRANCH = os.getenv("BRANCH", "main")
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
AUDIO_API_URL = os.getenv("AUDIO_API_URL")

# ==================== 辅助函数 ====================
def mask_url(url):
    import re
    return re.sub(r'(\?i=)([^&]{1})([^&]*)', r'\1\2***', url)

def log(msg, level="INFO"):
    prefix = {"INFO": "[INFO]", "WARN": "[WARN]", "ERROR": "[ERROR]"}.get(level, "[INFO]")
    print(f"{prefix} {msg}", flush=True)

# ==================== Telegram 通知 ====================
def send_tg_photo(token, chat_id, photo_path, caption, parse_mode='HTML'):
    if not token or not chat_id:
        return
    if not photo_path or not os.path.exists(photo_path):
        return
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    try:
        with open(photo_path, "rb") as photo_file:
            requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption, "parse_mode": parse_mode},
                files={"photo": photo_file},
                timeout=30,
            )
        log("Telegram 图片通知发送成功")
    except Exception as e:
        log(f"Telegram 通知异常: {e}", "ERROR")

def send_tg_message(token, chat_id, text):
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    except Exception as e:
        log(f"Telegram 消息异常: {e}", "ERROR")

# ==================== 获取 GitHub 工作流 ID（增强匹配） ====================
def get_github_workflow_id(owner, repo, workflow_file, token):
    """通过 GitHub API 获取工作流文件的数字 ID（支持多种输入格式）"""
    # 清理输入：去除首尾空格、引号
    workflow_file = workflow_file.strip().strip('"').strip("'")
    log(f"🔍 尝试获取工作流 ID，输入文件名: '{workflow_file}'", "DEBUG")

    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        workflows = data.get("workflows", [])
        log(f"📋 仓库中的工作流: {[wf['path'] for wf in workflows]}", "DEBUG")

        # 准备候选匹配项（多种格式）
        candidates = []
        if workflow_file.startswith(".github/workflows/"):
            candidates.append(workflow_file)
        else:
            candidates.append(f".github/workflows/{workflow_file}")
        candidates.append(workflow_file)
        candidates.append(os.path.basename(workflow_file))

        # 去重
        candidates = list(dict.fromkeys(candidates))
        log(f"🔍 将尝试匹配以下路径: {candidates}", "DEBUG")

        for wf in workflows:
            wf_path = wf.get("path", "")
            for cand in candidates:
                if wf_path == cand or os.path.basename(wf_path) == cand:
                    log(f"✅ 找到匹配的工作流: {wf_path} (ID: {wf.get('id')})")
                    return wf.get("id")

        log(f"❌ 未找到任何匹配的工作流。尝试的文件名: {candidates}", "ERROR")
        return None
    except Exception as e:
        log(f"❌ 获取工作流 ID 失败: {e}", "ERROR")
        return None

# ==================== cron-job.org 定时任务管理 ====================
def schedule_cronjob(trigger_timestamp):
    """
    使用 cron-job.org API 创建定时任务，触发时间为 trigger_timestamp（Unix 时间戳）。
    使用数字工作流 ID 构造 URL。
    """
    if not CRONJOB_API_KEY or not GH_TOKEN or not REPO_OWNER or not REPO_NAME:
        log("缺少 CRONJOB_API_KEY 或 GH_TOKEN 等环境变量，跳过定时任务设置", "WARN")
        return False

    # 获取工作流数字 ID
    workflow_id = get_github_workflow_id(REPO_OWNER, REPO_NAME, WORKFLOW_FILE, GH_TOKEN)
    if not workflow_id:
        log("⚠️ 无法获取工作流 ID，跳过调度", "WARN")
        return False

    dt = datetime.fromtimestamp(trigger_timestamp, tz=timezone.utc)
    schedule = {
        "minutes": [dt.minute],
        "hours": [dt.hour],
        "mdays": [dt.day],
        "months": [dt.month],
        "wdays": [-1],
        "timezone": "UTC",
        "expiresAt": (dt + timedelta(minutes=5)).strftime("%Y%m%d%H%M%S")
    }

    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/workflows/{workflow_id}/dispatches"

    payload = {
        "job": {
            "enabled": True,
            "url": url,
            "requestMethod": 1,
            "saveResponses": True,
            "schedule": schedule,
            "extendedData": {
                "headers": {
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {GH_TOKEN}",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "Content-Type": "application/json",
                    "User-Agent": "cron-job.org/1.0"
                },
                "body": json.dumps({"ref": BRANCH})
            }
        }
    }

    headers = {
        "Authorization": f"Bearer {CRONJOB_API_KEY}",
        "Content-Type": "application/json"
    }

    log(f"📤 发送请求到 cron-job.org，触发时间: {dt.strftime('%Y-%m-%d %H:%M UTC')}")
    log(f"请求体: {json.dumps(payload, indent=2)}")

    try:
        resp = requests.put("https://api.cron-job.org/jobs", headers=headers, json=payload, timeout=30)
        log(f"响应状态码: {resp.status_code}")
        log(f"响应内容: {resp.text[:500]}")
        if resp.status_code == 200:
            job_id = resp.json().get("jobId")
            log(f"✅ cron-job 创建成功，ID: {job_id}")
            send_tg_message(
                TG_BOT_TOKEN, TG_CHAT_ID,
                f"📅 cron-job 已创建，ID: `{job_id}`\n触发时间: {dt.strftime('%Y-%m-%d %H:%M UTC')}"
            )
            return True
        else:
            log(f"❌ 创建 cron 任务失败: HTTP {resp.status_code}", "ERROR")
            return False
    except Exception as e:
        log(f"❌ 调用 cron-job.org API 异常: {e}", "ERROR")
        return False

# ==================== WARP IP 去重管理 ====================
class WarpManager:
    def __init__(self):
        self._used_ips: set = set()

    def _run(self, args: list, timeout: int = 30) -> subprocess.CompletedProcess:
        cmd = ["sudo", "warp-cli", "--accept-tos"] + args
        log(f"[WARP] 执行: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.stdout.strip():
            log(f"[WARP] stdout: {result.stdout.strip()}")
        if result.stderr.strip():
            log(f"[WARP] stderr: {result.stderr.strip()}", "WARN")
        return result

    def _get_current_ip(self) -> str:
        try:
            r = subprocess.run(
                ["curl", "-s", "--max-time", "15", "https://api.ipify.org"],
                capture_output=True, text=True, timeout=20
            )
            return r.stdout.strip()
        except Exception:
            return ""

    def _wait_connected(self, max_wait: int = 60) -> bool:
        log(f"[WARP] 等待 VPN 连接就绪（最多 {max_wait}s）...")
        start = time.time()
        while time.time() - start < max_wait:
            try:
                r = subprocess.run(
                    ["curl", "-s", "--max-time", "10",
                     "https://www.cloudflare.com/cdn-cgi/trace"],
                    capture_output=True, text=True, timeout=15
                )
                trace = r.stdout
                if "warp=on" in trace or "warp=plus" in trace:
                    ip_lines = [l for l in trace.splitlines() if l.startswith("ip=")]
                    ip = ip_lines[0].split("=")[1] if ip_lines else "unknown"
                    log(f"[WARP] ✅ VPN 就绪，出口 IP: {ip}")
                    return True
            except Exception as e:
                log(f"[WARP] 等待中... ({e})", "WARN")
            time.sleep(3)
        log("[WARP] ❌ 等待超时，warp 未激活", "ERROR")
        return False

    def _do_one_rotate(self) -> str:
        self._run(["disconnect"])
        time.sleep(2)
        self._run(["registration", "delete"])
        time.sleep(2)
        result = self._run(["registration", "new"], timeout=30)
        if result.returncode != 0:
            log("[WARP] ❌ 注册失败", "ERROR")
            return ""
        time.sleep(3)
        self._run(["connect"])
        time.sleep(5)
        if not self._wait_connected(max_wait=60):
            log("[WARP] ❌ WARP 连接失败", "ERROR")
            return ""
        return self._get_current_ip()

    def rotate_ip(self, attempt_idx: int = 0, max_attempts: int = 5) -> bool:
        log(f"[WARP] ========== 第 {attempt_idx + 1} 次 IP 轮换 ==========")
        log(f"[WARP] 已用 IP 池: {self._used_ips if self._used_ips else '(空)'}")
        old_ip = self._get_current_ip()
        log(f"[WARP] 旧 IP: {old_ip}")
        for i in range(1, max_attempts + 1):
            log(f"[WARP] 轮换尝试 {i}/{max_attempts}")
            new_ip = self._do_one_rotate()
            if not new_ip:
                log(f"[WARP] ⚠️  第 {i} 次轮换失败，继续重试", "WARN")
                continue
            if new_ip in self._used_ips:
                log(f"[WARP] ♻️  IP {new_ip} 已被本次运行使用过，继续尝试...", "WARN")
                continue
            self._used_ips.add(new_ip)
            if new_ip != old_ip:
                log(f"[WARP] ✅ IP 已变化: {old_ip} → {new_ip}")
            else:
                log(f"[WARP] ⚠️  IP 与旧 IP 相同（{new_ip}），但未被本轮其他请求使用，接受", "WARN")
            log(f"[WARP] 已用 IP 池: {self._used_ips}")
            return True
        log(f"[WARP] ⚠️  {max_attempts} 次尝试均为重复 IP，使用当前 IP 继续执行", "WARN")
        new_ip = self._get_current_ip()
        if new_ip:
            self._used_ips.add(new_ip)
        return True

    def record_initial_ip(self):
        ip = self._get_current_ip()
        if ip:
            self._used_ips.add(ip)
            log(f"[WARP] 记录初始 IP: {ip}，已用 IP 池: {self._used_ips}")

_warp_manager = None
def get_warp_manager():
    global _warp_manager
    if _warp_manager is None:
        _warp_manager = WarpManager()
    return _warp_manager

# ==================== reCAPTCHA 辅助函数 ====================
def find_recaptcha_frame(page, kind):
    try:
        for frame in page.get_frames():
            frame_url = frame.url or ""
            if "recaptcha" in frame_url and kind in frame_url:
                return frame
    except Exception:
        pass
    return None

def is_recaptcha_solved(page):
    try:
        for frame in page.get_frames():
            try:
                token = frame.run_js(
                    "return document.querySelector(\"textarea[name='g-recaptcha-response']\")?.value"
                )
                if token and len(token) > 30:
                    return True
            except Exception:
                pass
    except Exception:
        pass
    anchor = find_recaptcha_frame(page, "anchor")
    if anchor:
        try:
            checked = anchor.run_js(
                "return document.querySelector('#recaptcha-anchor')?.getAttribute('aria-checked') === 'true'"
            )
            if checked:
                return True
        except Exception:
            pass
    return False

def is_blocked(page):
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        return False
    try:
        return bool(bframe.run_js("""
            const h = document.querySelector('.rc-doscaptcha-header-text');
            if (h && h.textContent.toLowerCase().includes('try again later')) return true;
            const e = document.querySelector('.rc-audiochallenge-error-message');
            if (e && e.offsetParent !== null) return true;
            return false;
        """))
    except Exception:
        return False

def click_recaptcha_checkbox(page):
    anchor = find_recaptcha_frame(page, "anchor")
    if not anchor:
        for _ in range(120):
            anchor = find_recaptcha_frame(page, "anchor")
            if anchor:
                break
            time.sleep(1)
    if not anchor:
        raise RuntimeError("未找到 reCAPTCHA anchor frame")
    checkbox = anchor.ele('#recaptcha-anchor', timeout=3)
    if not checkbox:
        raise RuntimeError("未找到 reCAPTCHA 复选框")
    page.actions.move_to(checkbox, duration=random.uniform(0.4, 1.0))
    time.sleep(random.uniform(0.2, 0.5))
    try:
        checkbox.click()
    except Exception:
        checkbox.click(by_js=True)
    time.sleep(3)
    if is_blocked(page):
        raise CaptchaBlocked("点击复选框后检测到 IP 被封锁")

def switch_to_audio(page):
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        return False
    try:
        input_box = bframe.ele('#audio-response', timeout=1)
        if input_box and input_box.states.is_displayed:
            return True
    except Exception:
        pass
    for attempt in range(3):
        try:
            audio_btn = bframe.ele('#recaptcha-audio-button', timeout=3)
            if audio_btn:
                try:
                    audio_btn.click()
                except Exception:
                    audio_btn.click(by_js=True)
                time.sleep(3)
                if is_blocked(page):
                    raise CaptchaBlocked("点击音频按钮后检测到 IP 被封锁")
                input_box = bframe.ele('#audio-response', timeout=1)
                if input_box and input_box.states.is_displayed:
                    return True
        except CaptchaBlocked:
            raise
        except Exception:
            pass
        try:
            bframe.run_js("""
                const btn = document.querySelector('#recaptcha-audio-button');
                if (btn) btn.click();
            """)
            time.sleep(3)
            if is_blocked(page):
                raise CaptchaBlocked("JS点击音频按钮后检测到 IP 被封锁")
            input_box = bframe.ele('#audio-response', timeout=1)
            if input_box and input_box.states.is_displayed:
                return True
        except CaptchaBlocked:
            raise
        except Exception:
            pass
        time.sleep(2)
    return False

def is_audio_mode(page):
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        return False
    try:
        input_box = bframe.ele('#audio-response', timeout=1)
        return bool(input_box and input_box.states.is_displayed)
    except Exception:
        return False

def get_audio_url(page):
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        return None
    for _ in range(10):
        try:
            link = bframe.ele('.rc-audiochallenge-tdownload-link', timeout=1)
            if link:
                href = link.attr('href')
                if href and len(href) > 10:
                    return html.unescape(href)
            link = bframe.ele('.rc-audiochallenge-ndownload-link', timeout=1)
            if link:
                href = link.attr('href')
                if href and len(href) > 10:
                    return html.unescape(href)
            audio = bframe.ele('#audio-source', timeout=1)
            if audio:
                src = audio.attr('src')
                if src and len(src) > 10:
                    return html.unescape(src)
        except Exception:
            pass
        time.sleep(1)
    return None

def reload_challenge(page):
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        return
    try:
        reload_btn = bframe.ele('#recaptcha-reload-button', timeout=2)
        if reload_btn:
            try:
                reload_btn.click()
            except Exception:
                reload_btn.click(by_js=True)
            time.sleep(3)
    except Exception:
        pass

# ==================== 语音识别（含备用 API） ====================
def recognize_audio_via_api(mp3_path):
    api_url = os.getenv("AUDIO_API_URL")
    if not api_url or not CAPTCHA_API_KEY:
        log("未配置 AUDIO_API_URL 或 CAPTCHA_API_KEY，跳过 API 语音识别", "WARN")
        return None
    try:
        with open(mp3_path, "rb") as f:
            files = {"audio": f}
            headers = {"Authorization": f"Bearer {CAPTCHA_API_KEY}"}
            resp = requests.post(api_url, files=files, headers=headers, timeout=30)
            resp.raise_for_status()
            result = resp.json()
            text = result.get("text") or result.get("result") or result.get("data")
            if text and len(text) > 0:
                log(f"备用 API 识别结果: {text}")
                return text
            else:
                log(f"备用 API 未返回有效文本: {result}", "WARN")
                return None
    except Exception as e:
        log(f"备用 API 语音识别异常: {e}", "ERROR")
        return None

def recognize_audio(mp3_path):
    try:
        wav_path = mp3_path.replace(".mp3", ".wav")
        AudioSegment.from_mp3(mp3_path).export(wav_path, format="wav")
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as src:
            audio_data = recognizer.record(src)
            text = recognizer.recognize_google(audio_data)
        try:
            os.remove(wav_path)
        except Exception:
            pass
        if text:
            log(f"Google 识别结果: {text}")
            return text
    except Exception as e:
        log(f"Google 语音识别失败: {e}", "WARN")

    log("尝试使用备用 API 进行语音识别...")
    return recognize_audio_via_api(mp3_path)

def fill_and_verify(page, text):
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        return False
    try:
        input_box = bframe.ele('#audio-response', timeout=2)
        if not input_box:
            return False
        input_box.click()
        input_box.clear()
        input_box.input(text)
    except Exception:
        return False
    time.sleep(random.uniform(0.5, 1.5))
    try:
        verify_btn = bframe.ele('#recaptcha-verify-button', timeout=2)
        if verify_btn:
            try:
                verify_btn.click()
            except Exception:
                verify_btn.click(by_js=True)
    except Exception:
        pass
    return True

def download_audio(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.google.com/",
    }
    urls = [url]
    if "recaptcha.net" in url:
        urls.append(url.replace("recaptcha.net", "www.google.com"))
    elif "google.com" in url:
        urls.append(url.replace("www.google.com", "recaptcha.net"))
    for audio_url in urls:
        try:
            r = requests.get(audio_url, headers=headers, timeout=30)
            r.raise_for_status()
            if len(r.content) < 1000:
                continue
            path = tempfile.mktemp(suffix=".mp3")
            with open(path, "wb") as f:
                f.write(r.content)
            return path
        except Exception:
            pass
    return None

class CaptchaBlocked(Exception):
    pass

def solve_recaptcha(page):
    start = time.time()
    while time.time() - start < 15:
        if find_recaptcha_frame(page, "anchor"):
            break
        time.sleep(1)
    else:
        raise RuntimeError("reCAPTCHA 加载超时")

    dl_fails = 0
    for i in range(MAX_CAPTCHA):
        if is_recaptcha_solved(page):
            return True
        if is_blocked(page):
            raise CaptchaBlocked("IP 被 Google reCAPTCHA 封锁")

        if i == 0:
            click_recaptcha_checkbox(page)
            time.sleep(2)
            if is_recaptcha_solved(page):
                return True

        if not is_audio_mode(page):
            if not switch_to_audio(page):
                time.sleep(3)
                if not switch_to_audio(page):
                    click_recaptcha_checkbox(page)
                    time.sleep(3)
                    continue
            time.sleep(random.uniform(2, 4))

        if is_blocked(page):
            raise CaptchaBlocked("音频模式检测到 IP 被封锁")

        audio_url = get_audio_url(page)
        if not audio_url:
            reload_challenge(page)
            continue

        mp3 = download_audio(audio_url)
        if not mp3:
            dl_fails += 1
            if dl_fails >= 3:
                raise RuntimeError("音频连续下载失败")
            reload_challenge(page)
            time.sleep(random.uniform(3, 6))
            continue
        dl_fails = 0

        text = recognize_audio(mp3)
        try:
            os.remove(mp3)
        except Exception:
            pass
        if not text:
            reload_challenge(page)
            time.sleep(3)
            continue

        log(f"识别结果: [{text}]")
        fill_and_verify(page, text)
        time.sleep(5)
        if is_recaptcha_solved(page):
            return True
        reload_challenge(page)
        time.sleep(random.uniform(2, 4))

    raise RuntimeError("验证码达到最大尝试次数")

# ==================== 页面元素获取 ====================
def get_server_name(page):
    try:
        ele = page.ele('#serverName', timeout=2)
        if ele:
            return ele.text.strip()
    except Exception:
        pass
    return "未知"

def get_expire_time(page):
    try:
        ele = page.ele('#expireDate', timeout=2)
        if ele:
            return ele.text.strip()
    except Exception:
        pass
    selectors = ['text:Expires in:', 'text:Deletes on:']
    for selector in selectors:
        try:
            ele = page.ele(selector, timeout=1)
            if ele:
                text = (ele.text or "").strip()
                if ":" in text:
                    return text.split(":", 1)[1].strip()
                if text:
                    return text
        except Exception:
            pass
    return "未知"

def build_notification(success, url, server_name, old_expire, new_expire=None, failure_reason=""):
    masked = mask_url(url)
    if success:
        lines = [
            "✅ 续订成功",
            "",
            f"服务器：{server_name}",
            f"到期: {old_expire} -> {new_expire}",
            f"URL: {url}",
        ]
    else:
        lines = [
            "❌ 续订失败",
            "",
            f"服务器：{server_name}",
            f"URL: {url}",
        ]
        if failure_reason:
            lines.append(f"失败原因: {failure_reason}")
    lines.append("")
    lines.append("Host2Play Auto Renew")
    return "\n".join(lines)

def capture_page_screenshot(page, file_name):
    try:
        page.get_screenshot(path=file_name)
        return file_name
    except Exception as e:
        log(f"截图失败: {e}", "WARN")
        return None

# ==================== 单个 URL 续期流程 ====================
def renew_single_url(url, attempt_idx: int = 0):
    success = False
    server_name = "未知"
    old_expire = "未知"
    new_expire = "未知"
    screenshot_path = None
    failure_reason = ""
    screenshot_dir = "output/screenshots"
    os.makedirs(screenshot_dir, exist_ok=True)

    vdisplay = Xvfb(width=1280, height=720, colordepth=24)
    vdisplay.start()

    try:
        for attempt in range(1, MAX_RENEW_RETRIES_PER_URL + 1):
            log(f"{'='*20} 续期尝试 {attempt}/{MAX_RENEW_RETRIES_PER_URL} {'='*20}")
            page = None
            try:
                co = ChromiumOptions()
                co.set_browser_path('/usr/bin/google-chrome')
                co.set_argument('--no-sandbox')
                co.set_argument('--disable-dev-shm-usage')
                co.set_argument('--disable-gpu')
                co.set_argument('--disable-setuid-sandbox')
                co.set_argument('--disable-software-rasterizer')
                co.set_argument('--disable-extensions')
                co.set_argument('--no-first-run')
                co.set_argument('--no-default-browser-check')
                co.set_argument('--disable-popup-blocking')
                co.set_argument('--window-size=1280,720')
                co.set_argument('--log-level=3')
                co.set_argument('--silent')
                user_data_dir = tempfile.mkdtemp()
                co.set_user_data_path(user_data_dir)
                co.auto_port()
                co.headless(False)
                page = ChromiumPage(co)

                page.add_init_js("""
                    const getParameter = WebGLRenderingContext.prototype.getParameter;
                    WebGLRenderingContext.prototype.getParameter = function(parameter) {
                        if (parameter === 37445) return 'Intel Inc.';
                        if (parameter === 37446) return 'Intel(R) UHD Graphics 630';
                        return getParameter.apply(this, [parameter]);
                    };
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
                """)

                log(f"访问: {mask_url(url)}")
                page.get(url, retry=3)
                time.sleep(random.uniform(5, 8))

                server_name = get_server_name(page)
                old_expire = get_expire_time(page)
                log(f"服务器: {server_name}, 到期时间: {old_expire}")

                page.run_js("""
                    const cssSelectors = ['ins.adsbygoogle', 'iframe[src*="ads"]', '.modal-backdrop'];
                    cssSelectors.forEach(sel => {
                        document.querySelectorAll(sel).forEach(el => el.remove());
                    });
                """)
                time.sleep(2)
                consent_btn = page.ele('tag:button@@text():Consent', timeout=2)
                if consent_btn:
                    consent_btn.click()
                    time.sleep(3)

                for _ in range(3):
                    scroll_y = random.randint(200, 600)
                    page.scroll.down(scroll_y)
                    time.sleep(random.uniform(0.5, 1.5))
                    page.actions.move(random.randint(100, 800), random.randint(100, 500))
                    time.sleep(random.uniform(0.5, 1.0))
                time.sleep(random.uniform(1.0, 2.0))

                log("打开续期弹窗...")
                renew_btn1 = page.ele('xpath://button[contains(text(), "Renew server")]', timeout=3)
                if renew_btn1:
                    try:
                        renew_btn1.click()
                    except Exception:
                        renew_btn1.click(by_js=True)
                else:
                    page.run_js(
                        "document.querySelectorAll('button').forEach(b => "
                        "{if(b.textContent.includes('Renew server')) b.click();});"
                    )
                time.sleep(3)

                for _ in range(8):
                    if (page.ele('text:Expires in:', timeout=0.5)
                            or page.ele('text:Deletes on:', timeout=0.5)):
                        break
                    time.sleep(1)

                renew_btn2 = page.ele('xpath://button[contains(text(), "Renew server")]', timeout=2)
                if renew_btn2:
                    try:
                        renew_btn2.click()
                    except Exception:
                        renew_btn2.click(by_js=True)
                time.sleep(random.uniform(7, 10))

                anchor_frame = find_recaptcha_frame(page, "anchor")
                if not anchor_frame:
                    log("未检测到 reCAPTCHA，检查是否已直接成功")
                    new_expire = get_expire_time(page)
                    if new_expire != old_expire and new_expire != "未知":
                        success = True
                    else:
                        failure_reason = "未找到 reCAPTCHA 验证码区域"
                    break

                log("启动 reCAPTCHA 音频破解...")
                try:
                    solved = solve_recaptcha(page)
                except CaptchaBlocked:
                    log("IP 被封锁，使用 WARP 去重轮换后重试", "WARN")
                    failure_reason = "IP 被 reCAPTCHA 封锁"
                    try:
                        page.quit()
                    except Exception:
                        pass
                    page = None
                    if attempt < MAX_RENEW_RETRIES_PER_URL:
                        get_warp_manager().rotate_ip(attempt_idx=attempt - 1)
                        continue
                    break
                except Exception as e:
                    log(f"reCAPTCHA 异常: {e}", "ERROR")
                    failure_reason = f"reCAPTCHA 异常: {e}"
                    break

                if not solved:
                    failure_reason = "未通过 reCAPTCHA 验证"
                    break

                log("点击最终 Renew 按钮")
                final_btn = page.ele(
                    'xpath://button[normalize-space(text())="Renew"]', timeout=3
                )
                if final_btn:
                    try:
                        final_btn.click()
                    except Exception:
                        final_btn.click(by_js=True)
                    time.sleep(10)
                    new_expire = get_expire_time(page)
                    if new_expire != old_expire and new_expire != "未知":
                        log(f"到期时间已更新: {old_expire} -> {new_expire}")
                        success = True
                    else:
                        page_text = (page.html or "").lower()
                        if any(w in page_text for w in ["successfully", "renewed"]):
                            success = True
                        else:
                            failure_reason = "续期后未检测到成功标志"
                else:
                    failure_reason = "找不到最终 Renew 按钮"
                break

            except Exception as e:
                log(f"续期尝试异常: {e}", "ERROR")
                failure_reason = f"运行异常: {str(e)[:200]}"
                if attempt < MAX_RENEW_RETRIES_PER_URL:
                    if page:
                        try:
                            page.quit()
                        except Exception:
                            pass
                        page = None
                    get_warp_manager().rotate_ip(attempt_idx=attempt - 1)
                    continue
                break

            finally:
                if page:
                    screen_name = (
                        f"host2play-{server_name}"
                        f"-{'success' if success else 'fail'}.png"
                    )
                    screenshot_path = capture_page_screenshot(
                        page, os.path.join(screenshot_dir, screen_name)
                    )
                    try:
                        page.quit()
                    except Exception:
                        pass
    finally:
        vdisplay.stop()

    return success, server_name, old_expire, new_expire, screenshot_path, failure_reason

# ==================== 主入口 ====================
def main():
    # 调度下次执行（当前时间 + 450 分钟）
    now = datetime.now(timezone.utc)
    next_run = now + timedelta(minutes=450)
    trigger_timestamp = next_run.timestamp()
    schedule_cronjob(trigger_timestamp)

    if not RENEW_URLS:
        log("请在 RENEW_URLS 列表中添加续期链接", "ERROR")
        sys.exit(1)

    get_warp_manager().record_initial_ip()

    total_success = 0
    for idx, url in enumerate(RENEW_URLS, 1):
        log(f"{'#'*60}")
        log(f"处理第 {idx} 个链接: {mask_url(url)}")
        log(f"{'#'*60}")

        success, server_name, old_expire, new_expire, screenshot, failure_reason = \
            renew_single_url(url, attempt_idx=idx - 1)

        if success:
            caption = build_notification(True, url, server_name, old_expire, new_expire)
            total_success += 1
        else:
            caption = build_notification(
                False, url, server_name, old_expire, failure_reason=failure_reason
            )

        send_tg_photo(TG_BOT_TOKEN, TG_CHAT_ID, screenshot, caption, parse_mode='HTML')

    log(f"全部完成，成功 {total_success}/{len(RENEW_URLS)} 个链接")
    if total_success < len(RENEW_URLS):
        sys.exit(1)

if __name__ == "__main__":
    main()
