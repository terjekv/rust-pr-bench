#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from resolve_threshold import resolve_gungraun_threshold

SUPPORTED_BACKENDS = {"gungraun", "criterion", "all"}
SUPPORTED_COMMENT_MODES = {"always", "on-regression", "never"}


def parse_bool(value: str, name: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"{name} must be 'true' or 'false'")


def parse_threshold(value: str, name: str, *, allow_unset: bool = False) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    if parsed < 0 and not (allow_unset and parsed == -1):
        suffix = " or -1" if allow_unset else ""
        raise ValueError(f"{name} must be non-negative{suffix}")
    return parsed


def action_input(name: str, default: str = "") -> str:
    key = f"RUST_PR_BENCH_{name.upper()}"
    return os.environ.get(key, default)


def load_event() -> dict[str, Any]:
    path = os.environ.get("GITHUB_EVENT_PATH", "").strip()
    if not path:
        return {}
    try:
        payload = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def resolve_revisions(event: dict[str, Any]) -> tuple[str, str, int | None]:
    pull_request = event.get("pull_request")
    pr = pull_request if isinstance(pull_request, dict) else {}
    base = pr.get("base") if isinstance(pr.get("base"), dict) else {}
    head = pr.get("head") if isinstance(pr.get("head"), dict) else {}

    base_sha = action_input("base_sha").strip() or str(base.get("sha") or "").strip()
    head_sha = (
        action_input("head_sha").strip()
        or str(head.get("sha") or "").strip()
        or os.environ.get("GITHUB_SHA", "").strip()
    )
    if not base_sha:
        raise ValueError(
            "unable to resolve the base revision; pass base_sha when not running on pull_request"
        )
    if not head_sha:
        raise ValueError("unable to resolve the head revision; pass head_sha")

    number = pr.get("number") or event.get("number")
    pr_number = int(number) if isinstance(number, int) or str(number).isdigit() else None
    return base_sha, head_sha, pr_number


def run(command: list[str], *, cwd: pathlib.Path | None = None, check: bool = True) -> int:
    completed = subprocess.run(command, cwd=cwd, check=check)
    return completed.returncode


def git_commit_exists(repository: pathlib.Path, revision: str) -> bool:
    completed = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
        cwd=repository,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def write_action_outputs(values: dict[str, str]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output_path:
        return
    with pathlib.Path(output_path).open("a", encoding="utf-8") as handle:
        handle.writelines(f"{key}={value}\n" for key, value in values.items())


def prepare_regression_overrides(work_dir: pathlib.Path, approval_label: str) -> pathlib.Path:
    output = work_dir / "regression-overrides.json"
    command = [
        sys.executable,
        str(SCRIPT_DIR / "regression_overrides.py"),
        "--approval-label",
        approval_label,
        "--output",
        str(output),
    ]
    metadata = work_dir / "pr-regression-overrides.json"
    if metadata.exists():
        command.extend(["--pr-metadata", str(metadata)])
    run(command)
    return output


def expand_cases(
    repository: pathlib.Path,
    work_dir: pathlib.Path,
    base_sha: str,
    head_sha: str,
    backend: str,
) -> list[dict[str, Any]]:
    matrix_path = work_dir / "matrix.json"
    command = [
        sys.executable,
        str(SCRIPT_DIR / "expand_matrix.py"),
        "--repo-path",
        str(repository),
        "--working-directory",
        action_input("working_directory", "."),
        "--benchmarks-json",
        action_input("benchmarks_json", "[]"),
        "--feature-sets-json",
        action_input("feature_sets_json", '[{"name":"default","features":""}]'),
        "--backend",
        backend,
        "--criterion-cli-args",
        action_input("criterion_cli_args", "--noplot"),
        "--head-sha",
        head_sha,
        "--base-sha",
        base_sha,
        "--cargo-args",
        action_input("cargo_args"),
        "--output",
        str(matrix_path),
    ]
    if parse_bool(action_input("auto_discover", "true"), "auto_discover"):
        command.append("--auto-discover")
    if parse_bool(
        action_input("auto_detect_moved_benchmarks", "false"),
        "auto_detect_moved_benchmarks",
    ):
        command.append("--auto-detect-moved-benchmarks")
    run(command)
    payload = json.loads(matrix_path.read_text(encoding="utf-8"))
    cases = payload.get("include")
    if not isinstance(cases, list) or not cases:
        raise ValueError("benchmark expansion produced no cases")
    return cases


def run_cases(
    repository: pathlib.Path,
    work_dir: pathlib.Path,
    cases: list[dict[str, Any]],
    base_sha: str,
    head_sha: str,
) -> bool:
    had_errors = False
    statistic = action_input("criterion_statistic", "mean").strip().lower()
    if statistic not in {"mean", "median"}:
        raise ValueError("criterion_statistic must be 'mean' or 'median'")

    for case in cases:
        backend = str(case["backend"])
        case_dir = work_dir / "artifacts" / backend / str(case["id"])
        case_dir.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            str(SCRIPT_DIR / "run_pair.py"),
            "--repo-path",
            str(repository),
            "--working-directory",
            action_input("working_directory", "."),
            "--benchmark-name",
            str(case["benchmark_name"]),
            "--feature-name",
            str(case["feature_name"]),
            "--command",
            str(case["command"]),
            "--head-command",
            str(case["head_command"]),
            "--base-command",
            str(case["base_command"]),
            "--move-source",
            str(case.get("move_source") or ""),
            "--move-target",
            str(case.get("move_target") or ""),
            "--move-candidates",
            json.dumps(case.get("move_candidates", [])),
            "--backend",
            backend,
            "--criterion-statistic",
            statistic,
            "--setup-command",
            action_input("setup_command"),
            "--readiness-command",
            action_input("readiness_command"),
            "--teardown-command",
            action_input("teardown_command"),
            "--readiness-timeout-seconds",
            action_input("readiness_timeout_seconds", "60"),
            "--head-sha",
            head_sha,
            "--base-sha",
            base_sha,
            "--output",
            str(case_dir / "result.json"),
        ]
        if case.get("moved"):
            command.append("--moved")
        if case.get("move_ambiguous"):
            command.append("--move-ambiguous")
        if run(command, check=False) != 0:
            had_errors = True
    return had_errors


def optional_threshold(specific: float, generic: float) -> float:
    return generic if specific == -1 else specific


def render_backend(
    work_dir: pathlib.Path,
    backend: str,
    threshold: float,
    overrides: pathlib.Path,
    head_sha: str,
    run_at: str,
    pr_number: int | None,
    omit_run_meta: bool,
) -> tuple[pathlib.Path, dict[str, Any]]:
    report = work_dir / f"report-{backend}.md"
    summary = work_dir / f"summary-{backend}.json"
    command = [
        sys.executable,
        str(SCRIPT_DIR / "render_report.py"),
        "--artifacts-dir",
        str(work_dir / "artifacts" / backend),
        "--threshold",
        f"{threshold:g}",
        "--backend",
        backend,
        "--markdown-output",
        str(report),
        "--summary-output",
        str(summary),
        "--history-key",
        f"{backend}-history",
        "--head-sha",
        head_sha,
        "--run-at",
        run_at,
        "--max-history",
        "10",
        "--regression-overrides-input",
        str(overrides),
    ]
    history = work_dir / f"history-{backend}.json"
    if history.exists():
        command.extend(["--history-input", str(history)])
    if pr_number is not None:
        command.extend(["--pr-number", str(pr_number)])
    if omit_run_meta:
        command.append("--omit-run-meta")
    run(command)
    return report, json.loads(summary.read_text(encoding="utf-8"))


def compose_report(
    work_dir: pathlib.Path,
    reports: dict[str, pathlib.Path],
    run_at: str,
    head_sha: str,
    pr_number: int | None,
) -> pathlib.Path:
    output = work_dir / "report.md"
    if len(reports) == 1:
        shutil.copyfile(next(iter(reports.values())), output)
        return output

    command = [
        sys.executable,
        str(SCRIPT_DIR / "compose_combined_report.py"),
        "--output",
        str(output),
        "--report-gungraun",
        str(reports["gungraun"]),
        "--report-criterion",
        str(reports["criterion"]),
        "--run-at",
        run_at,
        "--head-sha",
        head_sha,
    ]
    if pr_number is not None:
        command.extend(["--pr-number", str(pr_number)])
    run(command)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--work-dir", required=True)
    args = parser.parse_args()

    source_repository = pathlib.Path(args.repository).resolve()
    work_dir = pathlib.Path(args.work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    backend = action_input("backend", "gungraun").strip().lower()
    if backend not in SUPPORTED_BACKENDS:
        raise ValueError("backend must be 'gungraun', 'criterion', or 'all'")
    comment_mode = action_input("comment_mode", "always").strip().lower()
    if comment_mode not in SUPPORTED_COMMENT_MODES:
        raise ValueError("comment_mode must be 'always', 'on-regression', or 'never'")

    generic_threshold = parse_threshold(
        action_input("regression_threshold_pct", "3"), "regression_threshold_pct"
    )
    gungraun_specific = parse_threshold(
        action_input("regression_threshold_pct_gungraun", "-1"),
        "regression_threshold_pct_gungraun",
        allow_unset=True,
    )
    criterion_specific = parse_threshold(
        action_input("regression_threshold_pct_criterion", "-1"),
        "regression_threshold_pct_criterion",
        allow_unset=True,
    )
    fail_on_regression = parse_bool(
        action_input("fail_on_regression", "false"), "fail_on_regression"
    )
    base_sha, head_sha, pr_number = resolve_revisions(load_event())
    for revision, label in ((base_sha, "base"), (head_sha, "head")):
        if not git_commit_exists(source_repository, revision):
            raise ValueError(
                f"{label} revision {revision!r} is unavailable; checkout with fetch-depth: 0"
            )

    benchmark_repository = work_dir / "repository"
    run(
        [
            "git",
            "worktree",
            "add",
            "--detach",
            str(benchmark_repository),
            head_sha,
        ],
        cwd=source_repository,
    )
    try:
        overrides = prepare_regression_overrides(
            work_dir, action_input("regression_override_label")
        )
        cases = expand_cases(
            benchmark_repository, work_dir, base_sha, head_sha, backend
        )
        had_errors = run_cases(
            benchmark_repository, work_dir, cases, base_sha, head_sha
        )
        run_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        selected_backends = (
            ["gungraun", "criterion"] if backend == "all" else [backend]
        )
        reports: dict[str, pathlib.Path] = {}
        summaries: list[dict[str, Any]] = []
        for selected in selected_backends:
            threshold = (
                resolve_gungraun_threshold(generic_threshold, gungraun_specific)
                if selected == "gungraun"
                else optional_threshold(criterion_specific, generic_threshold)
            )
            report, summary = render_backend(
                work_dir,
                selected,
                threshold,
                overrides,
                head_sha,
                run_at,
                pr_number,
                omit_run_meta=backend == "all",
            )
            reports[selected] = report
            summaries.append(summary)
        report_path = compose_report(work_dir, reports, run_at, head_sha, pr_number)
        has_regressions = any(bool(item.get("has_regressions")) for item in summaries)
        has_unaccepted = any(
            bool(item.get("has_unaccepted_regressions")) for item in summaries
        )
        should_fail = had_errors or (fail_on_regression and has_unaccepted)
        write_action_outputs(
            {
                "has_regressions": str(has_regressions).lower(),
                "has_unaccepted_regressions": str(has_unaccepted).lower(),
                "had_errors": str(had_errors).lower(),
                "report_path": str(report_path),
                "should_fail": str(should_fail).lower(),
            }
        )
    finally:
        run(
            ["git", "worktree", "remove", "--force", str(benchmark_repository)],
            cwd=source_repository,
            check=False,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
