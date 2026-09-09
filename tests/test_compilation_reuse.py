"""Real Cargo integration: shared compilation must preserve each revision's binaries."""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

from testlib import REPO_ROOT


@unittest.skipUnless(shutil.which("cargo"), "Cargo is required")
class CompilationReuseTests(unittest.TestCase):
    def test_multiple_benches_reuse_builds_and_run_correct_side_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            repo = root / "repo"
            repo.mkdir()

            def git(*args):
                return subprocess.check_output(
                    ["git", *args], cwd=repo, text=True
                ).strip()

            git("init", "-q", "-b", "main")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.com")
            (repo / "src/bin").mkdir(parents=True)
            (repo / "benches").mkdir()
            (repo / "Cargo.toml").write_text("""[package]
name = "reuse-fixture"
version = "0.1.0"
edition = "2021"
[[bench]]
name = "first"
harness = false
[[bench]]
name = "second"
harness = false
""")
            (repo / "src/lib.rs").write_text("pub fn value() -> u64 { 100 }\n")
            (repo / "src/bin/helper.rs").write_text(
                'fn main() { println!("{}", reuse_fixture::value()); }\n'
            )
            bench = r"""fn main() {
    let result = std::process::Command::new(env!("CARGO_BIN_EXE_helper")).output().unwrap();
    assert!(result.status.success());
    let value = String::from_utf8(result.stdout).unwrap();
    assert_eq!(value.trim().parse::<u64>().unwrap(), reuse_fixture::value());
    let target = std::path::PathBuf::from(std::env::var("CARGO_TARGET_DIR").unwrap())
        .join("criterion/fixture/new");
    std::fs::create_dir_all(&target).unwrap();
    std::fs::write(target.join("estimates.json"),
        format!("{{\"mean\":{{\"point_estimate\":{}}}}}", value.trim())).unwrap();
}
"""
            for name in ("first", "second"):
                (repo / f"benches/{name}.rs").write_text(bench)
            # Keep the dependency identity stable between side checkouts.
            subprocess.run(
                ["cargo", "generate-lockfile", "--offline"], cwd=repo, check=True
            )
            git("add", ".")
            git("-c", "commit.gpgsign=false", "commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            (repo / "src/lib.rs").write_text("pub fn value() -> u64 { 110 }\n")
            git("add", ".")
            git("-c", "commit.gpgsign=false", "commit", "-qm", "head")
            head = git("rev-parse", "HEAD")
            environment = {
                **os.environ,
                "GITHUB_WORKSPACE": str(repo),
                "RUST_PR_BENCH_CACHE": "false",
                "RUST_PR_BENCH_BACKEND": "criterion",
                "RUST_PR_BENCH_BASE_SHA": base,
                "RUST_PR_BENCH_HEAD_SHA": head,
                "RUST_PR_BENCH_BENCHMARKS_JSON": '["first", "second"]',
                "RUST_PR_BENCH_AUTO_DISCOVER": "false",
                "RUST_PR_BENCH_CARGO_ARGS": "--offline",
            }
            work = root / "work"
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts/action_entrypoint.py"),
                    "--repository",
                    str(repo),
                    "--work-dir",
                    str(work),
                ],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            results = list((work / "artifacts").rglob("result.json"))
            self.assertEqual(len(results), 2)
            for path in results:
                data = json.loads(path.read_text())
                self.assertEqual(data["head_total"], 110, data)
                self.assertEqual(data["base_total"], 100, data)
            manifests = [
                json.loads(path.read_text())
                for path in (work / "precompiled").rglob("manifest.json")
            ]
            self.assertEqual(len(manifests), 2)
            for cases in manifests:
                self.assertTrue(all(item["precompiled"] for item in cases))
                self.assertGreater(cases[1]["fresh"], 0, cases)
                self.assertLess(cases[1]["rebuilt"], cases[0]["rebuilt"], cases)
            self.assertEqual(git("rev-parse", "HEAD"), head)
            # The cache directory contains build outputs, never measurements.
            self.assertFalse(
                list((repo / ".rust-pr-bench-cache").rglob("estimates.json"))
            )
