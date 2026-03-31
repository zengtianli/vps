#!/usr/bin/env python3
"""GitHub Webhook Receiver - 接收 push 事件，触发对应 repo 的 git pull 和后续操作"""

import hashlib
import os
import hmac
import json
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
BUILD_SCRIPT = "/var/www/build_docs.sh"
LOG_FILE = "/var/log/webhook_build.log"

# repo name → 本地路径映射（所有 GitHub repos）
REPO_PATHS = {
    # 核心仓库
    "docs": "/var/www/docs",
    "scripts": "/var/www/scripts",
    "claude-config": "/var/www/claude-config",
    "zdwp": "/var/www/zdwp",
    "learn": "/var/www/learn",
    "vps": "/var/www",
    "edict": "/opt/edict",
    "essays": "/var/www/essays",
    "resume": "/var/www/resume",
    "reports": "/var/www/reports",
    "web": "/var/www/web",
    "dockit": "/opt/dockit",
    # 新增仓库
    "cclog": "/opt/cclog",
    "dockit-raycast": "/opt/dockit-raycast",
    "extensions": "/opt/extensions",
    "sync": "/opt/sync",
    "zengtianli": "/opt/zengtianli",
    # hydro 系列
    "hydro-toolkit": "/opt/hydro/hydro-toolkit",
    "hydro-annual": "/opt/hydro/hydro-annual",
    "hydro-capacity": "/opt/hydro/hydro-capacity",
    "hydro-district": "/opt/hydro/hydro-district",
    "hydro-efficiency": "/opt/hydro/hydro-efficiency",
    "hydro-geocode": "/opt/hydro/hydro-geocode",
    "hydro-irrigation": "/opt/hydro/hydro-irrigation",
    "hydro-qgis": "/opt/hydro/hydro-qgis",
    "hydro-rainfall": "/opt/hydro/hydro-rainfall",
    "hydro-reservoir": "/opt/hydro/hydro-reservoir",
    "hydro-risk": "/opt/hydro/hydro-risk",
    # OAuth proxy
    "oauth-proxy": "/var/www/oauth-proxy",
}

# pull 之后需要 restart 的服务
RESTART_SERVICES = {
    "oauth-proxy": "oauth-proxy",
    "dockit": "dockit",
}

# 需要 fetch+reset（而非 pull）的 repo
FORCE_RESET_REPOS = {"edict"}


def run_pull(repo_name, repo_path):
    """后台执行 git pull，不阻塞 webhook 响应"""
    try:
        # 如果本地目录不存在，先 clone
        import os
        if not os.path.isdir(os.path.join(repo_path, ".git")):
            parent = os.path.dirname(repo_path)
            os.makedirs(parent, exist_ok=True)
            result = subprocess.run(
                ["git", "clone", f"https://github.com/zengtianli/{repo_name}.git", repo_path],
                capture_output=True, text=True, timeout=300
            )
            with open(LOG_FILE, "a") as f:
                f.write(f"--- [{repo_name}] git clone (first time) ---\n")
                f.write(result.stdout)
                if result.stderr:
                    f.write(result.stderr)
                f.write(f"--- Exit code: {result.returncode} ---\n\n")
            return

        # edict 等用 fetch + reset --hard
        if repo_name in FORCE_RESET_REPOS:
            subprocess.run(["git", "-C", repo_path, "fetch", "origin"],
                           capture_output=True, timeout=60)
            result = subprocess.run(
                ["git", "-C", repo_path, "reset", "--hard", "origin/main"],
                capture_output=True, text=True, timeout=60
            )
        else:
            result = subprocess.run(
                ["git", "-C", repo_path, "pull"],
                capture_output=True, text=True, timeout=120
            )
        with open(LOG_FILE, "a") as f:
            f.write(f"--- [{repo_name}] git pull triggered by webhook ---\n")
            f.write(result.stdout)
            if result.stderr:
                f.write(result.stderr)
            f.write(f"--- Exit code: {result.returncode} ---\n\n")

        # edict 额外同步 SOUL.md 到 openclaw workspace
        if repo_name == "edict":
            import glob as _glob
            for soul in _glob.glob(f"{repo_path}/agents/*/SOUL.md"):
                agent = soul.split("/")[-2]
                dest = f"/root/.openclaw/workspace-{agent}/soul.md"
                import shutil as _shutil
                try:
                    _shutil.copy2(soul, dest)
                except FileNotFoundError:
                    pass
            with open(LOG_FILE, "a") as f:
                f.write(f"--- [edict] SOUL.md synced to openclaw workspaces ---\n\n")

        # docs 额外触发站点重建
        if repo_name == "docs":
            build = subprocess.run(
                ["bash", BUILD_SCRIPT],
                capture_output=True, text=True, timeout=300
            )
            with open(LOG_FILE, "a") as f:
                f.write(f"--- [docs] build triggered ---\n")
                f.write(build.stdout)
                if build.stderr:
                    f.write(build.stderr)
                f.write(f"--- Build exit code: {build.returncode} ---\n\n")

        # 通用 restart 逻辑
        service = RESTART_SERVICES.get(repo_name)
        if service:
            restart = subprocess.run(
                ["systemctl", "restart", service],
                capture_output=True, text=True, timeout=30
            )
            with open(LOG_FILE, "a") as f:
                f.write(f"--- [{repo_name}] systemctl restart {service} ---\n")
                if restart.stderr:
                    f.write(restart.stderr)
                f.write(f"--- Restart exit code: {restart.returncode} ---\n\n")

    except Exception as e:
        with open(LOG_FILE, "a") as f:
            f.write(f"[{repo_name}] Error: {e}\n\n")


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/webhook/github-build":
            self.send_response(404)
            self.end_headers()
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        # 验证 GitHub HMAC 签名
        sig_header = self.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(
            SECRET.encode(), body, hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(sig_header, expected):
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"Invalid signature")
            return

        # 只处理 push 事件
        event = self.headers.get("X-GitHub-Event", "")
        if event != "push":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Ignored event: " + event.encode())
            return

        # 从 payload 解析 repo 名称
        try:
            payload = json.loads(body)
            repo_name = payload.get("repository", {}).get("name", "")
        except Exception:
            repo_name = ""

        repo_path = REPO_PATHS.get(repo_name)
        if not repo_path:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(f"Ignored repo: {repo_name}".encode())
            return

        # 后台触发 git pull
        threading.Thread(target=run_pull, args=(repo_name, repo_path), daemon=True).start()

        self.send_response(200)
        self.end_headers()
        self.wfile.write(f"Pull triggered for {repo_name}".encode())

    def log_message(self, format, *args):
        """静默日志，避免刷屏"""
        pass


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 9000), WebhookHandler)
    print("Webhook receiver listening on :9000")
    server.serve_forever()
