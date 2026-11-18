"""Antigravity MCP entry point and local dashboard HTTP server."""

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import webbrowser

from usage import APP_DATA, ROOT, UUID, Usage, model_catalog, quota_snapshot


ASSETS = {
    "": ("dashboard.html", "text/html"),
    "dashboard.css": ("dashboard.css", "text/css"),
    "dashboard.js": ("dashboard.js", "text/javascript"),
}


class Dashboard(HTTPServer):
    def __init__(self, token, app_data=APP_DATA):
        super().__init__(("127.0.0.1", 0), Handler)
        self.prefix = "/" + token + "/"
        self.usage = Usage(app_data)
        self.app_data = app_data
        self.timeout = 2
        self.last_used = time.monotonic()
        self.quota = None
        self.quota_checked = 0
        self.catalog = {}
        self.catalog_checked = float("-inf")

    def quotas(self):
        if time.monotonic() - self.quota_checked >= 60 or self.quota is None:
            self.quota = quota_snapshot(self.app_data)
            self.quota_checked = time.monotonic()
        return self.quota

    def models(self):
        if time.monotonic() - self.catalog_checked >= 60:
            self.catalog = model_catalog(app_data=self.app_data) or self.catalog
            self.catalog_checked = time.monotonic()
        return self.catalog


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, data, media, status=200):
        self.send_response(status)
        self.send_header("Content-Type", media + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'self'; style-src 'self'; "
                         "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (ConnectionError, OSError):
            pass

    def json(self, value, status=200):
        self.respond(json.dumps(value, ensure_ascii=False, allow_nan=False).encode(),
                     "application/json", status)

    def do_GET(self):
        url = urlsplit(self.path)
        if (self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}"
                or not url.path.startswith(self.server.prefix)):
            self.json({"error": "页面不存在"}, 404)
            return
        self.server.last_used = time.monotonic()
        route = url.path[len(self.server.prefix):]
        if route in ASSETS:
            name, media = ASSETS[route]
            self.respond((ROOT / "web" / name).read_bytes(), media)
            return
        if route != "data":
            self.json({"error": "页面不存在"}, 404)
            return
        args = {key: values[-1] for key, values in parse_qs(url.query).items()}
        try:
            hours = args.get("hours", "")
            report = self.server.usage.report(
                args.get("start", ""), args.get("end", ""), args.get("conversation", ""),
                args.get("fx") == "1", args.get("only") == "1", self.server.models(),
                int(hours) if hours.isdigit() else 0, args.get("day") == "1",
            )
            report["quota"] = self.server.quotas()
        except (ValueError, OSError) as error:
            self.json({"error": str(error) if isinstance(error, ValueError) else "暂时无法读取本地数据"}, 400)
            return
        self.json(report)


def launch(conversation=""):
    if conversation and not UUID.fullmatch(conversation):
        raise ValueError("请输入真实的 Antigravity 会话 UUID")
    with tempfile.TemporaryDirectory(prefix="agy-usage-") as folder:
        ready = Path(folder) / "ready"
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        child = subprocess.Popen(
            [sys.executable, "-B", str(Path(__file__).resolve()), "--serve", str(ready)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options,
        )
        for _ in range(60):
            if ready.is_file():
                url = ready.read_text(encoding="utf-8")
                return url + ("?" + urlencode({"conversation": conversation}) if conversation else "")
            if child.poll() is not None:
                break
            time.sleep(0.05)
        if child.poll() is None:
            child.terminate()
        raise RuntimeError("本地看板未能启动")


def serve(ready):
    with Dashboard(secrets.token_urlsafe(24)) as server:
        temporary = ready.with_suffix(".tmp")
        temporary.write_text(f"http://127.0.0.1:{server.server_port}{server.prefix}", encoding="utf-8")
        temporary.replace(ready)
        while time.monotonic() - server.last_used < 600:
            server.handle_request()


TOOL = {
    "name": "open_dashboard",
    "description": "打开 Antigravity 本地用量看板。只返回一个本机浏览器链接。",
    "inputSchema": {"type": "object", "properties": {
        "conversation_id": {"type": "string", "description": "可选。已知的真实 Antigravity 会话 UUID。"}
    }, "additionalProperties": False},
}


def mcp():
    for line in sys.stdin.buffer:
        request = {}
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                continue
            method = request.get("method")
            if "id" not in request:
                continue
            params = request.get("params") or {}
            if method == "initialize":
                requested = params.get("protocolVersion")
                version = requested if requested in {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"} else "2025-06-18"
                result = {"protocolVersion": version, "capabilities": {"tools": {}},
                          "serverInfo": {"name": "agy-usage", "version": "1.0.0"}}
            elif method == "tools/list":
                result = {"tools": [TOOL]}
            elif method == "tools/call" and params.get("name") == TOOL["name"]:
                arguments = params.get("arguments") or {}
                try:
                    url = launch(arguments.get("conversation_id", ""))
                    result = {"content": [{"type": "text", "text": f"[打开 Antigravity 用量看板]({url})"}]}
                except (OSError, ValueError, RuntimeError, TypeError):
                    result = {"isError": True, "content": [{"type": "text", "text": "看板未启动。请检查本机 Python 和会话 ID。"}]}
            elif method == "ping":
                result = {}
            else:
                print(json.dumps({"jsonrpc": "2.0", "id": request["id"],
                                  "error": {"code": -32601, "message": "Method not found"}}), flush=True)
                continue
            print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
        except (ValueError, AttributeError, TypeError):
            identifier = request.get("id") if isinstance(request, dict) else None
            print(json.dumps({"jsonrpc": "2.0", "id": identifier,
                              "error": {"code": -32600, "message": "Invalid request"}}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp", action="store_true")
    parser.add_argument("--serve", type=Path)
    parser.add_argument("--start", default="")
    args = parser.parse_args()
    if args.mcp:
        mcp()
    elif args.serve:
        serve(args.serve)
    else:
        webbrowser.open(launch(args.start))
