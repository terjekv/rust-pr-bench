#!/usr/bin/env python3
import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile


PACKAGE_RUNNERS = {
    "iai-callgrind": "iai-callgrind-runner",
    "gungraun": "gungraun-runner",
}
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


class DispatchError(RuntimeError):
    pass


def validate_version(package: str, version: str) -> str:
    match = SEMVER_RE.fullmatch(version)
    if not match:
        raise DispatchError(
            f"{package}: invalid library version {version!r}; "
            "expected an exact semantic version such as 0.19.4"
        )

    core = tuple(int(match.group(index)) for index in range(1, 4))
    prerelease = match.group(4)
    if package == "iai-callgrind" and (core != (0, 16, 1) or prerelease):
        raise DispatchError(
            "iai-callgrind: Rust PR Bench supports the legacy library version "
            f"0.16.1, but the benchmark requested {version}; pin iai-callgrind to "
            "0.16.1 or migrate the benchmark to gungraun >= 0.17.0"
        )
    if package == "gungraun" and (
        core < (0, 17, 0) or (core == (0, 17, 0) and prerelease)
    ):
        raise DispatchError(
            "gungraun: unsupported library version "
            f"{version}; Rust PR Bench supports gungraun versions beginning at 0.17.0"
        )
    return version


def default_cache_dir() -> pathlib.Path:
    configured = os.environ.get("RUST_PR_BENCH_RUNNER_CACHE")
    if configured:
        return pathlib.Path(configured).expanduser()
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        return pathlib.Path(xdg_cache).expanduser() / "rust-pr-bench" / "runners"
    return pathlib.Path.home() / ".cache" / "rust-pr-bench" / "runners"


def install_commands(
    package: str,
    runner: str,
    version: str,
    install_root: pathlib.Path,
) -> list[tuple[str, list[str]]]:
    commands: list[tuple[str, list[str]]] = []
    binstall = shutil.which("cargo-binstall")
    if binstall:
        commands.append(
            (
                "cargo-binstall",
                [
                    binstall,
                    "--no-confirm",
                    "--force",
                    "--disable-strategies",
                    "compile",
                    "--root",
                    str(install_root),
                    f"{runner}@{version}",
                ],
            )
        )

    cargo = shutil.which("cargo")
    if cargo:
        commands.append(
            (
                "cargo install --locked",
                [
                    cargo,
                    "install",
                    "--locked",
                    "--force",
                    "--root",
                    str(install_root),
                    "--version",
                    version,
                    runner,
                ],
            )
        )
    if not commands:
        raise DispatchError(
            f"{package} {version}: neither cargo-binstall nor cargo is available; "
            f"install {runner} {version} manually or ensure Cargo is on PATH"
        )
    return commands


def install_runner(package: str, version: str, cache_dir: pathlib.Path) -> pathlib.Path:
    runner = PACKAGE_RUNNERS[package]
    version_dir = cache_dir / runner / version
    runner_path = version_dir / "bin" / runner
    if runner_path.is_file() and os.access(runner_path, os.X_OK):
        return runner_path

    version_dir.parent.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    with tempfile.TemporaryDirectory(
        prefix=f".{version}.install-",
        dir=version_dir.parent,
    ) as temporary:
        install_root = pathlib.Path(temporary)
        for installer_name, command in install_commands(
            package, runner, version, install_root
        ):
            try:
                completed = subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    check=False,
                )
            except OSError as error:
                failures.append(f"{installer_name} could not start: {error}")
                continue
            installed = install_root / "bin" / runner
            if completed.returncode == 0 and installed.is_file():
                if version_dir.exists():
                    shutil.rmtree(version_dir)
                os.replace(install_root, version_dir)
                return runner_path
            failures.append(f"{installer_name} exited with status {completed.returncode}")

    details = "; ".join(failures)
    raise DispatchError(
        f"{package} {version}: failed to install exact runner {runner} {version}"
        f" ({details}). Check network access and that this version exists on crates.io; "
        f"you can reproduce with `cargo install --locked --version {version} {runner}`"
    )


def dispatch(package: str, runner_args: list[str], cache_dir: pathlib.Path) -> None:
    if package not in PACKAGE_RUNNERS:
        raise DispatchError(
            f"unknown runner family {package!r}; expected iai-callgrind or gungraun"
        )
    if not runner_args:
        raise DispatchError(
            f"{package}: the benchmark did not pass its library version as the first argument"
        )
    version = validate_version(package, runner_args[0])
    runner_path = install_runner(package, version, cache_dir)
    os.execv(str(runner_path), [str(runner_path), *runner_args])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True, choices=tuple(PACKAGE_RUNNERS))
    parser.add_argument("runner_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    try:
        dispatch(args.package, args.runner_args, default_cache_dir())
    except DispatchError as error:
        print(f"Rust PR Bench runner dispatch error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
