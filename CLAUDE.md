# VPS 项目

## 概况

- **服务商**：PureVoltage
- **IP**：<VPS_IP>
- **系统**：Ubuntu 22.04 LTS（内核 5.15.0）
- **配置**：4 核 AMD EPYC / 8GB / 100GB / 500G 流量/月
- **线路**：美国普通线路
- **SSH 登录**：`ssh root@<VPS_IP>`

## 已部署服务

| 服务 | 端口 | 访问方式 | 状态 |
|------|------|----------|------|
| Marzban 面板 | 8000 | SSH 隧道 → `http://127.0.0.1:8000/dashboard/` | 运行中 |
| VLESS + Reality | 443 | Shadowrocket 客户端连接 | 运行中 |
| Shadowsocks | 1080 | 客户端连接 | 运行中 |

## 已完成的优化

- [x] BBR + FQ 拥塞控制（2026-03-10）
- [x] TCP 缓冲区加大至 32MB
- [x] TCP Fast Open 开启
- [x] MTU 自动探测开启
- 备份：`/etc/sysctl.conf.bak.20260310`

## 本目录文件

| 文件 | 说明 |
|------|------|
| `vps-proxy-guide.md` | VPS 完整知识文档（硬件、软件、协议、网络优化原理、操作记录） |
| `openclaw-vps-plan.md` | OpenClaw VPS 部署计划（待执行） |

## 常用操作

```bash
# SSH 登录
ssh root@<VPS_IP>

# 访问 Marzban 面板（本地终端执行）
ssh -L 8000:localhost:8000 root@<VPS_IP>
# 然后浏览器打开 http://127.0.0.1:8000/dashboard/
# 账号：tianli / <PASSWORD>

# 检查 VPS 服务状态
ssh root@<VPS_IP> "docker ps && sysctl net.ipv4.tcp_congestion_control"

# 查看 Marzban 日志
ssh root@<VPS_IP> "docker logs marzban-marzban-1 --tail 50"
```

## 待办

- [ ] 部署 OpenClaw（见 `openclaw-vps-plan.md`）
