#!/usr/bin/env python3
import argparse
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_cache import (
    Cache,
    append_record,
    build_identity,
    cache_root,
    config_paths,
    digest,
    download_cache,
    enabled,
)
from executable_cache import (
    ExecutableCache,
    case_identity,
    identity as executable_identity,
    relative_path,
)

LOCAL_DOWNLOADS: dict[str, dict[str, Any]] = {}
LOCAL_BUILDS: dict[str, dict[str, Any]] = {}

NATIVE_TARGET_CPU_RE = re.compile(r"target-cpu\s*=\s*native", re.IGNORECASE)


def git_checkout(repo_path: pathlib.Path, ref: str) -> None:
    subprocess.run(
        ["git", "checkout", "--force", "--quiet", ref], cwd=repo_path, check=True
    )


def find_benchmark_executable(output: str, benchmark_name: str) -> pathlib.Path | None:
    candidates: list[pathlib.Path] = []
    for line in output.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("reason") != "compiler-artifact" or not message.get(
            "executable"
        ):
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
) -> bool:
    paths: set[pathlib.Path] = set()
    complete = True
    directories_path = runtime_dir.parent / "runtime-directories.json"
    directories = (
        set(json.loads(directories_path.read_text()))
        if directories_path.exists()
        else set()
    )
    for line in output.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("reason") == "build-script-executed" and message.get("out_dir"):
            directory = pathlib.Path(message["out_dir"])
            if directory.is_dir() and directory.resolve().is_relative_to(
                target_dir.resolve()
            ):
                relative = directory.relative_to(target_dir).as_posix()
                relative_path(relative)
                directories.add(relative)
                destination = runtime_dir / directory.relative_to(target_dir)
                destination.mkdir(parents=True, exist_ok=True)
                paths.update(
                    path
                    for path in directory.rglob("*")
                    if not path.is_dir() or path.is_symlink()
                )
            else:
                complete = False
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
            complete = False
            continue
        if (
            not source.is_file()
            or source.is_symlink()
            or not source.resolve().is_relative_to(target_dir.resolve())
        ):
            complete = False
            continue
        destination = runtime_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    if directories:
        directories_path.write_text(json.dumps(sorted(directories)))
    return complete


def cargo_config_paths(
    workdir: pathlib.Path, repo_path: pathlib.Path
) -> list[pathlib.Path]:
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
        value for name, value in environment.items() if name.endswith("RUSTFLAGS")
    )
    for config_path in config_paths(workdir):
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
    label: str = "",
) -> dict[str, Any]:
    command = shlex.split(str(case["compile_command"]))
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(target_dir)
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    observations = {
        "compile_seconds": round(time.monotonic() - started, 3),
        "fresh": 0,
        "rebuilt": 0,
    }
    for line in completed.stdout.splitlines():
        try:
            artifact = json.loads(line)
        except ValueError:
            continue
        if artifact.get("reason") == "compiler-artifact":
            observations["fresh" if artifact.get("fresh") else "rebuilt"] += 1
    append_record(
        output_dir, {"label": label or case["benchmark_name"], **observations}
    )
    (output_dir / f"compile-{case['id']}.log").write_text(completed.stderr)
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

    runtime_complete = copy_runtime_artifacts(
        completed.stdout, target_dir, output_dir / "_runtime"
    )
    case_dir = output_dir / str(case["id"])
    case_dir.mkdir(parents=True, exist_ok=True)
    destination = case_dir / "benchmark"
    shutil.copy2(executable, destination)
    destination.chmod(destination.stat().st_mode | 0o111)
    return {
        "id": case["id"],
        "benchmark_name": case["benchmark_name"],
        "precompiled": True,
        "runtime_complete": runtime_complete,
        **observations,
    }


def compile_group(
    repo_path: pathlib.Path,
    workdir: pathlib.Path,
    ref: str,
    cases: list[dict[str, Any]],
    output_dir: pathlib.Path,
    *,
    group_id: str = "default",
    side: str = "head",
    peer_ref: str = "",
    peer_cases: list[dict[str, Any]] | None = None,
    peer_identity: tuple[str, str] | None = None,
    download_writer: bool = True,
    allow_native: bool = False,
    target_dir: pathlib.Path | None = None,
) -> list[dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    git_checkout(repo_path, ref)
    target_dir = target_dir or cache_root(repo_path) / "target"
    native = native_target_cpu_requested(cases, workdir, repo_path)
    if native and not allow_native:
        results = [
            {
                "id": case["id"],
                "benchmark_name": case["benchmark_name"],
                "precompiled": False,
                "reason": "target-cpu=native",
            }
            for case in cases
        ]
        append_record(
            output_dir, {"label": f"{group_id}/{side}", "restore": "native-disabled"}
        )
    else:
        key_components: dict[str, str] = {}
        compatibility, dependencies = build_identity(
            repo_path, workdir, cases, details=key_components
        )
        executables = None
        if enabled("CACHE_BINARIES", "false") and enabled("CACHE"):
            try:
                if native:
                    raise ValueError("target-cpu=native")
                exact_identity = executable_identity(
                    repo_path, workdir, target_dir, cases, (compatibility, dependencies)
                )
                # Each distinct source revision owns an entry; head owns equal pairs.
                same_peer = (
                    bool(peer_ref)
                    and subprocess.check_output(
                        ["git", "rev-parse", peer_ref], cwd=repo_path, text=True
                    ).strip()
                    == exact_identity["revision"]
                    and case_identity(peer_cases or cases) == exact_identity["cases"]
                )
                executables = ExecutableCache(
                    repo_path, exact_identity, writer=side == "head" or not same_peer
                )
                executables.cache.record["key_components"].update(
                    {f"build.{key}": value for key, value in key_components.items()}
                )
                reused = executables.restore(output_dir)
                if reused is not None:
                    append_record(
                        output_dir,
                        {
                            **executables.cache.record,
                            "label": f"{group_id}/{side} executables",
                        },
                    )
                    (output_dir / "manifest.json").write_text(
                        json.dumps(reused, indent=2)
                    )
                    return reused
            except (OSError, ValueError, subprocess.CalledProcessError) as error:
                append_record(
                    output_dir,
                    {
                        "kind": "executables",
                        "restore": "ineligible",
                        "reason": str(error),
                        "label": f"{group_id}/{side} executables",
                    },
                )
        writer = side == "head"
        peer = peer_identity
        if side == "base" and peer_identity is not None:
            writer = peer_identity != (compatibility, dependencies)
        elif side == "base" and peer_ref:
            try:
                git_checkout(repo_path, peer_ref)
                peer = build_identity(repo_path, workdir, peer_cases or cases)
                writer = peer != (compatibility, dependencies)
            finally:
                git_checkout(repo_path, ref)
        downloads = download_cache(
            repo_path,
            dependencies,
            writer=download_writer
            and (side == "head" or (peer is not None and dependencies != peer[1])),
        )
        if downloads.key not in LOCAL_DOWNLOADS:
            downloads.restore()
            LOCAL_DOWNLOADS[downloads.key] = dict(downloads.record)
        else:
            downloads.record.update(
                restore="local",
                matched_key=LOCAL_DOWNLOADS[downloads.key].get("matched_key", ""),
            )
        target_dir.mkdir(parents=True, exist_ok=True)
        build = Cache(
            "build",
            [target_dir],
            digest([compatibility, group_id]),
            dependencies,
            writer=writer,
        )
        build.record["key_components"] = key_components
        if native:
            build.record["restore"] = "native-disabled"
        elif build.key in LOCAL_BUILDS:
            build.record.update(
                restore="local",
                matched_key=LOCAL_BUILDS[build.key].get("matched_key", ""),
            )
        else:
            build.restore()
            LOCAL_BUILDS[build.key] = dict(build.record)
            if build.record["restore"] == "error":
                # A partially extracted archive must not be mistaken for a valid build.
                shutil.rmtree(target_dir)
                target_dir.mkdir(parents=True)
        results = [
            precompile_case(
                case,
                workdir,
                target_dir,
                output_dir,
                f"{group_id}/{side} {case['benchmark_name']}",
            )
            for case in cases
        ]
        success = all(item["precompiled"] for item in results)
        if not native:
            build.save(success)
        downloads.save(success)
        for entry in (downloads, build):
            append_record(
                output_dir,
                {**entry.record, "label": f"{group_id}/{side} {entry.record['kind']}"},
            )
        (output_dir / "build.json").write_text(
            json.dumps({"target_dir": str(target_dir)})
        )
        if executables is not None:
            try:
                after = executable_identity(
                    repo_path,
                    workdir,
                    target_dir,
                    cases,
                    build_identity(repo_path, workdir, cases),
                )
                if after != executables.identity:
                    raise ValueError("build-inputs-changed")
                executables.save(output_dir, results)
            except (OSError, ValueError, subprocess.CalledProcessError) as error:
                executables.cache.record.update(
                    save="inputs-changed", reason=str(error)
                )
            append_record(
                output_dir,
                {**executables.cache.record, "label": f"{group_id}/{side} executables"},
            )
    (output_dir / "manifest.json").write_text(json.dumps(results, indent=2))
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-path", required=True)
    parser.add_argument("--working-directory", default=".")
    parser.add_argument("--ref", required=True)
    parser.add_argument("--target-dir")
    parser.add_argument("--cases-json", default=os.environ.get("CASES_JSON", "[]"))
    parser.add_argument(
        "--peer-cases-json", default=os.environ.get("PEER_CASES_JSON", "[]")
    )
    parser.add_argument("--peer-ref", default="")
    parser.add_argument("--group-id", default="default")
    parser.add_argument("--side", default="head")
    parser.add_argument("--download-writer", default="true")
    parser.add_argument("--require-success", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    repository = pathlib.Path(args.repo_path).resolve()
    cases = json.loads(args.cases_json)
    if not isinstance(cases, list):
        raise ValueError("cases-json must be a JSON array")
    results = compile_group(
        repository,
        (repository / args.working_directory).resolve(),
        args.ref,
        cases,
        pathlib.Path(args.output).resolve(),
        group_id=args.group_id,
        side=args.side,
        peer_ref=args.peer_ref,
        peer_cases=json.loads(args.peer_cases_json),
        download_writer=args.download_writer == "true",
        target_dir=pathlib.Path(args.target_dir).resolve() if args.target_dir else None,
    )
    return int(
        args.require_success and not all(item["precompiled"] for item in results)
    )


if __name__ == "__main__":
    raise SystemExit(main())
