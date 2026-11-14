#!/usr/bin/env python3
"""线上回复质量检查：按固定用例请求公开咨询入口，检查结果后撤回测试工单。

    python scripts/run_online_reply_check.py --api-url https://<Railway 域名> --dry-run
    python scripts/run_online_reply_check.py --api-url https://<Railway 域名> --confirm-live

每条用例会新建 1 张线上工单并调用真实模型（约 2-3 次）。检查完成后脚本凭本次
反馈令牌调用撤回接口关闭工单、移出人工队列；--keep 才会保留。
公开入口限流为每分钟 5 次，脚本默认每 13 秒发一条。
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "evaluation" / "online_checks" / "reply_checks.json"
DEFAULT_OUTPUT = ROOT / "evaluation" / "reports" / "online_reply_check"
_CJK = re.compile(r"[一-鿿]")


def evaluate_case(case: dict, data: dict, insight: dict) -> list[str]:
    """返回这条用例没通过的检查项；空列表表示通过。只做确定性检查。"""
    failures: list[str] = []
    reply = str(data.get("response") or "")
    status = data.get("status")
    reason = data.get("handling_reason")
    if case.get("status") and status not in case["status"]:
        failures.append(f"status={status}，期望 {case['status']}")
    if case.get("handling_reason") and (reason or "none") not in case["handling_reason"]:
        # 没有转人工时 handling_reason 为空；期望里写了原因就必须命中其一。
        if not (status == "answered" and reason is None and "none" in case["handling_reason"]):
            failures.append(f"handling_reason={reason}，期望 {case['handling_reason']}")
    if case.get("intent") and insight.get("intent") not in case["intent"]:
        failures.append(f"intent={insight.get('intent')}，期望 {case['intent']}")
    called = " ".join(str(t.get("tool_name", "")) for t in insight.get("tool_calls", []) or [])
    for tool in case.get("tools", []):
        if tool not in called:
            failures.append(f"没有调用 Tool {tool}")
    if not reply.strip():
        failures.append("回复为空")
    language = case.get("language")
    if language == "zh" and not _CJK.search(reply):
        failures.append("回复不是中文")
    if language == "en" and _CJK.search(reply):
        failures.append("回复不是英文")
    includes = case.get("must_include_any") or []
    if includes and not any(item.lower() in reply.lower() for item in includes):
        failures.append(f"回复不含任一关键内容 {includes}")
    for pattern in case.get("must_not_match", []):
        if re.search(pattern, reply):
            failures.append(f"回复命中禁止内容 /{pattern}/")
    if case.get("max_chars") and len(reply) > case["max_chars"]:
        failures.append(f"回复 {len(reply)} 字，超过 {case['max_chars']}")
    # 内部引用标记和草稿痕迹不应出现在用户看到的回复里。
    if re.search(r"\[S\d+\]|【S\d+】", reply):
        failures.append("回复里残留内部引用标记")
    return failures


class Client:
    def __init__(self, api_url: str) -> None:
        self.api_url = api_url.rstrip("/")
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def call(self, method: str, path: str, body=None, headers=None) -> tuple[int, dict]:
        hdrs = {"User-Agent": "supportgpt-online-reply-check", **(headers or {})}
        payload = json.dumps(body).encode() if body is not None else None
        if payload:
            hdrs["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.api_url + path, data=payload, headers=hdrs, method=method
        )
        try:
            with self.opener.open(request, timeout=120) as response:
                return response.status, json.loads(response.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode() or "{}")
            except ValueError:
                return exc.code, {}
        except Exception as exc:  # 网络错误也要走到清理和报告
            return 0, {"error": repr(exc)}


def run(args: argparse.Namespace) -> int:
    dataset = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    cases = [c for c in dataset["cases"] if not args.only or c["id"] in args.only]
    print(f"用例 {len(cases)} 条，预计新建 {len(cases)} 张线上工单、约 {len(cases) * 3} 次模型调用，"
          f"耗时约 {len(cases) * args.interval // 60 + 1} 分钟。")
    if args.dry_run or not args.confirm_live:
        print("未发送任何请求（需要 --confirm-live）。")
        return 0
    client = Client(args.api_url)
    session = f"reply-check-{uuid.uuid4().hex[:12]}"
    rows, last, leftovers = [], 0.0, []
    for case in cases:
        time.sleep(max(0.0, args.interval - (time.time() - last)))
        last = time.time()
        code, data = client.call(
            "POST", "/support/requests",
            {"customer_id": case["customer"], "message": case["message"],
             "session_id": f"{session}-{case['id']}"},
            {"X-Request-Id": uuid.uuid4().hex},
        )
        insight: dict = {}
        run_id, token = data.get("agent_run_id"), data.get("feedback_token")
        if run_id and token:
            _, insight = client.call(
                "GET", f"/support/runs/{run_id}/insight", headers={"X-Feedback-Token": token}
            )
        failures = [f"HTTP {code}"] if code != 201 else evaluate_case(case, data, insight)
        cleaned = "保留"
        if run_id and token and not args.keep:
            wcode, _ = client.call(
                "POST", f"/support/runs/{run_id}/withdraw", headers={"X-Feedback-Token": token}
            )
            cleaned = "已撤回" if wcode == 200 else f"撤回失败 HTTP {wcode}"
            if wcode != 200:
                leftovers.append(data.get("ticket_id"))
        elif code == 201 and not args.keep:
            cleaned = "无法撤回（缺少令牌）"
            leftovers.append(data.get("ticket_id"))
        rows.append({
            "id": case["id"], "passed": not failures, "failures": failures,
            "status": data.get("status"), "handling_reason": data.get("handling_reason"),
            "intent": insight.get("intent"), "qa_score": insight.get("qa_score"),
            "ticket_id": data.get("ticket_id"), "cleanup": cleaned,
            "response": str(data.get("response") or "")[:600],
        })
        mark = "PASS" if not failures else "FAIL"
        print(f"[{mark}] {case['id']}: {data.get('status')}/{data.get('handling_reason')} "
              f"intent={insight.get('intent')} qa={insight.get('qa_score')} 清理={cleaned}")
        for failure in failures:
            print(f"       - {failure}")
        print(f"       回复: {str(data.get('response') or '')[:160]}", flush=True)
    passed = sum(row["passed"] for row in rows)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"api_url": args.api_url, "dataset_version": dataset.get("version"),
              "passed": passed, "total": len(rows), "leftover_ticket_ids": leftovers, "cases": rows}
    (out_dir / "online_reply_check_latest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n通过 {passed}/{len(rows)}；未清理的工单：{leftovers or '无'}")
    print(f"报告：{out_dir / 'online_reply_check_latest.json'}")
    return 0 if passed == len(rows) and not leftovers else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--only", nargs="*", default=[], help="只跑这些用例 id")
    parser.add_argument("--interval", type=int, default=13, help="每条间隔秒数（公开入口每分钟限 5 次）")
    parser.add_argument("--keep", action="store_true", help="保留测试工单，不撤回")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-live", action="store_true", help="确认会新建线上工单并调用真实模型")
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
