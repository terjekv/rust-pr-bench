"""Activate one side's executable and runtime files immediately before execution."""

import json
import os
import pathlib
import shlex
import shutil
import subprocess


def command_for(case_dir: pathlib.Path, arguments: str, fallback: str) -> str:
    case_dir = case_dir.resolve()
    binary = case_dir / "benchmark"
    if not binary.is_file():
        return fallback
    group_dir = case_dir.parent
    try:
        metadata = json.loads((group_dir / "build.json").read_text())
    except (OSError, ValueError):
        return fallback
    target = pathlib.Path(metadata["target_dir"])
    runtime = group_dir / "_runtime"
    libraries = set()
    if runtime.exists():
        for source in runtime.rglob("*"):
            if source.is_file():
                destination = target / source.relative_to(runtime)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                if source.suffix in {".so", ".dylib", ".dll"}:
                    libraries.add(str(destination.parent))
    # Match Cargo's dynamic loader search path, including libstd for prefer-dynamic.
    version = subprocess.check_output(["rustc", "-vV"], text=True)
    host = next(
        line.removeprefix("host: ")
        for line in version.splitlines()
        if line.startswith("host: ")
    )
    sysroot = subprocess.check_output(
        ["rustc", "--print", "sysroot"], text=True
    ).strip()
    libraries.add(str(pathlib.Path(sysroot) / "lib/rustlib" / host / "lib"))
    library_path = ":".join([*sorted(libraries), os.environ.get("LD_LIBRARY_PATH", "")])
    binary.chmod(binary.stat().st_mode | 0o111)
    return f"env LD_LIBRARY_PATH={shlex.quote(library_path)} {shlex.quote(str(binary))} {arguments}"
