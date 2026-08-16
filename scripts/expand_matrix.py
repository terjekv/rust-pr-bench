#!/usr/bin/env python3
import argparse
import hashlib
import json
import pathlib
import re
import shlex
import subprocess
import sys
import tomllib
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from backend_names import (  # noqa: E402
    BACKENDS,
    GUNGRAUN_BACKEND,
    normalize_backend,
    normalize_backend_selection,
)

NATIVE_TARGET_CPU_RE = re.compile(r"target-cpu\s*=\s*native", re.IGNORECASE)


def expand_backends(selection: str) -> list[str]:
    if selection == "all":
        return list(BACKENDS)
    return [selection]


def infer_name_backend(name: str) -> str | None:
    lowered = name.lower()
    has_callgrind = any(
        marker in lowered for marker in ("gungraun", "iai_callgrind", "callgrind")
    )
    has_criterion = "criterion" in lowered
    if has_callgrind and not has_criterion:
        return GUNGRAUN_BACKEND
    if has_criterion and not has_callgrind:
        return "criterion"
    return None


def read_manifest(path: pathlib.Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def workspace_member_dirs(workdir: pathlib.Path) -> list[pathlib.Path]:
    manifest_path = workdir / "Cargo.toml"
    manifest = read_manifest(manifest_path)
    dirs: list[pathlib.Path] = []

    if manifest.get("package"):
        dirs.append(workdir)

    workspace = manifest.get("workspace")
    members = workspace.get("members", []) if isinstance(workspace, dict) else []
    exclude = workspace.get("exclude", []) if isinstance(workspace, dict) else []
    excluded_dirs: set[pathlib.Path] = set()
    for pattern in exclude:
        if not isinstance(pattern, str):
            continue
        excluded_dirs.update(path.resolve() for path in workdir.glob(pattern))
    for member in members:
        if not isinstance(member, str):
            continue
        for path in sorted(workdir.glob(member)):
            if (
                path.resolve() not in excluded_dirs
                and (path / "Cargo.toml").exists()
                and path not in dirs
            ):
                dirs.append(path)

    return dirs or [workdir]


def package_name(crate_dir: pathlib.Path) -> str | None:
    package = read_manifest(crate_dir / "Cargo.toml").get("package")
    if isinstance(package, dict) and package.get("name"):
        return str(package["name"])
    return None


def relative_path(path: pathlib.Path, start: pathlib.Path) -> str:
    rel = path.relative_to(start)
    return "." if rel.as_posix() == "." else rel.as_posix()


def discover_crate_benchmarks(
    repo_path: pathlib.Path,
    workdir: pathlib.Path,
    crate_dir: pathlib.Path,
    backend_selection: str,
) -> list[dict[str, Any]]:
    benches_dir = crate_dir / "benches"
    if not benches_dir.exists():
        return []

    selected_backends = set(expand_backends(backend_selection))
    benchmarks: list[dict[str, Any]] = []
    for path in sorted(benches_dir.glob("*.rs")):
        if path.name == "mod.rs":
            continue

        inferred_backend = infer_name_backend(path.stem)
        if inferred_backend and inferred_backend not in selected_backends:
            continue

        candidate_backends = [inferred_backend] if inferred_backend else list(BACKENDS)
        for backend in candidate_backends:
            if backend not in selected_backends:
                continue
            crate_rel = relative_path(crate_dir, workdir)
            repo_rel = relative_path(crate_dir, repo_path)
            spec: dict[str, Any] = {
                "name": path.stem if crate_rel == "." else f"{crate_rel}/{path.stem}",
                "bench": path.stem,
                "backend": backend,
                "crate": crate_rel,
                "repo_crate": repo_rel,
            }
            if crate_rel != ".":
                spec["manifest_path"] = f"{crate_rel}/Cargo.toml"
            name = package_name(crate_dir)
            if name:
                spec["package_name"] = name
            benchmarks.append(spec)
    return benchmarks


def discover_benchmarks(
    repo_path: pathlib.Path, working_directory: str, backend_selection: str
) -> list[dict[str, Any]]:
    workdir = repo_path / working_directory
    benchmarks: list[dict[str, Any]] = []
    for crate_dir in workspace_member_dirs(workdir):
        benchmarks.extend(discover_crate_benchmarks(repo_path, workdir, crate_dir, backend_selection))
    return benchmarks


def git_checkout(repo_path: pathlib.Path, ref: str) -> None:
    subprocess.run(["git", "checkout", "--force", "--quiet", ref], cwd=repo_path, check=True)


def discover_at_ref(
    repo_path: pathlib.Path,
    working_directory: str,
    backend_selection: str,
    ref: str,
) -> list[dict[str, Any]]:
    git_checkout(repo_path, ref)
    return discover_benchmarks(repo_path, working_directory, backend_selection)


def move_key(spec: dict[str, Any]) -> tuple[str, str]:
    return (
        normalize_backend(str(spec.get("backend", GUNGRAUN_BACKEND))),
        str(spec.get("bench", "")),
    )


def benchmark_location(spec: dict[str, Any]) -> tuple[str, str, str]:
    return (*move_key(spec), str(spec.get("repo_crate", "")))


def pair_moved_benchmarks(
    head_benchmarks: list[dict[str, Any]], base_benchmarks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    head_locations = {benchmark_location(spec) for spec in head_benchmarks}
    base_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for spec in base_benchmarks:
        # A source must be gone from head before it can be considered a move.
        # This prevents a newly added same-named benchmark from being compared
        # with an unrelated benchmark that still exists in another crate.
        if benchmark_location(spec) not in head_locations:
            base_by_key.setdefault(move_key(spec), []).append(spec)

    paired: list[dict[str, Any]] = []
    for head in head_benchmarks:
        candidates = base_by_key.get(move_key(head), [])
        if not candidates:
            paired.append(head)
            continue

        head_package = head.get("package_name")
        package_matches = [item for item in candidates if item.get("package_name") == head_package]
        selected: dict[str, Any] | None = None
        if len(package_matches) == 1:
            selected = package_matches[0]
        elif len(candidates) == 1:
            selected = candidates[0]

        if selected and selected.get("repo_crate") != head.get("repo_crate"):
            item = dict(head)
            item["base"] = selected
            item["moved"] = True
            item["move_source"] = selected.get("repo_crate")
            item["move_target"] = head.get("repo_crate")
            paired.append(item)
            candidates.remove(selected)
        elif selected:
            paired.append(head)
        else:
            item = dict(head)
            item["move_ambiguous"] = True
            item["move_candidates"] = [candidate.get("repo_crate") for candidate in candidates]
            paired.append(item)
    return paired


def normalize_feature_sets(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("feature_sets_json must be a JSON array")

    normalized: list[dict[str, Any]] = []
    for entry in raw:
        if isinstance(entry, str):
            normalized.append({"name": entry, "features": entry})
            continue
        if not isinstance(entry, dict):
            raise ValueError("feature set entries must be objects or strings")
        name = entry.get("name") or (entry.get("features") or "default")
        normalized.append(
            {
                "name": str(name),
                "features": str(entry.get("features", "")),
                "no_default_features": bool(entry.get("no_default_features", False)),
            }
        )
    if not normalized:
        normalized = [{"name": "default", "features": "", "no_default_features": False}]
    return normalized


def build_command(
    spec: dict[str, Any],
    feature_set: dict[str, Any],
    cargo_args: str,
    backend: str,
    criterion_cli_args: str,
) -> str:
    features = feature_set["features"].strip()
    no_default = feature_set.get("no_default_features", False)

    command = spec.get("command")
    if command:
        command = str(command)
        command = command.replace("{features}", features)
        command = command.replace(
            "{no_default_features_flag}", "--no-default-features" if no_default else ""
        )
        if "{features}" not in str(spec.get("command")) and features:
            command += f" --features {shlex.quote(features)}"
        if "{no_default_features_flag}" not in str(spec.get("command")) and no_default:
            command += " --no-default-features"
    else:
        bench = spec.get("bench")
        if not bench:
            raise ValueError(f"benchmark spec '{spec.get('name', 'unknown')}' is missing 'bench'")

        parts = ["cargo", "bench", "--bench", shlex.quote(str(bench))]
        manifest_path = spec.get("manifest_path")
        if manifest_path:
            parts.extend(["--manifest-path", shlex.quote(str(manifest_path))])
        package = spec.get("package")
        if package:
            parts.extend(["--package", shlex.quote(str(package))])
        if features:
            parts.extend(["--features", shlex.quote(features)])
        if no_default:
            parts.append("--no-default-features")
        extra_args = spec.get("args")
        if extra_args:
            parts.append(str(extra_args))
        if cargo_args.strip():
            parts.append(cargo_args.strip())
        if backend == "criterion":
            criterion_args = str(spec.get("criterion_args", criterion_cli_args)).strip()
            if criterion_args:
                parts.extend(["--", criterion_args])
        command = " ".join(parts)

    if command and spec.get("command") and cargo_args.strip():
        command = f"{command} {cargo_args.strip()}"

    return " ".join(command.split())


def command_spec(spec: dict[str, Any], side: str) -> dict[str, Any]:
    nested = spec.get(side)
    if isinstance(nested, dict):
        # Move detection stores a complete base-side discovery result. Merging
        # it over the head spec would retain head-only fields that are absent
        # at the old location, such as a workspace member's manifest_path.
        if side == "base" and spec.get("moved"):
            return dict(nested)
        merged = dict(spec)
        merged.update(nested)
        return merged
    key = f"{side}_command"
    if spec.get(key):
        merged = dict(spec)
        merged["command"] = spec[key]
        return merged
    return spec


def expand_benchmark_entry(entry: Any, backend_selection: str) -> list[dict[str, Any]]:
    selected_backends = expand_backends(backend_selection)

    def with_backends(base: dict[str, Any], explicit_backend: str | None) -> list[dict[str, Any]]:
        if explicit_backend:
            if explicit_backend not in selected_backends:
                return []
            item = dict(base)
            item["backend"] = explicit_backend
            return [item]

        items: list[dict[str, Any]] = []
        for backend in selected_backends:
            item = dict(base)
            item["backend"] = backend
            items.append(item)
        return items

    if isinstance(entry, str):
        return with_backends({"name": entry, "bench": entry}, explicit_backend=None)
    if isinstance(entry, dict):
        explicit_backend = entry.get("backend")
        normalized: str | None = None
        if explicit_backend:
            normalized = normalize_backend(str(explicit_backend))
        return with_backends(entry, explicit_backend=normalized)

    raise ValueError("benchmark entries must be objects or strings")


def make_matrix(
    benchmarks: list[dict[str, Any]],
    feature_sets: list[dict[str, Any]],
    cargo_args: str,
    criterion_cli_args: str,
) -> dict[str, list[dict[str, Any]]]:
    include: list[dict[str, Any]] = []
    for bench in benchmarks:
        backend = normalize_backend(str(bench.get("backend", GUNGRAUN_BACKEND)))
        bench_name = str(bench.get("name") or bench.get("bench") or "benchmark")
        for feature_set in feature_sets:
            case_seed = f"{backend}|{bench_name}|{feature_set['name']}|{feature_set['features']}"
            case_id = hashlib.sha1(case_seed.encode("utf-8")).hexdigest()[:10]
            group_seed = (
                f"{feature_set['name']}|{feature_set['features']}|"
                f"{feature_set.get('no_default_features', False)}"
            )
            build_group_id = hashlib.sha1(group_seed.encode("utf-8")).hexdigest()[:10]
            command = build_command(bench, feature_set, cargo_args, backend, criterion_cli_args)
            head_command = build_command(
                command_spec(bench, "head"),
                feature_set,
                cargo_args,
                backend,
                criterion_cli_args,
            )
            base_command = build_command(
                command_spec(bench, "base"),
                feature_set,
                cargo_args,
                backend,
                criterion_cli_args,
            )
            head_split = split_benchmark_command(head_command)
            base_split = split_benchmark_command(base_command)
            precompile = (
                head_split is not None
                and base_split is not None
                and not uses_native_target_cpu(head_command)
                and not uses_native_target_cpu(base_command)
            )
            include.append(
                {
                    "id": case_id,
                    "backend": backend,
                    "benchmark_name": bench_name,
                    "feature_name": feature_set["name"],
                    "command": command,
                    "head_command": head_command,
                    "base_command": base_command,
                    "precompile": precompile,
                    "build_group_id": build_group_id,
                    "head_run_args": head_split[1] if head_split else "",
                    "base_run_args": base_split[1] if base_split else "",
                    "moved": bool(bench.get("moved")),
                    "move_source": bench.get("move_source"),
                    "move_target": bench.get("move_target"),
                    "move_ambiguous": bool(bench.get("move_ambiguous")),
                    "move_candidates": bench.get("move_candidates", []),
                }
            )
    return {"include": include}


def split_benchmark_command(command: str) -> tuple[str, str] | None:
    """Split a cargo bench command into its compile command and binary arguments."""
    try:
        parts = shlex.split(command)
    except ValueError:
        return None

    if len(parts) < 4 or parts[:2] != ["cargo", "bench"]:
        return None
    if parts.count("--bench") != 1:
        return None
    bench_index = parts.index("--bench")
    if bench_index + 1 >= len(parts):
        return None

    separator = parts.index("--") if "--" in parts else len(parts)
    cargo_parts = parts[:separator]
    binary_parts = parts[separator + 1 :] if separator < len(parts) else []
    if "--bench" not in binary_parts:
        binary_parts.insert(0, "--bench")
    if "--no-run" not in cargo_parts:
        cargo_parts.append("--no-run")
    message_format_index = next(
        (index for index, part in enumerate(cargo_parts) if part == "--message-format"), None
    )
    if message_format_index is not None and message_format_index + 1 < len(cargo_parts):
        cargo_parts[message_format_index + 1] = "json"
    else:
        cargo_parts = [
            "--message-format=json" if part.startswith("--message-format=") else part
            for part in cargo_parts
        ]
    if not any(
        part == "--message-format" or part.startswith("--message-format=") for part in cargo_parts
    ):
        cargo_parts.extend(["--message-format", "json"])

    return shlex.join(cargo_parts), shlex.join(binary_parts)


def uses_native_target_cpu(command: str) -> bool:
    """Return whether a command requests host-specific machine code."""
    return NATIVE_TARGET_CPU_RE.search(command) is not None


def make_build_matrix(matrix: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    metadata: dict[tuple[str, str], dict[str, Any]] = {}
    for item in matrix["include"]:
        if not item["precompile"]:
            continue
        for side in ("head", "base"):
            key = (item["build_group_id"], side)
            split = split_benchmark_command(item[f"{side}_command"])
            if split is None:
                continue
            groups.setdefault(key, []).append(
                {
                    "id": item["id"],
                    "benchmark_name": item["benchmark_name"],
                    "compile_command": split[0],
                }
            )
            metadata[key] = {
                "id": f"{item['build_group_id']}-{side}",
                "group_id": item["build_group_id"],
                "side": side,
                "feature_name": item["feature_name"],
                "enabled": True,
            }

    include: list[dict[str, Any]] = []
    for key, cases in groups.items():
        include.append({**metadata[key], "cases": cases})
    if not include:
        include.append(
            {
                "id": "disabled",
                "group_id": "disabled",
                "side": "head",
                "feature_name": "default",
                "enabled": False,
                "cases": [],
            }
        )
    return {"include": include}


def normalize_option_values(
    argv: list[str], value_options: set[str], known_options: set[str]
) -> list[str]:
    normalized: list[str] = []
    idx = 0
    while idx < len(argv):
        token = argv[idx]
        if token in value_options and idx + 1 < len(argv):
            next_token = argv[idx + 1]
            if next_token.startswith("-") and next_token not in known_options:
                normalized.append(f"{token}={next_token}")
                idx += 2
                continue
        normalized.append(token)
        idx += 1
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-path", required=True)
    parser.add_argument("--working-directory", default=".")
    parser.add_argument("--benchmarks-json", required=True)
    parser.add_argument("--feature-sets-json", required=True)
    parser.add_argument("--backend", default=GUNGRAUN_BACKEND)
    parser.add_argument("--criterion-cli-args", default="--noplot")
    parser.add_argument("--auto-discover", action="store_true")
    parser.add_argument("--auto-detect-moved-benchmarks", action="store_true")
    parser.add_argument("--head-sha")
    parser.add_argument("--base-sha")
    parser.add_argument("--cargo-args", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--build-output")
    parsed_argv = normalize_option_values(
        sys.argv[1:],
        value_options={"--criterion-cli-args", "--cargo-args"},
        known_options=set(parser._option_string_actions.keys()),
    )
    args = parser.parse_args(parsed_argv)

    repo_path = pathlib.Path(args.repo_path).resolve()
    backend_selection = normalize_backend_selection(args.backend)

    benchmarks_raw = json.loads(args.benchmarks_json)
    if benchmarks_raw and not isinstance(benchmarks_raw, list):
        raise ValueError("benchmarks_json must be a JSON array")

    benchmarks: list[dict[str, Any]] = []
    for entry in benchmarks_raw:
        benchmarks.extend(expand_benchmark_entry(entry, backend_selection))

    if args.auto_discover and not benchmarks:
        if args.auto_detect_moved_benchmarks and args.head_sha and args.base_sha:
            git_checkout(repo_path, args.head_sha)
            head_benchmarks = discover_benchmarks(repo_path, args.working_directory, backend_selection)
            base_benchmarks = discover_at_ref(
                repo_path, args.working_directory, backend_selection, args.base_sha
            )
            git_checkout(repo_path, args.head_sha)
            benchmarks = pair_moved_benchmarks(head_benchmarks, base_benchmarks)
        else:
            benchmarks = discover_benchmarks(repo_path, args.working_directory, backend_selection)

    if not benchmarks:
        print(
            f"No benchmarks configured for backend '{backend_selection}'. "
            "Provide benchmarks_json or enable auto_discover with benches/*.rs",
            file=sys.stderr,
        )
        return 1

    feature_sets = normalize_feature_sets(json.loads(args.feature_sets_json))

    matrix = make_matrix(
        benchmarks,
        feature_sets,
        args.cargo_args,
        args.criterion_cli_args,
    )
    pathlib.Path(args.output).write_text(json.dumps(matrix), encoding="utf-8")
    if args.build_output:
        pathlib.Path(args.build_output).write_text(
            json.dumps(make_build_matrix(matrix)), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
