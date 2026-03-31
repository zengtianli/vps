#!/usr/bin/env python3
"""
飞书指令路由服务
监听 HTTP 端口 9002，接收 OpenClaw 转发的飞书指令，执行对应操作。

支持指令：
  /rebuild  → 触发 docs 站点重建
  /status   → 返回 VPS 状态摘要
  /review   → 触发今日学习回顾
  /morning  → 手动触发早朝简报
  /clear    → 重置太子对话历史（清空 session）
  /log      → 查看服务日志（/log [服务名]）
  /restart  → 重启服务（/restart [服务名]）
  /git      → 拉取 repo 最新代码（/git [repo名]）
  /save     → 保存内容为网页文件（/save 标题\n内容）
  /ts       → 翻译并存文件（/ts 内容），中→英或英→中，返回链接
  /help     → 显示可用指令

部署：systemd service，OpenClaw taizi 通过 HTTP 调用。
"""

import glob
import os
import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

CLAUDE_BASE_URL = os.environ.get("MMKG_BASE_URL", "https://code.mmkg.cloud")
CLAUDE_API_KEY = os.environ.get("MMKG_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-5"

PORT = 9002
CN_TZ = timezone(timedelta(hours=8))
OPENCLAW_AGENTS_DIR = "/root/.openclaw/agents"

ALLOWED_SERVICES = [
    "feishu-commands",
    "edict-dashboard",
    "edict-refresh",
    "cc-chat",
    "alert-webhook",
    "github-webhook",
]

REPO_PATHS = {
    "vps": "/var/www",
    "edict": "/opt/edict",
    "docs": "/var/www/docs",
    "scripts": "/var/www/scripts",
    "learn": "/var/www/learn",
}


def run_cmd(cmd, timeout=60):
    """执行命令并返回输出"""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() + ("\n" + r.stderr.strip() if r.stderr.strip() else "")
    except subprocess.TimeoutExpired:
        return "⚠️ 命令执行超时"
    except Exception as e:
        return f"❌ 执行失败: {e}"


def handle_rebuild():
    """触发 docs 站点重建"""
    result = run_cmd("cd /var/www && bash build_docs.sh 2>&1 | tail -10", timeout=120)
    return f"🔨 Docs 站点重建完成\n\n{result}"


def handle_status():
    """返回 VPS 状态摘要"""
    sections = []

    # Disk
    disk = run_cmd("df -h / | tail -1 | awk '{print $3\"/\"$2\" (\"$5\" used)\"}'")
    sections.append(f"💾 磁盘: {disk}")

    # Memory
    mem = run_cmd("free -h | awk 'NR==2{print $3\"/\"$2}'")
    sections.append(f"🧠 内存: {mem}")

    # Load
    load = run_cmd("uptime | awk -F'average:' '{print $2}'")
    sections.append(f"⚡ 负载:{load}")

    # Docker
    docker = run_cmd("docker ps --format '{{.Names}}: {{.Status}}' 2>/dev/null")
    sections.append(f"\n🐳 Docker:\n{docker}")

    # Systemd services
    svcs = []
    for svc in ["cc-chat", "edict-dashboard", "edict-refresh", "github-webhook", "alert-webhook"]:
        status = run_cmd(f"systemctl is-active {svc} 2>/dev/null")
        icon = "✅" if status == "active" else "❌"
        svcs.append(f"  {icon} {svc}: {status}")
    sections.append("\n⚙️ 服务:\n" + "\n".join(svcs))

    # Recent builds
    builds = run_cmd("grep 'Build complete\\|build_site.py' /var/log/docs_build.log 2>/dev/null | tail -3")
    if builds:
        sections.append(f"\n🏗️ 最近构建:\n{builds}")

    return "\n".join(sections)


def handle_review():
    """触发今日学习回顾"""
    today = datetime.now(CN_TZ).strftime("%Y-%m-%d")
    result = run_cmd(f"/usr/bin/python3 /var/www/learning_digest.py daily {today} 2>&1", timeout=180)
    return f"📚 学习回顾 ({today})\n\n{result}"


def handle_morning():
    """手动触发早朝简报"""
    result = run_cmd("/usr/bin/python3 /var/www/morning_briefing.py 2>&1", timeout=120)
    return f"🌅 早朝简报\n\n{result}"


def handle_clear():
    """重置太子对话历史（清空 session jsonl 文件，保留 session 元数据）"""
    sessions_json = f"{OPENCLAW_AGENTS_DIR}/taizi/sessions/sessions.json"
    cleared_files = []
    errors = []

    try:
        with open(sessions_json, "r") as f:
            sessions = json.load(f)

        for key, meta in sessions.items():
            session_file = meta.get("sessionFile", "")
            if session_file and session_file.endswith(".jsonl"):
                try:
                    open(session_file, "w").close()  # truncate
                    cleared_files.append(session_file.split("/")[-1])
                except Exception as e:
                    errors.append(f"{session_file}: {e}")

    except Exception as e:
        return f"❌ 读取 sessions.json 失败: {e}"

    if errors:
        return f"⚠️ 部分清除失败:\n" + "\n".join(errors)

    count = len(cleared_files)
    if count == 0:
        return "ℹ️ 没有找到可清除的 session"

    return f"🗑️ 已清空太子对话历史（{count} 个 session）\n下一条消息将开启全新对话。"


def handle_log(svc):
    """查看服务日志"""
    if not svc:
        return f"📋 可查看日志的服务：\n" + "\n".join(f"  • {s}" for s in ALLOWED_SERVICES) + "\n\n用法：/log [服务名]"
    if svc not in ALLOWED_SERVICES:
        return f"❌ 服务 '{svc}' 不在白名单\n可用服务：{', '.join(ALLOWED_SERVICES)}"
    result = run_cmd(f"journalctl -u {svc} -n 50 --no-pager 2>&1")
    return f"📄 {svc} 最近日志：\n\n{result}"


def handle_restart(svc):
    """重启服务"""
    if not svc:
        return f"📋 可重启的服务：\n" + "\n".join(f"  • {s}" for s in ALLOWED_SERVICES) + "\n\n用法：/restart [服务名]"
    if svc not in ALLOWED_SERVICES:
        return f"❌ 服务 '{svc}' 不在白名单\n可用服务：{', '.join(ALLOWED_SERVICES)}"
    run_cmd(f"systemctl restart {svc}")
    status = run_cmd(f"systemctl is-active {svc}")
    icon = "✅" if status == "active" else "❌"
    return f"{icon} {svc} 已重启，当前状态：{status}"


def handle_git(repo):
    """拉取 repo 最新代码"""
    if not repo:
        repo_list = "\n".join(f"  • {k} → {v}" for k, v in REPO_PATHS.items())
        return f"📋 可拉取的 repo：\n{repo_list}\n\n用法：/git [repo名]"
    if repo not in REPO_PATHS:
        return f"❌ repo '{repo}' 不在白名单\n可用：{', '.join(REPO_PATHS.keys())}"
    path = REPO_PATHS[repo]
    result = run_cmd(f"git -C {path} pull 2>&1 | tail -3")
    return f"📦 {repo} ({path}) pull 结果：\n\n{result}"


def call_claude(prompt, system="你是一个翻译助手。"):
    """调用 Claude API，返回文本结果"""
    payload = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": 4096,
        "system": system,
        "messages": [{"role": "user", "content": prompt}]
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{CLAUDE_BASE_URL}/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01",
            "User-Agent": "curl/7.88.1",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            text_block = next((b for b in data["content"] if b.get("type") == "text"), None)
            return text_block["text"] if text_block else "❌ 无文本响应"
    except Exception as e:
        return f"❌ API 调用失败: {e}"


def handle_ts(arg):
    """翻译内容并存为 HTML 文件，返回链接"""
    if not arg:
        return "用法：/ts 要翻译的内容\n支持中→英、英→中自动判断"

    # 判断方向
    cn_chars = sum(1 for c in arg if '\u4e00' <= c <= '\u9fff')
    if cn_chars > len(arg) * 0.1:
        direction = "中文→英文"
        prompt = f"请将以下中文翻译成英文，保持原文格式和段落结构，只输出译文，不要解释：\n\n{arg}"
        title = "翻译结果（中→英）"
    else:
        direction = "英文→中文"
        prompt = f"请将以下英文翻译成中文，保持原文格式和段落结构，只输出译文，不要解释：\n\n{arg}"
        title = "翻译结果（英→中）"

    translated = call_claude(prompt)
    if translated.startswith("❌"):
        return translated

    content = f"## 原文\n\n{arg}\n\n---\n\n## 译文（{direction}）\n\n{translated}"
    return handle_save(f"{title}\n{content}")


def handle_save(arg):
    """保存内容为 HTML 网页，返回可访问链接"""
    import markdown as md_lib
    import re

    if not arg:
        return "用法：/save 标题\n内容（支持 Markdown 格式）"

    # 第一行是标题，其余是内容
    parts = arg.split('\n', 1)
    title = parts[0].strip() if parts else "未命名"
    content = parts[1].strip() if len(parts) > 1 else ""

    if not content:
        content = title
        title = "输出文件"

    # Markdown 转 HTML
    body_html = md_lib.markdown(content, extensions=['extra', 'nl2br'])

    timestamp = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M")
    safe_title = re.sub(r'[^\w\u4e00-\u9fff-]', '_', title)[:40]
    filename = datetime.now(CN_TZ).strftime("%Y%m%d-%H%M%S") + f"-{safe_title}.html"
    filepath = f"/var/www/outputs/{filename}"
    url = f"https://docs.tianlizeng.cloud/outputs/{filename}"

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           max-width: 820px; margin: 40px auto; padding: 0 24px; color: #333; line-height: 1.7; }}
    h1 {{ font-size: 1.6em; border-bottom: 2px solid #eee; padding-bottom: 12px; margin-bottom: 24px; }}
    h2, h3 {{ margin-top: 1.5em; }}
    code {{ background: #f5f5f5; padding: 2px 6px; border-radius: 3px; font-size: 0.9em; }}
    pre {{ background: #f5f5f5; padding: 16px; border-radius: 6px; overflow-x: auto; }}
    pre code {{ background: none; padding: 0; }}
    blockquote {{ border-left: 4px solid #ddd; margin: 0; padding-left: 16px; color: #666; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
    th {{ background: #f9f9f9; }}
    .meta {{ color: #999; font-size: 12px; margin-top: 40px; padding-top: 12px; border-top: 1px solid #eee; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  {body_html}
  <div class="meta">生成时间：{timestamp}｜由飞书 Bot 自动生成</div>
</body>
</html>"""

    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html)
        return f"✅ 文件已生成：\n{url}"
    except Exception as e:
        return f"❌ 写入失败：{e}"


def handle_help():
    return """📋 可用指令:
/rebuild         - 重建 docs 站点
/status          - VPS 状态概览
/review          - 生成今日学习回顾
/morning         - 手动触发早朝简报
/clear           - 清空太子对话历史（开启新对话）
/log [服务名]    - 查看服务日志（无参数列出可用服务）
/restart [服务名]- 重启服务（无参数列出可用服务）
/git [repo名]    - 拉取 repo 最新代码（无参数列出可用 repo）
/save 标题\\n内容 - 保存内容为网页，返回 HTTPS 链接
/ts 内容         - 翻译并存文件（中↔英自动判断），返回链接
/help            - 显示本帮助"""


COMMANDS = {
    "/rebuild": handle_rebuild,
    "/status": handle_status,
    "/review": handle_review,
    "/morning": handle_morning,
    "/clear": handle_clear,
    "/help": handle_help,
    "rebuild": handle_rebuild,
    "重建": handle_rebuild,
    "状态": handle_status,
    "回顾": handle_review,
    "早朝": handle_morning,
    "清空": handle_clear,
    "帮助": handle_help,
}

# 带参数的指令（第一个词是指令，剩余是参数）
PARAMETERIZED_COMMANDS = {
    "/log": handle_log,
    "/restart": handle_restart,
    "/git": handle_git,
    "/save": handle_save,
    "/ts": handle_ts,
    "log": handle_log,
    "restart": handle_restart,
    "git": handle_git,
    "save": handle_save,
    "ts": handle_ts,
    "日志": handle_log,
    "重启": handle_restart,
    "拉取": handle_git,
    "保存": handle_save,
}


class CommandHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 1024 * 1024:
            self.send_error(413)
            return

        body = self.rfile.read(length).decode("utf-8", errors="ignore")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {"command": body.strip()}

        command = data.get("command", "").strip()
        command_lower = command.lower()
        result = None

        # 解析指令和参数（带参数指令优先匹配）
        parts = command_lower.split(None, 1)
        cmd_key = parts[0] if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd_key in PARAMETERIZED_COMMANDS:
            result = PARAMETERIZED_COMMANDS[cmd_key](arg)
        else:
            for cmd, handler in COMMANDS.items():
                if command_lower == cmd or command_lower.startswith(cmd + " "):
                    result = handler()
                    break

        if result is None:
            result = f"❓ 未知指令: {command}\n\n" + handle_help()

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps({"result": result}, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        now = datetime.now(CN_TZ).strftime("%H:%M:%S")
        print(f"[{now}] {args[0]}")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    server = HTTPServer(("127.0.0.1", port), CommandHandler)
    print(f"飞书指令路由服务启动 → 127.0.0.1:{port}")
    server.serve_forever()
