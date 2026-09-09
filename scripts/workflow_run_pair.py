"""Run a reusable-workflow matrix case with the same runtime path as the action."""

import json
import os
import pathlib
import subprocess
import sys

from build_cache import cache_root


def main() -> int:
    case = json.loads(os.environ["CASE_JSON"])
    repository = pathlib.Path("repo").resolve()
    artifacts = pathlib.Path("artifacts").resolve()
    artifacts.mkdir(exist_ok=True)
    os.environ["RUST_PR_BENCH_RUNNER_CACHE"] = str(cache_root(repository) / "runners")
    os.environ["RUST_PR_BENCH_PERFORMANCE_DIR"] = str(artifacts)
    os.environ["RUST_PR_BENCH_CACHE_RUNNER_WRITER"] = str(
        case.get("runner_cache_writer", False)
    ).lower()
    command = [
        sys.executable,
        str(pathlib.Path(__file__).with_name("run_pair.py")),
        "--repo-path",
        str(repository),
    ]
    values = {
        "working-directory": os.environ["WORKING_DIRECTORY"],
        "benchmark-name": case["benchmark_name"],
        "feature-name": case["feature_name"],
        "command": case["command"],
        "head-command": case["head_command"],
        "base-command": case["base_command"],
        "move-source": case.get("move_source") or "",
        "move-target": case.get("move_target") or "",
        "move-candidates": json.dumps(case.get("move_candidates", [])),
        "backend": case["backend"],
        "criterion-statistic": os.environ["CRITERION_STATISTIC"],
        "head-sha": os.environ["HEAD_SHA"],
        "base-sha": os.environ["BASE_SHA"],
        "output": str(artifacts / "result.json"),
    }
    for name in (
        "setup-command",
        "readiness-command",
        "teardown-command",
        "readiness-timeout-seconds",
    ):
        values[name] = os.environ[name.upper().replace("-", "_")]
    for side in ("head", "base"):
        values[f"{side}-precompiled"] = str(
            pathlib.Path("precompiled") / side / case["id"]
        )
        values[f"{side}-run-args"] = case.get(f"{side}_run_args", "")
    command.extend(f"--{key}={value}" for key, value in values.items())
    if case.get("moved"):
        command.append("--moved")
    if case.get("move_ambiguous"):
        command.append("--move-ambiguous")
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
