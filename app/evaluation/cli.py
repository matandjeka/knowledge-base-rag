"""Execute deterministic or live evaluation and enforce deployment gates."""

import argparse
import asyncio
from pathlib import Path

from app.api.dependencies import get_query_service
from app.core.config import get_settings
from app.evaluation.execution import (
    OpenAIFaithfulnessJudge,
    QueryServiceEvaluator,
    build_deterministic_query_service,
)
from app.evaluation.framework import EvaluationTier, RetrievalConfiguration
from app.evaluation.reporting import attach_baseline, load_benchmark, load_report, write_report
from app.evaluation.runner import run_evaluation


def main() -> int:
    """Run the real application evaluation path and write an immutable report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--baseline-report", type=Path)
    parser.add_argument("--establish-baseline", action="store_true")
    parser.add_argument(
        "--tier", choices=tuple(EvaluationTier), default=EvaluationTier.DETERMINISTIC
    )
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--workspace-id", default="local")
    parser.add_argument("--judge-model")
    arguments = parser.parse_args()
    if bool(arguments.baseline_report) == arguments.establish_baseline:
        parser.error("choose exactly one of --baseline-report or --establish-baseline")
    tier = EvaluationTier(arguments.tier)
    if tier is EvaluationTier.LIVE and not arguments.allow_live:
        parser.error("live evaluation requires --allow-live")
    if arguments.judge_model and tier is not EvaluationTier.LIVE:
        parser.error("--judge-model is only available for live evaluation")
    return asyncio.run(_run(arguments))


async def _run(arguments: argparse.Namespace) -> int:
    benchmark = load_benchmark(arguments.benchmark)
    configuration = RetrievalConfiguration.model_validate_json(arguments.configuration.read_text())
    if EvaluationTier(arguments.tier) is EvaluationTier.DETERMINISTIC:
        service = await build_deterministic_query_service(benchmark)
        evaluator = QueryServiceEvaluator(service)
    else:
        settings = get_settings()
        judge = None
        if arguments.judge_model:
            if settings.openai_api_key is None:
                raise ValueError("OPENAI_API_KEY is required for live faithfulness judging")
            judge = OpenAIFaithfulnessJudge(
                api_key=settings.openai_api_key.get_secret_value(),
                model_name=arguments.judge_model,
                timeout_seconds=60,
                max_retries=2,
            )
        evaluator = QueryServiceEvaluator(
            get_query_service(), workspace_id=arguments.workspace_id, judge=judge
        )
    report = await run_evaluation(
        benchmark,
        configuration,
        evaluator,
        tier=EvaluationTier(arguments.tier),
        allow_live=arguments.allow_live,
        judge_model=evaluator.judge_model,
    )
    if arguments.baseline_report:
        report = attach_baseline(report, load_report(arguments.baseline_report))
    path = write_report(report, arguments.output_directory)
    print(path)
    if arguments.establish_baseline:
        return 0
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
