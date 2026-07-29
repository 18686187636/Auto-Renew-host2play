# Host2Play 自动续期工具

> 基于 DrissionPage + 音频识别 + WARP IP 轮换的 Host2Play 免费服务器自动续期方案，支持多链接、Telegram 通知、cron-job.org 调度。

---

## 📖 项目简介

本项目用于自动续期 Host2Play 提供的免费 Minecraft 服务器。它通过模拟浏览器操作，自动处理 reCAPTCHA 音频验证（优先使用 Google 免费语音识别，失败时回退到备用 API），并集成了 Cloudflare WARP 实现 IP 轮换，有效避免因 IP 被封锁导致的续期失败。

配合 cron-job.org 定时任务，可实现每 7.5 小时自动运行一次，完全解放双手。

---

## ✨ 功能特性

- 🤖 **全自动续期**：无需人工干预，自动登录 → 点击续期 → 处理 reCAPTCHA → 提交。
- 🎧 **音频验证码识别**：优先使用 `speech_recognition` + Google 免费 API，失败时可切换备用 API（需自行配置）。
- 🔁 **IP 轮换**：集成 Cloudflare WARP，每次重试自动更换出口 IP，并记录已用 IP 池，避免重复。
- 📱 **Telegram 通知**：续期成功/失败时发送带截图的推送，便于监控。
- ⏰ **外部调度**：通过 cron-job.org API 动态创建/更新定时任务，每 450 分钟（7.5 小时）触发一次 GitHub Actions。
- 🖼️ **自动截图**：每个关键步骤均截图保存，可上传为 Actions Artifacts 供调试。

---

## 📁 文件结构

```
.
├── .github/workflows/renew.yml     # GitHub Actions 工作流（仅手动触发）
├── main.py                          # 主续期脚本（含所有逻辑）
├── requirements.txt                 # Python 依赖
└── README.md                        # 本文档
```

---

## 🔧 环境变量（Secrets）

在 GitHub 仓库 `Settings → Secrets and variables → Actions` 中添加以下变量：

| 变量名 | 必填 | 说明 |
|--------|------|------|
| `CAPTCHA_API_KEY` | ❌ | Ace Data Cloud API Key（备用语音识别） |
| `AUDIO_API_URL` | ❌ | 备用语音识别 API 端点（需配合 `CAPTCHA_API_KEY`） |
| `CRONJOB_API_KEY` | ✅ | cron-job.org API Key（从控制台 Settings 生成） |
| `CRONJOB_JOB_ID` | ❌ | 已有定时任务 ID（首次运行后自动创建，建议后续填入） |
| `GH_TOKEN` | ✅ | GitHub Personal Access Token（需 `repo` 和 `workflow` 权限） |
| `REPO_OWNER` | ✅ | 仓库所有者用户名 |
| `REPO_NAME` | ✅ | 仓库名称 |
| `WORKFLOW_FILE` | ❌ | 工作流文件名，默认 `renew.yml` |
| `BRANCH` | ❌ | 触发分支，默认 `main` |
| `TG_BOT_TOKEN` | ❌ | Telegram Bot Token（通知用） |
| `TG_CHAT_ID` | ❌ | Telegram 接收消息的 Chat ID |

> **首次运行**：如果 `CRONJOB_JOB_ID` 未设置，脚本会自动创建任务，并将新 ID 通过 Telegram 发送给您，届时请将其添加到 Secrets。

---

## 🚀 部署步骤

### 1. 克隆仓库
```bash
git clone https://github.com/your-username/Auto-Renew-host2play.git
cd Auto-Renew-host2play
```

### 2. 添加续期链接
编辑 `main.py`，在 `RENEW_URLS` 列表中添加你的续期链接（可多个）：
```python
RENEW_URLS = [
    "https://host2play.gratis/server/renew?i=你的UUID",
    # 更多链接...
]
```

### 3. 配置 GitHub Secrets
在仓库设置中添加上述 Secrets。

### 4. 手动触发工作流
- 进入 Actions 页面，选择 **Host2Play 续期** 工作流。
- 点击 **Run workflow** → **Run workflow**。

### 5. 确认 cron-job 创建
运行成功后，查看 Actions 日志，若出现 `✅ cron-job 创建成功，ID: xxxxx`，则任务已自动创建。  
若未收到 Telegram 通知，请检查 Secrets 是否正确。

### 6. 将 cron-job ID 加入 Secrets（可选但推荐）
将收到的 `CRONJOB_JOB_ID` 添加到 Secrets，以便下次运行时更新（而非新建）任务。

---

## ⏰ 调度机制

本项目使用 **cron-job.org** 的外部调度，而非 GitHub Actions 内置 `schedule`。

- **触发周期**：每次运行成功（或强制调度）后，脚本计算 `当前 UTC 时间 + 450 分钟`，通过 API 创建/更新一个**单次执行**的定时任务。
- **任务类型**：`schedule` 字段使用绝对时间（`hours`、`minutes`、`mdays`、`months`、`expiresAt`），触发后会立即过期，避免重复执行。
- **更新逻辑**：若 `CRONJOB_JOB_ID` 存在，则 `PATCH` 更新现有任务；否则 `PUT` 创建新任务。

> **注意**：cron-job.org 免费版每日请求次数有限（100 次），本项目每 7.5 小时调用一次，完全在限制内。

---

## 📦 依赖安装（本地测试用）

```bash
pip install -r requirements.txt
```

`requirements.txt` 内容：
```
DrissionPage
xvfbwrapper
requests
SpeechRecognition
pydub
```

此外需安装 `ffmpeg`（用于音频格式转换）：
- Ubuntu/Debian: `sudo apt-get install ffmpeg`
- macOS: `brew install ffmpeg`
- Windows: 从 [ffmpeg.org](https://ffmpeg.org/download.html) 下载并添加环境变量

---

## 🧠 工作原理（流程简述）

1. **启动浏览器**（使用 DrissionPage，headless 模式 + Xvfb 虚拟显示）。
2. **访问续期链接** → 点击 **Consent**（如有）→ 点击 **Renew server** 按钮。
3. **检测 reCAPTCHA**：
   - 若未出现，检查是否已直接成功（通过到期时间变化判断）。
   - 若出现，进入音频验证流程。
4. **音频验证**：
   - 切换到音频模式 → 下载音频文件 → 语音识别（Google 免费 → 备用 API）→ 填入结果并验证。
5. **验证通过后**，点击最终 **Renew** 按钮完成续期。
6. **刷新页面，获取新到期时间**，通过 Telegram 发送结果及截图。
7. **调度**：调用 cron-job.org API 安排下次执行。

---

## 🛠️ 故障排查

### ❌ 登录/续期失败
- 检查 `RENEW_URLS` 中的 UUID 是否正确。
- 查看日志中 `服务器: xxx, 到期时间: xxx` 是否与网站一致。
- 若提示 IP 被封锁，脚本会自动触发 WARP 轮换，但若 WARP 不可用，需更换代理。

### ❌ cron-job 创建失败（500 错误）
- 确认 `CRONJOB_API_KEY`、`GH_TOKEN`、`REPO_OWNER`、`REPO_NAME` 均正确。
- 检查 GitHub Token 是否具有 `workflow` 权限。
- 查看日志中打印的请求体，确保 `schedule` 字段格式正确（参考 Therose cloud 脚本）。

### ❌ 音频识别失败
- 确保 `ffmpeg` 已安装。
- 检查网络是否能访问 Google 语音识别服务。
- 若使用备用 API，请确认 `AUDIO_API_URL` 和 `CAPTCHA_API_KEY` 正确。

### ❌ Telegram 通知收不到
- 检查 `TG_BOT_TOKEN` 和 `TG_CHAT_ID` 是否正确。
- 确保 Bot 已被添加到群组/频道（若为群组，需将 Bot 设为管理员）。

---

## 📸 调试截图

脚本会在 `output/screenshots/` 目录下保存 PNG 截图，文件名包含步骤名和时间戳。  
在 GitHub Actions 中，这些截图会被自动上传为 Artifacts，可下载查看。

---

## 📄 许可证

本项目仅供学习交流使用，请勿滥用。使用前请确保遵守 Host2Play 的服务条款。

---

## 🤝 贡献

欢迎提交 Issue 或 Pull Request，共同完善本项目。

---

## 📞 联系

如有问题，请通过 GitHub Issues 反馈。
