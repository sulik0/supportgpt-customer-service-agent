"""运行带成本保护的真实 LLM Workflow Replay 回归测试。"""

import argparse
import asyncio
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import settings
from src.database import init_db
from src.evaluation.offline_rag import (
    load_evaluation_dataset,
    run_offline_evaluation,
)
from src.evaluation.real_llm_regression import (
    build_real_llm_run_plan,
    merge_dataset_files,
    prepare_judge_environment,
    require_live_confirmation,
)
from src.observability.tracing import init_tracing


def parse_args() -> argparse.Namespace:
    """解析真实模型回归的用例套件、评委和成本保护参数。"""
    parser = argparse.ArgumentParser(
        description="Run guarded real-LLM Dataset + Workflow Replay regression."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=(
            PROJECT_ROOT / "evaluation" / "baseline" / "supportgpt_baseline_100.json"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "evaluation" / "reports" / "real_llm",
    )
    parser.add_argument(
        "--extra-dataset",
        type=Path,
        default=(
            PROJECT_ROOT
            / "evaluation"
            / "online_regression"
            / "supportgpt_online_regression.json"
        ),
        help="Supplementary cases merged into the run; pass a missing path to skip.",
    )
    parser.add_argument("--suite", choices=("smoke", "full"), default="smoke")
    parser.add_argument(
        "--case-id",
        action="append",
        default=None,
        help="Run an explicit case id; repeat to select multiple cases.",
    )
    parser.add_argument("--rag-engine", choices=("ragas", "local"), default="local")
    parser.add_argument(
        "--agent-engine", choices=("deepeval", "local"), default="local"
    )
    parser.add_argument(
        "--max-workflow-calls",
        type=int,
        default=40,
        help="Maximum estimated Analyzer/Resolver/QA live calls.",
    )
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Acknowledge that the run sends data to a model service and may incur cost.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration and print the plan without calling a model.",
    )
    return parser.parse_args()


async def main() -> None:
    """先输出无密钥计划，再在显式确认后执行真实回归。"""
    args = parse_args()
    if args.extra_dataset.exists():
        args.dataset = merge_dataset_files(
            [args.dataset, args.extra_dataset],
            args.output_dir / "_merged_dataset.json",
        )
    cases = load_evaluation_dataset(args.dataset)
    plan = build_real_llm_run_plan(
        settings=settings,
        cases=cases,
        suite=args.suite,
        explicit_case_ids=args.case_id,
        max_workflow_calls=args.max_workflow_calls,
    )
    metadata = {
        **plan.report_metadata(),
        "rag_engine": args.rag_engine,
        "agent_engine": args.agent_engine,
    }
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    if args.rag_engine == "ragas" or args.agent_engine == "deepeval":
        print(
            "Judge mode adds external evaluator calls that are not included in "
            "estimated_workflow_llm_calls."
        )
    if args.dry_run:
        print("Dry run completed; no model request was sent.")
        return

    require_live_confirmation(args.confirm_live)
    prepare_judge_environment(
        settings,
        rag_engine=args.rag_engine,
        agent_engine=args.agent_engine,
    )
    init_tracing()
    await init_db()
    paths = await run_offline_evaluation(
        dataset_path=args.dataset,
        output_dir=args.output_dir,
        rag_engine=args.rag_engine,
        agent_engine=args.agent_engine,
        case_ids=plan.case_ids,
        execution_metadata=metadata,
    )
    print(f"JSON report: {paths['json']}")
    print(f"Markdown report: {paths['markdown']}")


if __name__ == "__main__":
    asyncio.run(main())
