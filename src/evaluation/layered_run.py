"""多层题集运行：一次回放若干层，保存结果文件，并按层出报告。

每道题记录所属层；通过与否沿用 baseline 的逐题判定（evaluate_baseline_behavior），
不另造一套判定。门禁阈值按层配置，合计分数不参与门禁。
"""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.config import settings
from src.evaluation.baseline_evaluation import (
    BaselineExpectations,
    _source_revision,
    evaluate_baseline_behavior,
)
from src.evaluation.manifest import (
    MANIFEST_PATH,
    PROJECT_ROOT,
    load_manifest,
    validate_manifest,
)
from src.evaluation.security_judge import judge_security
from src.evaluation.offline_rag import (
    WorkflowRunner,
    collect_workflow_records,
    load_evaluation_dataset,
)
from src.evaluation.tool_fixtures import install_tool_fixtures
from src.promptops.runtime import active_bundle, prompt_scope

TOTAL_NOTE = "题数会变，不用于比较和门禁"
def online_layers(manifest: Mapping[str, Any]) -> List[str]:
    """带线上检查题（online_file）的层：这部分题测已部署的服务，部署后用单独脚本跑。"""
    return [n for n, v in manifest["layers"].items() if v.get("online_file")]


def select_layers(manifest: Mapping[str, Any], names: Sequence[str] | str) -> List[str]:
    """names 为层名列表，或字符串 'all'。"""
    available = list(manifest["layers"])
    if names == "all":
        return available
    unknown = [name for name in names if name not in available]
    if unknown:
        raise ValueError(f"清单里没有这些层：{', '.join(unknown)}")
    return list(names)


def _model_name() -> str:
    provider = settings.LLM_PROVIDER.lower()
    if provider == "azure":
        return settings.AZURE_OPENAI_DEPLOYMENT or "azure"
    if provider == "openai":
        return settings.LLM_MODEL_NAME or "openai-compatible"
    return "mock"


def summarize(case_rows: Sequence[Mapping[str, Any]], layers: Sequence[str]) -> Dict[str, Any]:
    per_layer: Dict[str, Any] = {}
    for name in layers:
        rows = [row for row in case_rows if row["layer"] == name]
        passed = sum(1 for row in rows if row["passed"])
        per_layer[name] = {
            "total": len(rows),
            "passed": passed,
            "pass_rate": round(passed / len(rows), 6) if rows else None,
            "failed_ids": [row["id"] for row in rows if not row["passed"]],
        }
    total_passed = sum(1 for row in case_rows if row["passed"])
    return {
        "layers": per_layer,
        "total": {
            "total": len(case_rows),
            "passed": total_passed,
            "pass_rate": round(total_passed / len(case_rows), 6) if case_rows else None,
            "note": TOTAL_NOTE,
        },
    }


def evaluate_gates(
    summary: Mapping[str, Any], gates: Optional[Mapping[str, float]]
) -> Dict[str, Any]:
    """按层判断：任何一层低于阈值，整次失败；合计不参与。没配阈值的层不判断。"""
    results: Dict[str, Any] = {}
    for name, threshold in (gates or {}).items():
        layer = summary["layers"].get(name)
        if layer is None:
            continue  # 本次没跑这一层
        rate = layer["pass_rate"]
        results[name] = {
            "threshold": threshold,
            "pass_rate": rate,
            "passed": rate is not None and rate >= threshold,
        }
    return {"layers": results, "passed": all(item["passed"] for item in results.values())}


def render_report(result: Mapping[str, Any]) -> str:
    meta, summary, gate = result["metadata"], result["summary"], result["gate"]
    lines = [
        f"# 分层评测报告 {result['run_id']}",
        "",
        f"- 套件/层：`{meta['suite']}`；模型：`{meta['model']}`；"
        f"代码版本：`{meta['source_revision'][:12]}`",
        f"- 提示词：`{meta['prompt_bundle']['bundle_id']}` {meta['prompt_bundle']['version']}",
        "",
        "## 1. 门禁结果（按层）",
        "",
    ]
    if gate["layers"]:
        lines += ["| 层 | 阈值 | 通过率 | 结果 |", "|---|---|---|---|"]
        for name, item in gate["layers"].items():
            lines.append(
                f"| {name} | {item['threshold']} | {item['pass_rate']} "
                f"| {'通过' if item['passed'] else '失败'} |"
            )
        lines.append("")
        lines.append(f"**整次：{'通过' if gate['passed'] else '失败'}**")
    else:
        lines.append("本次没有配置门禁阈值。")
    failed = [(n, v["failed_ids"]) for n, v in summary["layers"].items() if v["failed_ids"]]
    lines += ["", "失败的题目：" + ("无" if not failed else "")]
    for name, ids in failed:
        lines.append(f"- {name}：{', '.join(ids)}")
    lines += ["", "## 2. 每层题数和通过率", "", "| 层 | 题数 | 通过 | 通过率 | 指纹 |", "|---|---|---|---|---|"]
    for name, item in summary["layers"].items():
        layer_meta = meta["layers"][name]
        lines.append(
            f"| {name} | {item['total']} | {item['passed']} | {item['pass_rate']} "
            f"| `{layer_meta['fingerprint'][:12]}` |"
        )
    total = summary["total"]
    lines += [
        "",
        "## 3. 合计",
        "",
        f"{total['passed']}/{total['total']}，通过率 {total['pass_rate']}（{total['note']}）",
        "",
    ]
    return "\n".join(lines)


async def run_layered_evaluation(
    layers: Sequence[str] | str,
    output_dir: Path,
    *,
    suite_name: Optional[str] = None,
    gates: Optional[Mapping[str, float]] = None,
    manifest_path: Path = MANIFEST_PATH,
    root: Path = PROJECT_ROOT,
    workflow_runner: Optional[WorkflowRunner] = None,
    execution_metadata: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    manifest = load_manifest(manifest_path)
    names = select_layers(manifest, layers)
    # 只校验本次要跑的层，别的层正在加题不影响这次运行。
    errors = validate_manifest(manifest_path, root, only=names)
    if errors:
        raise ValueError("题集清单校验失败，先处理：\n" + "\n".join(errors))

    cases = []
    layer_of: Dict[str, str] = {}
    judge_of: Dict[str, str] = {}
    for name in names:
        if manifest["layers"][name]["count"] == 0:
            continue  # 空层（如 unsorted）没有题可回放
        for case in load_evaluation_dataset(
            root / manifest["layers"][name]["file"],
            validate_agent=False,
            validate_security=False,
        ):
            cases.append(case)
            layer_of[case.id] = name
            judge_of[case.id] = manifest["layers"][name].get("judge", "baseline")

    with prompt_scope():
        bundle = active_bundle()
        prompt_bundle = {"bundle_id": bundle.bundle_id, "version": bundle.version,
                         "node_hashes": bundle.metadata()["node_hashes"]}
        with install_tool_fixtures(cases):
            records = await collect_workflow_records(
                cases, workflow_runner, include_security_metadata=False
            )

    case_rows = []
    for record in records:
        if judge_of[record.case.id] == "security":
            behavior = judge_security(record.workflow_output)
        else:
            behavior = evaluate_baseline_behavior(
                record.workflow_output,
                BaselineExpectations.from_mapping(record.case.agent_expectations),
            )
        case_rows.append(
            {
                "id": record.case.id,
                "layer": layer_of[record.case.id],
                "passed": behavior.passed,
                "failures": list(behavior.failures),
            }
        )

    summary = summarize(case_rows, names)
    now = datetime.now(timezone.utc)
    result = {
        "run_id": now.strftime("%Y%m%d_%H%M%S"),
        "generated_at": now.isoformat(),
        "metadata": {
            "suite": suite_name or ",".join(names),
            "layers": {
                name: {
                    "count": manifest["layers"][name]["count"],
                    "fingerprint": manifest["layers"][name]["fingerprint"],
                }
                for name in names
            },
            "model": _model_name(),
            "prompt_bundle": prompt_bundle,
            "source_revision": _source_revision(),
            "execution": dict(execution_metadata or {}),
        },
        "cases": case_rows,
        "summary": summary,
        "gate": evaluate_gates(summary, gates),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"layered_{result['run_id']}.json"
    md_path = output_dir / f"layered_{result['run_id']}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_report(result), encoding="utf-8")
    shutil.copyfile(json_path, output_dir / "layered_latest.json")
    shutil.copyfile(md_path, output_dir / "layered_latest.md")
    result["paths"] = {"json": json_path, "markdown": md_path}
    return result
