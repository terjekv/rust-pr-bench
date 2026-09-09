import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from testlib import load_script_module

cache = load_script_module("build_cache", "scripts/build_cache.py")


class BuildCacheTests(unittest.TestCase):
    def test_source_changes_reuse_dependencies_but_flags_and_lockfiles_partition(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = pathlib.Path(temporary)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / "Cargo.toml").write_text(
                '[package]\nname="fixture"\nversion="0.1.0"\n'
            )
            (repo / "Cargo.lock").write_text("# old lock\n")
            (repo / "lib.rs").write_text("// old source\n")
            case = [
                {
                    "compile_command": "cargo bench --bench first --features serde --no-run"
                }
            ]
            with mock.patch.dict(
                os.environ,
                {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]},
                clear=True,
            ):
                original = cache.build_identity(repo, repo, case)
                (repo / "lib.rs").write_text("// new source\n")
                self.assertEqual(original, cache.build_identity(repo, repo, case))
                (repo / "Cargo.lock").write_text("# new lock\n")
                changed = cache.build_identity(repo, repo, case)
                self.assertEqual(original[0], changed[0])
                self.assertNotEqual(original[1], changed[1])
                with mock.patch.dict(os.environ, {"RUSTFLAGS": "-C opt-level=1"}):
                    self.assertNotEqual(
                        changed[0], cache.build_identity(repo, repo, case)[0]
                    )
                other_bench = [
                    {
                        "compile_command": "cargo bench --bench second --features serde --no-run"
                    }
                ]
                self.assertEqual(changed, cache.build_identity(repo, repo, other_bench))
                other_features = [
                    {
                        "compile_command": "cargo bench --bench second --features other --no-run"
                    }
                ]
                self.assertNotEqual(
                    changed[0], cache.build_identity(repo, repo, other_features)[0]
                )

    def test_disabled_and_read_only_caches_never_save(self):
        entry = cache.Cache("build", [pathlib.Path("unused")], "compiler", "deps")
        with mock.patch.dict(os.environ, {"RUST_PR_BENCH_CACHE": "false"}, clear=True):
            with mock.patch.object(entry, "operation") as operation:
                entry.restore()
                entry.save()
                operation.assert_not_called()
                self.assertEqual(entry.record["restore"], "disabled")
        with mock.patch.dict(
            os.environ,
            {"ACTIONS_RUNTIME_TOKEN": "test", "RUST_PR_BENCH_CACHE_SAVE": "false"},
            clear=True,
        ):
            with mock.patch.object(
                entry,
                "operation",
                return_value={"status": "fallback", "matched_key": "older"},
            ) as operation:
                entry.restore()
                entry.save()
                operation.assert_called_once_with("restore")
                self.assertEqual(entry.record["save"], "read-only")
                self.assertEqual(entry.record["matched_key"], "older")

    def test_failed_build_and_non_writer_do_not_save(self):
        with mock.patch.dict(os.environ, {"ACTIONS_RUNTIME_TOKEN": "test"}, clear=True):
            entry = cache.Cache("build", [], "compiler", "deps", writer=False)
            with mock.patch.object(entry, "operation") as operation:
                entry.save()
                self.assertEqual(entry.record["save"], "read-only")
                entry.save(False)
                self.assertEqual(entry.record["save"], "build-failed")
                operation.assert_not_called()

    def test_exact_hit_does_not_attempt_an_immutable_save(self):
        with mock.patch.dict(os.environ, {"ACTIONS_RUNTIME_TOKEN": "test"}, clear=True):
            entry = cache.Cache("build", [], "compiler", "deps")
            with mock.patch.object(
                entry,
                "operation",
                return_value={"status": "exact", "matched_key": entry.key},
            ) as operation:
                entry.restore()
                entry.save()
                operation.assert_called_once_with("restore")
                self.assertEqual(entry.record["save"], "already-exists")

    def test_transport_timeout_is_nonfatal_and_recorded(self):
        entry = cache.Cache("build", [], "compiler", "deps")
        with mock.patch.object(
            cache.subprocess, "run", side_effect=subprocess.TimeoutExpired("node", 600)
        ):
            self.assertEqual(entry.operation("restore"), {"status": "error"})
        self.assertIn("restore_seconds", entry.record)

    def test_paths_and_namespace_match_across_interfaces(self):
        with mock.patch.dict(
            os.environ,
            {
                "GITHUB_WORKSPACE": "/work/project",
                "RUST_PR_BENCH_CACHE_NAMESPACE": "ci",
            },
            clear=True,
        ):
            workflow = cache.cache_root(pathlib.Path("/work/project/repo"))
            action = cache.cache_root(pathlib.Path("/runner/temp/worktree"))
            self.assertEqual(workflow, action)


class PerformanceReportTests(unittest.TestCase):
    def test_report_distinguishes_restore_outcomes_without_claiming_savings(self):
        report = load_script_module(
            "performance_report", "scripts/performance_report.py"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            for state in ("exact", "fallback", "miss", "error", "disabled"):
                cache.append_record(
                    root, {"label": state, "restore": state, "compile_seconds": 2.5}
                )
            output = report.render(root)
            for state in ("exact", "fallback", "miss", "error", "disabled"):
                self.assertIn(state, output)
            self.assertIn("not time saved", output)
            self.assertIn("2.50s", output)
