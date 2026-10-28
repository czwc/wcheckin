# WorkBuddy 每日自动签到（GitHub Actions）

「Buddy 加油站」每日 100 积分，脚本零依赖（纯 Python 标准库），幂等设计（当天重复跑会自动跳过）。

## 一、获取凭证（本机一次性操作）

WorkBuddy 桌面客户端登录后，登录态存在本地文件。在项目目录运行：

```bash
python checkin.py --show-token --full
```

记下输出的 `refreshToken` 和 `domain`（domain 为空则用默认 `www.workbuddy.ai`）。

> ⚠️ refreshToken 等于账号钥匙，不要提交进仓库、不要发给任何人，只放 GitHub Secrets。

## 二、GitHub 仓库配置

1. 新建一个 **私有仓库**，把本目录推上去
2. Settings → Secrets and variables → Actions → New repository secret：

| Secret 名 | 值 | 必填 |
|---|---|---|
| `WB_REFRESH_TOKEN` | 上一步的 refreshToken | 是 |
| `WB_DOMAIN` | 上一步的 domain（空则填 `www.workbuddy.ai`） | 是 |
| `WB_ACCESS_TOKEN` | 可留空（脚本会用 refreshToken 自动刷新） | 否 |
| `WECOM_WEBHOOK` | 企业微信群机器人 webhook 地址，结果推送到群 | 否 |

## 三、验证

仓库 Actions 页 → WorkBuddy Daily Checkin → Run workflow 手动触发一次，
看运行日志输出 `{"status": "ok", "action": "checked_in", ...}` 即成功。

## 定时说明

- 北京时间每天 **00:30** 主跑、**08:30** 补跑（GitHub 定时有 0~30 分钟随机延迟，属正常）
- 公开仓库 60 天无 push 会被 GitHub 停掉定时任务 → 请用私有仓库或偶尔推一个 commit

## 已知限制 / 风险

- **活动状态**：签到接口依赖「Buddy 加油站」限时活动。状态接口返回 `active=false` 时脚本会
  上报 `activity_inactive`（活动未开启/已结束），这不是故障；活动开启后会自动恢复领取。
- **新版客户端加密**：2026-09 起部分客户端登录态改为 AES-256-GCM 加密信封。脚本会自动跳过
  加密文件、优先读 `workbuddy-desktop-ai.info`（实测为明文）。若两个文件都加密，脚本会明确报错，
  此时改用 WorkBuddy 内置方案：专家市场搜「积分签到」装「WorkBuddy 积分签到助手」建每日定时任务
  （需客户端开机在线）。
- 官方活动规则为手动签到，自动化签到存在积分回收/账号限制的灰色风险，自担风险、勿用于批量账号。
