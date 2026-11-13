#!/usr/bin/env python3
"""部署后冒烟检查：确认线上服务活着、前后端接得上、（可选）能真实答题。

只用标准库，不导入 src，可在任何机器上对 Railway / Vercel 的线上地址运行。
默认只做只读检查；--with-chat 会创建 1 张真实工单并调用 1 次线上模型。

用法：
    python scripts/run_post_deploy_smoke.py \
        --api-url https://xxx.up.railway.app \
        --frontend-url https://xxx.vercel.app
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"
# 与 .env.example 的 PUBLIC_DEMO_PROFILE_IDS 对应，公开入口只认这些虚构画像。
DEFAULT_PROFILE = "cust_101"
DEFAULT_MESSAGE = "你们的退货政策是什么？"


@dataclass
class Result:
    name: str
    status: str
    detail: str = ""


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text())


class Http:
    """带 Cookie 的最小 HTTP 客户端；非 2xx 也返回 Response，不抛异常。"""

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: Any = None,
        timeout: float | None = None,
    ) -> Response:
        data = None
        hdrs = {"User-Agent": "supportgpt-post-deploy-smoke", **(headers or {})}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with self._opener.open(req, timeout=timeout or self.timeout) as resp:
                return Response(
                    resp.status,
                    {k.lower(): v for k, v in resp.headers.items()},
                    resp.read(),
                )
        except urllib.error.HTTPError as exc:
            return Response(
                exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()
            )
        except (urllib.error.URLError, OSError) as exc:
            # 连不上、超时、证书错误：status=0，原因放在 body 里。
            return Response(0, {}, str(exc).encode("utf-8"))


def origin_of(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def http_detail(resp: Response) -> str:
    if resp.status == 0:
        return f"无法连接: {resp.text()[:150]}"
    return f"HTTP {resp.status}"


def check_health(client: Http, api: str) -> Result:
    """/health 只证明进程活着，数据库、模型等由后面的检查覆盖。"""
    name = "API /health"
    resp = client.request("GET", api + "/health")
    if resp.status != 200:
        return Result(name, FAIL, http_detail(resp))
    try:
        status = resp.json().get("status")
    except ValueError:
        return Result(name, FAIL, "返回不是 JSON，可能打到了别的服务")
    if status != "healthy":
        return Result(name, FAIL, f"status={status!r}")
    return Result(name, PASS, "进程存活（不代表数据库/模型可用）")


def check_frontend(client: Http, frontend: str) -> tuple[Result, str]:
    name = "前端首页"
    resp = client.request("GET", frontend)
    html = resp.text() if resp.status == 200 else ""
    if resp.status != 200:
        return Result(name, FAIL, http_detail(resp)), ""
    if "<div id=\"root\"" not in html and "<div id='root'" not in html:
        return Result(name, FAIL, "页面里没有 React 根节点，可能不是本项目的构建"), ""
    return Result(name, PASS, "HTTP 200"), html


def check_frontend_bundle(
    client: Http, frontend: str, html: str, api: str
) -> Result:
    """前端构建时写死 VITE_API_BASE_URL；这里确认它指向线上后端而不是 localhost。"""
    name = "前端指向的后端地址"
    scripts = re.findall(r'<script[^>]+src="([^"]+\.js)"', html)
    if not scripts:
        return Result(name, WARN, "首页里没找到 JS 文件，无法检查")
    api_origin = origin_of(api)
    seen_localhost = False
    for src in scripts:
        resp = client.request("GET", urljoin(frontend + "/", src))
        if resp.status != 200:
            continue
        text = resp.text()
        if api_origin in text:
            return Result(name, PASS, f"构建产物包含 {api_origin}")
        if re.search(r"localhost:8000|127\.0\.0\.1:8000", text):
            seen_localhost = True
    if seen_localhost:
        return Result(
            name,
            FAIL,
            "构建产物仍指向 localhost:8000，Vercel 的 VITE_API_BASE_URL 没生效"
            "（改完变量要重新部署）",
        )
    return Result(name, WARN, f"构建产物里没找到 {api_origin}，请人工确认")


def check_cors(client: Http, api: str, frontend_origin: str) -> Result:
    """模拟浏览器预检请求，确认后端 CORS 放行前端域名。"""
    name = f"CORS 放行 {frontend_origin}"
    resp = client.request(
        "OPTIONS",
        api + "/support/requests",
        headers={
            "Origin": frontend_origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    allowed = resp.headers.get("access-control-allow-origin")
    if resp.status >= 400 or allowed != frontend_origin:
        return Result(
            name,
            FAIL,
            f"HTTP {resp.status}, allow-origin={allowed!r}；"
            "检查 Railway 的 CORS_ALLOWED_ORIGINS",
        )
    if resp.headers.get("access-control-allow-credentials") != "true":
        return Result(name, FAIL, "缺少 allow-credentials，访客 Cookie 会被浏览器丢弃")
    return Result(name, PASS, "预检通过")


def check_chat(
    client: Http,
    api: str,
    *,
    profile: str,
    message: str,
    timeout: float,
) -> list[Result]:
    """走一遍公开咨询入口：写库 → Workflow → 模型 → 公开运行摘要。"""
    name = "公开咨询 POST /support/requests"
    resp = client.request(
        "POST",
        api + "/support/requests",
        body={"customer_id": profile, "message": message},
        timeout=timeout,
    )
    if resp.status != 201:
        return [Result(name, FAIL, f"HTTP {resp.status}: {resp.text()[:200]}")]
    data = resp.json()
    results = []
    reply = data.get("response") or data.get("message")
    if data.get("status") not in {"answered", "pending_human"} or not reply:
        return [Result(name, FAIL, f"返回内容异常: {json.dumps(data, ensure_ascii=False)[:200]}")]
    if data.get("handling_reason") == "processing_exception":
        # 工单被保留并转人工，说明某个下游（模型/RAG/数据库）当时不可用。
        results.append(
            Result(name, FAIL, "返回 processing_exception：Workflow 内有下游服务失败")
        )
    else:
        results.append(
            Result(
                name,
                PASS,
                f"status={data['status']}, handling_reason={data.get('handling_reason')}",
            )
        )

    run_id = data.get("agent_run_id")
    iname = "公开运行摘要 /support/runs/{id}/insight"
    if not run_id:
        results.append(Result(iname, FAIL, "响应没有 agent_run_id，AgentRun 未落库"))
        return results
    token = data.get("feedback_token")
    if not token:
        results.append(Result(iname, FAIL, "响应没有 feedback_token，无法读取运行摘要"))
        return results
    # 运行摘要凭反馈令牌授权，令牌来自咨询响应。
    ins = client.request(
        "GET",
        f"{api}/support/runs/{run_id}/insight",
        headers={"X-Feedback-Token": token},
    )
    if ins.status != 200:
        results.append(Result(iname, FAIL, f"HTTP {ins.status}"))
        return results
    info = ins.json()
    tokens = info.get("tokens_total")
    path = info.get("workflow_path") or []
    if not path:
        results.append(Result(iname, FAIL, "workflow_path 为空"))
    elif not tokens:
        # Mock 模型或遥测缺失时 Token 为空；线上不该是这样。
        results.append(
            Result(iname, WARN, f"tokens_total={tokens!r}，线上可能仍在用 Mock 模型")
        )
    else:
        results.append(
            Result(
                iname,
                PASS,
                f"节点 {len(path)} 个, tokens={tokens}, qa_score={info.get('qa_score')}",
            )
        )
    return results


def run_checks(args: argparse.Namespace, client: Http | None = None) -> list[Result]:
    client = client or Http()
    api = args.api_url.rstrip("/")
    results = [check_health(client, api)]
    if results[0].status == FAIL:
        # 后端都不通，后面的检查没有意义。
        return results

    for frontend in args.frontend_url:
        frontend = frontend.rstrip("/")
        fe_result, html = check_frontend(client, frontend)
        fe_result.name += f"（{frontend}）"
        results.append(fe_result)
        if fe_result.status == PASS:
            results.append(check_frontend_bundle(client, frontend, html, api))
        results.append(check_cors(client, api, origin_of(frontend)))

    if args.with_chat:
        results.extend(
            check_chat(
                client,
                api,
                profile=args.profile,
                message=args.message,
                timeout=args.chat_timeout,
            )
        )
    else:
        results.append(
            Result("公开咨询（真实问答）", SKIP, "未加 --with-chat，不创建工单、不调模型")
        )
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="部署后冒烟检查")
    parser.add_argument(
        "--api-url",
        default=os.getenv("SMOKE_API_URL"),
        help="后端地址（Railway 公网域名），也可用环境变量 SMOKE_API_URL。",
    )
    parser.add_argument(
        "--frontend-url",
        action="append",
        default=None,
        help="前端地址（Vercel 域名），可重复；也可用 SMOKE_FRONTEND_URLS（逗号分隔）。",
    )
    parser.add_argument(
        "--with-chat",
        action="store_true",
        help="发一条真实咨询：会在线上创建 1 张工单并调用 1 次模型。",
    )
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument("--chat-timeout", type=float, default=90.0)
    parser.add_argument("--json", action="store_true", help="输出 JSON。")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.api_url:
        print("缺少 --api-url（或环境变量 SMOKE_API_URL）", file=sys.stderr)
        return 2
    if args.frontend_url is None:
        env = os.getenv("SMOKE_FRONTEND_URLS", "")
        args.frontend_url = [u.strip() for u in env.split(",") if u.strip()]

    results = run_checks(args)
    if args.json:
        print(json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(f"[{r.status}] {r.name}: {r.detail}")
        print(
            "\n未覆盖：数据库/Redis 的独立状态、LangSmith 等遥测是否上报"
            "（只能到对应后台查看）。"
        )
    return 1 if any(r.status == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
