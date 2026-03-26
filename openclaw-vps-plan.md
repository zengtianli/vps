# OpenClaw VPS 部署计划

> 创建：2026-03-10
> 状态：待执行

## 背景

tianli 已在本地 macOS 上安装并配置好 OpenClaw（v2026.3.7），使用 MMKG 中转站（`code.mmkg.cloud`）+ Claude 模型。现在要在 VPS 上也部署一套。

## VPS 环境

- IP：<VPS_IP>
- 系统：Ubuntu 22.04 LTS
- 配置：4 核 / 8GB / 100GB
- 已有服务：Marzban（Docker，端口 8000）、Xray（443/1080）
- 网络优化：BBR + FQ 已开启

## 本地 vs VPS 的关键差异

| 项目 | 本地 macOS | VPS Ubuntu |
|------|-----------|------------|
| Daemon 管理 | launchd (plist) | systemd (service) |
| Node 安装 | 已有 | 需要安装（推荐 nvm 或 NodeSource） |
| pnpm 安装 | 已有 | 需要安装 |
| 网络 | 通过 Shadowrocket 代理 | 直连，VPS 本身就在海外 |
| Gateway 绑定 | loopback（本机用） | 需考虑：仅 loopback + SSH 隧道，还是绑 0.0.0.0 |
| MMKG 中转站 | 需要代理直连规则 | VPS 在海外，直连 mmkg.cloud 无障碍 |

## 执行步骤

### 阶段 1：环境准备
- [ ] 在 VPS 上安装 Node.js >= 22（推荐 NodeSource 或 nvm）
- [ ] 安装 pnpm
- [ ] 验证环境：`node -v && pnpm -v`

### 阶段 2：安装 OpenClaw
- [ ] `pnpm add -g openclaw@latest`
- [ ] `pnpm approve-builds -g`
- [ ] 运行 `openclaw onboard`（需决定：交互式还是非交互式）

### 阶段 3：配置
- [ ] 编辑 `~/.openclaw/openclaw.json`，配置 MMKG 中转站
- [ ] 创建 systemd service 文件（替代 macOS 的 launchd plist）
- [ ] 设置环境变量（`ANTHROPIC_API_KEY` 等）

### 阶段 4：验证
- [ ] `openclaw doctor`
- [ ] `openclaw gateway status`
- [ ] 通过 SSH 隧道访问 Dashboard 测试
- [ ] 发送测试消息验证模型调用

## 待确认问题

1. **Gateway 绑定方式**：loopback（安全，需 SSH 隧道访问）还是 0.0.0.0（方便但需额外安全措施）？
2. **渠道接入**：VPS 上的 OpenClaw 主要通过什么渠道使用？Telegram？WhatsApp？Web？
3. **和本地的关系**：VPS 上是独立实例还是和本地联动？

## 参考文档

- 本地配置文档：`~/openclaw/CLAUDE.md`
- 安装指南：`~/openclaw/setup.md`
- 中转站配置：`~/openclaw/mmkg-proxy-config.md`
- 故障排除：`~/openclaw/troubleshooting.md`
- VPS 信息：`~/docs/knowledge/vps-proxy-guide.md`
