import json

import pytest

from src.evaluation.layered_run import evaluate_gates, run_layered_evaluation, summarize
from src.evaluation.manifest import file_fingerprint


def _case(case_id, forbidden=("tool.bad",)):
    return {
        "id": case_id, "query": "q", "reference_answer": "a", "expected_sources": [],
        "category": "general", "risk_level": "low",
        "agent_expectations": {"forbidden_tools": list(forbidden)},
    }


def _setup(tmp_path, layers):
    manifest = {"layers": {}}
    for name, ids in layers.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"cases": [_case(i) for i in ids]}), encoding="utf-8")
        manifest["layers"][name] = {
            "file": path.name, "frozen": name == "core", "count": len(ids),
            "fingerprint": file_fingerprint(path),
        }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _runner(bad_ids):
    """假工作流：bad_ids 里的题会执行禁用工具。"""
    async def run(state):
        case_id = state["request_id"].removeprefix("offline-eval-")
        calls = [{"tool_name": "tool.bad", "allowed": True}] if case_id in bad_ids else []
        return {"intent": "general", "department": "general", "tool_calls": calls,
                "suggested_response": "", "context_citations": [], "workflow_path": [],
                "errors": []}
    return run


@pytest.mark.asyncio
async def test_run_records_layer_per_case_and_metadata(tmp_path):
    manifest_path = _setup(tmp_path, {"core": ["c1", "c2"], "inj": ["i1"]})

    result = await run_layered_evaluation(
        ["core", "inj"], tmp_path / "out", manifest_path=manifest_path, root=tmp_path,
        workflow_runner=_runner({"i1"}),
    )

    assert {(row["id"], row["layer"]) for row in result["cases"]} == {
        ("c1", "core"), ("c2", "core"), ("i1", "inj")}
    meta = result["metadata"]
    assert meta["layers"]["core"]["count"] == 2 and meta["layers"]["inj"]["fingerprint"]
    assert meta["model"] and meta["prompt_bundle"]["node_hashes"] and meta["source_revision"]
    saved = json.loads((tmp_path / "out" / "layered_latest.json").read_text())
    assert saved["summary"]["layers"]["inj"]["failed_ids"] == ["i1"]
    report = (tmp_path / "out" / "layered_latest.md").read_text()
    assert "题数会变，不用于比较和门禁" in report and "i1" in report


@pytest.mark.asyncio
async def test_only_requested_layers_run(tmp_path):
    manifest_path = _setup(tmp_path, {"core": ["c1"], "inj": ["i1"]})
    result = await run_layered_evaluation(
        ["core"], tmp_path / "out", manifest_path=manifest_path, root=tmp_path,
        workflow_runner=_runner(set()),
    )
    assert [row["layer"] for row in result["cases"]] == ["core"]


@pytest.mark.asyncio
async def test_tampered_layer_blocks_the_run(tmp_path):
    manifest_path = _setup(tmp_path, {"core": ["c1"]})
    (tmp_path / "core.json").write_text(json.dumps({"cases": [_case("c1"), _case("c9")]}))
    with pytest.raises(ValueError, match="清单校验失败"):
        await run_layered_evaluation(
            ["core"], tmp_path / "out", manifest_path=manifest_path, root=tmp_path,
            workflow_runner=_runner(set()),
        )


def test_gate_is_per_layer_and_total_does_not_matter():
    # core 全过、inj 只有 1/2 通过：合计 11/12 很高，但 inj 低于 0.9，整次失败。
    rows = [{"id": f"c{i}", "layer": "core", "passed": True} for i in range(10)]
    rows += [{"id": "i1", "layer": "inj", "passed": True},
             {"id": "i2", "layer": "inj", "passed": False}]
    summary = summarize(rows, ["core", "inj"])

    gate = evaluate_gates(summary, {"core": 0.95, "inj": 0.9})

    assert summary["total"]["pass_rate"] > 0.9
    assert gate["layers"]["core"]["passed"] and not gate["layers"]["inj"]["passed"]
    assert gate["passed"] is False
    # 没跑的层不判断；没配阈值则不判断
    assert evaluate_gates(summarize(rows[:10], ["core"]), {"core": 0.95, "inj": 0.9})["passed"]
