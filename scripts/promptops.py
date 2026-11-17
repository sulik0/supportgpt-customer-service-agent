"""PromptOps / EvalOps V1：注册、成对回放、晋级和回滚。"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    """显式区分 Mock 与付费实验，所有发布操作要求操作人和原因。"""
    parser = argparse.ArgumentParser(description="SupportGPT PromptOps / EvalOps V1")
    parser.add_argument("--registry", type=Path, default=None)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show environment pointers and release history")
    export = commands.add_parser("export-default", help="Export current built-in templates")
    export.add_argument("--output", type=Path, required=True)
    register = commands.add_parser("register", help="Register an immutable Prompt Bundle")
    register.add_argument("file", type=Path)
    evaluate = commands.add_parser("evaluate", help="Replay current and candidate on the fixed 100 cases")
    evaluate.add_argument("--bundle", required=True)
    evaluate.add_argument("--environment", choices=["staging", "production"], default="staging")
    evaluate.add_argument("--policy", type=Path, default=PROJECT_ROOT / "evaluation/quality_gate_policy.json")
    mode = evaluate.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", action="store_true")
    mode.add_argument("--live", action="store_true")
    evaluate.add_argument("--confirm-live", action="store_true")
    evaluate.add_argument("--max-workflow-calls", type=int, default=600,
                          help="Combined estimated LLM budget for BOTH 100-case replays")
    evaluate.add_argument("--limit", type=int, default=None,
                          help="Development sample only; incomplete reports cannot pass the gate")
    for operation in ("promote", "rollback"):
        command = commands.add_parser(operation)
        command.add_argument("--actor", required=True)
        command.add_argument("--reason", required=True)
        if operation == "promote":
            command.add_argument("--experiment", required=True)
            command.add_argument("--policy", type=Path, default=PROJECT_ROOT / "evaluation/quality_gate_policy.json")
        else:
            command.add_argument("--environment", choices=["staging", "production"], required=True)
    return parser.parse_args()


async def evaluate(args, registry) -> int:
    """隔离业务存储并成对回放相同数据，固定两次运行各自的 Prompt。"""
    from src.config import settings
    from src.database import init_db
    from src.evaluation.baseline_evaluation import run_baseline_evaluation_v1
    from src.evaluation.offline_rag import load_evaluation_dataset
    from src.evaluation.real_llm_regression import build_real_llm_run_plan, require_live_confirmation
    from src.promptops.experiments import save_experiment, source_state
    from src.promptops.runtime import prompt_scope

    baseline_path = PROJECT_ROOT / "evaluation/baseline/supportgpt_baseline_100.json"
    cases = load_evaluation_dataset(baseline_path, validate_agent=False, validate_security=False)
    if args.limit is not None and not 1 <= args.limit <= 100:
        raise ValueError("--limit must be between 1 and 100.")
    current = registry.register(registry.resolve(args.environment).payload())
    from src.promptops.defaults import default_payload
    candidate = registry.register(default_payload()) if args.bundle == "default" else registry.load(args.bundle)
    source = source_state()
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    metadata = {"mode": "ci_offline_workflow_replay", "suite": "full", "llm_provider": "mock"}
    if settings.DECISION_PROVIDER == "jev_replay":
        from src.decision.replay import recording_sha256
        metadata.update({
            "decision_provider": "jev_replay",
            "decision_recording_sha256": recording_sha256(Path(settings.DECISION_REPLAY_PATH)),
        })
    if args.live:
        selected = [case.id for case in cases[:args.limit]] if args.limit else None
        plan = build_real_llm_run_plan(
            settings=settings, cases=cases, suite="full", explicit_case_ids=selected,
            max_workflow_calls=args.max_workflow_calls // 2,
        )
        require_live_confirmation(args.confirm_live)
        metadata = plan.report_metadata()
    # 不加载可变部署指针覆盖本次实验，Git SHA 使用实际工作树而非外部注入值。
    os.environ["GIT_COMMIT_SHA"] = source["revision"]
    await init_db()
    with tempfile.TemporaryDirectory(prefix="promptops-reports-") as temporary:
        reports = []
        for label, bundle in (("current", current), ("candidate", candidate)):
            print(f"Replaying {label}: {bundle.version} ({bundle.bundle_id})", flush=True)
            with prompt_scope(bundle):
                paths = await run_baseline_evaluation_v1(
                    baseline_path, Path(temporary) / label, limit=args.limit,
                    execution_metadata={**metadata, "promptops_role": label},
                )
            reports.append(json.loads(paths["snapshot_json"].read_text(encoding="utf-8")))
        if source_state() != source:
            raise ValueError("Source state changed during evaluation; rerun the experiment.")
        experiment_path = save_experiment(
            registry, bundle=candidate, report=reports[1], baseline=reports[0],
            policy=policy, environment=args.environment,
            expected_current=current.bundle_id, source=source,
        )
    evidence = json.loads(experiment_path.read_text(encoding="utf-8"))
    print(f"Experiment: {experiment_path}")
    print(f"Markdown: {experiment_path.with_suffix('.md')}")
    print("PASS" if evidence["assessment"]["passed"] else "FAIL")
    return 0 if evidence["assessment"]["passed"] else 1


def main() -> int:
    """CLI 使用文件系统发布权限，不暴露可修改策略的公共 HTTP API。"""
    args = parse_args()
    with tempfile.TemporaryDirectory(prefix="promptops-runtime-") as runtime:
        if args.command == "evaluate":
            # 必须在导入 Settings / Provider 前隔离，防止评测写入开发业务库。
            os.environ.update({
                "APP_ENV": "testing", "DATABASE_URL": f"sqlite+aiosqlite:///{runtime}/eval.db",
                "VECTOR_DB_PERSIST_DIR": f"{runtime}/chromadb", "OTEL_ENABLED": "false",
                "TOOL_OUTBOX_WORKER_ENABLED": "false", "PROMPT_BUNDLE_ID": "",
                "LANGGRAPH_CHECKPOINT_ENABLED": "false",
            })
            if args.mock:
                from scripts.run_ci_quality_gate import _configure_offline_environment
                _configure_offline_environment(Path(runtime))

        from src.config import settings
        from src.promptops.defaults import default_payload
        from src.promptops.registry import PromptRegistry, atomic_json

        registry = PromptRegistry(args.registry or Path(settings.PROMPT_REGISTRY_DIR))
        if args.command == "evaluate":
            return asyncio.run(evaluate(args, registry))
        if args.command == "export-default":
            if args.output.exists():
                raise ValueError("Output exists; choose a new candidate path.")
            atomic_json(args.output, default_payload())
            print(args.output)
        elif args.command == "register":
            bundle = registry.register(json.loads(args.file.read_text(encoding="utf-8")))
            print(json.dumps(bundle.metadata(), ensure_ascii=False, indent=2))
        elif args.command == "status":
            value = registry.state()
            value["effective"] = {env: registry.resolve(env).metadata() for env in ("staging", "production")}
            print(json.dumps(value, ensure_ascii=False, indent=2))
        elif args.command == "promote":
            from src.promptops.experiments import promote_experiment
            event = promote_experiment(
                registry, experiment_id=args.experiment,
                policy=json.loads(args.policy.read_text(encoding="utf-8")),
                actor=args.actor, reason=args.reason,
            )
            print(json.dumps(event, ensure_ascii=False, indent=2))
        elif args.command == "rollback":
            print(json.dumps(registry.rollback(
                environment=args.environment, actor=args.actor, reason=args.reason,
            ), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError) as exc:
        print(f"PromptOps: {exc}", file=sys.stderr)
        raise SystemExit(2)
