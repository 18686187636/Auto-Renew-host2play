# Host2Play 自动续期（每7小时50分）

利用 GitHub Actions + cron-job.org 自动续期 `host2play.gratis` 服务器，并通过 Telegram 实时通知续期结果。

## 核心逻辑

- **触发频率**：每 **470 分钟**（7小时50分）执行一次，确保在 8 小时过期前 10 分钟完成续期。
- **续期动作**：每次触发均会执行续期（不依赖到期时间判断），以应对服务器有效期仅为 8 小时的情况。
- **打码服务**：通过 2captcha 自动解决 reCAPTCHA v2 验证。
- **通知**：续期成功或失败均通过 Telegram Bot 发送消息，内容包括操作时间、续期后的到期时间。
- **状态存储**：将最新到期时间写入 `expiry.txt` 并提交至仓库，便于追踪。
- **计划任务管理**：自动调用 cron-job.org API 创建或更新一个间隔为 470 分钟的触发器，无需手动干预。

## 配置

### 1. GitHub Secrets

在仓库 `Settings → Secrets and variables → Actions` 添加以下变量：

| Secret 名称 | 说明 |
|------------|------|
| `TG_BOT_TOKEN` | Telegram Bot Token（从 @BotFather 获取） |
| `TG_CHAT_ID` | 接收通知的 Telegram 用户/群组 ID |
| `CRONJOB_API_KEY` | cron-job.org 账户的 API Key（在个人设置中生成） |
| `CAPTCHA_API_KEY` | 2captcha 账户的 API Key |
| `CRONJOB_JOB_ID` | **可选**：已存在的 cron-job ID，若留空则自动创建新任务（建议首次运行后记录并填入，便于后续更新） |

> `GITHUB_TOKEN` 由 GitHub 自动生成，无需手动设置。

### 2. 首次部署

1. 将本仓库克隆或创建，并推送上述文件。
2. 设置所有 Secrets。
3. 手动触发一次工作流（`Actions` → `Auto Renew Server` → `Run workflow`）。
4. 检查运行日志，确认续期成功，并收到 Telegram 通知。
5. 首次运行会自动在 cron-job.org 创建一个间隔 470 分钟的任务，并输出 Job ID（可在日志中查找）。请将该 ID 填入 `CRONJOB_JOB_ID` Secret，以便后续更新使用（非必须）。

## 工作流触发方式

- 手动触发（`workflow_dispatch`）用于测试。
- 自动触发由 cron-job.org 每 470 分钟调用一次 GitHub API (`workflow_dispatch`)。

## 续期流程详解

1. 访问续期页面，提取 reCAPTCHA 的 `sitekey`。
2. 将 `sitekey` 提交至 2captcha 打码平台，获取验证令牌。
3. 构造续期请求（含令牌及必要参数），提交至服务器。
4. 解析返回的响应，提取新的到期时间。
5. 将时间写入 `expiry.txt` 并提交到仓库。
6. 发送 Telegram 通知（含新到期时间）。
7. 确保 cron-job.org 的任务处于最新状态（若 ID 变化或更新配置）。

## 文件说明

- `.github/workflows/renew.yml`：GitHub Actions 工作流定义。
- `renew.py`：核心续期脚本，包含打码、请求、通知、cron-job 管理等。
- `requirements.txt`：Python 依赖。
- `expiry.txt`：由脚本自动生成，记录最近一次续期后的到期时间（提交到仓库）。

## 注意事项

- **续期请求细节**：脚本中的续期 API 地址和参数基于推测，实际请根据浏览器抓包调整 `perform_renewal()` 中的请求方式。
- **reCAPTCHA 版本**：当前适配 v2（隐形），若为 v3 或其他，需相应修改打码流程。
- **cron-job API**：脚本使用 `type: interval` 模式创建 470 分钟间隔的任务，若 API 版本有变，请对照官方文档调整字段。
- **费用**：2captcha 按次收费，建议监控打码消耗。

## 故障排查

- 若续期失败，检查日志中错误信息，常见原因：验证码未通过、请求参数错误、网络问题。
- 若未收到 Telegram 通知，检查 Secrets 及网络连通性。
- 若 cron-job 未创建，检查 API Key 权限及网络。

## 许可证

MIT
