"""Activate one side's executable and runtime files immediately before execution."""

import json
import os
import pathlib
import shlex
import shutil
import subprocess

from executable_cache import relative_path, source_revision, verify_bundle


def clear_declared_paths(root: pathlib.Path, names: list[str]) -> None:
    """Replace snapshots without retaining files that exist only in the other side."""
    destinations = [root / relative_path(name) for name in names]
    if any(not path.resolve().is_relative_to(root.resolve()) for path in destinations):
        raise ValueError("runtime directory escapes its root")
    for destination in destinations:
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        elif destination.exists() or destination.is_symlink():
            destination.unlink()


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
                clear_declared_paths(repository, manifest["identity"]["runtime_paths"])
                for source in assets.rglob("*"):
                    destination = repository / source.relative_to(assets)
                    if not destination.resolve().is_relative_to(repository):
                        return fallback
                    if source.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, destination)
        directories_path = group_dir / "runtime-directories.json"
        if directories_path.exists():
            directories = json.loads(directories_path.read_text())
            clear_declared_paths(target, directories)
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
