#!/usr/bin/env python3
"""
workspace_watcher.py - 监控太子 workspace，新文件自动生成 HTML 并发飞书消息
"""
import json
import os
import re
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta, timezone

WORKSPACE = "/root/.openclaw/workspace-taizi"
OUTPUTS_DIR = "/var/www/outputs"
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
OWNER_OPEN_ID = os.environ.get("FEISHU_OWNER_OPEN_ID", "")
BASE_URL = "https://docs.tianlizeng.cloud/outputs"
CN_TZ = timezone(timedelta(hours=8))

SKIP_FILES = {
    "AGENTS.md", "BOOTSTRAP.md", "HEARTBEAT.md", "IDENTITY.md",
    "SOUL.md", "TOOLS.md", "USER.md", "soul.md", "workspace-state.json"
}


def get_feishu_token():
    payload = json.dumps({
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET
    }).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "curl/7.88.1"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())["tenant_access_token"]


def send_feishu_message(token, text):
    payload = json.dumps({
        "receive_id": OWNER_OPEN_ID,
        "msg_type": "text",
        "content": json.dumps({"text": text})
    }).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
            "User-Agent": "curl/7.88.1"
        }
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def upload_and_send_file(token, filepath, filename):
    """上传文件到飞书，然后发送文件消息"""
    import mimetypes

    # 构造 multipart/form-data
    boundary = "----FeishuBoundary7788"
    with open(filepath, "rb") as f:
        file_data = f.read()

    body = (
        ("--" + boundary + "\r\n"
         "Content-Disposition: form-data; name=\"file_type\"\r\n\r\n"
         "stream\r\n").encode() +
        ("--" + boundary + "\r\n"
         "Content-Disposition: form-data; name=\"file_name\"\r\n\r\n" +
         filename + "\r\n").encode() +
        ("--" + boundary + "\r\n"
         "Content-Disposition: form-data; name=\"file\"; filename=\"" + filename + "\"\r\n"
         "Content-Type: application/octet-stream\r\n\r\n").encode() +
        file_data +
        ("\r\n--" + boundary + "--\r\n").encode()
    )

    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/files",
        data=body,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "multipart/form-data; boundary=" + boundary,
            "User-Agent": "curl/7.88.1"
        }
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())

    if result.get("code") != 0:
        raise Exception("upload failed: " + str(result))

    file_key = result["data"]["file_key"]

    # 发送文件消息
    payload = json.dumps({
        "receive_id": OWNER_OPEN_ID,
        "msg_type": "file",
        "content": json.dumps({"file_key": file_key})
    }).encode()
    req2 = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
            "User-Agent": "curl/7.88.1"
        }
    )
    with urllib.request.urlopen(req2, timeout=10) as resp:
        return json.loads(resp.read())


def save_as_html(title, content, filename_hint="file"):
    try:
        import markdown as md_lib
        body_html = md_lib.markdown(content, extensions=["extra", "nl2br"])
    except Exception:
        body_html = "<pre>" + content + "</pre>"

    timestamp = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M")
    safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", filename_hint)[:30]
    fname = datetime.now(CN_TZ).strftime("%Y%m%d-%H%M%S") + "-" + safe + ".html"
    fpath = os.path.join(OUTPUTS_DIR, fname)

    html = """<!DOCTYPE html>
<html lang="zh"><head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title}</title>
  <style>
    body{{font-family:-apple-system,sans-serif;max-width:820px;margin:40px auto;padding:0 24px;color:#333;line-height:1.7}}
    h1{{font-size:1.6em;border-bottom:2px solid #eee;padding-bottom:12px;margin-bottom:24px}}
    h2,h3{{margin-top:1.5em}}
    code{{background:#f5f5f5;padding:2px 6px;border-radius:3px;font-size:.9em}}
    pre{{background:#f5f5f5;padding:16px;border-radius:6px;overflow-x:auto;white-space:pre-wrap}}
    pre code{{background:none;padding:0}}
    blockquote{{border-left:4px solid #ddd;margin:0;padding-left:16px;color:#666}}
    table{{border-collapse:collapse;width:100%}}
    th,td{{border:1px solid #ddd;padding:8px 12px}}
    th{{background:#f9f9f9}}
    .meta{{color:#999;font-size:12px;margin-top:40px;padding-top:12px;border-top:1px solid #eee}}
  </style>
</head><body>
  <h1>{title}</h1>
  {body}
  <div class="meta">生成时间：{ts}｜workspace 自动转存</div>
</body></html>""".format(title=title, body=body_html, ts=timestamp)

    with open(fpath, "w", encoding="utf-8") as f:
        f.write(html)
    return BASE_URL + "/" + fname


def process_new_file(filepath):
    filename = os.path.basename(filepath)
    if filename in SKIP_FILES:
        return
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".txt", ".md"):
        return

    try:
        with open(filepath, encoding="utf-8") as f:
            content = f.read().strip()
    except Exception:
        return

    if not content or len(content) < 10:
        return

    token = get_feishu_token()

    # 直接发文件附件
    try:
        upload_and_send_file(token, filepath, filename)
        ts = datetime.now(CN_TZ).strftime("%H:%M:%S")
        print("[" + ts + "] 文件已发送: " + filename)
    except Exception as e:
        # 上传失败则降级发 HTML 链接
        print("文件上传失败，降级发链接: " + str(e))
        try:
            title = os.path.splitext(filename)[0].replace("_", " ")
            url = save_as_html(title, content, os.path.splitext(filename)[0])
            send_feishu_message(token, "文件链接：\n" + url)
        except Exception as e2:
            print("发送失败: " + str(e2))


def main():
    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    print("开始监控 " + WORKSPACE)

    proc = subprocess.Popen(
        ["inotifywait", "-m", "-e", "close_write,moved_to", "--format", "%f", WORKSPACE],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
    )

    for line in proc.stdout:
        filename = line.strip()
        if not filename:
            continue
        filepath = os.path.join(WORKSPACE, filename)
        if os.path.isfile(filepath):
            time.sleep(0.5)
            process_new_file(filepath)


if __name__ == "__main__":
    main()
