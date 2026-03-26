# 域名 + Cloudflare + 反向代理 知识与方案

> 创建：2026-03-10
> 域名：tianlizeng.cloud
> 状态：待执行

---

## 一、基础知识

### 1.1 域名是什么

域名是 IP 地址的人类可读别名。你的 VPS IP 是 `<VPS_IP>`，记不住。有了域名后：

```
panel.tianlizeng.cloud → <VPS_IP>:8000 (Marzban)
board.tianlizeng.cloud → <VPS_IP>:7891 (看板)
```

域名本身不存储在你的服务器上，它存储在 **DNS 服务器**上。DNS 就是一本"电话簿"：

```
浏览器问：panel.tianlizeng.cloud 的 IP 是多少？
DNS 回答：<VPS_IP>
浏览器：好，我去连这个 IP
```

### 1.2 DNS 记录类型

| 类型 | 作用 | 举例 |
|------|------|------|
| **A** | 域名 → IPv4 地址 | `panel.tianlizeng.cloud → <VPS_IP>` |
| **AAAA** | 域名 → IPv6 地址 | 你的 VPS 没有 IPv6，不用管 |
| **CNAME** | 域名 → 另一个域名 | `www.tianlizeng.cloud → tianlizeng.cloud` |
| **MX** | 邮件服务器 | 不需要 |
| **TXT** | 验证信息 | 域名所有权验证等 |

### 1.3 Cloudflare 是什么

Cloudflare（简称 CF）是一个 CDN + 安全防护 + DNS 管理平台。你把域名的 NS（域名服务器）从阿里云转到了 CF，意味着：

- **DNS 管理**：在 CF 后台添加/修改 DNS 记录
- **CDN 代理**（橙色云朵 ☁️）：流量先经过 CF 再到你的服务器
- **免费 SSL**：CF 自动提供 HTTPS 证书
- **安全防护**：隐藏真实 IP、防 DDoS、防爬虫

### 1.4 Cloudflare 代理模式（重要概念）

在 CF 添加 DNS 记录时，有一个"代理状态"开关：

| 模式 | 图标 | 流量路径 | 特点 |
|------|------|---------|------|
| **Proxied（代理）** | 🟠 橙色云朵 | 浏览器 → CF → VPS | 隐藏真实 IP，自动 HTTPS，防 DDoS |
| **DNS only** | ⬜ 灰色云朵 | 浏览器 → VPS | 直连，IP 暴露，需自己管证书 |

**你的选择：Proxied 模式**（推荐）。

### 1.5 SSL/TLS 加密模式

CF 后台 → SSL/TLS → 加密模式，有 4 种：

| 模式 | CF → VPS 之间 | 推荐 |
|------|-------------|------|
| Off | 无加密 | ❌ |
| Flexible | CF → VPS 用 HTTP（不加密） | ⚠️ 可用但不完美 |
| **Full** | CF → VPS 用 HTTPS（自签证书即可） | ✅ 推荐 |
| Full (Strict) | CF → VPS 用 HTTPS（需要 CA 签发的证书） | ✅ 最安全 |

**推荐 Full 模式**：VPS 上用 CF 颁发的免费 Origin 证书（15 年有效），不用续期。

### 1.6 反向代理是什么

你的 VPS 上有多个服务，都监听不同端口：

```
Marzban: localhost:8000
看板:     localhost:7891
```

但域名访问默认只走 443（HTTPS）端口。**反向代理**（Nginx）的作用就是：

```
panel.tianlizeng.cloud:443 → Nginx 看到是 panel 子域名 → 转发到 localhost:8000
board.tianlizeng.cloud:443 → Nginx 看到是 board 子域名 → 转发到 localhost:7891
```

一个 Nginx 监听 443 端口，根据域名分流到不同后端服务。

---

## 二、你的架构设计

### 2.1 整体数据流

```
你的浏览器/手机
    ↓ https://panel.tianlizeng.cloud
Cloudflare CDN（全球节点）
    ↓ 自动 HTTPS 终结
    ↓ 用 Origin 证书加密回源
VPS Nginx (443)
    ↓ 根据子域名分流
    ├── panel.tianlizeng.cloud → localhost:8000 (Marzban)
    ├── board.tianlizeng.cloud → localhost:7891 (Edict 看板)
    └── sub.tianlizeng.cloud   → localhost:8000 (Marzban 订阅)
```

### 2.2 子域名规划

| 子域名 | 用途 | 后端 | CF 代理 |
|--------|------|------|---------|
| `panel.tianlizeng.cloud` | Marzban 管理面板 | localhost:8000 | 🟠 Proxied |
| `board.tianlizeng.cloud` | Edict 三省六部看板 | localhost:7891 | 🟠 Proxied |
| `sub.tianlizeng.cloud` | Marzban 订阅链接 | localhost:8000 | 🟠 Proxied |

> **注意**：主域名 `tianlizeng.cloud` 建议不指向 VPS，留给个人网站或其他用途。

### 2.3 安全层级

```
第 1 层：Cloudflare — 隐藏 IP、DDoS 防护、WAF
第 2 层：Nginx — 只接受 CF 来源的请求（可选）
第 3 层：服务本身 — Marzban 有账号密码、看板可加认证
```

---

## 三、执行步骤

### 3.1 Cloudflare 端配置（你在 CF 后台操作）

#### Step 1：设置 SSL 模式

CF 后台 → SSL/TLS → Overview → 选择 **Full**

#### Step 2：生成 Origin 证书

CF 后台 → SSL/TLS → Origin Server → Create Certificate

- 私钥类型：RSA (2048)
- 域名：`*.tianlizeng.cloud, tianlizeng.cloud`（通配符）
- 有效期：15 years

生成后会给你两个文件内容：
- **Origin Certificate**（公钥）→ 保存为 `origin.pem`
- **Private Key**（私钥）→ 保存为 `origin-key.pem`

> ⚠️ 私钥只显示一次，务必保存！

#### Step 3：添加 DNS 记录

CF 后台 → DNS → Records → Add record

| Type | Name | Content | Proxy |
|------|------|---------|-------|
| A | `panel` | `<VPS_IP>` | 🟠 Proxied |
| A | `board` | `<VPS_IP>` | 🟠 Proxied |
| A | `sub` | `<VPS_IP>` | 🟠 Proxied |

### 3.2 VPS 端配置（我来操作）

#### Step 1：安装 Nginx

```bash
apt install -y nginx
```

#### Step 2：上传 Origin 证书

```bash
mkdir -p /etc/nginx/ssl/
# 把 CF 生成的证书内容写入
# /etc/nginx/ssl/origin.pem      （公钥）
# /etc/nginx/ssl/origin-key.pem  （私钥）
```

#### Step 3：配置 Nginx 反向代理

```nginx
# /etc/nginx/sites-available/panel.tianlizeng.cloud
server {
    listen 443 ssl http2;
    server_name panel.tianlizeng.cloud;

    ssl_certificate     /etc/nginx/ssl/origin.pem;
    ssl_certificate_key /etc/nginx/ssl/origin-key.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

类似地为 `board` 和 `sub` 各创建一个配置文件。

#### Step 4：更新 Marzban 订阅地址

修改 `/opt/marzban/.env`：

```bash
# 改前（裸 IP）
XRAY_SUBSCRIPTION_URL_PREFIX = "http://<VPS_IP>:8000"

# 改后（域名）
XRAY_SUBSCRIPTION_URL_PREFIX = "https://sub.tianlizeng.cloud"
```

然后重启 Marzban：`docker restart marzban-marzban-1`

#### Step 5：验证

```bash
curl -I https://panel.tianlizeng.cloud/dashboard/
curl -I https://board.tianlizeng.cloud/
curl -I https://sub.tianlizeng.cloud/
```

---

## 四、安全加固（可选但推荐）

### 4.1 限制只接受 CF 来源

如果有人知道你的真实 IP，可以直接绕过 CF 访问 Nginx。防止这种情况：

```nginx
# 在 nginx server 块中添加
# Cloudflare IP 段（定期更新）
set_real_ip_from 173.245.48.0/20;
set_real_ip_from 103.21.244.0/22;
set_real_ip_from 103.22.200.0/22;
set_real_ip_from 103.31.4.0/22;
set_real_ip_from 141.101.64.0/18;
set_real_ip_from 108.162.192.0/18;
set_real_ip_from 190.93.240.0/20;
set_real_ip_from 188.114.96.0/20;
set_real_ip_from 197.234.240.0/22;
set_real_ip_from 198.41.128.0/17;
set_real_ip_from 162.158.0.0/15;
set_real_ip_from 104.16.0.0/13;
set_real_ip_from 104.24.0.0/14;
set_real_ip_from 172.64.0.0/13;
set_real_ip_from 131.0.72.0/22;
real_ip_header CF-Connecting-IP;
```

### 4.2 Cloudflare Zero Trust（高级）

CF 提供免费的 Zero Trust 功能，可以给面板加一层登录保护：
- 访问 `panel.tianlizeng.cloud` 时先跳转到 CF 登录页
- 用邮箱验证码或 Google 账号登录
- 通过后才能访问 Marzban 面板

这等于在 Marzban 自身的账号密码之外又加了一道门。

### 4.3 防火墙规则

VPS 上用 UFW 只允许 CF IP + SSH：

```bash
ufw allow 22/tcp          # SSH
ufw allow from 173.245.48.0/20 to any port 443
ufw allow from 103.21.244.0/22 to any port 443
# ... 其他 CF IP 段
ufw enable
```

---

## 五、与现有服务的关系

### 5.1 会影响代理（梯子）吗？

**不会。** VLESS Reality 用的是 443 端口，但它不是普通的 HTTPS 服务——它用 Reality 协议伪装成访问 microsoft.com。Nginx 监听的 443 是另一回事：

```
VLESS Reality (443) — Xray 直接监听，处理代理流量
Nginx (443)         — 处理域名访问的 Web 流量
```

**问题**：两个服务不能同时监听 443。需要用 Xray 的 fallback 功能把非代理流量转发给 Nginx，或者让 Nginx 监听其他端口（如 8443），CF 回源时指向 8443。

**推荐方案：Nginx 监听 8443，CF 回源到 8443**

```
CF (443) → VPS Nginx (8443) → 各服务
Xray (443) → 处理代理流量，不受影响
```

CF 支持自定义回源端口（在 Origin Rules 或 DNS 记录里设置）。

### 5.2 SSH 隧道还需要吗？

配好域名后，**日常不再需要 SSH 隧道**：
- 看 Marzban → 浏览器打开 `https://panel.tianlizeng.cloud/dashboard/`
- 看看板 → 浏览器打开 `https://board.tianlizeng.cloud`

SSH 隧道作为**备用方案**保留（CF 挂了或调试时用）。

### 5.3 端口全景（配置后）

| 端口 | 服务 | 监听方式 | 访问方式 |
|------|------|---------|---------|
| 22 | SSH | 0.0.0.0 | `ssh root@<VPS_IP>` |
| 443 | Xray (VLESS Reality) | 0.0.0.0 | Shadowrocket 客户端 |
| 1080 | Xray (Shadowsocks) | 0.0.0.0 | 客户端连接 |
| **8443** | **Nginx（新增）** | 0.0.0.0 | CF 回源 → 域名访问 |
| 7891 | Edict 看板 | 127.0.0.1 | Nginx 反代 |
| 8000 | Marzban 面板 | 127.0.0.1 | Nginx 反代 |
| 18789 | OpenClaw Gateway | 127.0.0.1 | 内部使用 |

---

## 六、Marzban 订阅链接的好处

改用域名后，Shadowrocket 订阅地址从：

```
http://<VPS_IP>:8000/sub/xxx
```

变成：

```
https://sub.tianlizeng.cloud/sub/xxx
```

好处：
1. **HTTPS 加密**：订阅内容不再明文传输
2. **隐藏 IP**：订阅链接不暴露 VPS 真实 IP
3. **方便更换 IP**：如果 VPS IP 被封，改 CF DNS 记录就行，客户端订阅地址不用变
4. **看起来更正规**：分享给朋友也好看

---

## 七、关键概念速查表

| 概念 | 一句话解释 |
|------|-----------|
| **DNS** | 域名→IP 的电话簿 |
| **A 记录** | 域名对应的 IPv4 地址 |
| **NS 记录** | 这个域名的 DNS 由谁管理（你的是 Cloudflare） |
| **Cloudflare Proxy** | 流量先过 CF 再到你服务器，隐藏 IP |
| **Origin 证书** | CF 颁发给你服务器的证书，用于 CF↔VPS 之间加密 |
| **Let's Encrypt** | 免费 CA 证书，需要 90 天续期（你不需要，用 CF Origin 就行） |
| **反向代理** | Nginx 根据域名把请求转发到不同后端服务 |
| **SSL 终结** | CF 帮你处理 HTTPS，到你服务器可以是 HTTP（Flexible）或 HTTPS（Full） |
| **Zero Trust** | CF 的零信任安全方案，给网站加一层登录保护 |
| **Fallback** | Xray 收到非代理流量时，转发给其他服务（如 Nginx） |

---

## 八、待你操作的步骤

完成以下步骤后告诉我，我来配置 VPS 端：

1. [ ] CF 后台设 SSL 模式为 **Full**
2. [ ] CF 后台生成 **Origin 证书**（通配符 `*.tianlizeng.cloud`），把证书内容发给我
3. [ ] CF 后台添加 3 条 **A 记录**（panel/board/sub → <VPS_IP>，开启 Proxied）

---

## 九、文件索引

| 文件 | 说明 |
|------|------|
| `~/vps/vps-proxy-guide.md` | VPS 基础信息 + 网络优化 + Marzban 使用 |
| `~/vps/usage-guide.md` | OpenClaw + Edict + Syncthing 日常使用指南 |
| `~/vps/domain-cloudflare-guide.md` | **本文件** — 域名 + CF + 反向代理知识与方案 |
| `~/vps/CLAUDE.md` | 项目总览（需更新） |
