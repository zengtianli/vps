#!/usr/bin/env python3
"""
学习回顾自动生成器
读取 CC 会话日志 + Learn 笔记 → 调 Claude API 生成总结 → 发邮件

用法:
  python3 learning_digest.py daily          # 生成当日回顾
  python3 learning_digest.py weekly         # 生成本周汇总
  python3 learning_digest.py daily 2026-03-21  # 指定日期
"""

import json
import sys
import os
import subprocess
import glob
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

# ── 配置 ──
_cfg_path = Path("/var/www/email_config.json")
_cfg = __import__("json").loads(_cfg_path.read_text()) if _cfg_path.exists() else {}
API_BASE = os.environ.get("MMKG_BASE_URL") or _cfg.get("mmkg_base_url", "https://code.mmkg.cloud")
API_KEY = os.environ.get("MMKG_API_KEY") or _cfg.get("mmkg_api_key", "")
MODEL = "claude-sonnet-4-5"  # Haiku 够用且便宜

CC_CONFIG = Path("/var/www/claude-config")
CC_SESSIONS = CC_CONFIG / "projects"
LEARN_DIR = Path("/var/www/learn")
OUTPUT_DIR = Path("/var/www/learning_reviews")
CN_TZ = timezone(timedelta(hours=8))


def get_today():
    return datetime.now(CN_TZ).strftime("%Y-%m-%d")


def get_week_range():
    """返回本周一到今天"""
    today = datetime.now(CN_TZ)
    monday = today - timedelta(days=today.weekday())
    return monday.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def load_session_index():
    idx_path = CC_CONFIG / "session_index.json"
    if idx_path.exists():
        return json.loads(idx_path.read_text())
    return []


def extract_user_messages(jsonl_path, max_chars=8000):
    """从 JSONL 提取用户消息（精简版，控制 token）"""
    messages = []
    try:
        with open(jsonl_path) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    msg = entry.get("message", {})
                    if msg.get("role") == "user":
                        content = msg.get("content", "")
                        if isinstance(content, str) and len(content) > 5:
                            messages.append(content[:200])  # 截断长消息
                except (json.JSONDecodeError, AttributeError):
                    continue
    except Exception:
        pass

    combined = "\n---\n".join(messages)
    return combined[:max_chars]


def get_sessions_for_date(date_str):
    """获取指定日期的所有会话"""
    index = load_session_index()
    sessions = []
    for s in index:
        st = s.get("start_time", "")
        if st.startswith(date_str) or st[:10] == date_str:
            sessions.append(s)

    # 也检查文件修改时间（index 可能不全）
    for proj_dir in CC_SESSIONS.iterdir():
        if not proj_dir.is_dir():
            continue
        for jsonl in proj_dir.glob("*.jsonl"):
            mtime = datetime.fromtimestamp(jsonl.stat().st_mtime, CN_TZ)
            if mtime.strftime("%Y-%m-%d") == date_str:
                sid = jsonl.stem
                if not any(s.get("session_id") == sid for s in sessions):
                    sessions.append({
                        "session_id": sid,
                        "project": proj_dir.name,
                        "start_time": mtime.isoformat(),
                        "file_path": str(jsonl),
                    })
    return sessions


def get_sessions_for_week(start_date, end_date):
    """获取一周的所有会话"""
    all_sessions = []
    d = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    while d <= end:
        all_sessions.extend(get_sessions_for_date(d.strftime("%Y-%m-%d")))
        d += timedelta(days=1)
    return all_sessions


def get_learn_changes(since_date):
    """获取 Learn 目录中指定日期后修改的文件"""
    changes = []
    for md in LEARN_DIR.rglob("*.md"):
        mtime = datetime.fromtimestamp(md.stat().st_mtime, CN_TZ)
        if mtime.strftime("%Y-%m-%d") >= since_date:
            try:
                content = md.read_text()[:2000]
                changes.append({
                    "file": str(md.relative_to(LEARN_DIR)),
                    "modified": mtime.strftime("%Y-%m-%d"),
                    "preview": content,
                })
            except Exception:
                pass
    return changes


def call_api(prompt, max_tokens=4096):
    """调用 Claude API（通过 curl，更可靠）"""
    import tempfile

    payload = json.dumps({
        "model": MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    })

    # 写入临时文件避免 shell 转义问题
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(payload)
        tmp_path = f.name

    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "120",
             "-X", "POST", f"{API_BASE}/v1/messages",
             "-H", "Content-Type: application/json",
             "-H", f"x-api-key: {API_KEY}",
             "-H", "anthropic-version: 2023-06-01",
             "-d", f"@{tmp_path}"],
            capture_output=True, text=True, timeout=130,
        )
        data = json.loads(result.stdout)
        for block in data["content"]:
            if block["type"] == "text":
                return block["text"]
        return data["content"][0].get("text", "")
    finally:
        os.unlink(tmp_path)


def build_daily_prompt(date_str, sessions, learn_changes):
    """构建每日回顾的 API prompt（总长 < 6000 chars）"""
    # 过滤：只保留 >2min 的会话
    meaningful = [s for s in sessions if s.get("duration_minutes", 0) > 2]
    # 按时长排序，取前 8 个
    meaningful.sort(key=lambda x: x.get("duration_minutes", 0), reverse=True)
    meaningful = meaningful[:8]

    session_summaries = []
    for s in meaningful:
        sid = s.get("session_id", "")
        jsonl_path = None
        for proj_dir in CC_SESSIONS.iterdir():
            if not proj_dir.is_dir():
                continue
            candidate = proj_dir / f"{sid}.jsonl"
            if candidate.exists():
                jsonl_path = candidate
                break
        if s.get("file_path"):
            jsonl_path = Path(s["file_path"])

        user_msgs = extract_user_messages(jsonl_path, max_chars=200) if jsonl_path else ""
        session_summaries.append(
            f"- [{s.get('duration_minutes',0)}min] "
            f"{s.get('title','')[:40]}: {user_msgs[:150]}"
        )

    sessions_text = "\n".join(session_summaries)[:3000]

    learn_text = ""
    if learn_changes:
        learn_text = "\nLearn 变更:\n"
        for c in learn_changes[:2]:
            learn_text += f"- {c['file']}: {c['preview'][:150]}\n"
        learn_text = learn_text[:500]

    return f"""根据以下 CC 会话记录，生成中文每日学习回顾 Markdown。

{date_str}，{len(sessions)} 个会话（展示前 {len(meaningful)} 个）

{sessions_text}

{learn_text}

请按以下模板生成 Markdown（不要加 ```markdown 代码块包裹）：

# 学习回顾 - {date_str}

## 今日学到的

### 1. [主题名] — 简短标题
- **是什么**：一句话解释
- **关键概念**：列出核心概念
- **怎么做的**：简述过程
- **踩过的坑**：如果有的话

（每个有实质学习内容的会话都列一个）

## 复习区（之前学过的，今天又用到的）
- 列出复习到的旧知识

## 一句话总结
> 用一句话总结今天的学习

注意：
- 跳过无实质内容的会话（如 hi、测试、中断的会话）
- 关注学到了什么新东西，而不是做了什么操作
- 技术术语保留英文原文
"""


def build_weekly_prompt(start_date, end_date, sessions, learn_changes):
    """构建周汇总的 API prompt"""
    # 按天分组
    daily_groups = {}
    for s in sessions:
        day = s.get("start_time", "")[:10]
        if day not in daily_groups:
            daily_groups[day] = []
        daily_groups[day].append(s.get("title", "")[:80])

    daily_summary = ""
    for day in sorted(daily_groups.keys()):
        titles = daily_groups[day]
        daily_summary += f"\n### {day}（{len(titles)} 个会话）\n"
        for t in titles:
            daily_summary += f"- {t}\n"

    learn_text = ""
    if learn_changes:
        learn_text = "\nLearn 变更:\n"
        for c in learn_changes[:5]:
            learn_text += f"- {c['file']}: {c['preview'][:150]}\n"
        learn_text = learn_text[:800]

    return f"""根据以下一周 CC 会话记录，生成中文周学习汇总。

{start_date} ~ {end_date}，共 {len(sessions)} 个会话

{daily_summary[:3500]}

{learn_text}

生成 Markdown，包含：本周概览、按主题分类（表格：概念/掌握程度⭐/来源）、每日记录、踩坑记录、核心认知升级、下周方向。

（列出所有有实质学习的主题）

## 本周每日记录
### 周一 M/DD — 一句话主题
- 要点 1
- 要点 2

## 本周踩坑记录
| 坑 | 原因 | 教训 |
|----|------|------|

## 核心认知升级
1. xxx

## 下周可以深入的方向
- xxx

注意：
- 跳过无实质内容的会话
- 关注学到了什么，而不是做了什么
- 技术术语保留英文原文
- 掌握程度标准：⭐ 刚接触 / ⭐⭐ 理解原理 / ⭐⭐⭐ 能独立操作
"""


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    date_arg = sys.argv[2] if len(sys.argv) > 2 else None

    OUTPUT_DIR.mkdir(exist_ok=True)

    if mode == "daily":
        date_str = date_arg or get_today()
        print(f"Generating daily review for {date_str}...")

        sessions = get_sessions_for_date(date_str)
        learn_changes = get_learn_changes(date_str)

        if not sessions and not learn_changes:
            print(f"No sessions or changes found for {date_str}")
            sys.exit(0)

        prompt = build_daily_prompt(date_str, sessions, learn_changes)
        md_content = call_api(prompt)

        filename = f"daily-{date_str}.md"
        out_path = OUTPUT_DIR / filename
        out_path.write_text(md_content, encoding="utf-8")
        print(f"Written to {out_path}")

        # 发邮件
        subprocess.run(
            ["python3", "/var/www/learning_email.py", str(out_path)],
            check=True,
        )

    elif mode == "weekly":
        if date_arg:
            # 解析为那周的周一到周日
            d = datetime.strptime(date_arg, "%Y-%m-%d")
            monday = d - timedelta(days=d.weekday())
            sunday = monday + timedelta(days=6)
            start_date = monday.strftime("%Y-%m-%d")
            end_date = sunday.strftime("%Y-%m-%d")
        else:
            start_date, end_date = get_week_range()

        print(f"Generating weekly review for {start_date} ~ {end_date}...")

        sessions = get_sessions_for_week(start_date, end_date)
        learn_changes = get_learn_changes(start_date)

        if not sessions and not learn_changes:
            print(f"No data found for {start_date} ~ {end_date}")
            sys.exit(0)

        prompt = build_weekly_prompt(start_date, end_date, sessions, learn_changes)
        md_content = call_api(prompt, max_tokens=8192)

        week_num = datetime.strptime(start_date, "%Y-%m-%d").isocalendar()[1]
        filename = f"weekly-{start_date[:4]}-W{week_num:02d}.md"
        out_path = OUTPUT_DIR / filename
        out_path.write_text(md_content, encoding="utf-8")
        print(f"Written to {out_path}")

        subprocess.run(
            ["python3", "/var/www/learning_email.py", str(out_path)],
            check=True,
        )

    else:
        print(f"Unknown mode: {mode}. Use 'daily' or 'weekly'")
        sys.exit(1)


if __name__ == "__main__":
    main()
