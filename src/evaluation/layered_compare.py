"""对比两次分层运行的结果文件：回退、修复、新题，以及核心层通过率。"""

import json
from pathlib import Path
from typing import Any, Dict


def compare_runs(previous: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    before = {row["id"]: row for row in previous["cases"]}
    after = {row["id"]: row for row in current["cases"]}
    both = before.keys() & after.keys()

    def ids(predicate):
        return sorted(i for i in both if predicate(before[i]["passed"], after[i]["passed"]))

    def core_rate(run):
        return run["summary"]["layers"].get("core", {}).get("pass_rate")

    return {
        "regressed": ids(lambda b, a: b and not a),
        "fixed": ids(lambda b, a: not b and a),
        "new": sorted(after.keys() - before.keys()),
        "core_pass_rate": {"previous": core_rate(previous), "current": core_rate(current)},
    }


def render_comparison(diff: Dict[str, Any]) -> str:
    rate = diff["core_pass_rate"]
    lines = [
        f"核心层通过率：{rate['previous']} → {rate['current']}",
        f"之前通过、现在失败（{len(diff['regressed'])}）：" + (", ".join(diff["regressed"]) or "无"),
        f"之前失败、现在通过（{len(diff['fixed'])}）：" + (", ".join(diff["fixed"]) or "无"),
        f"新题（{len(diff['new'])}）：" + (", ".join(diff["new"]) or "无"),
    ]
    return "\n".join(lines)


def load_result(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
