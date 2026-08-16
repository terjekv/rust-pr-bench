import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest

from testlib import REPO_ROOT


DISPATCHER = REPO_ROOT / "scripts" / "runner_dispatch.py"


class RunnerDispatchTests(unittest.TestCase):
    def write_executable(self, path: pathlib.Path, source: str) -> None:
        path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
        path.chmod(0o755)

    def make_environment(
        self,
        root: pathlib.Path,
        *,
        binstall_exit: int = 0,
        cargo_exit: int = 1,
    ) -> dict[str, str]:
        fake_bin = root / "bin"
        fake_bin.mkdir()
        installer_log = root / "installers.log"
        runner_template = root / "fake-runner"
        self.write_executable(
            runner_template,
            """\
            #!/bin/sh
            printf '%s\n' "$@" > "${FAKE_RUN_LOG}.args"
            /bin/cat > "${FAKE_RUN_LOG}.stdin"
            """,
        )
        installer = """\
            #!/bin/sh
            root=
            previous=
            package=
            for argument in "$@"; do
              if [ "$previous" = "--root" ]; then
                root="$argument"
              fi
              previous="$argument"
              package="$argument"
            done
            printf '%s %s\n' "$(basename "$0")" "$*" >> "$FAKE_INSTALLER_LOG"
            exit_code="${FAKE_BINSTALL_EXIT}"
            if [ "$(basename "$0")" = "cargo" ]; then
              exit_code="${FAKE_CARGO_EXIT}"
            fi
            if [ "$exit_code" -ne 0 ]; then
              exit "$exit_code"
            fi
            runner="${package%@*}"
            /bin/mkdir -p "$root/bin"
            /bin/cp "$FAKE_RUNNER_TEMPLATE" "$root/bin/$runner"
            /bin/chmod +x "$root/bin/$runner"
            """
        self.write_executable(fake_bin / "cargo-binstall", installer)
        self.write_executable(fake_bin / "cargo", installer)
        return {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "RUST_PR_BENCH_RUNNER_CACHE": str(root / "cache"),
            "FAKE_INSTALLER_LOG": str(installer_log),
            "FAKE_RUNNER_TEMPLATE": str(runner_template),
            "FAKE_BINSTALL_EXIT": str(binstall_exit),
            "FAKE_CARGO_EXIT": str(cargo_exit),
        }

    def dispatch(
        self,
        root: pathlib.Path,
        package: str,
        version: str,
        env: dict[str, str],
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        run_log = root / f"{package}-{version}"
        return subprocess.run(
            [
                sys.executable,
                str(DISPATCHER),
                "--package",
                package,
                version,
                *arguments,
            ],
            input="encoded benchmark payload",
            text=True,
            capture_output=True,
            env={**env, "FAKE_RUN_LOG": str(run_log)},
        )

    def test_selects_old_and_new_packages_and_forwards_exact_arguments_and_stdin(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            env = self.make_environment(root)
            cases = [
                ("iai-callgrind", "0.16.1", "iai-callgrind-runner"),
                ("gungraun", "0.19.4", "gungraun-runner"),
            ]
            for package, version, runner in cases:
                with self.subTest(package=package):
                    completed = self.dispatch(
                        root,
                        package,
                        version,
                        env,
                        "--lib-bench",
                        "crate",
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    run_log = root / f"{package}-{version}"
                    self.assertEqual(
                        pathlib.Path(f"{run_log}.args").read_text(encoding="utf-8"),
                        f"{version}\n--lib-bench\ncrate\n",
                    )
                    self.assertEqual(
                        pathlib.Path(f"{run_log}.stdin").read_text(encoding="utf-8"),
                        "encoded benchmark payload",
                    )
                    self.assertIn(
                        f"{runner}@{version}",
                        (root / "installers.log").read_text(encoding="utf-8"),
                    )

    def test_reuses_cached_exact_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            env = self.make_environment(root)
            for _ in range(2):
                completed = self.dispatch(root, "gungraun", "0.19.4", env)
                self.assertEqual(completed.returncode, 0, completed.stderr)

            installs = (root / "installers.log").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(installs), 1)

    def test_falls_back_to_locked_cargo_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            env = self.make_environment(root, binstall_exit=7, cargo_exit=0)
            completed = self.dispatch(root, "gungraun", "0.19.4", env)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            installs = (root / "installers.log").read_text(encoding="utf-8")
            self.assertIn("cargo-binstall", installs)
            self.assertIn("cargo install --locked", installs)

    def test_invalid_version_does_not_invoke_an_installer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            env = self.make_environment(root)
            completed = self.dispatch(root, "gungraun", "latest", env)

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("invalid library version", completed.stderr)
            self.assertFalse((root / "installers.log").exists())

    def test_enforces_supported_old_baseline_and_new_family_minimum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            env = self.make_environment(root)
            old = self.dispatch(root, "iai-callgrind", "0.16.0", env)
            new = self.dispatch(root, "gungraun", "0.16.1", env)

            self.assertNotEqual(old.returncode, 0)
            self.assertIn("supports the legacy library version 0.16.1", old.stderr)
            self.assertNotEqual(new.returncode, 0)
            self.assertIn("beginning at 0.17.0", new.stderr)
            self.assertFalse((root / "installers.log").exists())

    def test_failed_install_names_package_version_and_reproduction_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            env = self.make_environment(root, binstall_exit=7, cargo_exit=8)
            completed = self.dispatch(root, "iai-callgrind", "0.16.1", env)

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("iai-callgrind 0.16.1", completed.stderr)
            self.assertIn("failed to install exact runner", completed.stderr)
            self.assertIn(
                "cargo install --locked --version 0.16.1 iai-callgrind-runner",
                completed.stderr,
            )


if __name__ == "__main__":
    unittest.main()
