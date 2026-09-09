"""Exact, opt-in executable bundles. Cargo caches remain independent build hints."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shlex
import shutil
import stat
import subprocess
import tomllib
from typing import Any

from build_cache import Cache, cache_root, config_paths, digest

SCHEMA = 1


def sha256(path: pathlib.Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def relative_path(name: str) -> pathlib.Path:
    path = pathlib.PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or path.as_posix() != name
        or any(
            part in {"..", ".git", ".bench-target", ".rust-pr-bench-cache"}
            for part in path.parts
        )
        or "\\" in name
        or name == "."
    ):
        raise ValueError("unsafe bundle path")
    return pathlib.Path(path)


def runtime_paths() -> list[str]:
    paths = json.loads(os.environ.get("RUST_PR_BENCH_BINARY_CACHE_PATHS", "[]"))
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise ValueError(
            "binary_cache_paths must be a JSON array of repository-relative paths"
        )
    for name in paths:
        relative_path(name)
    return sorted(set(paths))


def case_identity(cases: list[dict]) -> list[dict]:
    return sorted(
        (
            {
                "id": case["id"],
                "benchmark_name": case["benchmark_name"],
                "command": shlex.split(case["compile_command"]),
            }
            for case in cases
        ),
        key=lambda case: case["id"],
    )


def source_revision(repository: pathlib.Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()


def check_manifest_paths(
    value: Any, directory: pathlib.Path, repository: pathlib.Path
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "path" and isinstance(item, str):
                if not (directory / item).resolve().is_relative_to(repository):
                    raise ValueError("external-path-dependency")
            check_manifest_paths(item, directory, repository)
    elif isinstance(value, list):
        for item in value:
            check_manifest_paths(item, directory, repository)


def identity(
    repository: pathlib.Path,
    workdir: pathlib.Path,
    target: pathlib.Path,
    cases: list[dict],
    build: tuple[str, str],
) -> dict:
    """Only committed, self-contained sources qualify; opaque inputs are caller-owned."""
    if subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--"], cwd=repository, check=False
    ).returncode:
        raise ValueError("dirty-source")
    entries = (
        subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=repository)
        .decode()
        .split("\0")
    )
    tracked = set()
    source_files = {}
    for entry in filter(None, entries):
        metadata, name = entry.split("\t", 1)
        mode = metadata.split()[0]
        if mode == "160000":
            raise ValueError("submodules-unsupported")
        path = repository / name
        if path.is_symlink():
            # External symlink inputs are not covered by the commit identity.
            if not path.resolve().is_relative_to(repository):
                raise ValueError("external-source-symlink")
        source_files[name] = sha256(path)
        tracked.add(name)
        if path.name == "Cargo.toml":
            check_manifest_paths(
                tomllib.loads(path.read_text()), path.parent, repository
            )
    if not any(
        str((parent / "Cargo.lock").relative_to(repository)) in tracked
        for parent in [workdir, *workdir.parents]
        if parent.is_relative_to(repository)
    ):
        raise ValueError("committed-lockfile-required")
    # Configs may also supply path overrides; include their contents via build identity.
    for config in config_paths(workdir):
        if config.is_file():
            parsed = tomllib.loads(config.read_text())
            check_manifest_paths(parsed, config.parent.parent, repository)
            if parsed.get("paths"):
                raise ValueError("cargo-path-overrides-unsupported")
    script_dir = pathlib.Path(__file__).resolve().parent
    return {
        "schema": SCHEMA,
        "implementation": digest(
            {
                p.name: sha256(p)
                for p in (
                    script_dir / "executable_cache.py",
                    script_dir / "precompile_benchmarks.py",
                    script_dir / "precompiled_runtime.py",
                    script_dir / "build_cache.py",
                )
            }
        ),
        "revision": source_revision(repository),
        "tags": digest(
            subprocess.check_output(
                [
                    "git",
                    "for-each-ref",
                    "--format=%(refname):%(objectname)",
                    "refs/tags",
                ],
                cwd=repository,
                text=True,
            )
        ),
        "source_files": digest(source_files),
        "build": list(build),
        "cases": case_identity(cases),
        "repository": str(repository),
        "target": str(target),
        "cargo_home": str(
            pathlib.Path(os.environ.get("CARGO_HOME", "~/.cargo"))
            .expanduser()
            .resolve()
        ),
        "sysroot": subprocess.check_output(
            ["rustc", "--print", "sysroot"], cwd=workdir, text=True
        ).strip(),
        "cargo": subprocess.check_output(
            ["cargo", "-V"], cwd=workdir, text=True
        ).strip(),
        "runners": {
            name: os.environ.get(name, "")
            for name in ("GUNGRAUN_RUNNER", "IAI_CALLGRIND_RUNNER")
        },
        "external_inputs": digest(os.environ.get("RUST_PR_BENCH_BINARY_CACHE_KEY", "")),
        "runtime_paths": runtime_paths(),
    }


def inventory(root: pathlib.Path, *, reports: bool = False) -> dict:
    """Reject links and special files, including directory links; hash every byte."""
    files = {}
    if root.is_symlink():
        raise ValueError("symlink-bundle")
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if (
            reports
            and "/" not in name
            and (
                name in {"manifest.json", "performance.jsonl"}
                or (name.startswith("compile-") and name.endswith(".log"))
            )
        ):
            continue
        relative_path(name)
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError("unsupported-runtime-file")
        if stat.S_ISREG(mode) and name != "bundle.json":
            files[name] = {
                "sha256": sha256(path),
                "size": path.stat().st_size,
                "executable": bool(mode & 0o111),
            }
        elif stat.S_ISDIR(mode):
            files[name] = {"directory": True}
    return files


def verify_bundle(
    root: pathlib.Path, expected: dict | None = None, *, reports: bool = False
) -> dict:
    # Inspect links before opening metadata, then compare the complete file set.
    actual = inventory(root, reports=reports)
    manifest = json.loads((root / "bundle.json").read_text())
    build = manifest["identity"]
    if not isinstance(build, dict):
        raise ValueError("invalid-identity")
    if build["schema"] != SCHEMA or (expected is not None and build != expected):
        raise ValueError("identity-mismatch")
    expected_files = manifest["files"]
    if not isinstance(expected_files, dict) or not all(
        isinstance(info, dict) for info in expected_files.values()
    ):
        raise ValueError("invalid-runtime-manifest")
    for name in expected_files:
        relative_path(name)
    # ZIP artifact transport omits empty directories. Recreate declared directories
    # after checking all files; unexpected directories are still rejected.
    if (actual.keys() - expected_files.keys()) or any(
        name not in actual and info != {"directory": True}
        for name, info in expected_files.items()
    ):
        raise ValueError("incomplete-runtime-manifest")
    for name, info in actual.items():
        recorded = expected_files[name]
        # Artifact distribution strips Unix modes. Restore execute bits only after verification.
        if {k: v for k, v in info.items() if k != "executable"} != {
            k: v for k, v in recorded.items() if k != "executable"
        } or (
            not info.get("directory")
            and not isinstance(recorded.get("executable"), bool)
        ):
            raise ValueError("runtime-checksum-mismatch")
    for case in build["cases"]:
        if not expected_files.get(f"{case['id']}/benchmark", {}).get("executable"):
            raise ValueError("missing-benchmark")
    metadata = json.loads((root / "build.json").read_text())
    if metadata != {
        "target_dir": build["target"],
        "repository": build["repository"],
        "executable_cache": True,
    }:
        raise ValueError("runtime-path-mismatch")
    return manifest


def copy_tree(source: pathlib.Path, destination: pathlib.Path) -> None:
    if source.is_symlink():
        raise ValueError("unsupported-runtime-file")
    if source.is_dir():
        inventory(source)
        shutil.copytree(source, destination, dirs_exist_ok=True)
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    else:
        raise ValueError("missing-runtime-file")


class ExecutableCache:
    def __init__(self, repository: pathlib.Path, build: dict, *, writer: bool = True):
        self.identity = build
        # A single stable archive path; groups run sequentially within a job.
        self.directory = cache_root(repository) / "executables"
        self.cache = Cache(
            "executables",
            [self.directory],
            str(SCHEMA),
            digest(build),
            writer=writer,
            exact_only=True,
        )
        self.cache.record["key_components"] = {k: digest(v) for k, v in build.items()}

    def clear(self) -> None:
        if self.directory.is_symlink():
            self.directory.unlink()
        elif self.directory.exists():
            shutil.rmtree(self.directory)

    def restore(self, output: pathlib.Path) -> list[dict] | None:
        self.clear()
        self.cache.restore()
        record = self.cache.record
        try:
            if record["restore"] not in {"exact", "fallback"}:
                return None
            # GitHub can prefix-match even the primary key. Never execute that match.
            if record.get("matched_key") != self.cache.key:
                raise ValueError("non-exact-key")
            manifest = verify_bundle(self.directory, self.identity)
            for name, info in manifest["files"].items():
                source = self.directory / name
                destination = output / name
                if info.get("directory"):
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                    destination.chmod(0o755 if info["executable"] else 0o644)
            shutil.copy2(self.directory / "bundle.json", output / "bundle.json")
            record["contents_bytes"] = sum(
                p.get("size", 0) for p in manifest["files"].values()
            )
            results = [
                {
                    "id": case["id"],
                    "benchmark_name": case["benchmark_name"],
                    "precompiled": True,
                    "executable_reused": True,
                }
                for case in self.identity["cases"]
            ]
            record["executables_reused"] = len(results)
            record["save"] = "already-exists"
            return results
        except (OSError, ValueError, KeyError, TypeError) as error:
            record.update(restore="rejected", reason=str(error))
            # No partial bundle may reach the normal precompile/artifact fallback path.
            for path in output.iterdir():
                if path.name != "performance.jsonl":
                    if path.is_dir() and not path.is_symlink():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
            return None
        finally:
            self.clear()

    def save(self, output: pathlib.Path, results: list[dict]) -> None:
        try:
            if not all(
                case.get("precompiled") and case.get("runtime_complete", True)
                for case in results
            ):
                self.cache.record["save"] = "incomplete-build"
                return
            self.clear()
            self.directory.mkdir(parents=True)
            for case in self.identity["cases"]:
                relative_path(case["id"])
                copy_tree(output / case["id"], self.directory / case["id"])
            if (output / "_runtime").exists():
                copy_tree(output / "_runtime", self.directory / "_runtime")
            repository = pathlib.Path(self.identity["repository"])
            for name in self.identity["runtime_paths"]:
                path = repository / name
                if not path.resolve().is_relative_to(repository):
                    raise ValueError("external-runtime-path")
                copy_tree(path, self.directory / "_assets" / name)
            metadata = {
                "target_dir": self.identity["target"],
                "repository": str(repository),
                "executable_cache": True,
            }
            (self.directory / "build.json").write_text(json.dumps(metadata))
            manifest = {"identity": self.identity, "files": inventory(self.directory)}
            (self.directory / "bundle.json").write_text(
                json.dumps(manifest, sort_keys=True)
            )
            verify_bundle(self.directory, self.identity)
            # Use exactly the verified snapshot for same-run execution as well.
            shutil.copytree(self.directory, output, dirs_exist_ok=True)
            self.cache.save()
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.cache.record.update(save="incomplete-bundle", reason=str(error))
        finally:
            self.clear()
