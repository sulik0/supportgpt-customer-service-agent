"""评测题集分层清单：每层的文件、题数和指纹，并校验它们没有被悄悄改动。

指纹是文件字节的 SHA-256，和 quality_gate_policy.json 里核心题集的算法一致。
核心层（frozen）指纹不一致只会校验失败，没有更新入口；补充层加题后用 update_layer 更新。
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PROJECT_ROOT / "evaluation" / "manifest.json"


class FrozenLayerError(RuntimeError):
    """尝试更新冻结层。"""


def file_fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(manifest_path: Path = MANIFEST_PATH) -> Dict[str, Any]:
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def read_cases(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases", payload) if isinstance(payload, dict) else payload
    if not isinstance(cases, list):
        raise ValueError(f"{path} 中没有题目列表。")
    return cases


def validate_manifest(
    manifest_path: Path = MANIFEST_PATH, root: Path = PROJECT_ROOT
) -> List[str]:
    """返回所有校验错误；空列表表示通过。"""
    errors: List[str] = []
    layers = load_manifest(manifest_path).get("layers", {})
    seen_ids: Dict[str, str] = {}
    for name, layer in layers.items():
        path = root / layer["file"]
        if not path.is_file():
            errors.append(f"[{name}] 文件不存在：{layer['file']}")
            continue
        cases = read_cases(path)
        if len(cases) != layer["count"]:
            errors.append(
                f"[{name}] 题数不一致：清单 {layer['count']}，实际 {len(cases)}"
            )
        actual = file_fingerprint(path)
        if actual != layer["fingerprint"]:
            hint = (
                "冻结层不允许修改，也没有更新命令"
                if layer.get("frozen")
                else f"题集有改动，确认后执行 update {name}"
            )
            errors.append(f"[{name}] 指纹不一致（{hint}）")
        for case in cases:
            case_id = case.get("id")
            if not case_id:
                errors.append(f"[{name}] 有题目缺少 id")
            elif case_id in seen_ids:
                errors.append(
                    f"id 重复：{case_id}（出现在 {seen_ids[case_id]} 和 {name}）"
                )
            else:
                seen_ids[case_id] = name
    return errors


def update_layer(
    name: str, manifest_path: Path = MANIFEST_PATH, root: Path = PROJECT_ROOT
) -> Dict[str, Any]:
    """只更新指定补充层的题数和指纹，其他层不动。"""
    manifest = load_manifest(manifest_path)
    layer = manifest["layers"].get(name)
    if layer is None:
        raise KeyError(f"清单里没有这一层：{name}")
    if layer.get("frozen"):
        raise FrozenLayerError(f"{name} 是冻结层，不能更新。")
    path = root / layer["file"]
    layer["count"] = len(read_cases(path))
    layer["fingerprint"] = file_fingerprint(path)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return layer
