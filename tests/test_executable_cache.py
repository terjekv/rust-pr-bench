import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from testlib import load_script_module

precompile = load_script_module(
    "precompile_executable_tests", "scripts/precompile_benchmarks.py"
)
executable = load_script_module("executable_cache_tests", "scripts/executable_cache.py")


class ExecutableCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.env = mock.patch.dict(
            os.environ,
            {
                "GITHUB_WORKSPACE": str(self.root),
                "ACTIONS_RUNTIME_TOKEN": "test",
                "RUST_PR_BENCH_CACHE": "true",
                "RUST_PR_BENCH_CACHE_SAVE": "true",
                "RUST_PR_BENCH_BINARY_CACHE_PATHS": "[]",
                "RUST_PR_BENCH_BINARY_CACHE_KEY": "",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.cases = [
            {
                "id": "first",
                "benchmark_name": "first",
                "compile_command": "cargo bench --bench first --no-run",
            }
        ]
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        (self.repo / "Cargo.toml").write_text(
            '[package]\nname="fixture"\nversion="0.1.0"\n'
        )
        (self.repo / "Cargo.lock").write_text("version = 4\n")
        (self.repo / "lib.rs").write_text("// fixture\n")
        self.commit()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, text=True).strip()

    def commit(self):
        self.git("add", ".")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "fixture")

    def identity(self, cases=None, build=("compiler", "deps")):
        return executable.identity(
            self.repo, self.repo, self.root / "target", cases or self.cases, build
        )

    def make_bundle(self):
        entry = executable.ExecutableCache(self.repo, self.identity())
        output = self.root / "compiled"
        (output / "first").mkdir(parents=True)
        (output / "first/benchmark").write_text("benchmark")
        (output / "first/benchmark").chmod(0o755)
        (output / "_runtime/release/build/out/empty").mkdir(parents=True)
        (output / "_runtime/release/build/out/value").write_text("runtime")
        remote = self.root / "remote"

        def save(operation):
            self.assertEqual(operation, "save")
            shutil.copytree(entry.directory, remote)
            return {"status": "saved"}

        with mock.patch.object(entry.cache, "operation", side_effect=save):
            entry.save(output, [{"precompiled": True}])
        self.assertEqual(entry.cache.record["save"], "saved", entry.cache.record)
        return entry, remote

    def restore(self, entry, remote, *, matched=None):
        def operation(name):
            self.assertEqual(name, "restore")
            shutil.copytree(remote, entry.directory, symlinks=True)
            return {"status": "exact", "matched_key": matched or entry.cache.key}

        with mock.patch.object(entry.cache, "operation", side_effect=operation):
            return entry.restore(self.root / "restored")

    def test_exact_verified_bundle_reuses_executable_and_runtime(self):
        entry, remote = self.make_bundle()
        self.assertTrue(entry.cache.exact_only)
        results = self.restore(entry, remote)
        self.assertTrue(results[0]["executable_reused"])
        self.assertEqual(entry.cache.record["executables_reused"], 1)
        self.assertTrue(
            (self.root / "restored/_runtime/release/build/out/empty").is_dir()
        )
        self.assertFalse((remote / "performance.jsonl").exists())

    def test_corrupt_missing_extra_and_symlink_files_reject_restore(self):
        for damage in (
            "corrupt",
            "missing",
            "extra",
            "symlink",
            "identity",
            "metadata",
        ):
            with self.subTest(damage=damage):
                entry, remote = self.make_bundle()
                value = remote / "_runtime/release/build/out/value"
                if damage == "corrupt":
                    value.write_text("tampered")
                elif damage == "missing":
                    value.unlink()
                elif damage == "extra":
                    (remote / "unexpected").write_text("extra")
                elif damage == "symlink":
                    value.unlink()
                    value.symlink_to(self.repo / "lib.rs")
                elif damage == "identity":
                    entry.identity = {**entry.identity, "revision": "other"}
                else:
                    (remote / "bundle.json").unlink()
                restored = self.root / "restored"
                restored.mkdir(exist_ok=True)
                self.assertIsNone(self.restore(entry, remote))
                self.assertEqual(entry.cache.record["restore"], "rejected")
                self.assertFalse((restored / "first/benchmark").exists())
                shutil.rmtree(remote)
                shutil.rmtree(self.root / "compiled")

    def test_primary_prefix_match_is_never_executed(self):
        entry, remote = self.make_bundle()
        (self.root / "restored").mkdir()
        self.assertIsNone(
            self.restore(entry, remote, matched=entry.cache.key + "-other")
        )
        self.assertEqual(entry.cache.record["reason"], "non-exact-key")

    def test_identity_partitions_source_cases_toolchain_paths_and_external_inputs(self):
        original = self.identity()
        (self.repo / "lib.rs").write_text("// changed\n")
        with self.assertRaisesRegex(ValueError, "dirty-source"):
            self.identity()
        self.commit()
        changed = self.identity()
        self.assertNotEqual(original, changed)
        for update in (
            {"compile_command": "cargo bench --bench first --no-run --profile dev"},
            {"id": "second", "benchmark_name": "second"},
        ):
            self.assertNotEqual(changed, self.identity([{**self.cases[0], **update}]))
        self.assertNotEqual(changed, self.identity(build=("new-compiler", "deps")))
        self.assertNotEqual(changed, self.identity(build=("compiler", "new-lock")))
        with mock.patch.dict(
            os.environ, {"RUST_PR_BENCH_BINARY_CACHE_KEY": "native-libs-v2"}
        ):
            self.assertNotEqual(changed, self.identity())
        with mock.patch.dict(
            os.environ, {"RUST_PR_BENCH_BINARY_CACHE_PATHS": '["fixture"]'}
        ):
            self.assertNotEqual(changed, self.identity())

    def test_missing_lock_and_external_path_dependencies_are_ineligible(self):
        (self.repo / "Cargo.lock").unlink()
        self.commit()
        with self.assertRaisesRegex(ValueError, "committed-lockfile-required"):
            self.identity()
        with (self.repo / "Cargo.toml").open("a") as handle:
            handle.write('[dependencies]\nexternal={path="../external"}\n')
        self.commit()
        with self.assertRaisesRegex(ValueError, "external-path-dependency"):
            self.identity()

    def test_unsafe_asset_paths_are_rejected(self):
        for name in (
            "/etc/passwd",
            "../outside",
            ".",
            ".git/config",
            "generated/../secret",
            "a\\b",
            ".bench-target",
        ):
            with (
                self.subTest(name=name),
                mock.patch.dict(
                    os.environ, {"RUST_PR_BENCH_BINARY_CACHE_PATHS": json.dumps([name])}
                ),
            ):
                with self.assertRaises(ValueError):
                    executable.runtime_paths()

    def test_restore_only_policy_does_not_publish_executables(self):
        entry, _ = self.make_bundle()
        with (
            mock.patch.dict(os.environ, {"RUST_PR_BENCH_CACHE_SAVE": "false"}),
            mock.patch.object(entry.cache, "operation") as operation,
        ):
            entry.save(self.root / "compiled", [{"precompiled": True}])
            self.assertEqual(entry.cache.record["save"], "read-only")
            operation.assert_not_called()

    def test_incomplete_runtime_is_not_published(self):
        entry = executable.ExecutableCache(self.repo, self.identity())
        with mock.patch.object(entry.cache, "operation") as operation:
            entry.save(
                self.root / "missing",
                [{"precompiled": True, "runtime_complete": False}],
            )
            operation.assert_not_called()
        self.assertEqual(entry.cache.record["save"], "incomplete-build")

    def test_compile_group_exact_hit_skips_all_cargo_and_dependency_cache_work(self):
        entry, remote = self.make_bundle()
        output = self.root / "output"
        with (
            mock.patch.dict(os.environ, {"RUST_PR_BENCH_CACHE_BINARIES": "true"}),
            mock.patch.object(
                precompile, "build_identity", return_value=("compiler", "deps")
            ),
            mock.patch.object(precompile, "precompile_case") as compile_case,
            mock.patch.object(precompile, "download_cache") as downloads,
            mock.patch.object(
                precompile.ExecutableCache,
                "restore",
                return_value=[{"precompiled": True, "executable_reused": True}],
            ),
        ):
            results = precompile.compile_group(
                self.repo, self.repo, "HEAD", self.cases, output
            )
            self.assertTrue(results[0]["executable_reused"])
            compile_case.assert_not_called()
            downloads.assert_not_called()

    def test_artifact_transport_can_drop_modes_and_empty_directories(self):
        entry, remote = self.make_bundle()
        (remote / "first/benchmark").chmod(0o644)
        (remote / "_runtime/release/build/out/empty").rmdir()
        results = self.restore(entry, remote)
        self.assertTrue(results[0]["executable_reused"])
        binary = self.root / "restored/first/benchmark"
        self.assertTrue(binary.stat().st_mode & 0o111)
        self.assertTrue(
            (self.root / "restored/_runtime/release/build/out/empty").is_dir()
        )

    def test_rejected_bundle_falls_back_to_cargo(self):
        with (
            mock.patch.dict(os.environ, {"RUST_PR_BENCH_CACHE_BINARIES": "true"}),
            mock.patch.object(
                precompile, "build_identity", return_value=("compiler", "deps")
            ),
            mock.patch.object(precompile.ExecutableCache, "restore", return_value=None),
            mock.patch.object(precompile.ExecutableCache, "save"),
            mock.patch.object(precompile.Cache, "restore"),
            mock.patch.object(precompile.Cache, "save"),
            mock.patch.object(
                precompile, "precompile_case", return_value={"precompiled": True}
            ) as compile_case,
        ):
            results = precompile.compile_group(
                self.repo, self.repo, "HEAD", self.cases, self.root / "output"
            )
            self.assertTrue(results[0]["precompiled"])
            compile_case.assert_called_once()

    def test_native_build_never_attempts_executable_reuse(self):
        with (
            mock.patch.dict(
                os.environ,
                {
                    "RUST_PR_BENCH_CACHE_BINARIES": "true",
                    "CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUSTFLAGS": "-C target-cpu=native",
                },
            ),
            mock.patch.object(precompile, "ExecutableCache") as bundles,
        ):
            result = precompile.compile_group(
                self.repo, self.repo, "HEAD", self.cases, self.root / "output"
            )
            self.assertEqual(result[0]["reason"], "target-cpu=native")
            bundles.assert_not_called()

    def test_stable_worktree_cannot_be_used_concurrently_or_delete_unrelated_files(
        self,
    ):
        module = load_script_module("worktree_tests", "scripts/benchmark_worktree.py")
        with mock.patch.dict(os.environ, {"RUST_PR_BENCH_CACHE_BINARIES": "true"}):
            with module.benchmark_worktree(
                self.repo, self.root / "work1", "HEAD"
            ) as repo:
                self.assertTrue(repo.is_dir())
                with self.assertRaisesRegex(ValueError, "already in use"):
                    with module.benchmark_worktree(
                        self.repo, self.root / "work2", "HEAD"
                    ):
                        self.fail("a simultaneous action acquired the worktree")
            self.assertFalse(repo.exists())
            repo.mkdir()
            (repo / "keep").write_text("unrelated data")
            with self.assertRaisesRegex(ValueError, "unrelated directory"):
                with module.benchmark_worktree(self.repo, self.root / "work3", "HEAD"):
                    self.fail("an unrelated directory was replaced")
            self.assertEqual((repo / "keep").read_text(), "unrelated data")
