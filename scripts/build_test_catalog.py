#!/usr/bin/env python3
"""生成本地测试分类页面 docs/test_catalog.html。

    python scripts/build_test_catalog.py

只读取仓库文件和 git 历史，不运行测试、不调用模型、不联网。
页面内容（数量、示例、最近变动、哈希是否一致）每次运行都重新计算。
"""
from __future__ import annotations

import ast
import hashlib
import html
import json
import random
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "test_catalog.html"
BASELINE = "evaluation/baseline/supportgpt_baseline_100.json"
GOLDEN = "evaluation/golden/support_qa_golden.json"
ONLINE_REG = "evaluation/online_regression/supportgpt_online_regression.json"
REPLY_CHECKS = "evaluation/online_checks/reply_checks.json"
RECORDING = "evaluation/decision_recordings/jev_baseline.json"
POLICY = "evaluation/quality_gate_policy.json"


def esc(text: object) -> str:
    return html.escape(str(text))


def load(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def sha256(rel: str) -> str:
    return hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()


def git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout
    except Exception:
        return ""


def recent_commits(paths: list[str], n: int = 5) -> list[tuple[str, str, str]]:
    out = git("log", f"-{n}", "--format=%ad|%h|%s", "--date=short", "--", *paths)
    rows = []
    for line in out.splitlines():
        d, h, s = line.split("|", 2)
        rows.append((d, h, s))
    return rows


def case_ids_at(rev: str, rel: str) -> set[str] | None:
    """某个提交时数据集里的用例 ID；取不到返回 None。"""
    raw = git("show", f"{rev}:{rel}")
    if not raw:
        return set()
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return {c.get("id") for c in data.get("cases", [])}


def dataset_changes(rel: str, n: int = 5) -> list[tuple[str, str, str, str]]:
    """每次提交对数据集用例的增减：(日期, 提交, 说明, 增删摘要)。"""
    rows = []
    for d, h, s in recent_commits([rel], n):
        before = case_ids_at(f"{h}^", rel)
        after = case_ids_at(h, rel)
        if before is None or after is None:
            diff = "无法解析"
        else:
            add, rem = len(after - before), len(before - after)
            diff = f"新增 {add} 条，删除 {rem} 条" if add or rem else "用例数不变（改了内容）"
        rows.append((d, h, s, diff))
    return rows


def changes_html(rows: list[tuple]) -> str:
    if not rows:
        return "<p class='muted'>没有取到 git 记录。</p>"
    body = []
    for r in rows:
        extra = f"<span class='tag'>{esc(r[3])}</span>" if len(r) > 3 else ""
        body.append(
            f"<li><span class='muted'>{esc(r[0])} {esc(r[1])}</span> {esc(r[2])} {extra}</li>"
        )
    return "<ul class='changes'>" + "".join(body) + "</ul>"


def example_case(c: dict, query_key: str = "query") -> str:
    exp = c.get("agent_expectations", {})
    bits = []
    for label, key in (("意图", "expected_intent"), ("部门", "expected_department")):
        if exp.get(key):
            bits.append(f"{label}={exp[key]}")
    if "should_escalate" in exp:
        bits.append("转人工" if exp["should_escalate"] else "不转人工")
    if exp.get("required_tools"):
        bits.append("必须调用 " + "、".join(exp["required_tools"]))
    if exp.get("forbidden_tools"):
        bits.append("禁止调用 " + "、".join(exp["forbidden_tools"]))
    ref = c.get("reference_answer", "")
    return (
        f"<div class='ex'><div class='q'>{esc(c.get(query_key, ''))}</div>"
        f"<div class='muted small'>{esc(c['id'])}</div>"
        f"<div class='small'>期望：{esc('；'.join(bits)) or '—'}</div>"
        + (f"<div class='small muted'>参考答案：{esc(ref)}</div>" if ref else "")
        + "</div>"
    )


def example_check(c: dict) -> str:
    bits = []
    for label, key in (
        ("状态", "status"),
        ("意图", "intent"),
        ("语言", "language"),
        ("必须包含其一", "must_include_any"),
    ):
        if c.get(key):
            v = c[key]
            bits.append(f"{label}={'/'.join(v) if isinstance(v, list) else v}")
    return (
        f"<div class='ex'><div class='q'>{esc(c.get('message', ''))}</div>"
        f"<div class='muted small'>{esc(c['id'])}</div>"
        f"<div class='small'>检查：{esc('；'.join(bits))}</div></div>"
    )


def pick(cases: list[dict], k: int, key=None) -> list[dict]:
    """固定种子抽样，保证每次生成的例子一样；优先覆盖不同分类。"""
    rnd = random.Random(7)
    if key is None:
        return rnd.sample(cases, min(k, len(cases)))
    seen, out = set(), []
    for c in rnd.sample(cases, len(cases)):
        g = key(c)
        if g not in seen:
            seen.add(g)
            out.append(c)
        if len(out) == k:
            break
    return out


def count_bars(counter: dict[str, int]) -> str:
    top = max(counter.values()) if counter else 1
    rows = []
    for k, v in sorted(counter.items(), key=lambda x: -x[1]):
        rows.append(
            f"<div class='bar'><span class='bl'>{esc(k)}</span>"
            f"<span class='bf' style='width:{v / top * 100:.0f}%'></span>"
            f"<span class='bn'>{v}</span></div>"
        )
    return "".join(rows)


def pytest_info() -> tuple[list[dict], int]:
    files = []
    total = 0
    for p in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        tests = [
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name.startswith("test_")
        ]
        doc = (ast.get_docstring(tree) or "").splitlines()
        files.append(
            {
                "name": p.name,
                "count": len(tests),
                "doc": doc[0] if doc else "",
                "tests": [
                    (t.name, (ast.get_docstring(t) or "").splitlines()[:1])
                    for t in tests
                ],
            }
        )
        total += len(tests)
    return files, total


def regression_ids() -> tuple[int, int]:
    """真实模型回归 regression 套件的 Baseline 用例数和线上补充用例数。"""
    tree = ast.parse(
        (ROOT / "src/evaluation/real_llm_regression.py").read_text(encoding="utf-8")
    )
    counts = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name):
            name = n.targets[0].id
            if name in ("REGRESSION_CASE_IDS", "ONLINE_REGRESSION_CASE_IDS"):
                counts[name] = len(n.value.elts)
    return counts.get("REGRESSION_CASE_IDS", 0), counts.get(
        "ONLINE_REGRESSION_CASE_IDS", 0
    )


def section(sid: str, title: str, badge: str, body: str) -> str:
    return (
        f"<section id='{sid}'><h2>{esc(title)} <span class='badge'>{esc(badge)}</span></h2>"
        f"{body}</section>"
    )


def kv(rows: list[tuple[str, str]]) -> str:
    return (
        "<table class='kv'>"
        + "".join(f"<tr><th>{esc(k)}</th><td>{v}</td></tr>" for k, v in rows)
        + "</table>"
    )


def build() -> str:
    baseline = load(BASELINE)
    bcases = baseline["cases"]
    policy = load(POLICY)
    rec = load(RECORDING)
    golden = load(GOLDEN)["cases"]
    online_reg = load(ONLINE_REG)["cases"]
    checks = load(REPLY_CHECKS)["cases"]
    pfiles, ptotal = pytest_info()
    reg_base, reg_online = regression_ids()

    # 数据集是否被改过
    base_sha = sha256(BASELINE)
    pol_sha = policy["dataset"]["sha256"]
    sha_ok = base_sha == pol_sha
    rec_sha = sha256(RECORDING)
    pol_rec = next(
        (
            r["value"]
            for r in policy["profiles"]["pull_request"]["requirements"]
            if r["name"] == "decision_recording_sha256"
        ),
        "",
    )
    rec_ok = rec_sha == pol_rec

    sections = []

    # ---- 总览 ----
    sections.append(
        section(
            "overview",
            "一张表分清",
            "先看这个",
            f"""
<p>关键点：<b>Baseline 数据集只有一份（100 条）</b>，PR 门禁和手动真实门禁都是拿它去跑，区别只在“用什么模型”和“谁来触发”。
真实问题回归、线上回复检查是另外两份独立的数据。pytest 是代码测试，不是数据集。</p>
<table class='grid'>
<tr><th>名称</th><th>测什么</th><th>用什么模型</th><th>数据在哪</th><th>条数</th><th>什么时候跑</th><th>花钱吗</th></tr>
<tr><td><a href='#pytest'>① pytest</a></td><td>代码逻辑：状态机、Tool 治理、RAG、安全检测、API 等</td><td>Mock</td><td>tests/</td><td>{ptotal} 个用例 / {len(pfiles)} 个文件</td><td>本地随时；CI 每次 PR</td><td>不花</td></tr>
<tr><td><a href='#baseline'>② Baseline 数据集</a></td><td>（这是数据，不是一种测试）100 条固定客服问题和期望结果</td><td>—</td><td>{BASELINE}</td><td>{len(bcases)}</td><td>被③和④使用</td><td>—</td></tr>
<tr><td><a href='#pr'>③ PR Mock 门禁</a></td><td>整条 Workflow 的分类、部门、Tool、转人工、审批是否符合期望</td><td>Mock，Jev 分类读录音</td><td>用②</td><td>{len(bcases)}</td><td>每次 PR 自动（CI）</td><td>不花</td></tr>
<tr><td><a href='#release'>④ 手动真实门禁</a></td><td>同样的 100 条，换真实模型跑</td><td>真实模型</td><td>用②</td><td>{len(bcases)}</td><td>发布前在 GitHub 手动触发</td><td>花钱</td></tr>
<tr><td><a href='#regress'>⑤ 真实问题回归</a></td><td>线上真实出过问题的用例 + 精选 Baseline 用例，用真实模型验证改动</td><td>真实模型</td><td>{ONLINE_REG} + Baseline 里挑的 {reg_base} 条</td><td>{reg_base + reg_online}</td><td>改 Prompt/Agent/意图后手动跑</td><td>花钱</td></tr>
<tr><td><a href='#online'>⑥ 线上回复检查</a></td><td>已部署的线上服务回复质量（状态、意图、语言、关键词）</td><td>线上真实模型</td><td>{REPLY_CHECKS}</td><td>{len(checks)}</td><td>部署后手动</td><td>花钱，会建临时工单并撤回</td></tr>
<tr><td><a href='#golden'>⑦ Golden 小数据集</a></td><td>早期的 RAG/Agent 评测样本，被 pytest 和 run_agent_eval.py 引用</td><td>Mock</td><td>{GOLDEN}</td><td>{len(golden)}</td><td>随 pytest</td><td>不花</td></tr>
</table>
""",
        )
    )

    # ---- pytest ----
    ex_tests = []
    rnd = random.Random(7)
    with_doc = [
        (f["name"], n, d[0])
        for f in pfiles
        for n, d in f["tests"]
        if d
    ]
    for fname, n, d in rnd.sample(with_doc, min(6, len(with_doc))):
        ex_tests.append(
            f"<div class='ex'><div class='q'>{esc(d)}</div>"
            f"<div class='muted small'>{esc(fname)} :: {esc(n)}</div></div>"
        )
    rows = "".join(
        f"<tr><td>{esc(f['name'])}</td><td>{f['count']}</td><td>{esc(f['doc'])}</td></tr>"
        for f in sorted(pfiles, key=lambda x: -x["count"])
    )
    sections.append(
        section(
            "pytest",
            "① pytest",
            f"{ptotal} 个用例",
            kv(
                [
                    ("测什么", "代码本身对不对：确定性逻辑（状态机、权限、Risk Engine、Guard、RAG 检索、API 返回等）。不判断“回答好不好”。"),
                    ("模型", "全部 Mock，分类固定为规则模式（tests/conftest.py 强制设置）。"),
                    ("怎么跑", "<code>python -m pytest -q</code>；单文件 <code>python -m pytest tests/test_xxx.py -q</code>"),
                    ("失败意味着", "代码行为被改坏了。与回答质量无关。"),
                    ("数量说明", "按函数名以 test_ 开头统计，参数化用例按 1 个算，实际运行数会更多。"),
                ]
            )
            + "<h3>例子（随机抽 6 个有说明的用例）</h3>"
            + "".join(ex_tests)
            + "<h3>最近变动（tests/ 目录）</h3>"
            + changes_html(recent_commits(["tests"], 8))
            + f"<details><summary>全部 {len(pfiles)} 个测试文件（按用例数排序）</summary>"
            f"<table class='grid'><tr><th>文件</th><th>用例数</th><th>说明</th></tr>{rows}</table></details>",
        )
    )

    # ---- baseline ----
    cat = {}
    for c in bcases:
        cat[c["category"]] = cat.get(c["category"], 0) + 1
    tags = {}
    for c in bcases:
        for t in c.get("tags", []):
            tags[t] = tags.get(t, 0) + 1
    sha_note = (
        "<span class='ok'>一致</span>" if sha_ok else "<span class='bad'>不一致：数据集被改过，PR 门禁会失败</span>"
    )
    sections.append(
        section(
            "baseline",
            "② Baseline 数据集",
            f"{len(bcases)} 条 · v{baseline['version']}",
            kv(
                [
                    ("是什么", "100 条固定的客服问题，每条写明期望的意图、部门、必须/禁止调用的 Tool、是否转人工、是否需要审批。③和④都拿它当考卷。"),
                    ("规则", "不能为了通过而降低期望；数据集 SHA256 固定在 quality_gate_policy.json，改了门禁就失败。"),
                    ("与策略文件的哈希", sha_note),
                    ("文件", BASELINE),
                ]
            )
            + "<h3>按分类的条数</h3>"
            + count_bars(cat)
            + "<h3>按标签的条数（一条可有多个标签）</h3>"
            + count_bars(tags)
            + "<h3>例子（每种分类各取一条）</h3>"
            + "".join(example_case(c) for c in pick(bcases, 5, key=lambda c: c["category"]))
            + "<h3>最近变动</h3>"
            + changes_html(dataset_changes(BASELINE)),
        )
    )

    # ---- PR 门禁 ----
    pr_req = policy["profiles"]["pull_request"]["requirements"]
    req_rows = "".join(
        f"<tr><td>{esc(r['name'])}</td><td>{esc(r['operator'])} {esc(r['value'])[:40]}</td></tr>"
        for r in pr_req
        if not str(r["value"]).startswith(("9cf", "1c7"))
    )
    rec_note = (
        "<span class='ok'>一致</span>" if rec_ok else "<span class='bad'>不一致：录音被改过或重录后没更新策略哈希</span>"
    )
    sections.append(
        section(
            "pr",
            "③ PR Mock 门禁",
            "每次 PR 自动 · 免费",
            kv(
                [
                    ("测什么", "把 Baseline 的 100 条完整走一遍 Workflow（Analyzer → Skill → Tool/RAG → Resolver → QA → 转人工 → 审批），对照期望统计各项准确率。"),
                    ("模型", "回复和 QA 评判用 Mock；意图分类读 Jev 的录音（不调用 Jev，不需要 Key）。"),
                    ("通过标准", "意图、部门、必需 Tool 命中、人工升级、审批准确率都要 100%，禁止 Tool 违规率 0，不允许任何失败用例。"),
                    ("命令", "<code>python scripts/run_ci_quality_gate.py</code>"),
                    ("Jev 录音", f"{RECORDING}：{rec['entry_count']} 条，录制于 {esc(rec['recorded_at'])}，模型 {esc(rec['model'])}，问题集 {esc(next(iter(rec['question_sets'].values()), ''))[:60]}"),
                    ("录音哈希", rec_note),
                    ("它测不到什么", "真实模型的回答质量和真实模型的分类变化。Mock 通过 ≠ 真实模型下通过。"),
                ]
            )
            + "<h3>门禁要求</h3><table class='grid'><tr><th>项目</th><th>要求</th></tr>"
            + req_rows
            + "</table><h3>最近变动（策略文件 + 录音）</h3>"
            + changes_html(recent_commits([POLICY, RECORDING, "scripts/run_ci_quality_gate.py"], 6)),
        )
    )

    # ---- 手动真实门禁 ----
    rel = policy["profiles"]["release"]["requirements"]
    rel_rows = "".join(
        f"<tr><td>{esc(r['name'])}</td><td>{esc(r['operator'])} {esc(r['value'])[:40]}</td></tr>"
        for r in rel
    )
    sections.append(
        section(
            "release",
            "④ 手动真实门禁",
            "发布前手动 · 花钱",
            kv(
                [
                    ("测什么", "和③同一份 100 条，但用真实模型跑，检验真实回答下分类、Tool、转人工是否仍达标。"),
                    ("触发", "GitHub Actions 的 Release Quality Gate（workflow_dispatch），需勾选 confirm_live 并设置调用上限（默认 300）。"),
                    ("命令", "<code>python scripts/run_baseline_eval.py --dry-run</code>（先看预估）；<code>--confirm-live</code> 才真调用。"),
                    ("与③的区别", "数据相同，只有模型不同。③绿 ≠ ④绿。"),
                ]
            )
            + "<h3>发布门禁要求</h3><table class='grid'><tr><th>项目</th><th>要求</th></tr>"
            + rel_rows
            + "</table><h3>最近变动</h3>"
            + changes_html(recent_commits([".github/workflows/release-quality-gate.yml", "scripts/run_baseline_eval.py", "scripts/check_quality_gate.py"], 5)),
        )
    )

    # ---- 真实问题回归 ----
    sections.append(
        section(
            "regress",
            "⑤ 真实问题回归",
            f"{reg_base + reg_online} 条 · 花钱",
            kv(
                [
                    ("测什么", "改了 Prompt、Agent 节点、Tool 选择、意图识别后，用真实模型验证关键用例没有退化。"),
                    ("组成", f"Baseline 里精选 {reg_base} 条（REGRESSION_CASE_IDS）+ 线上真实模型测试发现问题后补的 {reg_online} 条（本节数据集）。"),
                    ("命令", "<code>python scripts/run_real_llm_regression.py --suite regression --dry-run</code> 看预估；<code>--confirm-live</code> 才真调用。"),
                    ("补充数据文件", f"{ONLINE_REG}（独立于固定 SHA256 的 Baseline，可以随时加）"),
                    ("加用例的时机", "线上又出了因真实模型行为导致的问题，就补一条。"),
                ]
            )
            + "<h3>补充的线上问题用例（全部）</h3>"
            + "".join(example_case(c) for c in online_reg)
            + "<h3>最近变动</h3>"
            + changes_html(dataset_changes(ONLINE_REG)),
        )
    )

    # ---- 线上回复检查 ----
    langs = {}
    for c in checks:
        langs[c.get("language", "未指定")] = langs.get(c.get("language", "未指定"), 0) + 1
    sections.append(
        section(
            "online",
            "⑥ 线上回复检查",
            f"{len(checks)} 条 · 花钱",
            kv(
                [
                    ("测什么", "已部署到 Railway 的线上服务：回复状态、转人工、意图、语言、关键内容。检查的是线上整条链路，不是代码。"),
                    ("命令", "<code>python scripts/run_online_reply_check.py --api-url &lt;Railway 域名&gt; --dry-run</code>；<code>--confirm-live</code> 才发请求"),
                    ("注意", "每条会在线上建 1 张工单，请求带 X-SupportGPT-Test 头，结束后自动撤回；约 3 分钟。"),
                    ("语言分布", esc(langs)),
                ]
            )
            + "<h3>例子</h3>"
            + "".join(example_check(c) for c in pick(checks, 5))
            + "<h3>最近变动</h3>"
            + changes_html(dataset_changes(REPLY_CHECKS)),
        )
    )

    # ---- golden ----
    sections.append(
        section(
            "golden",
            "⑦ Golden 小数据集",
            f"{len(golden)} 条",
            kv(
                [
                    ("是什么", "早期基于种子知识库写的 RAG/Agent 评测样本，被 tests/test_agent_evaluation.py 和 scripts/run_agent_eval.py 引用。不属于任何门禁。"),
                    ("文件", GOLDEN),
                ]
            )
            + "<h3>例子</h3>"
            + "".join(example_case(c) for c in pick(golden, 3))
            + "<h3>最近变动</h3>"
            + changes_html(dataset_changes(GOLDEN)),
        )
    )

    nav = "".join(
        f"<a href='#{i}'>{esc(t)}</a>"
        for i, t in (
            ("overview", "总览"),
            ("pytest", "① pytest"),
            ("baseline", "② Baseline"),
            ("pr", "③ PR 门禁"),
            ("release", "④ 真实门禁"),
            ("regress", "⑤ 真实回归"),
            ("online", "⑥ 线上检查"),
            ("golden", "⑦ Golden"),
        )
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SupportGPT 测试分类</title>
<style>
:root{{--bg:#fafaf8;--fg:#222;--muted:#777;--line:#ddd;--card:#fff;--acc:#2b5fd9;--ok:#1a7f37;--bad:#c62828}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1b1b1a;--fg:#e6e6e3;--muted:#9a9a95;--line:#3a3a38;--card:#242423;--acc:#7aa2ff;--ok:#56d364;--bad:#ff7b72}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"PingFang SC",sans-serif}}
nav{{position:sticky;top:0;background:var(--card);border-bottom:1px solid var(--line);padding:8px 16px;display:flex;gap:14px;flex-wrap:wrap;z-index:1}}
nav a{{color:var(--acc);text-decoration:none;font-size:14px}}
main{{max-width:1000px;margin:0 auto;padding:16px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 20px 16px;margin:16px 0}}
h1{{font-size:22px;margin:16px 0 0}} h2{{font-size:18px}} h3{{font-size:15px;margin:18px 0 6px}}
.badge{{font-size:12px;font-weight:400;border:1px solid var(--line);border-radius:10px;padding:1px 8px;color:var(--muted);margin-left:6px}}
table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top;font-size:13px}}
table.kv th{{width:130px;background:var(--bg);font-weight:600}}
.grid th{{background:var(--bg)}}
.ex{{border-left:3px solid var(--acc);padding:2px 10px;margin:8px 0}} .q{{font-weight:600}}
.muted{{color:var(--muted)}} .small{{font-size:12.5px}} code{{background:var(--bg);padding:1px 5px;border-radius:4px;font-size:12.5px}}
.ok{{color:var(--ok);font-weight:600}} .bad{{color:var(--bad);font-weight:600}}
.changes{{padding-left:18px;margin:4px 0}} .tag{{font-size:12px;border:1px solid var(--line);border-radius:8px;padding:0 6px;margin-left:4px}}
.bar{{display:flex;align-items:center;gap:8px;font-size:12.5px;margin:2px 0}} .bl{{width:150px;text-align:right}}
.bf{{height:10px;background:var(--acc);border-radius:2px;display:inline-block;min-width:2px}}
nav .sp{{flex:1}} button{{font:inherit;font-size:13px;padding:2px 12px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);cursor:pointer}}
details{{margin-top:10px}} summary{{cursor:pointer;color:var(--acc)}}
</style></head><body>
<nav>{nav}<span class="sp"></span><span id="rmsg" class="muted small"></span><button id="rbtn" onclick="refreshCatalog()">更新</button></nav>
<main>
<h1>SupportGPT 测试分类</h1>
<p class="muted">由 scripts/build_test_catalog.py 生成于 {datetime.now().strftime("%Y-%m-%d %H:%M")}。数量、例子、最近变动都是生成时从仓库读的；每天 05:00 自动更新一次。</p>
{''.join(sections)}
</main>
<script>
async function refreshCatalog() {{
  const b = document.getElementById('rbtn'), m = document.getElementById('rmsg');
  if (location.protocol === 'file:') {{
    m.textContent = '需通过本地服务打开：运行 python3 scripts/test_catalog_server.py，再访问 http://127.0.0.1:8765';
    return;
  }}
  b.disabled = true; m.textContent = '更新中…';
  try {{
    const r = await fetch('/refresh', {{method: 'POST'}});
    if (!r.ok) throw new Error(r.status);
    location.reload();
  }} catch (e) {{
    m.textContent = '更新失败：' + e.message; b.disabled = false;
  }}
}}
</script>
</body></html>
"""


def main() -> None:
    OUT.write_text(build(), encoding="utf-8")
    print(f"已生成 {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
