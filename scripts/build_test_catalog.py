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
import os
import re
import random
import shutil
import tempfile
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "test_catalog.html"
OUT_OTHER = ROOT / "docs" / "test_catalog_other.html"
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



CSS = """
:root{--bg:#f6f6f4;--fg:#1f1f1e;--muted:#8a8a85;--line:#e4e4e0;--card:#fff;--acc:#2f5bd8;--free:#1a7f37;--paid:#b45309}
@media (prefers-color-scheme:dark){:root{--bg:#171716;--fg:#ececea;--muted:#8d8d88;--line:#2e2e2c;--card:#222221;--acc:#8aa9ff;--free:#56d364;--paid:#f0a050}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"PingFang SC",sans-serif}
main{max-width:860px;margin:0 auto;padding:32px 20px 60px}
header{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:6px}
h1{font-size:24px;margin:0;font-weight:650}
.sp{flex:1}.lk{font-size:13px;color:var(--acc);text-decoration:none}.muted{color:var(--muted)}.small{font-size:13px}
button{font:inherit;font-size:13px;padding:4px 14px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);cursor:pointer}
button:hover{border-color:var(--acc)}
h2{font-size:13px;font-weight:600;color:var(--muted);margin:32px 0 10px;letter-spacing:.04em}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:10px 0}
.card>summary{list-style:none;cursor:pointer;padding:16px 18px;display:grid;grid-template-columns:1fr auto;gap:2px 16px;align-items:center}
.card>summary::-webkit-details-marker{display:none}
.name{font-size:16px;font-weight:600}
.num{font-size:26px;font-weight:650;grid-row:1/3;grid-column:2;text-align:right;line-height:1.1}
.num small{display:block;font-size:12px;font-weight:400;color:var(--muted)}
.what{color:var(--fg);font-size:14px}
.tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px;grid-column:1}
.tag{font-size:12px;padding:1px 9px;border-radius:10px;border:1px solid var(--line);color:var(--muted)}
.tag.free{color:var(--free);border-color:var(--free)}.tag.paid{color:var(--paid);border-color:var(--paid)}
.last{grid-column:1/3;font-size:12.5px;color:var(--muted);margin-top:8px}
.body{padding:0 18px 16px;border-top:1px solid var(--line)}
.body h3{font-size:13px;color:var(--muted);font-weight:600;margin:14px 0 6px}
.ex{margin:6px 0;padding-left:10px;border-left:2px solid var(--line)}
.ex .q{font-size:14px}.ex .e{font-size:12.5px;color:var(--muted)}
.ch{margin:0;padding-left:16px;font-size:13px}.ch li{margin:2px 0}
code{background:var(--bg);padding:1px 6px;border-radius:4px;font-size:12.5px}
.group{border:1px dashed var(--line);border-radius:14px;padding:4px 14px 8px;margin:10px 0}
.group>.gt{font-size:13px;margin:10px 0 0}
.bad{color:#c62828;font-weight:600}
.intro{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:6px 20px 14px;margin:14px 0}
.intro h3{font-size:13px;color:var(--muted);font-weight:600;margin:14px 0 4px}
.intro p{margin:4px 0;font-size:14px}
.when{display:grid;grid-template-columns:auto 1fr;gap:4px 14px;font-size:14px;margin:4px 0}
.when b{font-weight:600;white-space:nowrap}
"""

JS = """
async function refreshCatalog(){
  const b=document.getElementById('rbtn'),m=document.getElementById('rmsg');
  if(location.protocol==='file:'){m.textContent='需先运行 python3 scripts/test_catalog_server.py，再打开 http://127.0.0.1:8765';return;}
  b.disabled=true;m.textContent='更新中…';
  try{const r=await fetch('/refresh',{method:'POST'});if(!r.ok)throw new Error(r.status);location.reload();}
  catch(e){m.textContent='更新失败：'+e.message;b.disabled=false;}
}
"""


def tag(text: str, kind: str = "") -> str:
    return f"<span class='tag {kind}'>{esc(text)}</span>"


def ex_case(c: dict) -> str:
    exp = c.get("agent_expectations", {})
    bits = []
    if exp.get("expected_intent"):
        bits.append("意图 " + exp["expected_intent"])
    if "should_escalate" in exp:
        bits.append("转人工" if exp["should_escalate"] else "不转人工")
    return (
        f"<div class='ex'><div class='q'>{esc(c.get('query', ''))}</div>"
        f"<div class='e'>期望：{esc(' · '.join(bits)) or '—'}</div></div>"
    )


def ex_check(c: dict) -> str:
    bits = []
    if c.get("status"):
        bits.append("状态 " + "/".join(c["status"]))
    if c.get("intent"):
        bits.append("意图 " + "/".join(c["intent"]))
    if c.get("language"):
        bits.append("语言 " + c["language"])
    return (
        f"<div class='ex'><div class='q'>{esc(c.get('message', ''))}</div>"
        f"<div class='e'>检查：{esc(' · '.join(bits))}</div></div>"
    )


def short(msg: str) -> str:
    """提交说明去掉 feat(xx): 前缀，只留正文。"""
    return msg.split(": ", 1)[1] if ": " in msg[:30] else msg


def last_line(rows: list[tuple]) -> str:
    if not rows:
        return "最近变动：—"
    r = rows[0]
    extra = f"（{r[3]}）" if len(r) > 3 else ""
    return f"最近变动 {r[0]}：{short(r[2])}{extra}"


def changes(rows: list[tuple], n: int = 3) -> str:
    if not rows:
        return ""
    items = "".join(
        f"<li><span class='muted'>{esc(r[0])}</span> {esc(short(r[2]))}"
        + (f" <span class='muted'>（{esc(r[3])}）</span>" if len(r) > 3 else "")
        + "</li>"
        for r in rows[:n]
    )
    return f"<h3>最近变动</h3><ul class='ch'>{items}</ul>"


def card(name, what, tags, num, unit, last, body) -> str:
    return (
        f"<details class='card'><summary>"
        f"<span class='name'>{esc(name)}</span>"
        f"<span class='num'>{num}<small>{esc(unit)}</small></span>"
        f"<span class='what'>{esc(what)}</span>"
        f"<span class='tags'>{tags}</span>"
        f"<span class='last'>{esc(last)}</span></summary>"
        f"<div class='body'>{body}</div></details>"
    )


AI_CACHE = ROOT / "docs" / "test_catalog_ai.json"
AI_MODEL = "gpt-6.2-sol"
AI_CACHE_OTHER = ROOT / "docs" / "test_catalog_ai_other.json"
AI_PROMPT = """你在给一个不懂代码的项目负责人写“测试分类”页面的说明。下面 JSON 里每一项是一类测试/检查的事实（简介、数量、最近几次提交）。
对每一项输出三个字段：
- summary（最近变动）：这些提交让这项测试发生了什么变化、对测试结果有什么影响，60 到 120 字。给出的每条提交都改动过该项测试的文件或数据；没有提交时写“近期无改动”。
- status（现状）：这项测试现在是什么状态，80 字以内，只依据给定事实。
- blind（测不到什么）：这项测试发现不了哪类问题，40 到 80 字；可结合简介和常识推断，但不要编造具体数字。
写法要求：用大白话，像当面解释；技术名词（Mock、Workflow、哈希、Tool、CI、Jev 等）第一次出现时用括号简单说明它是什么；只用给定事实，不编造；直说事实，不用比喻、金句和总结式收尾。
只输出一个 JSON 对象，键与输入相同，值是 {"summary": "...", "status": "...", "blind": "..."}，不要任何其他文字。

事实：
"""


def ai_notes(facts: dict, cache_path: Path = AI_CACHE) -> dict:
    """用本机 codex 把最近变动写成人话；事实没变就用缓存。失败时返回空，页面退回原始提交说明。"""
    key = hashlib.sha256(
        json.dumps([AI_MODEL, AI_PROMPT, facts], ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        if cache.get("key") == key:
            return cache["notes"]
    except Exception:
        pass
    exe = shutil.which("codex") or "/opt/homebrew/bin/codex"
    env = {**os.environ, "PATH": os.environ.get("PATH", "") + ":/opt/homebrew/opt/node@20/bin:/opt/homebrew/bin:/usr/local/bin"}
    try:
        with tempfile.TemporaryDirectory() as tmp:  # 空目录运行，模型读不到仓库
            out = Path(tmp) / "out.txt"
            subprocess.run(
                [exe, "exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only",
                 "-m", AI_MODEL, "-C", tmp, "-o", str(out), "-"],
                input=AI_PROMPT + json.dumps(facts, ensure_ascii=False, indent=1),
                text=True, capture_output=True, timeout=300, env=env, check=True,
            )
            text = out.read_text(encoding="utf-8")
        notes = json.loads(text[text.index("{") : text.rindex("}") + 1])
        notes = {
            k: {f: str(v.get(f, "")) for f in ("summary", "status", "blind")}
            for k, v in notes.items()
            if k in facts and isinstance(v, dict)
        }
    except Exception as exc:
        print(f"codex 生成失败，页面使用原始提交说明：{exc}")
        return {}
    cache_path.write_text(
        json.dumps({"key": key, "model": AI_MODEL, "notes": notes}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return notes


def rows_facts(rows):
    return [{"date": r[0], "msg": short(r[2]), **({"dataset_diff": r[3]} if len(r) > 3 else {})} for r in rows[:3]]


def note_L(notes: dict, key: str, fallback: str) -> str:
    n = notes.get(key, {}).get("summary")
    return f"最近变动：{n}" if n else fallback


def note_body(notes: dict, key: str) -> str:
    """卡片展开后顶部的“当前状态”和“测不到什么”。"""
    n = notes.get(key, {})
    out = ""
    if n.get("status"):
        out += f"<h3>当前状态</h3><p class='small'>{esc(n['status'])}</p>"
    if n.get("blind"):
        out += f"<h3>测不到什么</h3><p class='small'>{esc(n['blind'])}</p>"
    return out


NOTE_FOOT = '<p class="muted small">“最近变动”“当前状态”“测不到什么”由 ' + AI_MODEL + " 根据提交记录和简介生成，仅供参考。</p>"


def build() -> str:
    baseline = load(BASELINE)
    bcases = baseline["cases"]
    policy = load(POLICY)
    rec = load(RECORDING)
    online_reg = load(ONLINE_REG)["cases"]
    checks = load(REPLY_CHECKS)["cases"]
    pfiles, ptotal = pytest_info()
    reg_base, reg_online = regression_ids()

    sha_ok = sha256(BASELINE) == policy["dataset"]["sha256"]
    pol_rec = next(
        (r["value"] for r in policy["profiles"]["pull_request"]["requirements"]
         if r["name"] == "decision_recording_sha256"), "")
    rec_ok = sha256(RECORDING) == pol_rec
    warn = ""
    if not sha_ok:
        warn += "<p class='bad'>Baseline 数据集与门禁策略里的哈希不一致，PR 门禁会失败。</p>"
    if not rec_ok:
        warn += "<p class='bad'>Jev 录音与门禁策略里的哈希不一致。</p>"

    base_changes = dataset_changes(BASELINE)
    base_ex = "<h3>例子</h3>" + "".join(
        ex_case(c) for c in pick(bcases, 3, key=lambda c: c["category"])
    )

    # pytest
    pt_rows = recent_commits(["tests"], 3)
    pt_body = (
        "<h3>怎么跑</h3><p class='small'><code>python -m pytest -q</code>　单文件："
        "<code>python -m pytest tests/test_xxx.py -q</code></p>"
        "<h3>例子</h3>"
        + "".join(
            f"<div class='ex'><div class='q'>{esc(d)}</div><div class='e'>{esc(f)}</div></div>"
            for f, d in random.Random(7).sample(
                [(f['name'], t[1][0]) for f in pfiles for t in f['tests'] if t[1]], 3
            )
        )
        + f"<p class='small muted'>共 {len(pfiles)} 个测试文件。失败说明代码行为被改坏，与回答好坏无关。</p>"
        + changes(pt_rows)
    )

    # PR 门禁
    pr_body = (
        "<h3>测什么</h3><p class='small'>把 100 条考卷完整走一遍 Workflow，对照期望统计意图、部门、Tool、转人工、审批的准确率，要求全部 100%。</p>"
        f"<p class='small'>意图分类读 Jev 录音（{rec['entry_count']} 条，录于 {esc(rec['recorded_at'])}），不调用付费模型。"
        "测不到真实模型下的表现。</p>"
        "<h3>命令</h3><p class='small'><code>python scripts/run_ci_quality_gate.py</code></p>"
        + warn + base_ex
        + changes(recent_commits([POLICY, RECORDING, "scripts/run_ci_quality_gate.py"], 3))
    )

    rel_body = (
        "<h3>测什么</h3><p class='small'>同一份 100 条考卷，换真实模型。检验真实回答下分类、Tool、转人工是否仍达标；"
        "额外要求延迟、Token 用量在限额内，转人工/审批/通过率不低于 99%。</p>"
        "<h3>怎么触发</h3><p class='small'>GitHub Actions 的 Release Quality Gate，手动勾选 confirm_live 并设调用上限（默认 300）。"
        "本地：<code>python scripts/run_baseline_eval.py --dry-run</code> 先看预估。</p>"
        + changes(recent_commits([".github/workflows/release-quality-gate.yml", "scripts/run_baseline_eval.py"], 3))
    )

    reg_body = (
        f"<h3>组成</h3><p class='small'>Baseline 里精选 {reg_base} 条 + 线上发现问题后补的 {reg_online} 条。"
        "改了 Prompt、Agent 节点、Tool 选择、意图识别后用它验证。</p>"
        "<h3>命令</h3><p class='small'><code>python scripts/run_real_llm_regression.py --suite regression --dry-run</code>"
        "　确认后加 <code>--confirm-live</code></p>"
        "<h3>线上补充用例</h3>" + "".join(ex_case(c) for c in online_reg[:3])
        + f"<p class='small muted'>其余 {len(online_reg) - 3} 条见 {ONLINE_REG}</p>"
        + changes(dataset_changes(ONLINE_REG))
    )

    onl_body = (
        "<h3>测什么</h3><p class='small'>已部署的线上服务：回复状态、转人工、意图、语言、关键词。每条在线上建 1 张测试工单，结束后自动撤回，约 3 分钟。</p>"
        "<h3>命令</h3><p class='small'><code>python scripts/run_online_reply_check.py --api-url &lt;Railway 域名&gt; --dry-run</code></p>"
        "<h3>例子</h3>" + "".join(ex_check(c) for c in pick(checks, 3))
        + changes(dataset_changes(REPLY_CHECKS))
    )

    t_last = lambda rows: last_line(rows)

    facts = {
        "pytest": {"简介": "代码逻辑对不对：状态机、权限、Risk Engine、安全检测、RAG、API，全部 Mock，不判断回答好坏", "用例数": ptotal, "测试文件数": len(pfiles), "最近提交": rows_facts(pt_rows)},
        "pr": {"简介": "用 Mock 模型和 Jev 录音跑固定 100 条考卷，检查分类、Tool、转人工、审批，每次 PR 自动跑", "考卷条数": len(bcases), "数据集哈希与策略一致": sha_ok, "Jev录音哈希与策略一致": rec_ok,
               "考卷最近提交": rows_facts(base_changes),
               "门禁策略和录音最近提交": rows_facts(recent_commits([POLICY, RECORDING, "scripts/run_ci_quality_gate.py"], 3))},
        "release": {"简介": "同样 100 条考卷换真实模型跑，发版前在 GitHub 手动触发，花钱", "最近提交": rows_facts(recent_commits([".github/workflows/release-quality-gate.yml", "scripts/run_baseline_eval.py"], 3))},
        "regress": {"简介": "线上出过问题的用例加精选考卷用例，用真实模型验证改动没有退化，手动跑，花钱", "用例数": reg_base + reg_online, "最近提交": rows_facts(dataset_changes(ONLINE_REG, 3))},
        "online": {"简介": "对已部署的线上服务发请求，检查状态、意图、语言、关键词，手动跑，花钱", "用例数": len(checks), "最近提交": rows_facts(dataset_changes(REPLY_CHECKS, 3))},
    }
    notes = ai_notes(facts)

    L = lambda key, fb: note_L(notes, key, fb)
    S = lambda key: note_body(notes, key)
    html_ = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SupportGPT 测试分类</title><style>{CSS}</style></head><body><main>
<header><h1>SupportGPT 测试分类</h1><a class="lk" href="test_catalog_other.html">其他检查 →</a><span class="sp"></span>
<span id="rmsg" class="muted small"></span><button id="rbtn" onclick="refreshCatalog()">更新</button></header>
<p class="muted small">更新于 {datetime.now().strftime("%Y-%m-%d %H:%M")}，每天 05:00 自动更新。点卡片展开详情。</p>
<div class="intro">
<h3>一共 5 项测试，分两层</h3>
<p><b>不花钱，Mock 模型，改代码就能跑</b>：pytest 测代码逻辑；PR Mock 门禁用 100 条固定考卷测整条处理流程（分类、调 Tool、转人工、审批）。</p>
<p><b>花钱，真实模型，手动跑</b>：手动真实门禁（同一份 100 条考卷）、真实问题回归（线上出过问题的用例）、线上回复检查（对已部署的服务发请求）。</p>
<p class="muted small">Mock 测试全过，不代表真实模型下也过。这是需要后面三项的原因。</p>
<h3>什么时候跑哪个</h3>
<div class="when">
<b>改了代码逻辑</b><span>pytest；PR 时 PR Mock 门禁自动跑</span>
<b>改了 Prompt / Agent / 意图</b><span>以上两项，再加真实问题回归</span>
<b>准备发版</b><span>手动真实门禁</span>
<b>部署之后</b><span>线上回复检查</span>
</div></div>

<h2>代码测试</h2>
{card("pytest", "代码逻辑对不对：状态机、权限、Risk Engine、安全检测、RAG、API。全部 Mock。",
      tag("Mock") + tag("免费", "free") + tag("每次 PR / 本地随时"), ptotal, "个用例", L("pytest", t_last(pt_rows)), S("pytest") + pt_body)}

<h2>同一份 100 条考卷（Baseline）</h2>
<div class="group">
<p class="gt muted small">下面两项用的是同一份数据，只是模型不同，所以“PR 绿”不等于“真实门禁绿”。</p>
{card("PR Mock 门禁", "用 Mock 模型和 Jev 录音跑 100 条，检查分类、Tool、转人工、审批。",
      tag("Mock + 录音") + tag("免费", "free") + tag("每次 PR 自动"), len(bcases), "条", L("pr", t_last(base_changes)), S("pr") + pr_body)}
{card("手动真实门禁", "同样 100 条，用真实模型跑。发版前的最后一关。",
      tag("真实模型") + tag("花钱", "paid") + tag("发版前手动"), len(bcases), "条", L("release", t_last(recent_commits([".github/workflows/release-quality-gate.yml"], 1))), S("release") + rel_body)}
</div>

<h2>补充用例（不属于考卷）</h2>
{card("真实问题回归", "线上真实出过问题的用例，加上精选的考卷用例，用真实模型验证改动没有退化。",
      tag("真实模型") + tag("花钱", "paid") + tag("改 Prompt/Agent 后手动"), reg_base + reg_online, "条", L("regress", t_last(dataset_changes(ONLINE_REG, 1))), S("regress") + reg_body)}
{card("线上回复检查", "对已部署的线上服务发请求，检查状态、意图、语言和关键词。",
      tag("线上真实模型") + tag("花钱", "paid") + tag("部署后手动"), len(checks), "条", L("online", t_last(dataset_changes(REPLY_CHECKS, 1))), S("online") + onl_body)}

{NOTE_FOOT if notes else ''}
<script>{JS}</script></main></body></html>
"""
    return html_


def _render_other(notes: dict, facts: dict) -> str:
    """第二页：主页面之外的检查（前端、MCP、演练、冒烟、压测、评测脚本）。"""

    def count(pattern: str, files) -> int:
        return sum(len(re.findall(pattern, f.read_text(encoding="utf-8"), re.M)) for f in files)

    fe = count(r"^\s*test\(", sorted((ROOT / "frontend/tests").glob("*.test.js")))
    mcp = count(r"def test_", sorted((ROOT / "tests/mcp").glob("test_*.py")))

    def row(name, what, tags, num, unit, paths, cmd, where, note=""):
        last = note_L(notes, name, last_line(recent_commits(paths, 1)) if paths else "最近变动：—")
        facts[name] = {
            "简介": what + (" " + note if note else ""),
            **({"用例数": num} if num != "" else {}),
            "最近提交": rows_facts(recent_commits(paths, 3)) if paths else [],
        }
        body = (
            note_body(notes, name)
            + f"<h3>命令</h3><p class='small'><code>{esc(cmd)}</code></p>"
            f"<h3>位置</h3><p class='small'>{esc(where)}</p>"
            + (f"<p class='small muted'>{esc(note)}</p>" if note else "")
            + changes(recent_commits(paths, 3) if paths else [])
        )
        return card(name, what, tags, num, unit, last, body)

    free, paid = tag("免费", "free"), tag("花钱", "paid")
    code = (
        row("前端测试", "界面里的筛选、设计系统、工具治理演示、公开页运行信息等逻辑。",
            tag("无模型") + free + tag("CI 自动"), fe, "个用例",
            ["frontend/tests"], "cd frontend && npm test", "frontend/tests/，5 个文件")
        + row("MCP Server 测试", "独立的 stdio 进程，只通过 HTTP 调员工 API；这里验证它的工具和返回。",
              tag("无模型") + free + tag("本地手动"), mcp, "个用例",
              ["tests/mcp", "src/mcp_server"], "python -m unittest discover -s tests/mcp -v",
              "tests/mcp/", "用独立轻量环境，不加载后端的 conftest；CI 里没有这一步。")
        + row("PromptOps 对比实验", "PR 门禁里的一步：用 Mock 验证 Prompt 版本成对对比、晋级的流程是否能走通。",
              tag("Mock") + free + tag("每次 PR 自动"), "", "",
              ["scripts/promptops.py", "src/promptops"],
              "python scripts/promptops.py --registry <目录> evaluate --bundle default --environment staging --mock",
              "scripts/promptops.py")
        + row("编译与构建检查", "只检查能不能编译、构建，不算测试：Python 编译、前端 build、Docker 镜像构建。",
              tag("无模型") + free + tag("CI 自动"), "", "",
              [".github/workflows/ci.yml"],
              "python -m compileall -q src scripts tests；npm run build", ".github/workflows/ci.yml")
    )
    drills = (
        row("PostgreSQL 故障演练", "多进程验证写 Tool 的状态机、租约、对账：崩溃、重复提交、超时后的处理。",
            tag("无模型") + free + tag("CI 自动，本机跑不了"), "", "",
            ["scripts/run_postgres_tool_drill.py", "src/tools"],
            "python scripts/run_postgres_tool_drill.py --confirm-isolated-database",
            "scripts/run_postgres_tool_drill.py",
            "需要 TOOL_DRILL_DATABASE_URL，库名必须以 _test 或 _drill 结尾。本机没有 PG 时不能算演练通过。")
        + row("参考 OMS 持久化演练", "用隔离的 PG Schema 验证参考 OMS 的多进程幂等和提交后崩溃。",
              tag("无模型") + free + tag("CI 自动，本机跑不了"), "", "",
              ["scripts/run_reference_oms_drill.py", "src/oms"],
              "python scripts/run_reference_oms_drill.py --confirm-isolated-database",
              "scripts/run_reference_oms_drill.py", "同样需要隔离的 PG 库。")
    )
    online = (
        row("部署后冒烟", "确认线上服务活着、前后端接得上；带 --with-chat 时真实问答一次。",
            tag("线上真实模型") + paid + tag("每次推送 main 自动"), "", "",
            ["scripts/run_post_deploy_smoke.py", ".github/workflows/post-deploy-smoke.yml"],
            "python scripts/run_post_deploy_smoke.py --api-url <Railway 域名> --frontend-url <Vercel 域名>",
            "scripts/run_post_deploy_smoke.py；.github/workflows/post-deploy-smoke.yml",
            "GitHub 上每次推送 main 都会触发：在线上建 1 张测试工单（结束后撤回）并调用 1 次模型。")
        + row("压测", "用 Locust 模拟并发客户聊天和员工审批。",
              tag("真实模型") + paid + tag("手动"), "", "",
              ["tests/load_test.py"], "locust -f tests/load_test.py",
              "tests/load_test.py；依赖 requirements/load.txt",
              "需要预先创建的压测账号（LOAD_TEST_USERNAME / LOAD_TEST_PASSWORD）。性能数据要在机器空闲时测。")
    )
    scripts_ = (
        row("run_agent_eval.py", "用 Golden 小数据集（13 条）跑 RAG 和 Agent 评测。不是门禁；用哪个模型取决于本地 .env。",
            tag("按 .env 配置") + tag("手动"), 13, "条",
            ["scripts/run_agent_eval.py", "evaluation/golden"], "python scripts/run_agent_eval.py",
            "evaluation/golden/support_qa_golden.json")
        + row("run_eval.py", "早期的评测脚本：4 条写死的问题，用 DeepEval 评判。", tag("按 .env 配置") + tag("手动"), 4, "条",
              ["scripts/run_eval.py"], "python scripts/run_eval.py", "scripts/run_eval.py")
        + row("compare_tool_selection.py", "离线对比 rules 与 react 两份 Baseline 报告的 Tool 选择效果和成本。",
              tag("无模型") + free + tag("手动"), "", "",
              ["scripts/compare_tool_selection.py"],
              "python scripts/compare_tool_selection.py --help", "scripts/compare_tool_selection.py",
              "读已有的报告文件，不重新跑测试。")
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SupportGPT 其他检查</title><style>{CSS}</style></head><body><main>
<header><h1>其他检查</h1><a class="lk" href="test_catalog.html">← 测试分类</a><span class="sp"></span>
<span id="rmsg" class="muted small"></span><button id="rbtn" onclick="refreshCatalog()">更新</button></header>
<p class="muted small">更新于 {datetime.now().strftime("%Y-%m-%d %H:%M")}。这一页放主页面之外的检查：前端、MCP、演练、冒烟、压测、评测脚本。点卡片展开详情。</p>
<div class="intro">
<h3>这一页是什么</h3>
<p>主页面的 5 项，判断的是“客服 Agent 答得对不对、流程走得对不对”。这一页的 11 项判断的是<b>工程本身是否正常</b>：前端能不能用、部署后线上活不活、写 Tool 出故障时数据会不会错。</p>
<h3>怎么看</h3>
<div class="when">
<b>平时不用管</b><span>前端测试、编译与构建检查、PromptOps 对比实验：CI（GitHub 自动检查）每次推送都会跑，免费。</span>
<b>本机跑不了</b><span>两个 PostgreSQL 演练：需要专用的 PG 库，只能看 CI 的结果。</span>
<b>会花钱或动线上</b><span>部署后冒烟（每次推送 main 自动触发）和压测。</span>
<b>只是工具</b><span>三个评测脚本：用来做对比分析，不是门禁，不影响发版。</span>
</div></div>
<h2>代码类检查</h2>{code}
<h2>演练（需要 PostgreSQL）</h2>{drills}
<h2>线上和性能</h2>{online}
<h2>评测脚本（不是门禁）</h2>{scripts_}
{NOTE_FOOT if notes else ''}
<script>{JS}</script></main></body></html>
"""


def build_other() -> str:
    facts: dict = {}
    _render_other({}, facts)  # 第一遍只为收集事实
    notes = ai_notes(facts, AI_CACHE_OTHER)
    return _render_other(notes, {})


def main() -> None:
    OUT.write_text(build(), encoding="utf-8")
    OUT_OTHER.write_text(build_other(), encoding="utf-8")
    print(f"已生成 {OUT.relative_to(ROOT)}、{OUT_OTHER.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
