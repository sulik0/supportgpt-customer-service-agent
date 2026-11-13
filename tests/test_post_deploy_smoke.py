"""部署后冒烟脚本测试：用本地假服务模拟线上，不访问外网、不调用模型。"""

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_post_deploy_smoke.py"
spec = importlib.util.spec_from_file_location("run_post_deploy_smoke", SCRIPT)
smoke = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = smoke  # dataclass 需要模块已注册
spec.loader.exec_module(smoke)


def start(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def make_api(*, cors_origin, chat_status=201, tokens=120, reason=None):
    class Api(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, payload=None, headers=None):
            body = json.dumps(payload or {}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self._send(200, {"status": "healthy"})
            elif self.path.startswith("/support/runs/"):
                if self.headers.get("X-Feedback-Token") != "tok-1":
                    self._send(403, {"detail": "Forbidden"})
                    return
                self._send(
                    200,
                    {
                        "workflow_path": ["ticket_analyzer", "qa"],
                        "tokens_total": tokens,
                        "qa_score": 0.9,
                        "approval_required": False,
                    },
                )
            else:
                self._send(404)

        def do_OPTIONS(self):
            self._send(
                200,
                headers={
                    "Access-Control-Allow-Origin": cors_origin,
                    "Access-Control-Allow-Credentials": "true",
                },
            )

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if chat_status != 201:
                self._send(chat_status, {"detail": "boom"})
                return
            self._send(
                201,
                {
                    "status": "answered" if not reason else "pending_human",
                    "response": "7 天内可退货。",
                    "message": "ok",
                    "handling_reason": reason,
                    "agent_run_id": "run-1",
                    "feedback_token": "tok-1",
                },
            )

    return Api


def make_frontend(api_url):
    class Fe(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/":
                body = (
                    '<div id="root"></div>'
                    '<script type="module" src="/assets/app.js"></script>'
                ).encode()
                ctype = "text/html"
            elif self.path == "/assets/app.js":
                body = f'const B="{api_url}";'.encode()
                ctype = "application/javascript"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(body)

    return Fe


def run(api_url, frontend_urls=(), **overrides):
    args = smoke.build_parser().parse_args(["--api-url", api_url])
    args.frontend_url = list(frontend_urls)
    for key, value in overrides.items():
        setattr(args, key, value)
    return {r.name: r for r in smoke.run_checks(args)}


def statuses(results):
    return {name: r.status for name, r in results.items()}


def test_health_and_chat_pass_without_frontend():
    api, api_url = start(make_api(cors_origin="http://x"))
    try:
        results = run(api_url, with_chat=True)
    finally:
        api.shutdown()
    assert set(statuses(results).values()) == {smoke.PASS}


def test_frontend_cors_and_bundle_pass_together():
    holder = {}

    class Fe(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/":
                body, ctype = (
                    b'<div id="root"></div><script src="/a.js"></script>',
                    "text/html",
                )
            else:
                body, ctype = f'x="{holder["api"]}"'.encode(), "text/javascript"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(body)

    fe, fe_url = start(Fe)
    api, api_url = start(make_api(cors_origin=fe_url))
    holder["api"] = api_url
    try:
        results = run(api_url, [fe_url])
    finally:
        api.shutdown()
        fe.shutdown()
    assert smoke.FAIL not in statuses(results).values()
    assert smoke.WARN not in statuses(results).values()


def test_cors_mismatch_fails():
    fe, fe_url = start(make_frontend("http://placeholder"))
    api, api_url = start(make_api(cors_origin="https://other.example"))
    try:
        results = run(api_url, [fe_url])
    finally:
        api.shutdown()
        fe.shutdown()
    cors = [r for name, r in results.items() if name.startswith("CORS")][0]
    assert cors.status == smoke.FAIL
    assert "CORS_ALLOWED_ORIGINS" in cors.detail


def test_bundle_pointing_to_localhost_fails():
    fe, fe_url = start(make_frontend("http://localhost:8000"))
    api, api_url = start(make_api(cors_origin=fe_url))
    try:
        results = run(api_url, [fe_url])
    finally:
        api.shutdown()
        fe.shutdown()
    bundle = results["前端指向的后端地址"]
    assert bundle.status == smoke.FAIL
    assert "VITE_API_BASE_URL" in bundle.detail


def test_chat_processing_exception_fails():
    api, api_url = start(make_api(cors_origin="x", reason="processing_exception"))
    try:
        results = run(api_url, with_chat=True)
    finally:
        api.shutdown()
    assert results["公开咨询 POST /support/requests"].status == smoke.FAIL


def test_zero_tokens_warns_not_fails():
    api, api_url = start(make_api(cors_origin="x", tokens=0))
    try:
        results = run(api_url, with_chat=True)
    finally:
        api.shutdown()
    insight = [r for n, r in results.items() if "insight" in n][0]
    assert insight.status == smoke.WARN


def test_chat_skipped_by_default():
    api, api_url = start(make_api(cors_origin="x"))
    try:
        results = run(api_url)
    finally:
        api.shutdown()
    assert results["公开咨询（真实问答）"].status == smoke.SKIP


def test_unreachable_api_exit_code_is_nonzero(capsys):
    # 端口 1 不会有服务；URLError 需要被当作失败，而不是抛出堆栈。
    code = smoke.main(["--api-url", "http://127.0.0.1:1"])
    assert code == 1
    assert "FAIL" in capsys.readouterr().out


def test_missing_api_url_returns_usage_error(monkeypatch):
    monkeypatch.delenv("SMOKE_API_URL", raising=False)
    assert smoke.main([]) == 2
