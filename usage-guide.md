# VPS AI 助手使用指南

> 更新：2026-03-10
> 适用于：飞书 AI 助手 + OpenClaw + Edict 三省六部 + Syncthing 同步

---

## 1. 整体架构

```
你的手机/电脑（飞书）
       ↓ 发消息
  飞书服务器（WebSocket）
       ↓
  VPS (<VPS_IP>)
  ├── OpenClaw Gateway (端口 18789)
  ├── Edict 三省六部（12 个 Agent）
  ├── 看板 Dashboard (端口 7891)
  └── /root/sync/（同步目录）
       ↕ Syncthing 实时同步
  你的 Mac
  ├── ~/Work/zdwp/      ↔ /root/sync/zdwp/
  ├── ~/Personal/essays/ ↔ /root/sync/essays/
  ├── ~/Learn/           ↔ /root/sync/learn/
  └── ~/Work/reports/    ↔ /root/sync/reports/
```

---

## 2. 日常使用

### 2.1 飞书发消息给 AI

打开飞书 → 找到「AI助手」机器人 → 直接发消息。

**消息会被太子(taizi) Agent 自动分拣：**

| 你发的内容 | 太子的处理 |
|-----------|-----------|
| 闲聊/简短问答（「token 消耗多少？」） | 太子直接回复你 |
| 工作指令（「帮我整理水利公司的 XX 文档」） | 太子转交中书省，启动三省六部流程 |

**触发正式流程的关键词**：「帮我做」「写一份」「调研」「部署」「传旨」「下旨」等。

### 2.2 任务流转过程

发出工作指令后，任务会按以下流程自动流转：

```
你（皇上）→ 太子分拣 → 中书省规划 → 门下省审核 → 尚书省派发 → 六部执行 → 回奏
```

每个环节：
1. **中书省**：拆解任务为子任务，制定方案
2. **门下省**：审核方案质量，不合格打回重做
3. **尚书省**：把审核通过的任务派给对应部门
4. **六部**：并行执行（兵部写代码、礼部写文档、户部分析数据等）
5. **回奏**：结果通过飞书发回给你

### 2.3 聊天命令

在飞书对话中可以直接发送：

| 命令 | 作用 |
|------|------|
| `/status` | 查看当前会话状态 |
| `/new` 或 `/reset` | 重置会话（换话题时用） |
| `/compact` | 压缩上下文（对话太长时用） |
| `/think high` | 让 AI 深度思考（复杂问题用） |
| `/think off` | 关闭深度思考（日常对话） |

---

## 3. 看板 Dashboard

### 3.1 启动看板

看板需要通过 SSH 隧道访问（端口 7891 只监听 localhost）。

**第一步：确保看板服务在运行**

```bash
# 在 VPS 上启动（已配置为后台运行）
ssh root@<VPS_IP>

# 启动看板 API 服务器（后台运行）
cd /opt/edict && nohup python3 dashboard/server.py &

# 启动数据刷新循环（每 15 秒同步状态）
nohup bash scripts/run_loop.sh &
```

**第二步：本地开 SSH 隧道**

```bash
# 在你的 Mac 终端执行（映射 7891 端口）
ssh -L 7891:localhost:7891 root@<VPS_IP>
```

**第三步：打开浏览器**

```
http://127.0.0.1:7891
```

### 3.2 看板 10 大面板

| # | 面板 | 功能 | 你怎么用 |
|---|------|------|---------|
| 1 | **旨意看板** | Kanban 拖拽看板，按状态分列 | 看任务在哪个阶段，能叫停/取消/恢复 |
| 2 | **省部调度** | 各部门工作量可视化 | 看哪个部门在忙，任务分配是否合理 |
| 3 | **奏折阁** | 已完成任务的归档文档 | 查看历史任务的完整执行记录和审计 |
| 4 | **模板库** | 9 种预置旨意模板 | 快速创建标准化任务 |
| 5 | **官员名册** | Agent 性能和 token 消耗排行 | 看哪个 Agent 效率高/消耗大 |
| 6 | **天下要闻** | 自动新闻聚合 + 订阅 | 早朝 Agent 自动推送 |
| 7 | **模型配置** | 每个 Agent 可独立切换模型 | 给重要部门用好模型，其他用便宜的 |
| 8 | **技能管理** | 查看/添加 Agent 技能 | 扩展 Agent 能力 |
| 9 | **会话监控** | 实时 OpenClaw 对话 | 看 Agent 当前在干什么 |
| 10 | **早朝仪式** | 每日统计 + 动画 | 每天打开看总览 |

### 3.3 任务状态含义

| 状态 | 看板列 | 说明 |
|------|--------|------|
| 收件 | Inbox | 太子刚收到，还没分拣 |
| 已规划 | Planned | 中书省已拆解方案 |
| 审议中 | Under Review | 门下省正在审核 |
| 已派发 | Dispatched | 尚书省已分配给六部 |
| 执行中 | Executing | 六部正在干活 |
| 待审批 | Awaiting Approval | 执行完毕，等你确认 |
| 已完成 | Done | 归档为奏折 |

---

## 4. 文件同步

### 4.1 同步目录

| 你的 Mac | VPS | 用途 |
|----------|-----|------|
| `~/Work/zdwp/` | `/root/sync/zdwp/` | 水利公司项目 |
| `~/Personal/essays/` | `/root/sync/essays/` | 论文 |
| `~/Learn/` | `/root/sync/learn/` | 学习笔记 |
| `~/Work/reports/` | `/root/sync/reports/` | 工作报告 |

### 4.2 同步机制

- **工具**：Syncthing，双向实时同步
- **延迟**：文件保存后秒级同步
- **冲突处理**：自动检测，保留两个版本让你选
- **Mac 休眠**：恢复后自动补同步

### 4.3 工作流举例

```
1. 你在飞书发：「帮我整理水利公司的项目周报」
2. AI 在 VPS 的 /root/sync/zdwp/ 里创建/修改文件
3. Syncthing 秒级同步到你 Mac 的 ~/Work/zdwp/
4. 你在 Mac 上打开文件校审
5. 你修改后保存，Syncthing 同步回 VPS
6. 下次 AI 操作时看到的就是你改过的版本
```

### 4.4 查看同步状态

```bash
# Mac 终端
syncthing cli show connections    # 查看连接状态
syncthing cli show folder-status zdwp  # 查看某个目录同步状态

# 或打开 Syncthing Web UI
open http://127.0.0.1:8384
```

---

## 5. 常用运维命令

### 5.1 SSH 登录

```bash
ssh root@<VPS_IP>
```

### 5.2 服务状态检查

```bash
# OpenClaw Gateway
ssh root@<VPS_IP> 'export PNPM_HOME="/root/.local/share/pnpm" && export PATH="$PNPM_HOME:$PATH" && openclaw gateway status'

# 看板服务
ssh root@<VPS_IP> 'ss -tlnp | grep 7891'

# Syncthing
ssh root@<VPS_IP> 'systemctl status syncthing@root --no-pager'

# 所有 Docker 容器（Marzban 等）
ssh root@<VPS_IP> 'docker ps'
```

### 5.3 查看日志

```bash
# OpenClaw 日志
ssh root@<VPS_IP> 'tail -50 /tmp/openclaw/openclaw-$(date +%Y-%m-%d).log'

# 看板刷新日志
ssh root@<VPS_IP> 'tail -20 /tmp/sansheng_liubu_refresh.log'

# Syncthing 日志
ssh root@<VPS_IP> 'journalctl -u syncthing@root --since "10 min ago" --no-pager'
```

### 5.4 重启服务

```bash
# 重启 OpenClaw Gateway
ssh root@<VPS_IP> 'export PNPM_HOME="/root/.local/share/pnpm" && export PATH="$PNPM_HOME:$PATH" && openclaw gateway restart'

# 重启 Syncthing
ssh root@<VPS_IP> 'systemctl restart syncthing@root'
```

### 5.5 访问 Marzban 面板（代理管理）

```bash
ssh -L 8000:localhost:8000 root@<VPS_IP>
# 浏览器打开 http://127.0.0.1:8000/dashboard/
# 账号：tianli / <PASSWORD>
```

---

## 6. 端口一览

| 端口 | 服务 | 访问方式 |
|------|------|---------|
| 443 | VLESS + Reality | Shadowrocket 客户端 |
| 1080 | Shadowsocks | 客户端连接 |
| 7891 | Edict 看板 | SSH 隧道 → http://127.0.0.1:7891 |
| 8000 | Marzban 面板 | SSH 隧道 → http://127.0.0.1:8000 |
| 8384 | Syncthing Web UI (VPS) | SSH 隧道（如需要） |
| 18789 | OpenClaw Gateway | 内部使用，不对外 |

---

## 7. 快速参考卡片

### 看看板

```bash
ssh -L 7891:localhost:7891 root@<VPS_IP>
# 浏览器 → http://127.0.0.1:7891
```

### 发任务

飞书 → AI助手 → 发消息（「帮我做 XX」）

### 查文件

OpenClaw 改的文件会自动同步到你 Mac 对应目录，直接在本地打开即可。
