# VPS 代理搭建知识整理

> 来源：朋友聊天讨论 + PureVoltage VPS 购买背景
> 日期：2026-03-09
> 最后更新：2026-03-09

---

## 你买的是什么

- **服务商**：PureVoltage
- **产品**：VPS（虚拟专用服务器）
- **流量**：500G/月
- **线路**：普通线路（非优化线路）

---

## VPS 信息

| 项目 | 值 |
|------|-----|
| IP | <VPS_IP> |
| 系统 | Ubuntu 22.04 LTS |
| 内核 | 5.15.0-171 |
| CPU | AMD EPYC 7742, 4 核 |
| 内存 | 8GB |
| 硬盘 | 100GB |
| 已安装 | Marzban（Docker）+ Xray 24.12.31 |
| 代理协议 | VLESS + Reality（443 端口）、Shadowsocks（1080 端口） |
| 面板端口 | 8000（仅 localhost） |
| 面板账号 | tianli / <PASSWORD> |

---

## 本地代理软件（Shadowrocket macOS）

> 从 Clash 迁移到 Shadowrocket，2026-03-09

| 项目 | 值 | 说明 |
|------|-----|------|
| 进程名 | MacPacketTunnel（显示为 MacPacket） | Shadowrocket macOS 版的进程名 |
| HTTP 代理端口 | **7890** | 已从默认改为 7890，和之前 Clash 保持一致 |
| 监听地址 | 127.0.0.1:7890 + <LOCAL_IP>:7890 | 本地回环 + 局域网 IP 都在监听 |
| SOCKS5 端口 | 未单独监听 | 仅 HTTP 代理，共用 7890 |
| 系统代理 | 未开启 | macOS 系统代理关闭，终端靠环境变量走代理 |

### 端口迁移说明

之前用 Clash 时代理端口是 7890，切换到 Shadowrocket 后把端口也改成了 7890，这样所有依赖 `127.0.0.1:7890` 的配置都不用改。

### 终端代理配置

终端代理通过环境变量实现，配置文件位于：

**`~/Documents/sync/shell/config/tools/proxy.zsh`**（由 `.zshrc` 自动 source）

```bash
# 默认开启（shell 启动时自动生效）
export http_proxy="http://127.0.0.1:7890"
export https_proxy="http://127.0.0.1:7890"
export all_proxy="socks5://127.0.0.1:7890"

# 快捷开关
proxy-on   # 开启代理
proxy-off  # 关闭代理
```

---

## Marzban 面板访问方法

面板只监听 `127.0.0.1:8000`（仅限 VPS 本机访问），不对外暴露，所以**必须用 SSH 隧道**。

### 步骤

```bash
# 1. 在本地终端执行，建立 SSH 隧道
ssh -L 8000:localhost:8000 root@<VPS_IP>

# 2. 保持终端不要关，浏览器打开：
#    http://127.0.0.1:8000/dashboard/
#                          ^^^^^^^^^^^
#                          注意：必须加 /dashboard/，不是根路径 /
#
# 3. 用 tianli / <PASSWORD> 登录
```

### 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| 页面黑屏/动画 | 访问了 `/` 而不是 `/dashboard/` | 加上 `/dashboard/` 路径 |
| 本地 8000 被占用 | 其他程序用了 8000 | 改用 `ssh -L 8001:localhost:8000 ...`，然后访问 `127.0.0.1:8001` |
| SSH 断了页面打不开 | 隧道断了 | 重新执行 ssh 命令 |

### 原理

```
你的浏览器
    ↓ 访问 127.0.0.1:8000
本地电脑的 8000 端口
    ↓ SSH 加密隧道（你的电脑 ←→ VPS）
VPS 上的 localhost:8000
    ↓
Marzban 面板响应
```

SSH 隧道就是在你电脑和 VPS 之间挖了一条加密地道。浏览器以为在访问本地 8000 端口，实际上请求通过地道到达了 VPS。

**为什么不直接对外开放 8000？** 面板暴露在公网容易被扫描和爆破，SSH 隧道只有持有密码/密钥的人能用。

---

## 网络优化（已执行）

> 2026-03-10 执行，所有优化已生效

### 我做了什么

1. **备份**了原始配置：`/etc/sysctl.conf.bak.20260310`
2. 在 `/etc/sysctl.conf` 末尾追加了以下参数
3. 执行 `sysctl -p` 使配置立即生效
4. 验证 BBR 已加载（`lsmod | grep bbr` 显示 `tcp_bbr` 模块）

### 优化前 vs 优化后

| 参数 | 优化前 | 优化后 | 作用 |
|------|--------|--------|------|
| tcp_congestion_control | cubic | **bbr** | 拥塞控制算法 |
| default_qdisc | fq_codel | **fq** | 队列调度器（配合 BBR） |
| rmem_max | 212992 (208KB) | **33554432 (32MB)** | 接收缓冲区上限 |
| wmem_max | 212992 (208KB) | **33554432 (32MB)** | 发送缓冲区上限 |
| tcp_rmem | 4096 131072 6291456 | **4096 87380 33554432** | TCP 接收缓冲区范围 |
| tcp_wmem | 4096 16384 4194304 | **4096 65536 33554432** | TCP 发送缓冲区范围 |
| tcp_fastopen | 1 | **3** | TCP 快速打开 |
| tcp_mtu_probing | 0 | **1** | MTU 自动探测 |

### 每项优化的原理

#### 1. BBR 拥塞控制（效果最大）

**问题**：TCP 传输需要决定"一次发多少数据"。默认的 cubic 算法逻辑是：
- 正常时逐渐加速
- 一旦发现丢包 → **立刻把速度砍一半**
- 然后再慢慢加速 → 又丢包 → 又砍一半...

在国际线路上丢包很常见（不是网络真出问题，而是链路拥挤），所以 cubic 会反复降速，体感很卡。

**BBR 的做法**：不看丢包，而是持续测量"实际带宽"和"最低延迟"，根据这两个值动态调整发送速度。偶尔丢个包不影响大局。

```
cubic：速度 → 丢包 → 砍半 → 慢慢恢复 → 丢包 → 砍半（锯齿形）
BBR：  速度 → 持续测量实际带宽 → 平稳运行（平滑曲线）
```

#### 2. FQ 队列调度器

**配合 BBR 使用**。FQ（Fair Queueing）对每个连接公平分配带宽，避免某个连接抢占所有资源。BBR 官方推荐搭配 FQ。

#### 3. TCP 缓冲区加大

**问题**：默认缓冲区只有 208KB。高延迟链路上，数据要在"管道"里飞行很长时间：

```
带宽利用率 = 缓冲区大小 / (带宽 × 延迟)

假设 100Mbps 带宽，200ms 延迟：
  需要的缓冲区 = 100Mbps × 0.2s = 2.5MB
  默认只有 208KB → 只能用到 8% 的带宽！
```

加大到 32MB 后，系统会按需使用，不会浪费内存，但上限够高，带宽能跑满。

#### 4. TCP Fast Open (TFO)

**正常 TCP 连接**：三次握手后才开始传数据（耗时 1.5 个 RTT）
**开启 TFO 后**：握手的同时就带上数据（节省 1 个 RTT）

你的延迟约 200ms，每次新连接省 200ms，打开网页时体感更快。

`tcp_fastopen = 3` 的含义：`1`（客户端）+ `2`（服务端）= `3`（两边都开）。

#### 5. MTU 探测

不同网络链路支持的最大数据包大小不同。默认不探测时，如果包太大被拆分会降低效率。开启后系统自动找到最优包大小。

### 如何验证

```bash
# SSH 到 VPS 后执行
sysctl net.ipv4.tcp_congestion_control    # 应显示 bbr
lsmod | grep bbr                          # 应显示 tcp_bbr
sysctl net.core.rmem_max                  # 应显示 33554432
```

### 如何回滚

如果出问题，恢复备份即可：
```bash
cp /etc/sysctl.conf.bak.20260310 /etc/sysctl.conf
sysctl -p
```

---

## 知识科普

### 线路类型

| 线路类型 | 说明 | 特点 |
|---------|------|------|
| **普通线路** | 走默认国际路由，不做特殊优化 | 便宜，但延迟较高、高峰期可能卡顿 |
| **CN2 GT** | 中国电信次优线路 | 比普通好一些，价格适中 |
| **CN2 GIA** | 中国电信最优线路，三网优化 | 延迟低、稳定，但价格贵 |
| **DMIT 三网优化** | DMIT 服务商提供的 CN2 GIA 线路 | 质量好，适合自用，价格较高 |

> 搬瓦工线路好但配置低且贵 → 提供 CN2 GIA 线路，但同价位配置不如普通线路 VPS。

### 伪装（混淆）

代理流量需要伪装成正常 HTTPS 流量，避免被识别。你的 VPS 用的是 **VLESS + Reality**，伪装成访问 `www.microsoft.com`，这是目前最好的伪装方案之一。

### Marzban

开源代理管理面板（基于 Xray），支持多协议、用户管理、流量统计、订阅链接生成。适合自用 + 分享给朋友。

### 提速方案对比

| 方案 | 推荐 | 理由 |
|------|------|------|
| **[x] BBR + FQ** | 强烈推荐 | 内核自带，零风险，效果最明显 |
| **[x] 加大 TCP 缓冲区** | 推荐 | 让高延迟线路带宽跑满 |
| **[x] TCP Fast Open** | 推荐 | 每次新连接省一个 RTT |
| **[x] MTU 探测** | 推荐 | 防止包大小不匹配导致的性能损失 |
| BBR2 | 不推荐 | 需换内核，收益不明显 |
| 锐速（LotServer） | 不推荐 | 闭源、不支持新内核、有安全隐患 |
| 换 CN2 GIA 线路 | 看预算 | 从根本上解决线路质量问题，但贵 |

---

## SSH 连接问题排查（2026-03-09，进行中）

### 现象

本地无法 SSH 连接 VPS，无论走代理还是直连都报错：

```
# 走机场代理（香港 168.93.202.192）
Connection closed by <VPS_IP> port 22

# 直连（无代理）
kex_exchange_identification: read: Connection reset by peer
Connection reset by <VPS_IP> port 22
```

### 已排除的原因

| 排查项 | 结果 |
|--------|------|
| VPS 是否在线 | 在线，ping 通（0.27ms） |
| SSH 端口是否开放 | 开放（nc 测试 port 22 succeeded） |
| SSH 服务是否运行 | 运行中（systemctl status sshd → active running） |
| PermitRootLogin | yes |
| /etc/hosts.deny | 空（无黑名单） |
| 出口 IP 问题 | 直连和代理都不行，排除 IP 被 ban |

### 关键线索

SSH 详细日志（`ssh -vvv`）显示：
```
kex_exchange_identification: Connection closed by remote host
```
连接在密钥交换阶段就被服务器关闭，**还没到认证步骤**。

### 待排查（下次继续）

在 VPS Console 里运行以下命令，把结果保存：

```bash
# 1. 检查 MaxStartups 配置
grep -i "maxstart" /etc/ssh/sshd_config

# 2. 查看 SSH 日志，看有没有拒绝记录
journalctl -u ssh --no-pager -n 30

# 3. 检查防火墙
iptables -L -n
ufw status

# 4. 检查 fail2ban
fail2ban-client status sshd 2>/dev/null || echo "fail2ban not installed"

# 5. 看看 SSH 的密钥交换算法
sshd -T | grep kexalgorithms
```

### 临时解决方案

通过 VPS 面板的 **Console**（VNC 网页终端）可以登录操作，不受 SSH 问题影响。

---

## 下一步行动

- [x] 获取 VPS 的 SSH 登录信息
- [x] 安装 Marzban 面板
- [x] 配置 VLESS + Reality 协议
- [x] 开启 BBR + 全套网络优化（2026-03-10）
- [ ] **修复 SSH 连接问题**（优先！排查命令见上方）
- [ ] 配置客户端连接测试（用 Shadowrocket 连接自建节点）
