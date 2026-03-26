# VPS Setup Guide

**English** | [中文](README_CN.md)

Complete guide for setting up a VPS with Nginx reverse proxy, Cloudflare integration, proxy panel, and system optimization.

[![VPS Guide](https://img.shields.io/badge/VPS-Setup_Guide-blue?style=for-the-badge)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

---

## What's Inside

| Guide | Topics |
|-------|--------|
| **Domain + Cloudflare** | DNS setup, Origin Rules, SSL certificates, port routing |
| **VPS Proxy Guide** | System optimization (BBR), Nginx reverse proxy, Marzban panel |
| **Usage Guide** | Day-to-day VPS management, service monitoring, troubleshooting |
| **OpenClaw Deployment** | AI agent framework deployment on VPS |

## Architecture

```
User Browser
    ↓ HTTPS (443)
Cloudflare (CDN + WAF)
    ↓ Origin Rule → custom port
VPS Nginx (SSL termination)
    ↓ proxy_pass
Backend Services (Streamlit, FastAPI, Marzban, etc.)
```

## Key Topics

- **System Optimization**: TCP BBR, kernel tuning, connection limits
- **Nginx Reverse Proxy**: Multi-site hosting with SNI, WebSocket support
- **Cloudflare Integration**: DNS management, Origin Rules, SSL certificates
- **Marzban Proxy Panel**: V2Ray/Xray management with subscription support
- **Shadowrocket Config**: iOS proxy rules and routing

## Note

All IP addresses and passwords have been replaced with placeholders (`<VPS_IP>`, `<PASSWORD>`, etc.) for security.

## License

MIT
