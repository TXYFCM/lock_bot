#!/usr/bin/env python3
"""proxy.py — Python 版本地代理，解决内网 API 跨域问题（替代 proxy.js）

配置: 从 config.json 读取，支持环境变量覆盖:
  PROXY_PORT      — 代理监听端口
  LOCKBOT_HOST    — Lock Bot 服务 IP
  LOCKBOT_PORT    — Lock Bot 服务端口
  MONQUERY_HOST   — Monquery 服务 IP
  MONQUERY_PORT   — Monquery 服务端口
"""

import http.server
import urllib.request
import urllib.error
import os
import json
import mimetypes

# ---- 加载配置 ----
_DEFAULTS = {
    "proxy": {"port": 8900, "bind": "0.0.0.0"},
    "backend": {
        "lockbot": {"host": "10.206.192.17", "port": 8875},
        "monquery": {"host": "api.mt.noah.baidu.com", "port": 8557},
    },
}


def _load_config():
    config = _DEFAULTS
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            print("✓ Loaded config.json")
        except Exception as e:
            print(f"⚠ Failed to parse config.json, using defaults: {e}")
    else:
        print("⚠ config.json not found, using built-in defaults")

    # 环境变量覆盖
    if os.environ.get("PROXY_PORT"):
        config["proxy"]["port"] = int(os.environ["PROXY_PORT"])
    if os.environ.get("LOCKBOT_HOST"):
        config["backend"]["lockbot"]["host"] = os.environ["LOCKBOT_HOST"]
    if os.environ.get("LOCKBOT_PORT"):
        config["backend"]["lockbot"]["port"] = int(os.environ["LOCKBOT_PORT"])
    if os.environ.get("MONQUERY_HOST"):
        config["backend"]["monquery"]["host"] = os.environ["MONQUERY_HOST"]
    if os.environ.get("MONQUERY_PORT"):
        config["backend"]["monquery"]["port"] = int(os.environ["MONQUERY_PORT"])

    return config


CONFIG = _load_config()
PORT = CONFIG["proxy"]["port"]
ROOT = os.path.dirname(os.path.abspath(__file__))

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
}


class ProxyHandler(http.server.BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in CORS_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()

    def proxy_to(self, host, port):
        """转发请求到目标服务器"""
        target_url = f"http://{host}:{port}{self.path}"
        try:
            body = None
            if self.command not in ("GET", "HEAD", "OPTIONS"):
                content_len = int(self.headers.get("Content-Length", 0))
                if content_len > 0:
                    body = self.rfile.read(content_len)

            req = urllib.request.Request(target_url, data=body, method=self.command)
            # 复制部分请求头
            for key in ("Content-Type", "Authorization", "Accept"):
                if key in self.headers:
                    req.add_header(key, self.headers[key])

            with urllib.request.urlopen(req, timeout=30) as resp:
                self.send_response(resp.status)
                for k, v in CORS_HEADERS.items():
                    self.send_header(k, v)
                # 透传响应头
                for k, v in resp.headers.items():
                    if k.lower() not in (
                        "access-control-allow-origin",
                        "access-control-allow-headers",
                        "access-control-allow-methods",
                        "transfer-encoding",
                    ):
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp.read())
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            for k, v in CORS_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            for k, v in CORS_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(f'{{"error":"Proxy failed","detail":"{e}"}}'.encode())

    def serve_file(self, file_path):
        """返回静态文件"""
        try:
            with open(file_path, "rb") as f:
                data = f.read()
            ext = os.path.splitext(file_path)[1]
            content_type = MIME.get(ext, "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(f"Not found: {self.path}".encode())

    def do_GET(self):
        self._handle()

    def _handle(self):
        lb = CONFIG["backend"]["lockbot"]
        mq = CONFIG["backend"]["monquery"]

        # 代理路由
        if self.path.startswith("/lockbot"):
            self.path = self.path[len("/lockbot"):] or "/"
            return self.proxy_to(lb["host"], lb["port"])

        if self.path.startswith("/monquery"):
            self.path = self.path[len("/monquery"):] or "/"
            return self.proxy_to(mq["host"], mq["port"])

        # 静态文件
        file_path = self.path.lstrip("/") or "demo.html"
        full_path = os.path.normpath(os.path.join(ROOT, file_path))
        # 安全检查
        if not full_path.startswith(ROOT):
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"Forbidden")
            return
        self.serve_file(full_path)

    def log_message(self, format, *args):
        print(f"[proxy] {args[0]}")


if __name__ == "__main__":
    bind = CONFIG["proxy"].get("bind", "0.0.0.0")
    server = http.server.HTTPServer((bind, PORT), ProxyHandler)
    print(f"✓ Proxy ready at http://localhost:{PORT}/demo.html")
    print(f"  Lock Bot  → {CONFIG['backend']['lockbot']['host']}:{CONFIG['backend']['lockbot']['port']} (via /lockbot)")
    print(f"  Monquery  → {CONFIG['backend']['monquery']['host']}:{CONFIG['backend']['monquery']['port']} (via /monquery)")
    server.serve_forever()
