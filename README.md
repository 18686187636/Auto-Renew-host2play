# Host2Play 自动续期工具

基于 DrissionPage + 音频识别 + WARP IP 轮换的 Host2Play 免费服务器自动续期方案，支持多链接、Telegram 通知、cron-job.org 调度。

## 功能特性

- 🤖 **全自动续期**：无需人工干预，自动登录 → 点击续期 → 处理 reCAPTCHA → 提交。
- 🎧 **音频验证码识别**：优先使用 Google 免费语音识别，失败时可切换备用 API。
- 🔁 **IP 轮换**：集成 Cloudflare WARP，每次重试自动更换出口 IP。
- 📱 **Telegram 通知**：续期成功/失败时发送带截图的推送。
- ⏰ **外部调度**：通过 cron-job.org API 动态创建定时任务，每 450 分钟触发一次 GitHub Actions。

## 环境变量（Secrets）

| 变量名 | 必填 | 说明 |
|--------|------|------|
| `CAPTCHA_API_KEY` | ❌ | Ace Data Cloud API Key（备用语音识别） |
| `AUDIO_API_URL` | ❌ | 备用语音识别 API 端点 |
| `CRONJOB_API_KEY` | ✅ | cron-job.org API Key |
| `GH_TOKEN` | ✅ | GitHub Personal Access Token（需 `repo` 和 `workflow` 权限） |
| `REPO_OWNER` | ✅ | 仓库所有者用户名 |
| `REPO_NAME` | ✅ | 仓库名称 |
| `WORKFLOW_FILE` | ✅ | 工作流文件名，如 `renew.yml` |
| `BRANCH` | ❌ | 触发分支，默认 `main` |
| `TG_BOT_TOKEN` | ❌ | Telegram Bot Token（通知用） |
| `TG_CHAT_ID` | ❌ | Telegram 接收消息的 Chat ID |

## 部署步骤

1. 将本项目 Fork 或克隆到您的 GitHub 仓库。
2. 在 `Settings → Secrets and variables → Actions` 中添加上述 Secrets。
3. 修改 `main.py` 中的 `RENEW_URLS` 列表，填入您的续期链接。
4. 手动触发 Actions（`workflow_dispatch`），脚本会自动创建 cron-job 任务。
5. 之后每 7.5 小时自动运行一次，无需人工干预。

## 本地测试

```bash
pip install -r requirements.txt
sudo apt-get install ffmpeg  # Ubuntu/Debian
python main.py
