import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from testlib import REPO_ROOT, load_script_module

action_entrypoint = load_script_module(
    "action_entrypoint", "scripts/action_entrypoint.py"
)


class ActionEntrypointTests(unittest.TestCase):
    def test_boolean_inputs_are_strict(self) -> None:
        self.assertTrue(action_entrypoint.parse_bool("true", "enabled"))
        self.assertFalse(action_entrypoint.parse_bool("FALSE", "enabled"))
        with self.assertRaisesRegex(ValueError, "enabled"):
            action_entrypoint.parse_bool("yes", "enabled")

    def test_resolve_revisions_prefers_explicit_inputs(self) -> None:
        event = {
            "number": 7,
            "pull_request": {
                "base": {"sha": "event-base"},
                "head": {"sha": "event-head"},
            },
        }
        environment = {
            "RUST_PR_BENCH_BASE_SHA": "input-base",
            "RUST_PR_BENCH_HEAD_SHA": "input-head",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            self.assertEqual(
                action_entrypoint.resolve_revisions(event),
                ("input-base", "input-head", 7),
            )

    def test_end_to_end_criterion_run_writes_report_and_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repository = root / "repository"
            repository.mkdir()
            subprocess.run(["git", "init", "-b", "main"], cwd=repository, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=repository,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=repository,
                check=True,
            )
            (repository / "bench.py").write_text(
                """import json, os, pathlib
assert os.environ['BENCH_TOKEN'] == os.environ['RUST_PR_BENCH_SIDE']
value = float(pathlib.Path('value.txt').read_text())
target = pathlib.Path(os.environ['CARGO_TARGET_DIR']) / 'criterion' / 'fixture' / 'new'
target.mkdir(parents=True, exist_ok=True)
payload = {'mean': {'point_estimate': value, 'confidence_interval': {'lower_bound': value, 'upper_bound': value}}}
(target / 'estimates.json').write_text(json.dumps(payload))
""",
                encoding="utf-8",
            )
            (repository / "value.txt").write_text("100", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(
                ["git", "-c", "commit.gpgsign=false", "commit", "-m", "base"],
                cwd=repository,
                check=True,
            )
            base_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repository,
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()
            (repository / "value.txt").write_text("110", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(
                ["git", "-c", "commit.gpgsign=false", "commit", "-m", "head"],
                cwd=repository,
                check=True,
            )
            head_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repository,
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()

            output_file = root / "github-output"
            environment = os.environ.copy()
            environment.update(
                {
                    "GITHUB_OUTPUT": str(output_file),
                    "RUST_PR_BENCH_BACKEND": "criterion",
                    "RUST_PR_BENCH_BENCHMARKS_JSON": json.dumps(
                        [
                            {
                                "name": "fixture",
                                "command": "python3 bench.py",
                                "backend": "criterion",
                            }
                        ]
                    ),
                    "RUST_PR_BENCH_AUTO_DISCOVER": "false",
                    "RUST_PR_BENCH_AUTO_DETECT_MOVED_BENCHMARKS": "false",
                    "RUST_PR_BENCH_FEATURE_SETS_JSON": '[{"name":"default","features":""}]',
                    "RUST_PR_BENCH_BASE_SHA": base_sha,
                    "RUST_PR_BENCH_HEAD_SHA": head_sha,
                    "RUST_PR_BENCH_REGRESSION_THRESHOLD_PCT": "3",
                    "RUST_PR_BENCH_REGRESSION_THRESHOLD_PCT_GUNGRAUN": "-1",
                    "RUST_PR_BENCH_REGRESSION_THRESHOLD_PCT_CRITERION": "-1",
                    "RUST_PR_BENCH_FAIL_ON_REGRESSION": "false",
                    "RUST_PR_BENCH_COMMENT_MODE": "never",
                    "RUST_PR_BENCH_SETUP_COMMAND": (
                        "printf 'BENCH_TOKEN=%s\\n' \"$RUST_PR_BENCH_SIDE\" >> "
                        "\"$RUST_PR_BENCH_ENV_FILE\""
                    ),
                    "RUST_PR_BENCH_READINESS_COMMAND": (
                        "test \"$BENCH_TOKEN\" = \"$RUST_PR_BENCH_SIDE\""
                    ),
                    "RUST_PR_BENCH_TEARDOWN_COMMAND": (
                        "printf '%s\\n' \"$RUST_PR_BENCH_SIDE\" >> "
                        "\"$RUST_PR_BENCH_REPOSITORY/../lifecycle-events\""
                    ),
                    "RUST_PR_BENCH_READINESS_TIMEOUT_SECONDS": "2",
                }
            )
            work_dir = root / "work"
            subprocess.run(
                [
                    "python3",
                    str(REPO_ROOT / "scripts" / "action_entrypoint.py"),
                    "--repository",
                    str(repository),
                    "--work-dir",
                    str(work_dir),
                ],
                check=True,
                env=environment,
            )

            outputs = dict(
                line.split("=", 1)
                for line in output_file.read_text(encoding="utf-8").splitlines()
            )
            self.assertEqual(outputs["has_regressions"], "true")
            self.assertEqual(outputs["has_unaccepted_regressions"], "true")
            self.assertEqual(outputs["had_errors"], "false")
            self.assertEqual(outputs["should_fail"], "false")
            report = pathlib.Path(outputs["report_path"])
            self.assertTrue(report.is_file())
            self.assertIn("Criterion Benchmark Report", report.read_text(encoding="utf-8"))
            self.assertEqual(
                (work_dir / "lifecycle-events").read_text(encoding="utf-8").splitlines(),
                ["head", "base"],
            )


if __name__ == "__main__":
    unittest.main()
