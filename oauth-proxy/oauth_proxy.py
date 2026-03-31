#!/usr/bin/env python3
"""CC API Management Platform — OAuth proxy + admin dashboard.

启动: python3 oauth_proxy.py
配置: 同目录 config.json（由 export_tokens.py 生成）
认证: Cloudflare Access（边缘拦截，不在应用层实现）
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import sqlite3
import time
from pathlib import Path

import aiohttp
from aiohttp import web

# ---------------------------------------------------------------------------
# Paths & Logging
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
DB_PATH = BASE_DIR / "proxy.db"
STATIC_DIR = BASE_DIR / "frontend" / "dist"
log = logging.getLogger("proxy")


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            key TEXT UNIQUE NOT NULL,
            group_name TEXT DEFAULT '',
            enabled INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS usage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key_id INTEGER,
            timestamp TEXT DEFAULT (datetime('now')),
            model TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_creation_tokens INTEGER DEFAULT 0,
            account_name TEXT,
            status_code INTEGER,
            latency_ms INTEGER DEFAULT 0,
            FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
        );
        CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_logs(timestamp);
        CREATE INDEX IF NOT EXISTS idx_usage_key ON usage_logs(api_key_id);
    """)
    conn.close()
    log.info("数据库初始化完成: %s", DB_PATH)


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def migrate_legacy_key(config: dict):
    """将旧的 proxy_api_key 迁移为第一个 API key。"""
    legacy = config.get("proxy_api_key", "")
    if not legacy:
        return
    conn = get_db()
    existing = conn.execute("SELECT id FROM api_keys WHERE key = ?", (legacy,)).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO api_keys (name, key, group_name) VALUES (?, ?, ?)",
            ("默认密钥", legacy, "default"),
        )
        conn.commit()
        log.info("已迁移旧 proxy_api_key 为默认 API 密钥")
    conn.close()


# ---------------------------------------------------------------------------
# Account Manager
# ---------------------------------------------------------------------------
class AccountManager:
    def __init__(self, config: dict):
        self.config = config
        self.accounts: list[dict] = []
        for acc in config["accounts"]:
            self.accounts.append({
                **acc,
                "status": "skip" if acc.get("skip") else "healthy",
                "skip": acc.get("skip", False),
                "cooldown_until": 0,
                "refresh_failures": 0,
                "last_used": 0,
            })
        self._next_idx = 0
        self._lock = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def refresh_token(self, acc: dict) -> bool:
        if not acc["refresh_token"]:
            return False
        session = await self.get_session()
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": acc["refresh_token"],
            "client_id": self.config["oauth_client_id"],
        }
        try:
            async with session.post(
                self.config["oauth_token_url"],
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.error("[%s] token 刷新失败: %s %s", acc["name"], resp.status, body[:200])
                    acc["refresh_failures"] += 1
                    if acc["refresh_failures"] >= self.config["max_refresh_failures"]:
                        acc["status"] = "disabled"
                    else:
                        acc["status"] = "error"
                    return False
                data = await resp.json()
                acc["access_token"] = data["access_token"]
                acc["refresh_token"] = data.get("refresh_token", acc["refresh_token"])
                acc["expires_at"] = int(time.time() * 1000) + data["expires_in"] * 1000
                acc["status"] = "healthy"
                acc["refresh_failures"] = 0
                log.info("[%s] token 刷新成功，有效期 %d 分钟", acc["name"], data["expires_in"] // 60)
                return True
        except Exception as e:
            log.error("[%s] token 刷新异常: %s", acc["name"], e)
            acc["refresh_failures"] += 1
            acc["status"] = "error"
            return False

    async def refresh_all(self):
        tasks = []
        for acc in self.accounts:
            if acc.get("skip"):
                log.info("[%s] skip=true，跳过刷新", acc["name"])
                continue
            tasks.append(self.refresh_token(acc))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        ok = sum(1 for r in results if r is True)
        skip_count = sum(1 for a in self.accounts if a.get("skip"))
        log.info("启动刷新完成: %d/%d 成功 (%d 个跳过)", ok, len(self.accounts) - skip_count, skip_count)

    async def save_config(self):
        self.config["accounts"] = [
            {
                "name": acc["name"],
                "email": acc.get("email", ""),
                "org_uuid": acc.get("org_uuid", ""),
                "refresh_token": acc["refresh_token"],
                "access_token": acc["access_token"],
                "expires_at": acc["expires_at"],
                "skip": acc.get("skip", False),
            }
            for acc in self.accounts
        ]
        with open(CONFIG_PATH, "w") as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)

    async def background_refresh_loop(self):
        margin_ms = self.config["token_refresh_margin_seconds"] * 1000
        while True:
            await asyncio.sleep(60)
            now = int(time.time() * 1000)
            refreshed = False
            for acc in self.accounts:
                if acc["status"] == "disabled" or acc.get("skip"):
                    continue
                if acc["expires_at"] - now < margin_ms:
                    log.info("[%s] token 即将过期，主动刷新...", acc["name"])
                    if await self.refresh_token(acc):
                        refreshed = True
            if refreshed:
                await self.save_config()

    def _is_available(self, acc: dict) -> bool:
        if acc.get("skip") or acc["status"] in ("disabled", "error", "skip"):
            return False
        if acc["status"] == "rate_limited":
            now = int(time.time() * 1000)
            if now < acc["cooldown_until"]:
                return False
            acc["status"] = "healthy"
        return True

    async def toggle_account(self, name: str) -> dict | None:
        for acc in self.accounts:
            if acc["name"] == name:
                acc["skip"] = not acc.get("skip", False)
                acc["status"] = "skip" if acc["skip"] else "healthy"
                await self.save_config()
                return acc
        return None

    async def pick_account(self, session_id: str | None = None) -> dict | None:
        async with self._lock:
            available = [a for a in self.accounts if self._is_available(a)]
            if not available:
                return None
            if session_id:
                idx = hash(session_id) % len(available)
                acc = available[idx]
            else:
                n = len(self.accounts)
                acc = None
                for _ in range(n):
                    candidate = self.accounts[self._next_idx % n]
                    self._next_idx = (self._next_idx + 1) % n
                    if self._is_available(candidate):
                        acc = candidate
                        break
                if acc is None:
                    return None
            now = int(time.time() * 1000)
            margin = self.config["token_refresh_margin_seconds"] * 1000
            if acc["expires_at"] - now < margin:
                if not await self.refresh_token(acc):
                    return None
                await self.save_config()
            acc["last_used"] = now
            return acc

    def mark_rate_limited(self, acc: dict, retry_after: int = 0):
        cooldown = retry_after or self.config["rate_limit_cooldown_seconds"]
        acc["status"] = "rate_limited"
        acc["cooldown_until"] = int(time.time() * 1000) + cooldown * 1000
        log.warning("[%s] 被限流，冷却 %ds", acc["name"], cooldown)

    def mark_error(self, acc: dict, reason: str):
        acc["status"] = "error"
        log.error("[%s] 标记异常: %s", acc["name"], reason)

    def get_status(self) -> list[dict]:
        now = int(time.time() * 1000)
        return [
            {
                "name": acc["name"],
                "email": acc.get("email", ""),
                "status": acc["status"],
                "skip": acc.get("skip", False),
                "token_expires_in_min": max(0, (acc["expires_at"] - now)) // 60000,
                "refresh_failures": acc["refresh_failures"],
            }
            for acc in self.accounts
        ]


# ---------------------------------------------------------------------------
# Proxy Handler
# ---------------------------------------------------------------------------
MAX_RETRY = 3


def validate_api_key(key: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT id, name, key, enabled FROM api_keys WHERE key = ?", (key,)
    ).fetchone()
    conn.close()
    if row and row["enabled"]:
        return dict(row)
    return None


def log_usage(api_key_id: int, model: str, input_t: int, output_t: int,
              cache_read: int, cache_create: int, account_name: str,
              status_code: int, latency_ms: int):
    conn = get_db()
    conn.execute(
        """INSERT INTO usage_logs
           (api_key_id, model, input_tokens, output_tokens,
            cache_read_tokens, cache_creation_tokens, account_name,
            status_code, latency_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (api_key_id, model, input_t, output_t, cache_read, cache_create,
         account_name, status_code, latency_ms),
    )
    conn.commit()
    conn.close()


async def handle_proxy(request: web.Request) -> web.StreamResponse:
    mgr: AccountManager = request.app["manager"]
    config = mgr.config
    t0 = time.time()

    api_key_str = request.headers.get("x-api-key", "")
    key_row = validate_api_key(api_key_str)
    if key_row is None:
        return web.json_response(
            {"error": {"type": "authentication_error", "message": "Invalid API key"}},
            status=401,
        )

    body = await request.read()
    model = ""
    try:
        payload = json.loads(body)
        is_stream = payload.get("stream", False)
        model = payload.get("model", "")

        cc_system = {
            "type": "text",
            "text": "You are Claude Code, Anthropic's official CLI for Claude.",
            "cache_control": {"type": "ephemeral"},
        }
        if "system" in payload:
            if isinstance(payload["system"], list):
                if not any("Claude Code" in (s.get("text", "") if isinstance(s, dict) else str(s))
                           for s in payload["system"]):
                    payload["system"].insert(0, cc_system)
            elif isinstance(payload["system"], str):
                payload["system"] = [cc_system, {"type": "text", "text": payload["system"]}]
        else:
            payload["system"] = [cc_system]

        body = json.dumps(payload).encode()
    except (json.JSONDecodeError, UnicodeDecodeError):
        is_stream = False

    path = request.path
    upstream_url = f"{config['upstream_base'].rstrip('/')}{path}?beta=true"
    session_id = request.headers.get("x-session-id")

    for attempt in range(MAX_RETRY):
        acc = await mgr.pick_account(session_id=session_id)
        if acc is None:
            return web.json_response(
                {"error": {"type": "overloaded_error", "message": "All accounts unavailable"}},
                status=503,
            )

        beta_parts = [
            "claude-code-20250219",
            "oauth-2025-04-20",
            "interleaved-thinking-2025-05-14",
            "fine-grained-tool-streaming-2025-05-14",
        ]
        client_beta = request.headers.get("anthropic-beta", "")
        if client_beta:
            for part in client_beta.split(","):
                part = part.strip()
                if part and part not in beta_parts:
                    beta_parts.append(part)

        headers = {
            "Authorization": f"Bearer {acc['access_token']}",
            "Content-Type": request.headers.get("Content-Type", "application/json"),
            "anthropic-version": request.headers.get("anthropic-version", "2023-06-01"),
            "anthropic-beta": ",".join(beta_parts),
        }

        session = await mgr.get_session()
        try:
            async with session.post(
                upstream_url, headers=headers, data=body,
                timeout=aiohttp.ClientTimeout(total=600),
            ) as upstream_resp:
                status = upstream_resp.status

                if status == 429:
                    retry_after = int(upstream_resp.headers.get("retry-after", 60))
                    mgr.mark_rate_limited(acc, retry_after)
                    continue

                if status == 401 and attempt == 0:
                    if await mgr.refresh_token(acc):
                        await mgr.save_config()
                        continue
                    else:
                        mgr.mark_error(acc, "401 + refresh failed")
                        continue

                if status == 403:
                    mgr.mark_error(acc, "403 forbidden")
                    continue

                log.info("[%s] %s %s → %d%s", acc["name"], request.method, path,
                         status, " (stream)" if is_stream else "")

                if is_stream and status == 200:
                    resp = web.StreamResponse(
                        status=status,
                        headers={
                            "Content-Type": upstream_resp.headers.get("Content-Type", "text/event-stream"),
                            "Cache-Control": "no-cache",
                        },
                    )
                    await resp.prepare(request)
                    final_usage = {}
                    async for chunk in upstream_resp.content.iter_any():
                        await resp.write(chunk)
                        try:
                            for line in chunk.decode(errors="ignore").split("\n"):
                                if line.startswith("data: ") and "usage" in line:
                                    d = json.loads(line[6:])
                                    if "usage" in d:
                                        final_usage = d["usage"]
                        except Exception:
                            pass
                    await resp.write_eof()
                    latency = int((time.time() - t0) * 1000)
                    log_usage(
                        key_row["id"], model,
                        final_usage.get("input_tokens", 0),
                        final_usage.get("output_tokens", 0),
                        final_usage.get("cache_read_input_tokens", 0),
                        final_usage.get("cache_creation_input_tokens", 0),
                        acc["name"], status, latency,
                    )
                    return resp
                else:
                    resp_body = await upstream_resp.read()
                    latency = int((time.time() - t0) * 1000)
                    try:
                        resp_json = json.loads(resp_body)
                        usage = resp_json.get("usage", {})
                        log_usage(
                            key_row["id"], model,
                            usage.get("input_tokens", 0),
                            usage.get("output_tokens", 0),
                            usage.get("cache_read_input_tokens", 0),
                            usage.get("cache_creation_input_tokens", 0),
                            acc["name"], status, latency,
                        )
                    except Exception:
                        log_usage(key_row["id"], model, 0, 0, 0, 0, acc["name"], status, latency)
                    return web.Response(
                        status=status, body=resp_body,
                        content_type=upstream_resp.headers.get("Content-Type", "application/json"),
                    )

        except asyncio.TimeoutError:
            log.error("[%s] 上游超时", acc["name"])
            return web.json_response(
                {"error": {"type": "timeout_error", "message": "Upstream timeout"}}, status=504)
        except Exception as e:
            log.error("[%s] 请求异常: %s", acc["name"], e)
            return web.json_response(
                {"error": {"type": "internal_error", "message": str(e)}}, status=502)

    return web.json_response(
        {"error": {"type": "overloaded_error", "message": "All retries exhausted"}}, status=503)


# ---------------------------------------------------------------------------
# Admin API (protected by Cloudflare Access at the edge)
# ---------------------------------------------------------------------------
async def handle_list_keys(request: web.Request) -> web.Response:
    conn = get_db()
    keys = conn.execute("""
        SELECT k.*,
            COALESCE(today.req_count, 0) as today_requests,
            COALESCE(today.total_input, 0) + COALESCE(today.total_output, 0) as today_tokens,
            COALESCE(month.req_count, 0) as month_requests,
            COALESCE(month.total_input, 0) + COALESCE(month.total_output, 0) as month_tokens
        FROM api_keys k
        LEFT JOIN (
            SELECT api_key_id, COUNT(*) as req_count,
                   SUM(input_tokens) as total_input, SUM(output_tokens) as total_output
            FROM usage_logs WHERE date(timestamp) = date('now') GROUP BY api_key_id
        ) today ON k.id = today.api_key_id
        LEFT JOIN (
            SELECT api_key_id, COUNT(*) as req_count,
                   SUM(input_tokens) as total_input, SUM(output_tokens) as total_output
            FROM usage_logs WHERE timestamp >= datetime('now', '-30 days') GROUP BY api_key_id
        ) month ON k.id = month.api_key_id
        ORDER BY k.created_at DESC
    """).fetchall()
    conn.close()
    return web.json_response([dict(r) for r in keys])


async def handle_create_key(request: web.Request) -> web.Response:
    data = await request.json()
    name = data.get("name", "Unnamed")
    group = data.get("group_name", "")
    key = f"sk-{secrets.token_hex(24)}"
    conn = get_db()
    conn.execute(
        "INSERT INTO api_keys (name, key, group_name) VALUES (?, ?, ?)",
        (name, key, group),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM api_keys WHERE key = ?", (key,)).fetchone()
    conn.close()
    return web.json_response(dict(row), status=201)


async def handle_update_key(request: web.Request) -> web.Response:
    key_id = int(request.match_info["id"])
    data = await request.json()
    conn = get_db()
    row = conn.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()
    if not row:
        conn.close()
        return web.json_response({"error": "Key not found"}, status=404)
    name = data.get("name", row["name"])
    group = data.get("group_name", row["group_name"])
    enabled = data.get("enabled", row["enabled"])
    conn.execute(
        "UPDATE api_keys SET name=?, group_name=?, enabled=? WHERE id=?",
        (name, group, int(enabled), key_id),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()
    conn.close()
    return web.json_response(dict(updated))


async def handle_delete_key(request: web.Request) -> web.Response:
    key_id = int(request.match_info["id"])
    conn = get_db()
    conn.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
    conn.commit()
    conn.close()
    return web.json_response({"ok": True})


async def handle_usage(request: web.Request) -> web.Response:
    days = int(request.query.get("days", "30"))
    limit = int(request.query.get("limit", "200"))
    conn = get_db()
    rows = conn.execute("""
        SELECT u.*, k.name as key_name
        FROM usage_logs u
        LEFT JOIN api_keys k ON u.api_key_id = k.id
        WHERE u.timestamp >= datetime('now', ?)
        ORDER BY u.timestamp DESC LIMIT ?
    """, (f"-{days} days", limit)).fetchall()
    conn.close()
    return web.json_response([dict(r) for r in rows])


async def handle_usage_summary(request: web.Request) -> web.Response:
    conn = get_db()
    today = conn.execute("""
        SELECT COUNT(*) as requests,
               COALESCE(SUM(input_tokens),0) as input_tokens,
               COALESCE(SUM(output_tokens),0) as output_tokens,
               COALESCE(SUM(cache_read_tokens),0) as cache_read_tokens,
               COALESCE(SUM(cache_creation_tokens),0) as cache_creation_tokens
        FROM usage_logs WHERE date(timestamp) = date('now')
    """).fetchone()
    month = conn.execute("""
        SELECT COUNT(*) as requests,
               COALESCE(SUM(input_tokens),0) as input_tokens,
               COALESCE(SUM(output_tokens),0) as output_tokens,
               COALESCE(SUM(cache_read_tokens),0) as cache_read_tokens,
               COALESCE(SUM(cache_creation_tokens),0) as cache_creation_tokens
        FROM usage_logs WHERE timestamp >= datetime('now', '-30 days')
    """).fetchone()
    daily = conn.execute("""
        SELECT date(timestamp) as date, COUNT(*) as requests,
               SUM(input_tokens) as input_tokens, SUM(output_tokens) as output_tokens
        FROM usage_logs WHERE timestamp >= datetime('now', '-30 days')
        GROUP BY date(timestamp) ORDER BY date
    """).fetchall()
    conn.close()
    return web.json_response({
        "today": dict(today),
        "month": dict(month),
        "daily": [dict(r) for r in daily],
    })


async def handle_accounts(request: web.Request) -> web.Response:
    mgr: AccountManager = request.app["manager"]
    return web.json_response(mgr.get_status())


async def handle_toggle_account(request: web.Request) -> web.Response:
    mgr: AccountManager = request.app["manager"]
    name = request.match_info["name"]
    acc = await mgr.toggle_account(name)
    if acc is None:
        return web.json_response({"error": "Account not found"}, status=404)
    return web.json_response({"name": acc["name"], "skip": acc["skip"], "status": acc["status"]})


# ---------------------------------------------------------------------------
# SPA fallback
# ---------------------------------------------------------------------------
async def handle_spa(request: web.Request) -> web.StreamResponse:
    rel_path = request.match_info.get("path", "")
    file_path = STATIC_DIR / rel_path
    if file_path.is_file():
        return web.FileResponse(file_path)
    index = STATIC_DIR / "index.html"
    if index.is_file():
        return web.FileResponse(index)
    return web.json_response({
        "message": "CC API Proxy is running.",
        "api_endpoint": "https://proxy.tianlizeng.cloud/v1/messages",
    })


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def on_startup(app: web.Application):
    mgr: AccountManager = app["manager"]
    log.info("启动刷新所有账号 token...")
    await mgr.refresh_all()
    await mgr.save_config()
    app["refresh_task"] = asyncio.create_task(mgr.background_refresh_loop())


async def on_cleanup(app: web.Application):
    mgr: AccountManager = app["manager"]
    task = app.get("refresh_task")
    if task:
        task.cancel()
    await mgr.close()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    config = load_config()
    init_db()
    migrate_legacy_key(config)

    mgr = AccountManager(config)
    log.info("加载 %d 个账号", len(mgr.accounts))

    app = web.Application()
    app["manager"] = mgr
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    # === API Proxy ===
    app.router.add_post("/v1/messages", handle_proxy)
    app.router.add_post("/v1/messages/count_tokens", handle_proxy)

    # === Admin API (auth by CF Access) ===
    app.router.add_get("/api/keys", handle_list_keys)
    app.router.add_post("/api/keys", handle_create_key)
    app.router.add_patch("/api/keys/{id}", handle_update_key)
    app.router.add_delete("/api/keys/{id}", handle_delete_key)
    app.router.add_get("/api/usage", handle_usage)
    app.router.add_get("/api/usage/summary", handle_usage_summary)
    app.router.add_get("/api/accounts", handle_accounts)
    app.router.add_post("/api/accounts/{name}/toggle", handle_toggle_account)

    # === SPA Frontend (catch-all, must be last) ===
    app.router.add_get("/{path:.*}", handle_spa)

    host = config.get("listen_host", "127.0.0.1")
    port = config.get("listen_port", 9100)
    log.info("启动服务 %s:%d", host, port)
    web.run_app(app, host=host, port=port, print=None)


if __name__ == "__main__":
    main()
