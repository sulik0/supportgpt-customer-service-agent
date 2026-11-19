"""用本机 codex（gpt-6.2-sol）评判客服回复是否正确。

  calibrate  用已知答案的样本（evaluation/tool_coverage/judge_calibration.json）校准评判模型
  judge      评判漏调诊断报告里的全部回复，并和关键事实检查对照

调用方式沿用 scripts/build_test_catalog.py：codex exec，只读，在空目录里运行，推理强度显式指定。
评判时打乱顺序、用中性编号，不告诉评判模型哪条来自哪种模式、哪条是人为改错的。
每次调用消耗你的 codex 额度；只发送模拟数据，没有真实客户信息。
"""

import argparse
import json
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CALIBRATION = ROOT / "evaluation" / "tool_coverage" / "judge_calibration.json"
COVERAGE_REPORT = ROOT / "evaluation" / "reports" / "tool_coverage" / "tool_coverage_latest.json"
OUT_DIR = ROOT / "evaluation" / "reports" / "tool_coverage"
JUDGE_MODEL = "gpt-6.2-sol"
JUDGE_EFFORT = "medium"
VERDICTS = ("correct", "incorrect", "not_answered", "uncertain")

GROUND_TRUTH = """【今天的日期】2026-11-19

【退款政策 v1】
- 退款须在首次订阅付款后 30 天内申请，且账户不能有未解决的争议。
- 退款获批后，通常 3 到 5 个工作日显示在银行账单上（最长 5 个工作日）。
- 30 天的起算点是首次订阅付款，不是后来的发票。

【退款政策 v2】
- 所有退款申请扣除 5% 的处理费。
- 退款申请最长可在购买后 60 天内提交。
- 退款退回原支付卡，3 到 5 个工作日到账。
- v2 没有写“有未解决争议则不能退款”这条规则，那是 v1 的规则，不能搬进 v2。

【订单数据，按客户】
- cust_101：订单 ORD-7001，已交付，商品 Enterprise SaaS User Pack (10)，总额 150.00 美元，20 天前下单；已付款，发票 INV-DEMO-7001 已开具。
- cust_102：订单 ORD-8002，商品 Developer API Key Pack，总额 25.00 美元，1 天前下单；已付款，发票未申请；物流：运输中，有“配送延误”异常，下一步是承运商调查，尚不能确认送达时间。订单还没有送达。
- cust_103：订单 ORD-9003，已交付，商品 Dedicated AWS Gateway Cluster 和 Enterprise Premium support SLA addon，总额 5400.00 美元，5 天前下单；已付款，发票 INV-DEMO-9003 已开具；保修权益：支持服务类权益生效中，不代表实体设备保修资格。

【物流数据（来自物流查询）】
- cust_101：ORD-7001 已送达，承运商 demo_carrier，运单号 DEMO-7001，无异常。
- cust_102：ORD-8002 运输中，承运商 demo_carrier，运单号 DEMO-8002，异常“配送延误”，下一步承运商调查。
- cust_103：ORD-9003 已送达，承运商 demo_carrier，运单号 DEMO-9003，无异常。

【退款与争议记录】
- 三位客户的订单数据里都没有退款申请或退款记录。
- cust_101 的历史工单里有一张 2026-11-04 已解决的工单（重复扣款，已退还重复发票的款项），它不是针对 ORD-7001 的退款申请。cust_102 没有历史工单；cust_103 只有一张已解决的 API 超时工单。
- 系统没有专门的争议查询工具；历史工单里没有未解决的争议。

【日期口径】
- 系统只有订单的下单日期和订单状态（已付款、已交付等），没有单独的付款日期，也没有送达日期。
- 把下单日期当作付款日期来推算 v1 的申请期限，视为合理做法，不算错误。
- 把下单日期说成送达日期，或编造系统里没有的日期，算错误。

【说明】回复引用的工具内容（订单状态、金额、承运商、运单号、有无退款记录等），与上面一致就不算编造；上面没有、也无法由上面推出的具体数据才算编造。"""

PROMPT = """你是客服回复的评审员。下面给出标准事实，以及若干条“客户问题 + 客服回复”。请逐条判断回复是否正确。

判断规则：
- correct：回答了客户问的问题，回复里的每个事实、数字和算式都与标准事实一致。
- incorrect：回复里有与标准事实矛盾的内容、算错的数、编造的订单信息，或声称已经替客户执行了退款等操作。
- not_answered：回复没有出错，但没有回答客户问的核心问题（例如如实说“无法确认”“请提供订单号”）。
- uncertain：标准事实不足以判断。
- 回复里没出现在标准事实中、也不与之矛盾的内容（例如“需要主管审批”这类说明），不算错，也不要因此扣分。
- 客户问的是某个订单，只看该客户自己的订单数据；回复引用别的客户的数据算 incorrect。
- 只依据标准事实，不凭常识补充。
- 先自己算一遍相关的数字，再判断。

每条回复只输出：id、verdict（上面四个值之一）、errors（发现的具体错误，没有就是空数组）、reason（40 字以内）。
只输出一个 JSON 数组，不要任何其他文字。

【标准事实】
"""


def run_codex(prompt: str) -> tuple[str, dict]:
    exe = shutil.which("codex") or "/opt/homebrew/bin/codex"
    with tempfile.TemporaryDirectory() as tmp:  # 空目录运行，模型读不到仓库
        out = Path(tmp) / "out.txt"
        proc = subprocess.run(
            [exe, "exec", "--skip-git-repo-check", "-s", "read-only", "-m", JUDGE_MODEL,
             "-c", f'model_reasoning_effort="{JUDGE_EFFORT}"', "--json", "-C", tmp, "-o", str(out), "-"],
            input=prompt, text=True, capture_output=True, timeout=900, check=True,
        )
        text = out.read_text(encoding="utf-8")
    usage = {}
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "turn.completed":
            usage = event.get("usage", {})
    return text, usage


def build_prompt(entries: list[dict]) -> str:
    blocks = []
    for entry in entries:
        blocks.append(
            f"【{entry['jid']}】客户 {entry['customer_id']}，适用政策 {entry['kb_version']}\n"
            f"客户问题：{entry['question']}\n客服回复：{entry['reply']}"
        )
    return PROMPT + GROUND_TRUTH + "\n\n【待评判】\n\n" + "\n\n".join(blocks)


def judge_entries(entries: list[dict], seed: int = 7) -> tuple[dict[str, dict], dict]:
    """打乱顺序、换中性编号后评判，返回 {原 key: 评判结果}。"""
    order = list(range(len(entries)))
    random.Random(seed).shuffle(order)
    shuffled = []
    for rank, index in enumerate(order, 1):
        shuffled.append({**entries[index], "jid": f"R{rank:02d}", "_key": entries[index]["key"]})
    text, usage = run_codex(build_prompt(shuffled))
    raw = json.loads(text[text.index("[") : text.rindex("]") + 1])
    by_jid = {item["id"]: item for item in raw if isinstance(item, dict)}
    results = {}
    for entry in shuffled:
        item = by_jid.get(entry["jid"], {})
        verdict = item.get("verdict") if item.get("verdict") in VERDICTS else "uncertain"
        results[entry["_key"]] = {
            "verdict": verdict,
            "errors": [str(e) for e in item.get("errors", [])],
            "reason": str(item.get("reason", "")),
            "judged": entry["jid"] in by_jid,
        }
    return results, usage


def calibrate() -> int:
    items = json.loads(CALIBRATION.read_text(encoding="utf-8"))["items"]
    entries = [{**item, "key": str(i)} for i, item in enumerate(items)]
    results, usage = judge_entries(entries)
    right = 0
    lines = ["# 评判模型校准", "", f"- 模型：{JUDGE_MODEL}；推理强度：{JUDGE_EFFORT}；样本 {len(items)} 条；用量：{usage}", ""]
    lines += ["| # | 来源 | 标准判断 | 评判结果 | 一致 | 说明 |", "|---|---|---|---|---|---|"]
    for i, item in enumerate(items):
        got = results[str(i)]
        ok = got["verdict"] == item["expected"]
        right += ok
        lines.append(f"| {i + 1} | {item['source']}：{item['note']} | {item['expected']} | {got['verdict']} | {'是' if ok else '**否**'} | {got['reason']} |")
    lines += ["", f"一致 {right} / {len(items)}"]
    for i, item in enumerate(items):
        got = results[str(i)]
        if got["verdict"] != item["expected"]:
            lines += ["", f"### 不一致 #{i + 1}：{item['note']}", f"- 回复：{item['reply']}", f"- 评判：{got['verdict']}；错误 {got['errors']}；{got['reason']}"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "judge_calibration_latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if right == len(items) else 1


def judge_report() -> int:
    report = json.loads(COVERAGE_REPORT.read_text(encoding="utf-8"))
    dataset = {c["id"]: c for c in json.loads((ROOT / "evaluation/tool_coverage/supportgpt_tool_coverage.json").read_text(encoding="utf-8"))["cases"]}
    entries = []
    for item in report["cases"]:
        for mode in ("react", "rules"):
            row = item[mode]
            case = dataset[row["id"]]
            entries.append({"key": f"{row['id']}|{mode}", "customer_id": case["customer_id"], "kb_version": case["kb_version"],
                            "question": case["query"], "reply": row["reply"]})
    results, usage = {}, {"input_tokens": 0, "output_tokens": 0}
    for start in range(0, len(entries), 30):  # 每次 30 条，避免一次输出过长
        chunk, chunk_usage = judge_entries(entries[start : start + 30], seed=start + 7)
        results.update(chunk)
        for field in usage:
            usage[field] += int(chunk_usage.get(field, 0))
    out = {"model": JUDGE_MODEL, "effort": JUDGE_EFFORT, "usage": usage, "results": results}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "judge_results_latest.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已评判 {len(results)} 条回复，用量 {usage}")
    write_comparison(report, results, usage)
    return 0


def write_comparison(report: dict, results: dict, usage: dict) -> None:
    """评判结果和关键事实检查对照，不一致的逐条列出。"""
    lines = ["# 回复评判（codex）与关键事实检查对照", "", f"- 评判模型 {JUDGE_MODEL}，推理强度 {JUDGE_EFFORT}；用量 {usage}", ""]
    table: dict[tuple, dict] = {}
    disagree = []
    for item in report["cases"]:
        for mode in ("react", "rules"):
            row = item[mode]
            judged = results[f"{row['id']}|{mode}"]
            bucket = table.setdefault((row["group"], mode), {})
            bucket[judged["verdict"]] = bucket.get(judged["verdict"], 0) + 1
            fact_ok = row["facts_ok"]
            judge_ok = judged["verdict"] == "correct"
            if fact_ok != judge_ok:
                disagree.append((row, mode, judged))
    lines += ["| 组 | 方式 | correct | incorrect | not_answered | uncertain |", "|---|---|---|---|---|---|"]
    for (group, mode), counts in sorted(table.items()):
        lines.append(f"| {group} | {mode} | " + " | ".join(str(counts.get(v, 0)) for v in VERDICTS) + " |")
    lines += ["", f"## 两种判断不一致的回复（{len(disagree)} 条）", ""]
    for row, mode, judged in disagree:
        lines += [f"### {row['id']} / {mode}", f"- 关键事实检查：{'通过' if row['facts_ok'] else '未通过'}；评判：{judged['verdict']}；错误 {judged['errors']}；{judged['reason']}",
                  f"- 回复：{row['reply'][:400]}", ""]
    (OUT_DIR / "judge_comparison_latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:20]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["calibrate", "judge"])
    args = parser.parse_args()
    sys.exit(calibrate() if args.command == "calibrate" else judge_report())
