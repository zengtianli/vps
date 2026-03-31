#!/usr/bin/env python3
"""
早朝简报 - 每日 06:00（北京时间）
CC 会话复盘 + 学习复盘 → HTML 邮件发送

数据源：
  - CC 会话日志：{CC_DIR}/*.jsonl（昨天的会话）
  - 学习笔记：{LEARN_DIR}/**/*.md（最近7天修改）
"""

import json
import os
import re
import smtplib
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ── 配置 ──
API_BASE = os.environ.get("MMKG_BASE_URL", "https://code.mmkg.cloud")
API_KEY = os.environ.get("MMKG_API_KEY", "")
MODEL = "claude-sonnet-4-5"
EMAIL_CONFIG = "/var/www/email_config.json"
CN_TZ = timezone(timedelta(hours=8))

# 路径（支持环境变量覆盖，本地测试用）
CC_DIR = os.environ.get(
    "CC_PROJECTS_DIR",
    "/var/www/claude-config/projects/-Users-tianli--claude",
)
LEARN_DIR = os.environ.get("LEARN_DIR", "/var/www/learn")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"


# ── API ──

def call_api(prompt, max_tokens=1024):
    payload = json.dumps({
        "model": MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    })
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(payload)
        tmp_path = f.name
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "90",
             "-X", "POST", f"{API_BASE}/v1/messages",
             "-H", "Content-Type: application/json",
             "-H", f"x-api-key: {API_KEY}",
             "-H", "anthropic-version: 2023-06-01",
             "-d", f"@{tmp_path}"],
            capture_output=True, text=True, timeout=100,
        )
        if not result.stdout:
            print(f"  API 返回空。stderr: {result.stderr[:200]}")
            return ""
        data = json.loads(result.stdout)
        if "error" in data:
            print(f"  API 错误: {data['error']}")
            return ""
        for block in data.get("content", []):
            if block["type"] == "text":
                return block["text"]
        return ""
    except Exception as e:
        print(f"  API 异常: {e}")
        return ""
    finally:
        os.unlink(tmp_path)


# ── 数据读取 ──

def get_user_text(content) -> str:
    """从 message.content 提取纯文本（兼容字符串和列表两种格式）"""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return " ".join(parts).strip()
    return ""


def get_yesterday_sessions() -> list[dict]:
    """
    读昨天的 CC 会话日志，返回 [{title, turns, preview}]
    每个 .jsonl 文件是一个会话，按 mtime 判断是否属于昨天。
    只读前 3 条 user 消息提取主题，不全量读。
    """
    now = datetime.now(CN_TZ)
    yesterday = now - timedelta(days=1)
    day_start = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = yesterday.replace(hour=23, minute=59, second=59, microsecond=999999)

    cc_path = Path(CC_DIR)
    if not cc_path.exists():
        print(f"  CC_DIR 不存在: {CC_DIR}")
        return []

    sessions = []
    for jsonl_file in cc_path.glob("*.jsonl"):
        mtime = datetime.fromtimestamp(jsonl_file.stat().st_mtime, tz=CN_TZ)
        if not (day_start <= mtime <= day_end):
            continue

        user_messages = []
        turns = 0
        try:
            with open(jsonl_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("type") == "user":
                        msg = obj.get("message", {})
                        if msg.get("role") == "user":
                            turns += 1
                            if len(user_messages) < 3:
                                text = get_user_text(msg.get("content", ""))
                                if text:
                                    user_messages.append(text)
        except Exception as e:
            print(f"  读取 {jsonl_file.name} 失败: {e}")
            continue

        if not user_messages:
            continue

        title = user_messages[0][:80]
        preview = " | ".join(m[:50] for m in user_messages[:3])
        sessions.append({
            "title": title,
            "turns": turns,
            "preview": preview,
            "file": jsonl_file.name,
        })

    sessions.sort(key=lambda s: s["turns"], reverse=True)
    return sessions


def get_recent_learn_notes() -> list[dict]:
    """
    读 LEARN_DIR 最近7天修改的 .md（最多两层深度），
    返回 [{filename, excerpt}]
    """
    learn_path = Path(LEARN_DIR)
    if not learn_path.exists():
        print(f"  LEARN_DIR 不存在: {LEARN_DIR}")
        return []

    cutoff = datetime.now(CN_TZ) - timedelta(days=7)
    notes = []

    # 根目录 + 一层子目录（不递归更深）
    patterns = ["*.md", "*/*.md"]
    seen = set()
    for pattern in patterns:
        for md_file in learn_path.glob(pattern):
            if md_file in seen:
                continue
            seen.add(md_file)
            mtime = datetime.fromtimestamp(md_file.stat().st_mtime, tz=CN_TZ)
            if mtime < cutoff:
                continue
            try:
                text = md_file.read_text(encoding="utf-8", errors="ignore")
                # 去掉 frontmatter 和 markdown 标记，取前200字
                text = re.sub(r"^---[\s\S]*?---\n", "", text)
                text = re.sub(r"#+ ", "", text)
                text = re.sub(r"\*\*|__|\*|_|`", "", text)
                excerpt = " ".join(text.split())[:200]
                # 显示相对路径（如 claude-code/Layer4-Hooks.md）
                try:
                    rel = md_file.relative_to(learn_path)
                except ValueError:
                    rel = md_file.name
                notes.append({
                    "filename": str(rel),
                    "excerpt": excerpt,
                    "mtime": mtime,
                })
            except Exception:
                continue

    notes.sort(key=lambda n: n["mtime"], reverse=True)
    return notes[:10]  # 最多10篇


# ── AI 汇总 ──

def generate_cc_recap(sessions: list[dict]) -> str:
    if not sessions:
        return "昨日无 CC 会话记录。"

    sessions_text = ""
    for i, s in enumerate(sessions, 1):
        sessions_text += f"{i}. [{s['turns']}轮] {s['preview']}\n"
    sessions_text = sessions_text[:3000]

    prompt = f"""以下是昨天的 Claude Code 会话记录（每条格式：序号. [对话轮数] 前几条用户消息预览）：

{sessions_text}

请用中文写一段简洁的昨日工作复盘（150字以内），包含：
1. 主要做了哪些任务（归类概括）
2. 有哪些关键成果或决策
语气直接，不要废话，不要列编号，写成连贯段落。"""

    result = call_api(prompt, max_tokens=400)
    return result or "（AI 汇总失败，请查看原始日志）"


def generate_learn_recap(notes: list[dict]) -> str:
    if not notes:
        return "最近7天无学习笔记更新。"

    notes_text = ""
    for n in notes:
        notes_text += f"【{n['filename']}】{n['excerpt'][:150]}\n\n"
    notes_text = notes_text[:3000]

    prompt = f"""以下是最近7天修改的学习笔记（文件名 + 内容摘要）：

{notes_text}

请用中文写一段简洁的学习进展总结（150字以内），包含：
1. 在学什么方向
2. 最值得注意的1-2个知识点或实战进展
语气直接，不要废话，不要列编号，写成连贯段落。"""

    result = call_api(prompt, max_tokens=400)
    return result or "（AI 汇总失败，请查看原始笔记）"


# ── HTML 生成 ──

def build_html(cc_recap: str, learn_recap: str, date_str: str) -> str:
    yesterday = (datetime.now(CN_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
    generated_at = datetime.now(CN_TZ).strftime("%H:%M")

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 640px; margin: 0 auto; padding: 16px; color: #333; }}
h1 {{ font-size: 20px; border-bottom: 2px solid #2563eb; padding-bottom: 8px; }}
h2 {{ font-size: 16px; margin-top: 24px; color: #1e40af; }}
.section {{ margin: 12px 0; padding: 14px; background: #f8fafc; border-radius: 6px; border-left: 3px solid #94a3b8; font-size: 14px; line-height: 1.7; }}
.footer {{ margin-top: 24px; font-size: 11px; color: #94a3b8; text-align: center; }}
</style></head><body>
<h1>🌅 早朝简报 · {date_str}</h1>

<h2>🧠 昨日复盘（{yesterday}）</h2>
<div class="section">{cc_recap}</div>

<h2>📚 学习进展（近7天）</h2>
<div class="section">{learn_recap}</div>

<div class="footer">
Generated at {generated_at} CST · VPS 自动化
</div></body></html>"""


# ── 邮件发送 ──

def send_email(subject, html_body):
    with open(EMAIL_CONFIG) as f:
        cfg = json.load(f)

    msg = MIMEMultipart("alternative")
    msg["From"] = cfg["sender"]
    msg["To"] = cfg["recipient"]
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as s:
        s.starttls()
        s.login(cfg["sender"], cfg["password"])
        s.send_message(msg)


# ── 主流程 ──

def main():
    now = datetime.now(CN_TZ)
    date_str = now.strftime("%Y-%m-%d")
    print(f"[{date_str}] 早朝简报开始生成...")

    # 1. 读 CC 会话
    print("  读取昨日 CC 会话...")
    sessions = get_yesterday_sessions()
    print(f"  找到 {len(sessions)} 个会话")

    # 2. 读学习笔记
    print("  读取最近学习笔记...")
    notes = get_recent_learn_notes()
    print(f"  找到 {len(notes)} 篇笔记")

    # 3. AI 汇总
    print("  生成 CC 复盘...")
    cc_recap = generate_cc_recap(sessions)

    print("  生成学习复盘...")
    learn_recap = generate_learn_recap(notes)

    # 4. 生成 HTML
    html = build_html(cc_recap, learn_recap, date_str)

    # 5. 发送 or 打印
    subject = f"🌅 早朝简报 · {date_str}"
    if DRY_RUN:
        print("\n" + "=" * 60)
        print(html)
        print("=" * 60)
        print("\n[DRY_RUN] 未发送邮件")
    else:
        send_email(subject, html)
        print(f"[{date_str}] 早朝简报已发送 ✅")


if __name__ == "__main__":
    main()
