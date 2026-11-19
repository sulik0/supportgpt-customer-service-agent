"""按层运行评测题集：--layers core,injection 或 --layers all。

默认 Mock 模型，不产生费用；配置了真实模型时需要 --confirm-live，--dry-run 只估算调用次数。
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import settings
from src.database import init_db
from src.evaluation.layered_run import run_layered_evaluation, select_layers
from src.evaluation.manifest import load_manifest
from src.evaluation.offline_rag import load_evaluation_dataset
from src.evaluation.real_llm_regression import (
    REAL_LLM_PROVIDERS,
    build_real_llm_run_plan,
    require_live_confirmation,
)
from src.observability.tracing import init_tracing


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layers", default="core", help="层名，逗号分隔，或 all")
    parser.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "evaluation" / "reports" / "layered"
    )
    parser.add_argument("--max-workflow-calls", type=int, default=300)
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    manifest = load_manifest()
    layers = select_layers(
        manifest, "all" if args.layers == "all" else args.layers.split(",")
    )
    metadata = {"mode": "layered", "layers": layers}
    if settings.LLM_PROVIDER.lower() in REAL_LLM_PROVIDERS:
        cases = [
            case
            for name in layers
            for case in load_evaluation_dataset(
                PROJECT_ROOT / manifest["layers"][name]["file"],
                validate_agent=False,
                validate_security=False,
            )
        ]
        plan = build_real_llm_run_plan(
            settings=settings,
            cases=cases,
            suite="full",
            explicit_case_ids=[case.id for case in cases],
            max_workflow_calls=args.max_workflow_calls,
        )
        metadata.update(plan.report_metadata())
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    if args.dry_run:
        print("Dry run completed; no Workflow or model request was sent.")
        return
    if metadata.get("llm_provider"):
        require_live_confirmation(args.confirm_live)
    init_tracing()
    await init_db()
    result = await run_layered_evaluation(
        layers, args.output_dir, execution_metadata=metadata
    )
    print(f"JSON: {result['paths']['json']}")
    print(f"Markdown: {result['paths']['markdown']}")
    for name, item in result["summary"]["layers"].items():
        print(f"{name}: {item['passed']}/{item['total']}  失败: {item['failed_ids'] or '无'}")


if __name__ == "__main__":
    asyncio.run(main())
