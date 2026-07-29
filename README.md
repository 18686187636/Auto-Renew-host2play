# Host2Play 自动续期脚本

# ⭐ **觉得有用？给个 Star 支持一下！**
> 注册地址：[https://host2play.gratis](https://host2play.gratis)

自动续期 Host2Play 免费游戏服务器的 GitHub Actions 脚本。基于 Chrome 自动化，自动处理 reCAPTCHA 音频验证，支持 IP 封锁自动切换 WARP，多服务器批量续期，并通过 Telegram 推送带截图的运行结果。

## ✨ 功能特性

- ✅ 多服务器支持 —— 一次配置多个续期链接，顺序执行
- ✅ 全自动 reCAPTCHA 破解 —— 音频识别 + 自动填写，无需人工干预
- ✅ 智能 IP 切换 —— 检测到 Google 封锁后自动通过 WARP 更换出口 IP
- ✅ Telegram 通知 —— 续期成功 / 失败均推送消息，附带页面截图
- ✅ 定时 + 手动运行 —— 默认每天多次自动执行，也支持手动触发
- ✅ 自动清理运行记录 —— 只保留最近 2 条 Actions 记录，仓库保持清爽
- ✅ 虚拟显示无头运行 —— 使用 Xvfb，不依赖图形界面

## 📋 前置要求

### 1. 配置续期链接（必须）

编辑仓库根目录下的 [main.py](./main.py)，找到23行 `RENEW_URLS` 列表，填入你的 Host2Play 服务器续期链接。

**链接格式：**
```
https://host2play.gratis/server/renew?i=你的服务器ID
```

**如何获取链接：**
打开 [Host2Play Minecraft 面板](https://host2play.gratis/panel/minecraft)，找到你的服务器，点击 **Copy public renew link** 即可复制续期链接，然后填入 `main.py` 的 `RENEW_URLS` 列表中。

示例：
```python
RENEW_URLS = [
    "https://host2play.gratis/server/renew?i=6666666-6666-6666-6666-66666666666",
    "https://host2play.gratis/server/renew?i=另一个服务器ID",
]
```

### 2. 配置 Telegram 通知（可选）

进入仓库 `Settings` → `Secrets and variables` → `Actions`，添加以下 Secrets：

| Secret 名称 | 必填 | 说明 | 示例 |
|------------|------|------|------|
| `TG_BOT_TOKEN` | ❌ | Telegram Bot Token | `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11` |
| `TG_CHAT_ID` | ❌ | 接收消息的 Chat ID | `123456789` |

> 不配置这两个 Secret 时，脚本仍会正常运行，只是不会发送 Telegram 通知。

**获取方式：**
- **Bot Token**：向 [@BotFather](https://t.me/BotFather) 发送 `/newbot` 创建机器人，获得 token。
- **Chat ID**：向 [@userinfobot](https://t.me/userinfobot) 发送任意消息即可获取。

### 3. 环境依赖（无需手动配置）

脚本运行在 GitHub Actions 的 `ubuntu-latest` 环境中，以下依赖会在运行时自动安装：
- Google Chrome
- Xvfb（虚拟显示）
- ffmpeg、unzip、curl
- WARP（网络代理，用于切换 IP）
- Python 库：`DrissionPage`, `xvfbwrapper`, `requests`, `SpeechRecognition`, `pydub`

## 🚀 使用方法

### 方法 1：定时自动运行（默认）

Fork 本仓库并完成上述配置后，工作流会按以下时间自动执行（UTC 时间）：

- `0 0,11,22 * * *`  → 每天 00:00、11:00、22:00（UTC）
- `30 5,16 * * *`  → 每天 05:30、16:30（UTC）

如需修改频率，编辑 `.github/workflows/Host2Play_Renew.yml` 中的 `schedule` 部分即可。

常用 cron 示例：
- `0 */6 * * *`  每 6 小时一次
- `0 0,12 * * *` 每天 0 点和 12 点（UTC）

### 方法 2：手动触发（GitHub 网页）

1. 进入仓库的 `Actions` 页面
2. 选择 **Host2Play 续期** 工作流
3. 点击 **Run workflow** → 点击绿色的 **Run workflow** 按钮

### 方法 3：API 调用

```bash
curl -X POST \
  -H "Authorization: Bearer ghp_你的Token" \
  -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/repos/你的用户名/你的仓库名/actions/workflows/Host2Play_Renew.yml/dispatches \
  -d '{"ref":"main"}'
```

## 🐛 常见问题

### 1. 如何获取续期链接？
打开 [Host2Play Minecraft 面板](https://host2play.gratis/panel/minecraft)，找到你的服务器，点击 **Copy public renew link** 即可获得续期链接。将其复制到 `main.py` 的 `RENEW_URLS` 列表中即可。

### 2. 验证码识别失败怎么办？
脚本内置了音频识别流程，但如果识别率较低，可能是以下原因：
- **网络问题**：Google 语音识别 API 访问不稳定，脚本会自动重试（最多 3 次下载、多次识别）。
- **IP 被标记**：频繁操作会导致 Google 展示更难的验证码，脚本会通过 WARP 自动更换 IP。
- **环境噪音**：音频下载或转换问题，建议检查 Actions 日志中的识别结果 `[INFO] 识别结果: [xxxx]`。

### 3. IP 被封锁导致无法继续？
当检测到 `try again later` 或错误提示时，脚本会自动调用 `restart_warp()` 断开并重新连接 WARP，以更换出口 IP，然后重新开始续期尝试。整个流程最多尝试 **50 次**，足够应对大多数封锁情况。

### 4. 为什么需要 WARP？
Host2Play 的续期页面嵌入了 Google reCAPTCHA。当短时间内多次尝试验证时，Google 可能会封禁当前 IP。WARP 能快速更换 Cloudflare 出口 IP，绕过封锁继续验证。

### 5. 没有收到 Telegram 通知？
- 确认是否已正确设置 `TG_BOT_TOKEN` 和 `TG_CHAT_ID` 两个 Secret。
- 在 Telegram 中先给 Bot 发送 `/start` 激活对话。
- 检查 Actions 日志中是否有 `Telegram 图片通知发送成功` 的日志，或者错误信息。

### 6. 截图在哪里查看？
每次 Actions 运行结束后，在运行详情页底部 **Artifacts** 区域可以下载 `screenshots-运行编号` 的压缩包，里面包含每次续期的成功/失败截图。

### 7. 能否同时续期多个服务器？
可以。在 `RENEW_URLS` 列表中填入多个链接即可，脚本会依次处理。每个链接独立尝试，互不影响，最终 Telegram 通知也会分别推送。

### 8. 运行时间很长是否正常？
正常。每次续期需要启动 Chrome、加载页面、处理验证码、可能还需要切换 IP 并重试，单个链接通常需要 2～5 分钟，遇到多次 IP 封锁可能会更久。GitHub Actions 限制最长 6 小时，完全足够。

## 🔒 安全建议

- ✅ **敏感信息存放**：Telegram Token 等信息请严格存储在 GitHub Secrets 中，不要直接写在代码里。
- ✅ **定期维护**：如果 Host2Play 网站更新了页面结构，可能需要更新元素定位逻辑，届时请关注仓库更新。
- ✅ **Actions 权限**：默认的 `GITHUB_TOKEN` 权限已满足需要，无需额外设置。
- ✅ **Fork 安全**：建议保持 Fork 仓库同步，以便获取最新的脚本修复。

## 📄 许可证

MIT License

---

**⚠️ 免责声明**：本脚本仅供学习交流使用，使用者需遵守 Host2Play 的服务条款。因使用本脚本造成的任何问题，作者不承担任何责任。
