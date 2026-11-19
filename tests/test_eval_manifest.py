import json
from pathlib import Path

import pytest

from src.evaluation.manifest import (
    MANIFEST_PATH,
    PROJECT_ROOT,
    FrozenLayerError,
    file_fingerprint,
    update_layer,
    validate_manifest,
)


def _case(case_id):
    return {"id": case_id, "query": "q"}


def _write_layer(root: Path, name: str, ids):
    path = root / f"{name}.json"
    path.write_text(json.dumps({"cases": [_case(i) for i in ids]}), encoding="utf-8")
    return path


def _setup(tmp_path, core_ids=("c1", "c2"), supp_ids=("s1",)):
    core = _write_layer(tmp_path, "core", core_ids)
    supp = _write_layer(tmp_path, "supp", supp_ids)
    manifest = {
        "layers": {
            "core": {"file": "core.json", "frozen": True, "count": len(core_ids),
                     "fingerprint": file_fingerprint(core)},
            "supp": {"file": "supp.json", "frozen": False, "count": len(supp_ids),
                     "fingerprint": file_fingerprint(supp)},
        }
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def test_real_manifest_passes_and_core_matches_gate_policy():
    assert validate_manifest() == []
    policy = json.loads((PROJECT_ROOT / "evaluation/quality_gate_policy.json").read_text())
    core = json.loads(MANIFEST_PATH.read_text())["layers"]["core"]
    assert core["frozen"] is True
    assert core["fingerprint"] == policy["dataset"]["sha256"]
    assert core["count"] == policy["dataset"]["case_count"]


def test_valid_manifest_passes(tmp_path):
    assert validate_manifest(_setup(tmp_path), tmp_path) == []


def test_editing_a_core_case_fails_validation(tmp_path):
    manifest_path = _setup(tmp_path)
    core = tmp_path / "core.json"
    data = json.loads(core.read_text())
    data["cases"][0]["query"] = "changed"
    core.write_text(json.dumps(data), encoding="utf-8")

    errors = validate_manifest(manifest_path, tmp_path)

    assert any("[core] 指纹不一致" in item for item in errors)
    with pytest.raises(FrozenLayerError):
        update_layer("core", manifest_path, tmp_path)


def test_duplicate_id_across_layers_fails(tmp_path):
    manifest_path = _setup(tmp_path, core_ids=("c1", "dup"), supp_ids=("dup",))
    errors = validate_manifest(manifest_path, tmp_path)
    assert any("id 重复：dup" in item for item in errors)


def test_count_mismatch_fails(tmp_path):
    manifest_path = _setup(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["layers"]["supp"]["count"] = 5
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert any("题数不一致" in item for item in validate_manifest(manifest_path, tmp_path))


def test_update_only_changes_the_named_supplement_layer(tmp_path):
    manifest_path = _setup(tmp_path)
    before = json.loads(manifest_path.read_text())
    _write_layer(tmp_path, "supp", ("s1", "s2", "s3"))
    assert validate_manifest(manifest_path, tmp_path)  # 加题后未更新，校验失败

    update_layer("supp", manifest_path, tmp_path)

    after = json.loads(manifest_path.read_text())
    assert after["layers"]["supp"]["count"] == 3
    assert after["layers"]["core"] == before["layers"]["core"]
    assert validate_manifest(manifest_path, tmp_path) == []
