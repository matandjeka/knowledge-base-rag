"""Immutable evaluation report comparison, serialization, and loading."""

import json
import os
import re
import tempfile
from pathlib import Path

from app.evaluation.framework import EvaluationReport, GoldenBenchmark
from app.evaluation.metrics import compare_with_baseline

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9_.-]+")


def load_benchmark(path: Path) -> GoldenBenchmark:
    """Load and strictly validate one committed golden benchmark."""
    return GoldenBenchmark.model_validate_json(path.read_text())


def load_report(path: Path) -> EvaluationReport:
    """Load and strictly validate one immutable evaluation artifact."""
    return EvaluationReport.model_validate_json(path.read_text())


def attach_baseline(candidate: EvaluationReport, baseline: EvaluationReport) -> EvaluationReport:
    """Compare compatible reports and attach explicit deployment gates."""
    if candidate.benchmark_sha256 != baseline.benchmark_sha256:
        raise ValueError("Evaluation reports use different benchmark content")
    gates = compare_with_baseline(
        candidate.aggregate,
        baseline.aggregate,
        candidate.slices,
        baseline.slices,
    )
    return candidate.model_copy(
        update={
            "baseline_configuration": baseline.configuration.name,
            "gates": gates,
            "passed": all(gate.passed for gate in gates),
        }
    )


def write_report(report: EvaluationReport, output_directory: Path) -> Path:
    """Write a new report without overwriting an existing evaluation artifact."""
    output_directory.mkdir(parents=True, exist_ok=True)
    timestamp = report.created_at.strftime("%Y%m%dT%H%M%S%fZ")
    configuration = _SAFE_FILENAME.sub("-", report.configuration.name).strip("-.")
    filename = f"{timestamp}-{configuration}-{report.benchmark_sha256[:12]}.json"
    target = output_directory / filename
    if target.exists():
        raise FileExistsError(f"Evaluation report already exists: {target.name}")
    payload = report.model_dump(mode="json")
    for observation in payload["observations"]:
        for segment in observation["answer_segments"]:
            segment["text"] = "[redacted]"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=output_directory, prefix=".evaluation-", suffix=".tmp", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.link(temporary_path, target)
    except FileExistsError as error:
        raise FileExistsError(f"Evaluation report already exists: {target.name}") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return target
