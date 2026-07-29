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
from selenium.webdriver.common.action_chains import ActionChains

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
    print(f"📤 发送 API 请求，问题代码: {question_code}")
    resp = requests.post(CAPTCHA_API_URL, json=payload, headers=headers, timeout=60)
    print(f"📥 响应状态码: {resp.status_code}")
    print(f"📄 响应内容预览: {resp.text[:200]}")
    if resp.status_code != 200:
        try:
            error_info = resp.json().get("error", {})
            raise Exception(f"API error: {error_info.get('code')} - {error_info.get('message')}")
        except:
            raise Exception(f"API 请求失败，状态码 {resp.status_code}，响应: {resp.text[:200]}")
    try:
        result = resp.json()
    except:
        raise Exception(f"API 返回非 JSON 响应: {resp.text[:200]}")
    solution = result.get("solution")
    if not solution:
        error = result.get("error")
        if error:
            raise Exception(f"API error: {error.get('code')} - {error.get('message')}")
        else:
            raise Exception(f"API 返回未知格式，完整响应: {json.dumps(result)}")
    objects = solution.get("objects")
    if not objects:
        raise Exception("API 返回成功但无 objects 字段")
    return objects, solution.get("size", 300)

# ==================== 改进后的 click_recaptcha_grid（使用 ActionChains） ====================
def click_recaptcha_grid(sb, objects, grid_size=300):
    try:
        iframes = sb.find_elements('iframe')
        for iframe in iframes:
            sb.switch_to_frame(iframe)
            try:
                sb.find_element('.rc-imageselect-payload', timeout=1)
                print("✅ 切换到图像挑战 iframe")
                break
            except:
                sb.switch_to_default_content()
                continue
        else:
            raise Exception("Could not find image challenge iframe")
    except Exception as e:
        raise Exception(f"Failed to switch to image challenge iframe: {e}")

    img_elem = None
    try:
        img_elem = sb.find_element('img', timeout=2)
    except:
        pass
    if not img_elem:
        try:
            img_elem = sb.find_element('canvas', timeout=2)
        except:
            pass
    if not img_elem:
        img_elem = sb.execute_script("""
            var imgs = document.querySelectorAll('img, canvas');
            for (var i = 0; i < imgs.length; i++) {
                var rect = imgs[i].getBoundingClientRect();
                if (rect.width > 50 && rect.height > 50) {
                    return imgs[i];
                }
            }
            return null;
        """)
    if not img_elem:
        raise Exception("Could not find image element in image challenge iframe")

    # 获取图片位置和尺寸
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

    # 获取页面滚动偏移
    scroll_x = sb.execute_script("return window.scrollX;")
    scroll_y = sb.execute_script("return window.scrollY;")

    actions = ActionChains(sb.driver)

    for idx in objects:
        row = idx // cols
        col = idx % cols
        # 计算绝对坐标（相对于页面左上角）
        x = left + col * cell_w + cell_w / 2
        y = top + row * cell_h + cell_h / 2
        # 转换为视口坐标（相对于浏览器可视区域）
        viewport_x = x - scroll_x
        viewport_y = y - scroll_y
        print(f"🔘 点击索引 {idx} 于视口坐标 ({viewport_x:.0f}, {viewport_y:.0f})")

        # 使用 ActionChains 移动到该坐标并点击
        actions.move_by_offset(viewport_x, viewport_y).click().perform()
        # 重置鼠标位置，避免累积偏移
        actions.move_by_offset(-viewport_x, -viewport_y).perform()
        time.sleep(0.5)

    sb.switch_to_default_content()

def extract_question_from_page(sb):
    try:
        elem = sb.find_element('.rc-imageselect-instructions', timeout=3)
        if elem:
            text = elem.text.strip()
            if text:
                return text
    except:
        pass

    try:
        iframes = sb.find_elements('iframe[src*="recaptcha"]')
        for iframe in iframes:
            sb.switch_to_frame(iframe)
            try:
                elem = sb.find_element('.rc-imageselect-instructions', timeout=2)
                if elem:
                    text = elem.text.strip()
                    if text:
                        sb.switch_to_default_content()
                        return text
            except:
                pass
            sb.switch_to_default_content()
    except:
        pass

    js_code = """
    function findQuestion() {
        var keywords = ['选择', '点击', '图片', '图像', '包含', '所有', '请选择', '请点击', 
                        'select', 'click', 'image', 'squares', 'with', 'crosswalks', 'bicycles',
                        'traffic lights', 'cars', 'motorcycles', 'buses', 'trucks', 'fire hydrant',
                        'boats', 'bridges', 'mountains', 'stairs', 'chimneys', 'palm trees',
                        'parking meters', 'school buses', 'tractors',
                        'selectați', 'autobuze', 'trotuare', 'semafoare', 'mașini', 'motoare',
                        'camioane', 'hidrant', 'bărci', 'poduri', 'munți', 'scări', 'coșuri',
                        'palmieri', 'parcometre', 'autobuze școlare', 'tractoare'];
        var texts = [];

        var bodyText = document.body.innerText || '';
        texts.push(bodyText);

        var iframes = document.getElementsByTagName('iframe');
        for (var i = 0; i < iframes.length; i++) {
            try {
                var iframeDoc = iframes[i].contentDocument || iframes[i].contentWindow.document;
                if (iframeDoc) {
                    var iframeText = iframeDoc.body.innerText || '';
                    texts.push(iframeText);
                }
            } catch(e) {}
        }

        var fullText = texts.join('\\n');
        var lines = fullText.split('\\n');

        for (var j = 0; j < lines.length; j++) {
            var line = lines[j].trim();
            if (line.length === 0) continue;
            for (var k = 0; k < keywords.length; k++) {
                if (line.toLowerCase().indexOf(keywords[k].toLowerCase()) !== -1) {
                    return line;
                }
            }
        }

        for (var j = 0; j < lines.length; j++) {
            var line = lines[j].trim();
            if (line.length > 10) {
                return line;
            }
        }
        return null;
    }
    return findQuestion();
    """
    question_text = sb.execute_script(js_code)
    if question_text and question_text.strip():
        return question_text.strip()
    else:
        with open("page_source_debug.html", "w", encoding="utf-8") as f:
            f.write(sb.get_page_source())
        try:
            iframes = sb.find_elements('iframe')
            for idx, iframe in enumerate(iframes):
                try:
                    sb.switch_to_frame(iframe)
                    with open(f"iframe_{idx}_debug.html", "w", encoding="utf-8") as f:
                        f.write(sb.get_page_source())
                    sb.switch_to_default_content()
                except:
                    sb.switch_to_default_content()
                    continue
        except:
            pass
        raise Exception("Could not find reCAPTCHA question text. Page source saved for debugging.")

def capture_recaptcha_image(sb):
    iframes = sb.find_elements('iframe')
    found = False
    for iframe in iframes:
        sb.switch_to_frame(iframe)
        try:
            sb.find_element('.rc-imageselect-payload', timeout=1)
            print("✅ 切换到图像挑战 iframe")
            found = True
            break
        except:
            sb.switch_to_default_content()
            continue
    if not found:
        try:
            sb.switch_to_frame('iframe[src*="recaptcha"][src*="image"]')
            found = True
            print("✅ 通过 src 切换到图像挑战 iframe")
        except:
            pass
    if not found:
        raise Exception("Could not find image challenge iframe")

    img_elem = None
    try:
        img_elem = sb.find_element('img', timeout=2)
        print("✅ 找到 <img> 元素")
    except:
        pass
    if not img_elem:
        try:
            img_elem = sb.find_element('canvas', timeout=2)
            print("✅ 找到 <canvas> 元素")
        except:
            pass
    if not img_elem:
        img_elem = sb.execute_script("""
            var imgs = document.querySelectorAll('img, canvas');
            for (var i = 0; i < imgs.length; i++) {
                var rect = imgs[i].getBoundingClientRect();
                if (rect.width > 50 && rect.height > 50) {
                    return imgs[i];
                }
            }
            return null;
        """)
        if img_elem:
            print("✅ 通过 JavaScript 找到图片元素")
    if not img_elem:
        raise Exception("Could not find image element in image challenge iframe")

    location = img_elem.location
    size = img_elem.size
    left = location['x']
    top = location['y']
    width = size['width']
    height = size['height']

    print(f"📐 图片位置: left={left}, top={top}, width={width}, height={height}")

    png_data = sb.driver.get_screenshot_as_png()
    img = Image.open(io.BytesIO(png_data))

    crop_left = max(0, int(left))
    crop_top = max(0, int(top))
    crop_right = min(img.width, int(left + width))
    crop_bottom = min(img.height, int(top + height))

    if crop_right <= crop_left or crop_bottom <= crop_top:
        raise Exception(f"Invalid crop dimensions: {crop_left}, {crop_top}, {crop_right}, {crop_bottom}")

    cropped = img.crop((crop_left, crop_top, crop_right, crop_bottom))
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

        print("🌐 打开续期页面...")
        sb.open(RENEW_URL)
        sb.wait_for_ready_state_complete()
        sb.sleep(8)
        screenshot_step(sb, "page_loaded")

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

        sitekey = sb.execute_script("""
            var elem = document.querySelector('.g-recaptcha');
            if (elem) return elem.getAttribute('data-sitekey');
            return null;
        """)
        print(f"🔑 sitekey: {sitekey}")
        screenshot_step(sb, "sitekey")

        print("🔘 第一次点击 Renew...")
        if not click_renew():
            error_msg = "点击 Renew 失败"
            screenshot_step(sb, "renew_click_failed")
            return False, None, error_msg, server_name
        time.sleep(3)
        screenshot_step(sb, "after_first_renew")

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
            screenshot_step(sb, "checkbox_error")
        else:
            screenshot_step(sb, "checkbox_checked")
            time.sleep(3)

        # ========== 3. 提取问题文本 ==========
        print("⏳ 等待图像验证并提取问题...")
        print(f"📄 当前页面标题: {sb.get_title()}")
        print(f"🔗 当前 URL: {sb.get_current_url()}")
        screenshot_step(sb, "before_extract")

        question_code = None
        max_attempts = 10
        for attempt in range(max_attempts):
            try:
                question_text = extract_question_from_page(sb)
                print(f"🧩 提取到的问题文本: {question_text}")

                question_map = {
                    # English (单复数)
                    "traffic lights": "/m/015qff",
                    "traffic light": "/m/015qff",
                    "crosswalks": "/m/014xcs",
                    "crosswalk": "/m/014xcs",
                    "bicycles": "/m/0199g",
                    "bicycle": "/m/0199g",
                    "cars": "/m/0k4j",
                    "car": "/m/0k4j",
                    "motorcycles": "/m/04_sv",
                    "motorcycle": "/m/04_sv",
                    "buses": "/m/01bjv",
                    "bus": "/m/01bjv",
                    "trucks": "/m/07jdr",
                    "truck": "/m/07jdr",
                    "fire hydrant": "/m/01pns0",
                    "fire hydrants": "/m/01pns0",
                    "boats": "/m/019jd",
                    "boat": "/m/019jd",
                    "bridges": "/m/015kr",
                    "bridge": "/m/015kr",
                    "mountains": "/m/09d_r",
                    "mountain": "/m/09d_r",
                    "stairs": "/m/01lynh",
                    "stair": "/m/01lynh",
                    "chimneys": "/m/01jk_4",
                    "chimney": "/m/01jk_4",
                    "palm trees": "/m/0cdl1",
                    "palm tree": "/m/0cdl1",
                    "parking meters": "/m/015qbp",
                    "parking meter": "/m/015qbp",
                    "school buses": "/m/02yvhj",
                    "school bus": "/m/02yvhj",
                    "tractors": "/m/013xlm",
                    "tractor": "/m/013xlm",
                    # Chinese
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
                    "楼梯": "/m/01lynh",
                    # Romanian
                    "semafoare": "/m/015qff",
                    "trotuare": "/m/014xcs",
                    "biciclete": "/m/0199g",
                    "mașini": "/m/0k4j",
                    "motoare": "/m/04_sv",
                    "autobuze": "/m/01bjv",
                    "camioane": "/m/07jdr",
                    "hidrant": "/m/01pns0",
                    "bărci": "/m/019jd",
                    "poduri": "/m/015kr",
                    "munți": "/m/09d_r",
                    "scări": "/m/01lynh",
                    "coșuri": "/m/01jk_4",
                    "palmieri": "/m/0cdl1",
                    "parcometre": "/m/015qbp",
                    "autobuze școlare": "/m/02yvhj",
                    "tractoare": "/m/013xlm"
                }

                question_code = None
                question_lower = question_text.lower()
                for key, code in question_map.items():
                    if key in question_lower:
                        question_code = code
                        break

                if not question_code:
                    match = re.search(r'/m/[a-z0-9]+', question_text)
                    if match:
                        question_code = match.group(0)

                if question_code:
                    print(f"🧩 问题代码: {question_code}")
                    screenshot_step(sb, "question_extracted")
                    break
                else:
                    print(f"⚠️ 无法从问题文本中提取代码: {question_text}")
            except Exception as e:
                print(f"尝试 {attempt+1}/{max_attempts} 提取失败: {e}")
                time.sleep(2)
                if attempt == 4:
                    print("⚠️ 尝试重新触发验证...")
                    click_renew()
                    time.sleep(3)

        if question_code is None:
            error_msg = "提取问题失败，可能图像验证未加载"
            screenshot_step(sb, "question_failed")
            return False, None, error_msg, server_name

        # ========== 4. 截取图像 ==========
        try:
            captcha_img = capture_recaptcha_image(sb)
            print("📸 图像已截取")
            screenshot_step(sb, "capture_done")
        except Exception as e:
            error_msg = f"截取图像失败: {e}"
            screenshot_step(sb, "capture_failed")
            return False, None, error_msg, server_name

        # ========== 5. 调用 API 识别 ==========
        try:
            objects, grid_size = solve_recaptcha_via_acedata(captcha_img, question_code)
            print(f"🧩 需要点击的索引: {objects}")
            screenshot_step(sb, "api_success")
        except Exception as e:
            error_msg = f"识别失败: {e}"
            screenshot_step(sb, "api_failed")
            return False, None, error_msg, server_name

        # ========== 6. 点击网格（使用 ActionChains） ==========
        try:
            click_recaptcha_grid(sb, objects, grid_size)
            print("✅ 网格点击完成")
            time.sleep(2)
            screenshot_step(sb, "grid_clicked")
        except Exception as e:
            error_msg = f"点击网格失败: {e}"
            screenshot_step(sb, "click_grid_failed")
            return False, None, error_msg, server_name

        # ========== 7. 点击 "Verify" 按钮 ==========
        print("🔘 尝试点击 'Verify' 按钮...")
        verify_clicked = False
        try:
            sb.click('button:contains("Verify")', timeout=3)
            verify_clicked = True
            print("✅ 点击了 'Verify' 按钮")
        except:
            pass

        if not verify_clicked:
            try:
                sb.execute_script("""
                    var btns = document.querySelectorAll('button');
                    for (var i = 0; i < btns.length; i++) {
                        if (btns[i].innerText.toLowerCase().includes('verify')) {
                            btns[i].click();
                            return true;
                        }
                    }
                    return false;
                """)
                print("✅ 通过 JavaScript 点击 'Verify' 按钮")
                verify_clicked = True
            except:
                pass

        if not verify_clicked:
            print("ℹ️ 未找到 'Verify' 按钮，可能无需点击")
        else:
            time.sleep(3)
            screenshot_step(sb, "after_verify")

        # ========== 8. 等待验证完成（检查 token） ==========
        print("⏳ 等待 reCAPTCHA 验证完成...")
        token_filled = False
        for attempt in range(15):
            token = sb.execute_script("""
                var textarea = document.getElementById('g-recaptcha-response');
                return textarea ? textarea.value : '';
            """)
            if token and token.strip():
                print(f"✅ 验证 token 已填充 (长度: {len(token)})")
                token_filled = True
                break
            time.sleep(1)
        if not token_filled:
            print("⚠️ 验证 token 仍为空，可能验证未完成")
            screenshot_step(sb, "token_not_filled")
        else:
            screenshot_step(sb, "token_filled")

        # ========== 9. 提交续期 ==========
        print("🔘 第二次点击 Renew（提交续期）...")
        if not click_renew():
            error_msg = "第二次点击失败"
            screenshot_step(sb, "second_renew_failed")
            return False, None, error_msg, server_name

        screenshot_step(sb, "after_submit")
        print("⏳ 等待续期处理（15秒）...")
        time.sleep(15)

        # ---- 刷新页面获取新到期时间 ----
        print("🔄 刷新页面...")
        sb.open(RENEW_URL)
        sb.wait_for_ready_state_complete()
        sb.sleep(5)
        screenshot_step(sb, "after_reload")

        # ========== 10. 提取新的到期时间 ==========
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
    print("🚀 Starting Host2Play renewal (ActionChains + Verify button)")
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
