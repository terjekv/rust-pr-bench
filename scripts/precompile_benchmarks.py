#!/usr/bin/env python3
import argparse
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
from typing import Any


NATIVE_TARGET_CPU_RE = re.compile(r"target-cpu\s*=\s*native", re.IGNORECASE)


def git_checkout(repo_path: pathlib.Path, ref: str) -> None:
    subprocess.run(["git", "checkout", "--force", "--quiet", ref], cwd=repo_path, check=True)


def find_benchmark_executable(output: str, benchmark_name: str) -> pathlib.Path | None:
    candidates: list[pathlib.Path] = []
    for line in output.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("reason") != "compiler-artifact" or not message.get("executable"):
            continue
        target = message.get("target", {})
        if "bench" not in target.get("kind", []):
            continue
        path = pathlib.Path(message["executable"])
        if target.get("name") == benchmark_name:
            return path
        candidates.append(path)
    return candidates[-1] if len(candidates) == 1 else None


def copy_runtime_artifacts(
    output: str, target_dir: pathlib.Path, runtime_dir: pathlib.Path
) -> None:
    paths: set[pathlib.Path] = set()
    for line in output.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("reason") != "compiler-artifact":
            continue
        target = message.get("target", {})
        if "bin" in target.get("kind", []) and message.get("executable"):
            paths.add(pathlib.Path(message["executable"]))
        for filename in message.get("filenames", []):
            path = pathlib.Path(filename)
            if path.suffix in {".so", ".dylib", ".dll"}:
                paths.add(path)

    for source in paths:
        try:
            relative = source.relative_to(target_dir)
        except ValueError:
            continue
        if not source.is_file():
            continue
        destination = runtime_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def cargo_config_paths(workdir: pathlib.Path, repo_path: pathlib.Path) -> list[pathlib.Path]:
    paths: list[pathlib.Path] = []
    current = workdir.resolve()
    root = repo_path.resolve()
    while current == root or root in current.parents:
        paths.extend((current / ".cargo" / name) for name in ("config.toml", "config"))
        if current == root:
            break
        current = current.parent
    return paths


def native_target_cpu_requested(
    cases: list[dict[str, Any]],
    workdir: pathlib.Path,
    repo_path: pathlib.Path,
    env: dict[str, str] | None = None,
) -> bool:
    values = [str(case.get("compile_command", "")) for case in cases]
    environment = env if env is not None else os.environ
    values.extend(
        environment.get(name, "")
        for name in ("RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS")
    )
    for config_path in cargo_config_paths(workdir, repo_path):
        try:
            values.append(config_path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return any(NATIVE_TARGET_CPU_RE.search(value) for value in values)


def precompile_case(
    case: dict[str, Any],
    workdir: pathlib.Path,
    target_dir: pathlib.Path,
    output_dir: pathlib.Path,
) -> dict[str, Any]:
    command = shlex.split(str(case["compile_command"]))
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(target_dir)
    completed = subprocess.run(
        command,
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        print(
            f"::warning title=Benchmark precompile failed::{case['benchmark_name']} "
            "will compile in its benchmark job"
        )
        return {
            "id": case["id"],
            "benchmark_name": case["benchmark_name"],
            "precompiled": False,
            "error_code": completed.returncode,
        }

    executable = find_benchmark_executable(
        completed.stdout, str(case["benchmark_name"]).split("/")[-1]
    )
    if executable is None or not executable.is_file():
        print(
            f"::warning title=Benchmark executable not found::{case['benchmark_name']} "
            "will compile in its benchmark job"
        )
        return {
            "id": case["id"],
            "benchmark_name": case["benchmark_name"],
            "precompiled": False,
        }

    copy_runtime_artifacts(completed.stdout, target_dir, output_dir / "_runtime")
    case_dir = output_dir / str(case["id"])
    case_dir.mkdir(parents=True, exist_ok=True)
    destination = case_dir / "benchmark"
    shutil.copy2(executable, destination)
    destination.chmod(destination.stat().st_mode | 0o111)
    return {
        "id": case["id"],
        "benchmark_name": case["benchmark_name"],
        "precompiled": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-path", required=True)
    parser.add_argument("--working-directory", default=".")
    parser.add_argument("--ref", required=True)
    parser.add_argument("--target-dir", required=True)
    parser.add_argument("--cases-json", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    repo_path = pathlib.Path(args.repo_path).resolve()
    workdir = (repo_path / args.working_directory).resolve()
    target_dir = pathlib.Path(args.target_dir).resolve()
    output_dir = pathlib.Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    target_dir.mkdir(parents=True, exist_ok=True)

    cases = json.loads(args.cases_json)
    if not isinstance(cases, list):
        raise ValueError("cases-json must be a JSON array")

    git_checkout(repo_path, args.ref)
    if native_target_cpu_requested(cases, workdir, repo_path):
        print(
            "::warning title=Native CPU tuning detected::Benchmark executables will be "
            "compiled on their execution runners"
        )
        results = [
            {
                "id": case["id"],
                "benchmark_name": case["benchmark_name"],
                "precompiled": False,
                "reason": "target-cpu=native",
            }
            for case in cases
        ]
    else:
        results = [precompile_case(case, workdir, target_dir, output_dir) for case in cases]
    (output_dir / "manifest.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
