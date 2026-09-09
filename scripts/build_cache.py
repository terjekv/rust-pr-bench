"""Cargo build cache identities and a fail-open GitHub Actions cache client.

Build entries are hints to Cargo, never permission to skip Cargo. The fixed
archive paths allow compatible builds in separate jobs/interfaces to share an
entry. Benchmark results and runtime service state are never stored here.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import platform
import re
import shlex
import subprocess
import tempfile
import time
from typing import Any


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:24]


def enabled(name: str, default: str = "true") -> bool:
    value = os.environ.get(f"RUST_PR_BENCH_{name}", default).lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name.lower()} must be true or false")
    return value == "true"


def cache_root(repository: pathlib.Path) -> pathlib.Path:
    workspace = pathlib.Path(
        os.environ.get("GITHUB_WORKSPACE", str(repository))
    ).resolve()
    namespace = digest(os.environ.get("RUST_PR_BENCH_CACHE_NAMESPACE", "default"))
    return workspace / ".rust-pr-bench-cache" / namespace


def config_paths(workdir: pathlib.Path) -> list[pathlib.Path]:
    paths = [
        parent / ".cargo" / name
        for parent in [workdir, *workdir.parents]
        for name in ("config", "config.toml")
    ]
    cargo_home = pathlib.Path(
        os.environ.get("CARGO_HOME", str(pathlib.Path.home() / ".cargo"))
    )
    return [*paths, cargo_home / "config", cargo_home / "config.toml"]


def build_identity(
    repository: pathlib.Path,
    workdir: pathlib.Path,
    cases: list[dict[str, Any]],
    *,
    details: dict | None = None,
) -> tuple[str, str]:
    """Return compatibility and dependency hashes, after revision checkout."""
    toolchain = subprocess.check_output(["rustc", "-vV"], cwd=workdir, text=True)
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.startswith(("CARGO_", "RUST", "CC", "CXX", "CFLAGS", "CMAKE", "LDFLAGS"))
        and not key.startswith("RUST_PR_BENCH_")
        and key not in {"CARGO_HOME", "CARGO_TARGET_DIR", "RUSTUP_HOME"}
    }
    commands = set()
    for case in cases:
        parts = shlex.split(case["compile_command"])
        normalized = []
        index = 0
        while index < len(parts):
            if parts[index] in {"--bench", "--message-format"}:
                index += 2
                continue
            if not parts[index].startswith("--message-format="):
                normalized.append(parts[index])
            index += 1
        commands.add(tuple(normalized))
    configs = [path.read_text() for path in config_paths(workdir) if path.is_file()]
    components = {
        "rustc": toolchain,
        "os": platform.system(),
        "arch": platform.machine(),
        "image": [os.environ.get("ImageOS", ""), os.environ.get("ImageVersion", "")],
        "environment": environment,
        "commands": sorted(commands),
        "configs": configs,
        "working_directory": str(workdir.relative_to(repository)),
    }
    compatibility = digest(components)
    paths = (
        subprocess.check_output(
            [
                "git",
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                ":(glob)**/Cargo.toml",
                ":(glob)**/Cargo.lock",
                ":(glob)**/rust-toolchain",
                ":(glob)**/rust-toolchain.toml",
                ":(exclude).rust-pr-bench-cache/**",
                ":(exclude).bench-target/**",
            ],
            cwd=repository,
        )
        .decode()
        .split("\0")
    )
    manifests = {}
    for name in sorted(set(paths)):
        path = repository / name
        if (
            path.name
            in {"Cargo.toml", "Cargo.lock", "rust-toolchain", "rust-toolchain.toml"}
            and path.is_file()
        ):
            manifests[name] = path.read_text()
    if details is not None:
        details.update({name: digest(value) for name, value in components.items()})
        details["manifests"] = digest(manifests)
    return compatibility, digest(manifests)


class Cache:
    def __init__(
        self,
        kind: str,
        paths: list[pathlib.Path],
        compatibility: str,
        dependencies: str,
        *,
        writer: bool = True,
    ):
        namespace = digest(os.environ.get("RUST_PR_BENCH_CACHE_NAMESPACE", "default"))
        self.prefix = f"rust-pr-bench-v1-{namespace}-{kind}-{compatibility}-"
        self.key = self.prefix + dependencies
        self.paths = paths
        self.writer = writer
        self.record: dict[str, Any] = {
            "kind": kind,
            "key": self.key,
            "restore": "disabled",
            "save": "disabled",
            "restore_seconds": 0,
            "save_seconds": 0,
        }

    def available(self) -> bool:
        return enabled("CACHE") and bool(os.environ.get("ACTIONS_RUNTIME_TOKEN"))

    def operation(self, operation: str) -> dict[str, Any]:
        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(
                prefix="rust-pr-bench-cache-"
            ) as temporary:
                request = pathlib.Path(temporary) / "request.json"
                result = pathlib.Path(temporary) / "result.json"
                request.write_text(
                    json.dumps(
                        {
                            "operation": operation,
                            "key": self.key,
                            "paths": [str(p) for p in self.paths],
                            "restore_keys": [self.prefix],
                        }
                    )
                )
                completed = subprocess.run(
                    [
                        os.environ.get("RUST_PR_BENCH_NODE", "node"),
                        str(
                            pathlib.Path(__file__).resolve().parents[1]
                            / "dist/cache/index.mjs"
                        ),
                        str(request),
                        str(result),
                    ],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=600,
                    env={**os.environ, "SEGMENT_DOWNLOAD_TIMEOUT_MINS": "2"},
                    check=False,
                )
                # Toolkit output includes useful cache progress, never credentials.
                print(completed.stdout, end="", flush=True)
                payload = json.loads(result.read_text())
                if f"Failed to {operation}:" in completed.stdout:
                    payload["status"] = "error"
                size = re.search(r"Cache Size: ~\d+ MB \((\d+) B\)", completed.stdout)
                if size:
                    self.record["archive_bytes"] = int(size[1])
                return payload
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return {"status": "error"}
        finally:
            self.record[f"{operation}_seconds"] = round(time.monotonic() - started, 3)

    def restore(self) -> None:
        if not self.available():
            self.record["restore"] = "unavailable" if enabled("CACHE") else "disabled"
            return
        result = self.operation("restore")
        self.record["restore"] = result["status"]
        self.record["matched_key"] = result.get("matched_key", "")

    def save(self, success: bool = True) -> None:
        if not self.available():
            return
        if not success:
            self.record["save"] = "build-failed"
        elif not enabled("CACHE_SAVE") or not self.writer:
            self.record["save"] = "read-only"
        elif self.record.get("matched_key") == self.key:
            self.record["save"] = "already-exists"
        elif not any(path.exists() for path in self.paths):
            self.record["save"] = "empty"
        else:
            self.record["save"] = self.operation("save")["status"]
        self.record["contents_bytes"] = sum(
            path.stat().st_size
            for root in self.paths
            if root.exists()
            for path in root.rglob("*")
            if path.is_file() and not path.is_symlink()
        )


def append_record(directory: pathlib.Path, record: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "performance.jsonl").open("a") as handle:
        handle.write(json.dumps(record) + "\n")


def download_cache(
    repository: pathlib.Path, dependencies: str, *, writer: bool
) -> Cache:
    cargo_home = pathlib.Path(
        os.environ.get("CARGO_HOME", str(pathlib.Path.home() / ".cargo"))
    )
    return Cache(
        "downloads",
        [
            cargo_home / "registry/index",
            cargo_home / "registry/cache",
            cargo_home / "git/db",
        ],
        platform.system(),
        dependencies,
        writer=writer,
    )
