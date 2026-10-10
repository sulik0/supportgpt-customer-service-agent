"""使用 Mock Provider 回放固定 100 条 Baseline，并执行 PR 质量门禁。"""

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
BASELINE_PATH = (
    PROJECT_ROOT / "evaluation" / "baseline" / "supportgpt_baseline_100.json"
)


def parse_args() -> argparse.Namespace:
    """解析报告目录与版本化门禁策略。"""
    parser = argparse.ArgumentParser(
        description="Run the free deterministic PR Agent quality gate."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "evaluation" / "reports" / "ci_quality_gate",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=PROJECT_ROOT / "evaluation" / "quality_gate_policy.json",
    )
    return parser.parse_args()


async def run() -> int:
    """在隔离数据库和向量目录中执行完整 Workflow Replay。"""
    args = parse_args()
    with tempfile.TemporaryDirectory(prefix="supportgpt-quality-gate-") as runtime:
        _configure_offline_environment(Path(runtime))

        # 延迟导入，避免本地真实模型配置进入 PR Gate。
        from src.database import init_db
        from src.evaluation.baseline_evaluation import run_baseline_evaluation_v1
        from src.evaluation.quality_gate import (
            evaluate_quality_gate,
            load_json_object,
            write_quality_gate_report,
        )

        await init_db()
        baseline_dir = args.output_dir / "baseline"
        paths = await run_baseline_evaluation_v1(
            BASELINE_PATH,
            baseline_dir,
            execution_metadata={
                "mode": "ci_offline_workflow_replay",
                "llm_provider": "mock",
                "suite": "full",
                "selected_case_count": 100,
                "dataset_case_count": 100,
                "replay_case_count": 100,
                "otel_enabled": False,
                "langsmith_via_otlp": False,
            },
        )
        report = load_json_object(paths["json"])
        policy = load_json_object(args.policy)
        result = evaluate_quality_gate(report, policy, profile="pull_request")
        gate_paths = write_quality_gate_report(result, args.output_dir)

    status = "PASS" if result["passed"] else "FAIL"
    print(f"PR Agent quality gate: {status}")
    print(f"Baseline report: {paths['json']}")
    print(f"Gate report: {gate_paths['json']}")
    _append_github_summary(gate_paths["markdown"])
    return 0 if result["passed"] else 1


def _configure_offline_environment(runtime: Path) -> None:
    """强制关闭网络模型和遥测，保证 PR Gate 免费且可重复。"""
    values = {
        "APP_ENV": "testing",
        "DATABASE_URL": f"sqlite+aiosqlite:///{runtime / 'quality_gate.db'}",
        "VECTOR_DB_PERSIST_DIR": str(runtime / "chromadb"),
        "LLM_PROVIDER": "mock",
        "OMS_PROVIDER": "mock",
        "LLM_MODEL_NAME": "mock",
        "LLM_BASE_URL": "",
        "LLM_API_KEY": "",
        "LLM_FAST_MODEL_NAME": "",
        "LLM_FAST_BASE_URL": "",
        "LLM_FAST_API_KEY": "",
        "LLM_ANALYZER_MODEL_NAME": "",
        "LLM_QA_MODEL_NAME": "",
        "QWEN3_GUARD_ENABLED": "false",
        "OTEL_ENABLED": "false",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "",
    }
    os.environ.update(values)


def _append_github_summary(markdown_path: Path) -> None:
    """将门禁 Markdown 附加到 GitHub Actions Summary。"""
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with Path(summary_path).open("a", encoding="utf-8") as stream:
        stream.write(markdown_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
