"""Activate one side's executable and runtime files immediately before execution."""

import json
import os
import pathlib
import shlex
import shutil
import subprocess

from executable_cache import source_revision, verify_bundle


def command_for(
    case_dir: pathlib.Path,
    arguments: str,
    fallback: str,
    *,
    workdir: pathlib.Path | None = None,
) -> str:
    case_dir = case_dir.resolve()
    binary = case_dir / "benchmark"
    if not binary.is_file():
        return fallback
    group_dir = case_dir.parent
    try:
        metadata = json.loads((group_dir / "build.json").read_text())
        target = pathlib.Path(metadata["target_dir"])
        if metadata.get("executable_cache"):
            manifest = verify_bundle(group_dir, reports=True)
            repository = pathlib.Path(metadata["repository"])
            if source_revision(repository) != manifest["identity"]["revision"]:
                return fallback
            for name, info in manifest["files"].items():
                if info.get("directory"):
                    (group_dir / name).mkdir(parents=True, exist_ok=True)
                else:
                    (group_dir / name).chmod(0o755 if info["executable"] else 0o644)
            assets = group_dir / "_assets"
            if assets.exists():
                for source in assets.rglob("*"):
                    destination = repository / source.relative_to(assets)
                    if not destination.resolve().is_relative_to(repository):
                        return fallback
                    if source.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, destination)
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError):
        return fallback
    runtime = group_dir / "_runtime"
    libraries = set()
    if runtime.exists():
        for source in runtime.rglob("*"):
            if source.is_dir():
                (target / source.relative_to(runtime)).mkdir(
                    parents=True, exist_ok=True
                )
            if source.is_file():
                destination = target / source.relative_to(runtime)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                if source.suffix in {".so", ".dylib", ".dll"}:
                    libraries.add(str(destination.parent))
    # Match Cargo's dynamic loader search path, including libstd for prefer-dynamic.
    version = subprocess.check_output(["rustc", "-vV"], cwd=workdir, text=True)
    host = next(
        line.removeprefix("host: ")
        for line in version.splitlines()
        if line.startswith("host: ")
    )
    sysroot = subprocess.check_output(
        ["rustc", "--print", "sysroot"], cwd=workdir, text=True
    ).strip()
    libraries.add(str(pathlib.Path(sysroot) / "lib/rustlib" / host / "lib"))
    library_path = ":".join([*sorted(libraries), os.environ.get("LD_LIBRARY_PATH", "")])
    binary.chmod(binary.stat().st_mode | 0o111)
    return f"env LD_LIBRARY_PATH={shlex.quote(library_path)} {shlex.quote(str(binary))} {arguments}"
