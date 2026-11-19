#!/usr/bin/env python3
"""本地查看测试分类页面：只监听 127.0.0.1，页面里的“更新”按钮会重新生成页面。

    python3 scripts/test_catalog_server.py      # 然后打开 http://127.0.0.1:8765
"""
from __future__ import annotations

import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_test_catalog as catalog  # noqa: E402

PORT = 8765


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str = "text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        pages = {
            "/": catalog.OUT,
            "/test_catalog.html": catalog.OUT,
            "/test_catalog_other.html": catalog.OUT_OTHER,
            "/test_baseline.html": catalog.OUT_BASELINE,
        }
        target = pages.get(self.path.split("?")[0])
        if target is None:
            return self._send(404, b"not found")
        if not target.exists():
            catalog.main()
        self._send(200, target.read_bytes(), "text/html; charset=utf-8")

    def do_POST(self):
        if self.path != "/refresh":
            return self._send(404, b"not found")
        try:
            catalog.main()
        except Exception as exc:  # 生成失败时把原因返回给页面
            return self._send(500, str(exc).encode("utf-8"))
        self._send(200, b"ok")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"http://127.0.0.1:{PORT}  （Ctrl+C 退出）")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
