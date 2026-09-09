#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import time
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from backend_names import GUNGRAUN_BACKEND, normalize_backend  # noqa: E402
from precompiled_runtime import command_for  # noqa: E402


ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
READINESS_RETRY_INTERVAL_SECONDS = 1.0


def parse_callgrind_summary(path: pathlib.Path) -> int | None:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for _ in range(300):
                line = handle.readline()
                if not line:
                    break
                if line.startswith("summary:"):
                    _, value = line.split(":", 1)
                    # Newer callgrind formats can emit multiple values in summary.
                    # We compare the primary event (first value) for compatibility.
                    first_token = value.strip().split()[0]
                    return int(first_token)
    except (OSError, ValueError, IndexError):
        return None
    return None


def scan_callgrind_files(target_dir: pathlib.Path) -> list[pathlib.Path]:
    if not target_dir.exists():
        return []

    candidates: list[pathlib.Path] = []
    for path in target_dir.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        if "callgrind.out" in name or name.startswith("callgrind."):
            candidates.append(path)
    return candidates


def normalize_callgrind_metric_name(path: pathlib.Path) -> str:
    # callgrind filenames can include run-specific numeric suffixes (for example PID).
    # Strip trailing ".<digits>" and normalize the renamed output directory so an
    # iai-callgrind base can be paired with a Gungraun head.
    parts = [
        GUNGRAUN_BACKEND if part in {"iai", "gungraun"} else part
        for part in path.parts
    ]
    normalized = re.sub(r"\.\d+$", "", pathlib.PurePosixPath(*parts).as_posix())
    return normalized


def scan_criterion_estimate_files(target_dir: pathlib.Path) -> list[pathlib.Path]:
    criterion_dir = target_dir / "criterion"
    if not criterion_dir.exists():
        return []

    candidates: list[pathlib.Path] = []
    for path in criterion_dir.rglob("estimates.json"):
        if path.is_file():
            candidates.append(path)
    return candidates


def criterion_metric_name(path: pathlib.Path, target_dir: pathlib.Path) -> str:
    rel = path.relative_to(target_dir / "criterion")
    parts = list(rel.parts)
    if parts and parts[-1] == "estimates.json":
        parts = parts[:-1]
    if parts and parts[-1] in {"new", "base"}:
        parts = parts[:-1]
    return "/".join(parts) if parts else rel.as_posix()


def parse_criterion_estimate(path: pathlib.Path, statistic: str) -> dict[str, float] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    metric = data.get(statistic)
    if not isinstance(metric, dict):
        return None

    point = metric.get("point_estimate")
    if point is None:
        return None

    try:
        value = float(point)
    except (TypeError, ValueError):
        return None

    conf = metric.get("confidence_interval")
    lower: float | None = None
    upper: float | None = None
    if isinstance(conf, dict):
        low = conf.get("lower_bound")
        high = conf.get("upper_bound")
        try:
            lower = float(low) if low is not None else None
        except (TypeError, ValueError):
            lower = None
        try:
            upper = float(high) if high is not None else None
        except (TypeError, ValueError):
            upper = None

    payload: dict[str, float] = {"value": value}
    if lower is not None:
        payload["lower"] = lower
    if upper is not None:
        payload["upper"] = upper
    return payload


def select_recent_files(
    files: list[pathlib.Path], start_ns: int, before_paths: set[str], fallback_limit: int
) -> list[pathlib.Path]:
    selected: list[pathlib.Path] = []
    for path in files:
        stat = path.stat()
        if str(path) not in before_paths or stat.st_mtime_ns >= start_ns:
            selected.append(path)
    if not selected:
        selected = sorted(files, key=lambda p: p.stat().st_mtime_ns, reverse=True)[:fallback_limit]
    return selected


def collect_callgrind_metrics(
    target_dir: pathlib.Path, start_ns: int, before_paths: set[str]
) -> dict[str, Any]:
    files = scan_callgrind_files(target_dir)
    selected = select_recent_files(files, start_ns, before_paths, fallback_limit=20)

    metrics: list[dict[str, Any]] = []
    for path in sorted(selected):
        summary = parse_callgrind_summary(path)
        if summary is None:
            continue
        metrics.append(
            {
                "metric": normalize_callgrind_metric_name(path.relative_to(target_dir)),
                "value": summary,
            }
        )

    if not metrics:
        return {"total": 0, "metrics": [], "missing": True, "missing_reason": "no callgrind metrics found"}

    total = sum(item["value"] for item in metrics)
    return {"total": total, "metrics": metrics, "metric_unit": "events"}


def collect_criterion_metrics(
    target_dir: pathlib.Path, start_ns: int, before_paths: set[str], statistic: str
) -> dict[str, Any]:
    files = scan_criterion_estimate_files(target_dir)
    selected = select_recent_files(files, start_ns, before_paths, fallback_limit=200)

    metrics: list[dict[str, Any]] = []
    for path in sorted(selected):
        estimate = parse_criterion_estimate(path, statistic)
        if not estimate:
            continue
        metric_entry: dict[str, Any] = {
            "metric": criterion_metric_name(path, target_dir),
            "value": estimate["value"],
            "statistic": statistic,
            "unit": "ns",
        }
        if "lower" in estimate:
            metric_entry["lower"] = estimate["lower"]
        if "upper" in estimate:
            metric_entry["upper"] = estimate["upper"]
        metrics.append(metric_entry)

    if not metrics:
        return {
            "total": 0,
            "metrics": [],
            "missing": True,
            "missing_reason": "no criterion estimates found",
        }

    total = sum(float(item["value"]) for item in metrics)
    return {
        "total": total,
        "metrics": metrics,
        "metric_unit": "ns",
        "comparison_statistic": statistic,
    }


def paired_metric_totals(
    base_metrics: list[dict[str, Any]], head_metrics: list[dict[str, Any]]
) -> tuple[float, float, int]:
    """Return totals formed only from metric identities present on both sides."""
    base_values = {item["metric"]: float(item["value"]) for item in base_metrics}
    head_values = {item["metric"]: float(item["value"]) for item in head_metrics}
    shared_names = base_values.keys() & head_values.keys()
    return (
        sum(base_values[name] for name in shared_names),
        sum(head_values[name] for name in shared_names),
        len(shared_names),
    )


def detect_missing_bench(command: str, cwd: pathlib.Path) -> str | None:
    try:
        parts = shlex.split(command)
    except ValueError:
        return None

    if not parts or parts[0] != "cargo":
        return None
    if "bench" not in parts:
        return None
    if "--bench" not in parts:
        return None
    if "--manifest-path" in parts or "--package" in parts or "-p" in parts:
        return None

    bench_index = parts.index("--bench") + 1
    if bench_index >= len(parts):
        return None
    bench_name = parts[bench_index]
    bench_path = cwd / "benches" / f"{bench_name}.rs"
    if not bench_path.exists():
        return f"missing bench file {bench_path.as_posix()}"
    return None


def is_missing_bench_error(output: str) -> bool:
    lowered = output.lower()
    return (
        "no bench target named" in lowered
        or "could not find bench" in lowered
        or "no benchmark target named" in lowered
    )

def is_missing_feature_error(output: str) -> bool:
    lowered = output.lower()
    return (
        "does not have the feature" in lowered
        or "does not have these features" in lowered
        or "does not contain this feature" in lowered
        or "does not contain these features" in lowered
        or "unknown feature" in lowered
        or "feature `" in lowered and " is not defined" in lowered
        or "no such feature" in lowered
    )


def runner_version_mismatch_family(output: str) -> str | None:
    lowered = output.lower()
    mismatch_text = any(
        marker in lowered
        for marker in ("version mismatch", " is newer than ", " is older than ")
    )
    if not mismatch_text:
        return None
    if "gungraun-runner" in lowered:
        return "gungraun"
    if "iai-callgrind-runner" in lowered:
        return "iai-callgrind"
    return None


def captured_output(stdout: str | bytes | None, stderr: str | bytes | None) -> str:
    def as_text(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value

    sections = []
    stdout_text = as_text(stdout).strip()
    stderr_text = as_text(stderr).strip()
    if stdout_text:
        sections.append(stdout_text)
    if stderr_text:
        sections.append(stderr_text)
    return "\n".join(sections)


def run_lifecycle_command(
    command: str,
    cwd: pathlib.Path,
    env: dict[str, str],
    *,
    timeout: float | None = None,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", command],
            cwd=cwd,
            check=False,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "exit_code": 124,
            "output": captured_output(exc.stdout, exc.stderr),
            "timed_out": True,
        }
    return {
        "exit_code": completed.returncode,
        "output": captured_output(completed.stdout, completed.stderr),
        "timed_out": False,
    }


def run_readiness_command(
    command: str,
    cwd: pathlib.Path,
    env: dict[str, str],
    timeout_seconds: float,
    *,
    retry_interval_seconds: float = READINESS_RETRY_INTERVAL_SECONDS,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    attempt_logs: list[str] = []
    attempts = 0
    last_result: dict[str, Any] = {
        "exit_code": 124,
        "output": "",
        "timed_out": True,
    }

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        attempts += 1
        last_result = run_lifecycle_command(command, cwd, env, timeout=remaining)
        attempt_output = str(last_result.get("output") or "")
        attempt_log = f"attempt {attempts}: exit {last_result['exit_code']}"
        if attempt_output:
            attempt_log += f"\n{attempt_output}"
        attempt_logs.append(attempt_log)
        if last_result["exit_code"] == 0:
            return {
                **last_result,
                "attempts": attempts,
                "output": "\n\n".join(attempt_logs),
            }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(retry_interval_seconds, remaining))

    return {
        "exit_code": 124,
        "timed_out": True,
        "attempts": attempts,
        "output": "\n\n".join(attempt_logs),
        "last_exit_code": last_result.get("exit_code"),
    }


def load_lifecycle_environment(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.lstrip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        if "=" not in line:
            raise ValueError(
                f"invalid lifecycle environment entry on line {line_number}: expected NAME=value"
            )
        name, value = line.split("=", 1)
        name = name.strip()
        if not ENVIRONMENT_NAME.fullmatch(name):
            raise ValueError(
                f"invalid lifecycle environment name {name!r} on line {line_number}"
            )
        if name == "CARGO_TARGET_DIR" or name.startswith("RUST_PR_BENCH_"):
            raise ValueError(f"lifecycle environment may not override {name}")
        if "\x00" in value:
            raise ValueError(
                f"invalid null byte in lifecycle environment value on line {line_number}"
            )
        values[name] = value
    return values


def lifecycle_execution_id(backend: str, case_slug: str, side: str) -> str:
    raw = ":".join(
        (
            os.environ.get("GITHUB_RUN_ID", "local"),
            os.environ.get("GITHUB_RUN_ATTEMPT", "1"),
            os.environ.get("GITHUB_JOB", "benchmark"),
            backend,
            case_slug,
            side,
            str(os.getpid()),
            os.urandom(8).hex(),
        )
    )
    readable = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return f"{readable[:48].rstrip('-')}-{digest}"


def write_lifecycle_log(
    log_dir: pathlib.Path,
    side: str,
    stage: str,
    stage_result: dict[str, Any],
) -> pathlib.Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{side}.{stage}.log"
    lines = [f"exit_code={stage_result['exit_code']}"]
    if stage_result.get("attempts") is not None:
        lines.append(f"attempts={stage_result['attempts']}")
    if stage_result.get("timed_out"):
        lines.append("timed_out=true")
    output = str(stage_result.get("output") or "")
    if output:
        lines.extend(("", output))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def lifecycle_metadata(
    stage_result: dict[str, Any], log_path: pathlib.Path
) -> dict[str, Any]:
    metadata = {
        "exit_code": stage_result["exit_code"],
        "log": log_path.name,
    }
    if stage_result.get("attempts") is not None:
        metadata["attempts"] = stage_result["attempts"]
    if stage_result.get("timed_out"):
        metadata["timed_out"] = True
    return metadata


def execution_error(
    stage: str, stage_result: dict[str, Any], reason: str
) -> dict[str, Any]:
    output = str(stage_result.get("output") or "")
    return {
        "total": 0,
        "metrics": [],
        "error": True,
        "error_code": stage_result["exit_code"],
        "error_output": f"[{stage}]\n{output}".strip(),
        "error_reason": reason,
        "error_stage": stage,
    }


def add_teardown_error(
    result: dict[str, Any], stage_result: dict[str, Any]
) -> dict[str, Any]:
    teardown_output = str(stage_result.get("output") or "")
    teardown_block = f"[teardown]\n{teardown_output}".strip()
    if result.get("error"):
        existing_output = str(result.get("error_output") or "").strip()
        result["error_output"] = "\n\n".join(
            item for item in (existing_output, teardown_block) if item
        )
        existing_reason = str(result.get("error_reason") or "command failed")
        result["error_reason"] = f"{existing_reason}; teardown command failed"
        existing_stage = str(result.get("error_stage") or "benchmark")
        result["error_stage"] = f"{existing_stage}+teardown"
        result["teardown_error_code"] = stage_result["exit_code"]
        return result
    return execution_error("teardown", stage_result, "teardown command failed")


def run_command(
    command: str,
    cwd: pathlib.Path,
    target_dir: pathlib.Path,
    backend: str,
    criterion_statistic: str,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    missing_reason = detect_missing_bench(command, cwd)
    if missing_reason:
        return {"total": 0, "metrics": [], "missing": True, "missing_reason": missing_reason}

    if backend == "criterion":
        before = {str(path) for path in scan_criterion_estimate_files(target_dir)}
    else:
        before = {str(path) for path in scan_callgrind_files(target_dir)}
    start_ns = time.time_ns()
    env = (environment or os.environ).copy()
    env["CARGO_TARGET_DIR"] = str(target_dir)

    try:
        subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            check=True,
            env=env,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        output = (exc.stdout or "") + "\n" + (exc.stderr or "")
        if is_missing_bench_error(output):
            return {"total": 0, "metrics": [], "missing": True, "missing_reason": "bench target not found"}
        if is_missing_feature_error(output):
            return {"total": 0, "metrics": [], "missing": True, "missing_reason": "feature not available"}
        mismatch_family = runner_version_mismatch_family(output)
        error_reason = (
            f"{mismatch_family} runner/library version mismatch"
            if mismatch_family
            else None
        )
        return {
            "total": 0,
            "metrics": [],
            "error": True,
            "error_code": exc.returncode,
            "error_output": output.strip(),
            "error_reason": error_reason or "benchmark command failed",
            "error_stage": "benchmark",
        }
    if backend == "criterion":
        return collect_criterion_metrics(target_dir, start_ns, before, criterion_statistic)
    return collect_callgrind_metrics(target_dir, start_ns, before)


def git_checkout(repo_path: pathlib.Path, ref: str) -> None:
    subprocess.run(["git", "checkout", "--force", "--quiet", ref], cwd=repo_path, check=True)


def run_execution(
    command: str,
    cwd: pathlib.Path,
    target_dir: pathlib.Path,
    backend: str,
    criterion_statistic: str,
    *,
    side: str,
    case_slug: str,
    repository: pathlib.Path,
    log_dir: pathlib.Path,
    setup_command: str = "",
    readiness_command: str = "",
    teardown_command: str = "",
    readiness_timeout_seconds: float = 60.0,
    readiness_retry_interval_seconds: float = READINESS_RETRY_INTERVAL_SECONDS,
) -> dict[str, Any]:
    target_dir.mkdir(parents=True, exist_ok=True)
    environment_file = target_dir / "lifecycle.env"
    environment_file.write_text("", encoding="utf-8")
    environment = os.environ.copy()
    environment.update(
        {
            "CARGO_TARGET_DIR": str(target_dir),
            "RUST_PR_BENCH_SIDE": side,
            "RUST_PR_BENCH_EXECUTION_ID": lifecycle_execution_id(
                backend, case_slug, side
            ),
            "RUST_PR_BENCH_ENV_FILE": str(environment_file),
            "RUST_PR_BENCH_REPOSITORY": str(repository),
            "RUST_PR_BENCH_WORKING_DIRECTORY": str(cwd),
        }
    )
    lifecycle: dict[str, Any] = {}
    result: dict[str, Any] | None = None

    try:
        if setup_command:
            setup = run_lifecycle_command(setup_command, cwd, environment)
            setup_log = write_lifecycle_log(log_dir, side, "setup", setup)
            lifecycle["setup"] = lifecycle_metadata(setup, setup_log)
            if setup["exit_code"] != 0:
                result = execution_error("setup", setup, "setup command failed")

        try:
            environment.update(load_lifecycle_environment(environment_file))
        except (OSError, UnicodeError, ValueError) as exc:
            environment_result = {
                "exit_code": 2,
                "output": str(exc),
                "timed_out": False,
            }
            environment_log = write_lifecycle_log(
                log_dir, side, "environment", environment_result
            )
            lifecycle["environment"] = lifecycle_metadata(
                environment_result, environment_log
            )
            if result is None:
                result = execution_error(
                    "environment",
                    environment_result,
                    "lifecycle environment file is invalid",
                )

        if result is None and readiness_command:
            readiness = run_readiness_command(
                readiness_command,
                cwd,
                environment,
                readiness_timeout_seconds,
                retry_interval_seconds=readiness_retry_interval_seconds,
            )
            readiness_log = write_lifecycle_log(
                log_dir, side, "readiness", readiness
            )
            lifecycle["readiness"] = lifecycle_metadata(
                readiness, readiness_log
            )
            if readiness["exit_code"] != 0:
                result = execution_error(
                    "readiness",
                    readiness,
                    f"readiness command timed out after {readiness_timeout_seconds:g} seconds",
                )

        if result is None:
            result = run_command(
                command,
                cwd,
                target_dir,
                backend,
                criterion_statistic,
                environment,
            )
    finally:
        if teardown_command:
            teardown = run_lifecycle_command(teardown_command, cwd, environment)
            teardown_log = write_lifecycle_log(
                log_dir, side, "teardown", teardown
            )
            lifecycle["teardown"] = lifecycle_metadata(teardown, teardown_log)
            if teardown["exit_code"] != 0:
                if result is None:
                    result = execution_error(
                        "teardown", teardown, "teardown command failed"
                    )
                else:
                    result = add_teardown_error(result, teardown)
        environment_file.unlink(missing_ok=True)

    if result is None:
        raise RuntimeError(f"{side} benchmark execution did not produce a result")
    result["lifecycle"] = lifecycle
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-path", required=True)
    parser.add_argument("--working-directory", default=".")
    parser.add_argument("--benchmark-name", required=True)
    parser.add_argument("--feature-name", required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument("--head-command")
    parser.add_argument("--base-command")
    parser.add_argument("--moved", action="store_true")
    parser.add_argument("--move-source")
    parser.add_argument("--move-target")
    parser.add_argument("--move-ambiguous", action="store_true")
    parser.add_argument("--move-candidates", default="[]")
    parser.add_argument("--backend", default=GUNGRAUN_BACKEND)
    parser.add_argument("--criterion-statistic", default="mean", choices=("mean", "median"))
    parser.add_argument("--setup-command", default="")
    parser.add_argument("--readiness-command", default="")
    parser.add_argument("--teardown-command", default="")
    parser.add_argument("--readiness-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-precompiled", default="")
    parser.add_argument("--base-precompiled", default="")
    parser.add_argument("--head-run-args", default="")
    parser.add_argument("--base-run-args", default="")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    if args.readiness_timeout_seconds <= 0:
        parser.error("--readiness-timeout-seconds must be greater than zero")

    backend = normalize_backend(args.backend)
    repo_path = pathlib.Path(args.repo_path).resolve()
    workdir = (repo_path / args.working_directory).resolve()
    if not workdir.exists():
        raise FileNotFoundError(f"working directory does not exist: {workdir}")

    case_slug = f"{args.benchmark_name}-{args.feature_name}".replace(" ", "-")
    head_target = repo_path / ".bench-target" / backend / case_slug / "head"
    base_target = repo_path / ".bench-target" / backend / case_slug / "base"
    head_target.mkdir(parents=True, exist_ok=True)
    base_target.mkdir(parents=True, exist_ok=True)

    head_command = args.head_command or args.command
    base_command = args.base_command or args.command
    output_path = pathlib.Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        git_checkout(repo_path, args.head_sha)
        if args.head_precompiled:
            head_command = command_for(
                pathlib.Path(args.head_precompiled), args.head_run_args, head_command, workdir=workdir
            )
        head = run_execution(
            head_command,
            workdir,
            head_target,
            backend,
            args.criterion_statistic,
            side="head",
            case_slug=case_slug,
            repository=repo_path,
            log_dir=output_path.parent,
            setup_command=args.setup_command,
            readiness_command=args.readiness_command,
            teardown_command=args.teardown_command,
            readiness_timeout_seconds=args.readiness_timeout_seconds,
        )

        git_checkout(repo_path, args.base_sha)
        if args.base_precompiled:
            base_command = command_for(
                pathlib.Path(args.base_precompiled), args.base_run_args, base_command, workdir=workdir
            )
        base = run_execution(
            base_command,
            workdir,
            base_target,
            backend,
            args.criterion_statistic,
            side="base",
            case_slug=case_slug,
            repository=repo_path,
            log_dir=output_path.parent,
            setup_command=args.setup_command,
            readiness_command=args.readiness_command,
            teardown_command=args.teardown_command,
            readiness_timeout_seconds=args.readiness_timeout_seconds,
        )
    finally:
        git_checkout(repo_path, args.head_sha)

    base_observed_total = base.get("total", 0)
    head_observed_total = head.get("total", 0)
    comparable_metric_count = 0
    if base.get("missing") or head.get("missing") or base.get("error") or head.get("error"):
        base_total = base_observed_total
        head_total = head_observed_total
        delta = 0
        delta_pct = float("nan")
    else:
        base_total, head_total, comparable_metric_count = paired_metric_totals(
            base["metrics"], head["metrics"]
        )
        delta = head_total - base_total
        if comparable_metric_count == 0:
            delta_pct = float("nan")
        else:
            delta_pct = ((delta / base_total) * 100.0) if base_total else 0.0

    result = {
        "backend": backend,
        "benchmark_name": args.benchmark_name,
        "feature_name": args.feature_name,
        "command": args.command,
        "head_command": head_command,
        "base_command": base_command,
        "moved": bool(args.moved),
        "move_source": args.move_source,
        "move_target": args.move_target,
        "move_ambiguous": bool(args.move_ambiguous),
        "move_candidates": json.loads(args.move_candidates),
        "base_total": base_total,
        "head_total": head_total,
        "base_observed_total": base_observed_total,
        "head_observed_total": head_observed_total,
        "comparable_metric_count": comparable_metric_count,
        "delta": delta,
        "delta_pct": delta_pct,
        "head_metrics": head["metrics"],
        "base_metrics": base["metrics"],
        "head_missing": bool(head.get("missing")),
        "base_missing": bool(base.get("missing")),
        "head_missing_reason": head.get("missing_reason"),
        "base_missing_reason": base.get("missing_reason"),
        "head_error": bool(head.get("error")),
        "base_error": bool(base.get("error")),
        "head_error_code": head.get("error_code"),
        "base_error_code": base.get("error_code"),
        "head_error_reason": head.get("error_reason"),
        "base_error_reason": base.get("error_reason"),
        "head_error_output": head.get("error_output"),
        "base_error_output": base.get("error_output"),
        "head_error_stage": head.get("error_stage"),
        "base_error_stage": base.get("error_stage"),
        "head_lifecycle": head.get("lifecycle", {}),
        "base_lifecycle": base.get("lifecycle", {}),
        "metric_unit": head.get("metric_unit") or base.get("metric_unit"),
        "comparison_statistic": head.get("comparison_statistic")
        or base.get("comparison_statistic")
        or ("mean" if backend == "criterion" else "summary"),
    }

    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    if head.get("error") or base.get("error"):
        output_dir = pathlib.Path(args.output).resolve().parent

        def emit_error(label: str, data: dict[str, Any]) -> None:
            if not data.get("error"):
                return
            output = data.get("error_output") or ""
            if not output:
                return
            reason = data.get("error_reason")
            if reason and reason.endswith("runner/library version mismatch"):
                family = reason.removesuffix(" runner/library version mismatch")
                runner = f"{family}-runner"
                print(
                    f"[{label}] error: {runner} does not match the {family} library. "
                    "Rust PR Bench normally dispatches the exact requested version; ensure "
                    "IAI_CALLGRIND_RUNNER and GUNGRAUN_RUNNER were not overridden, then retry."
                )
            log_path = output_dir / f"{label}.error.log"
            log_path.write_text(output, encoding="utf-8")
            print(f"[{label}] command failed; full output:")
            print(output)
            print(f"[{label}] full output written to {log_path.as_posix()}")

        emit_error("head", head)
        emit_error("base", base)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
