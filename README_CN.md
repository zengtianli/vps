# VPS 搭建指南

[English](README.md) | **中文**

完整的 VPS 搭建教程——Nginx 反向代理、Cloudflare 接入、代理面板和系统优化。

[![VPS Guide](https://img.shields.io/badge/VPS-搭建指南-blue?style=for-the-badge)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

---

## 内容概览

| 指南 | 主题 |
|------|------|
| **域名 + Cloudflare** | DNS 配置、Origin Rules、SSL 证书、端口路由 |
| **VPS 代理指南** | 系统优化（BBR）、Nginx 反代、Marzban 面板 |
| **使用指南** | 日常运维、服务监控、故障排查 |
| **OpenClaw 部署** | AI 多智能体框架部署 |

## 架构

```
用户浏览器
    ↓ HTTPS (443)
Cloudflare（CDN + WAF）
    ↓ Origin Rule → 自定义端口
VPS Nginx（SSL 终止）
    ↓ proxy_pass
后端服务（Streamlit、FastAPI、Marzban 等）
```

## 涵盖主题

- **系统优化**：TCP BBR、内核调优、连接数限制
- **Nginx 反代**：多站点 SNI 托管、WebSocket 支持
- **Cloudflare 接入**：DNS 管理、Origin Rules、SSL 证书
- **Marzban 代理面板**：V2Ray/Xray 管理 + 订阅支持
- **Shadowrocket 配置**：iOS 代理规则和路由

## 说明

所有 IP 地址和密码已替换为占位符（`<VPS_IP>`、`<PASSWORD>` 等）。

## 许可证

MIT
