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
        self.run_fixture()

    def test_executables_survive_fresh_jobs_and_measurements_run_again(self):
        self.run_fixture(executable_reuse=True)

    def run_fixture(self, *, executable_reuse=False):
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
            (repo / ".gitignore").write_text("generated/\n")
            (repo / "build.rs").write_text(r"""fn main() {
    let out = std::path::PathBuf::from(std::env::var("OUT_DIR").unwrap());
    std::fs::write(out.join("value"), "generated-runtime").unwrap();
    std::fs::create_dir_all("generated").unwrap();
    std::fs::write("generated/asset", "repository-runtime").unwrap();
    let head = std::fs::read_to_string("src/lib.rs").unwrap().contains("110");
    for path in [out.join("head-only"), std::path::PathBuf::from("generated/head-only")] {
        if head { std::fs::write(path, "head").unwrap(); }
        else { let _ = std::fs::remove_file(path); }
    }
}
""")
            bench = r"""fn main() {
    assert_eq!(std::fs::read_to_string(concat!(env!("OUT_DIR"), "/value")).unwrap(), "generated-runtime");
    assert_eq!(std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/generated/asset")).unwrap(), "repository-runtime");
    if let Ok(path) = std::env::var("MEASUREMENT_LOG") {
        use std::io::Write;
        writeln!(std::fs::OpenOptions::new().create(true).append(true).open(path).unwrap(), "measured").unwrap();
    }
    let result = std::process::Command::new(env!("CARGO_BIN_EXE_helper")).output().unwrap();
    assert!(result.status.success());
    let value = String::from_utf8(result.stdout).unwrap();
    assert_eq!(value.trim().parse::<u64>().unwrap(), reuse_fixture::value());
    assert_eq!(std::path::Path::new(concat!(env!("OUT_DIR"), "/head-only")).exists(), reuse_fixture::value() == 110);
    if std::env::var("RUST_PR_BENCH_CACHE_BINARIES").as_deref() == Ok("true") {
        assert_eq!(std::path::Path::new(concat!(env!("CARGO_MANIFEST_DIR"), "/generated/head-only")).exists(), reuse_fixture::value() == 110);
    }
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
            if executable_reuse:
                transport = root / "cache-transport"
                transport.write_text(
                    f"#!{sys.executable}\n"
                    + r"""
import json, pathlib, shutil, sys
request = json.loads(pathlib.Path(sys.argv[2]).read_text())
remote = pathlib.Path(__file__).parent / "remote" / request["key"]
result = {"status": "miss" if request["operation"] == "restore" else "not-saved"}
if "-executables-" in request["key"]:
    assert request["restore_keys"] == [], request
    path = pathlib.Path(request["paths"][0])
    if request["operation"] == "save":
        shutil.copytree(path, remote, dirs_exist_ok=True)
        result = {"status": "saved"}
    elif remote.exists():
        shutil.copytree(remote, path)
        result = {"status": "exact", "matched_key": request["key"]}
pathlib.Path(sys.argv[3]).write_text(json.dumps(result))
"""
                )
                transport.chmod(0o755)
                wrappers = root / "wrappers"
                wrappers.mkdir()
                wrapper = wrappers / "cargo"
                real_cargo = shutil.which("cargo")
                wrapper.write_text(
                    f"#!{sys.executable}\n"
                    + f"""
import os, pathlib, sys
if len(sys.argv) > 1 and sys.argv[1] == "bench":
    marker = pathlib.Path({str(root / "compilation-forbidden")!r})
    if marker.exists():
        raise SystemExit("unexpected Cargo compilation on executable cache hit")
os.execv({real_cargo!r}, [{real_cargo!r}, *sys.argv[1:]])
"""
                )
                wrapper.chmod(0o755)
                environment.update(
                    {
                        "RUST_PR_BENCH_CACHE": "true",
                        "RUST_PR_BENCH_CACHE_BINARIES": "true",
                        "RUST_PR_BENCH_BINARY_CACHE_PATHS": '["generated"]',
                        "RUST_PR_BENCH_NODE": str(transport),
                        "ACTIONS_RUNTIME_TOKEN": "test",
                        "MEASUREMENT_LOG": str(root / "measurements"),
                        "PATH": str(wrappers) + os.pathsep + environment["PATH"],
                    }
                )

            def execute(work):
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
                return result

            work = root / "work"
            execute(work)
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

            if executable_reuse:
                cold_records = json.loads((work / "performance.json").read_text())
                cold_executables = [
                    r for r in cold_records if r.get("kind") == "executables"
                ]
                self.assertEqual(len(cold_executables), 2, cold_records)
                self.assertTrue(
                    all(r["save"] == "saved" for r in cold_executables),
                    cold_executables,
                )
                for target in (repo / ".rust-pr-bench-cache").glob("*/target"):
                    shutil.rmtree(target)
                (root / "compilation-forbidden").touch()
                warm = root / "new-job-work-directory"
                execute(warm)
                records = json.loads((warm / "performance.json").read_text())
                self.assertEqual(len(records), 2, records)
                self.assertTrue(
                    all(
                        r["kind"] == "executables" and r["restore"] == "exact"
                        for r in records
                    ),
                    records,
                )
                self.assertEqual(sum(r["executables_reused"] for r in records), 4)
                for path in (warm / "artifacts").rglob("result.json"):
                    data = json.loads(path.read_text())
                    self.assertEqual(data["head_total"], 110, data)
                    self.assertEqual(data["base_total"], 100, data)
                self.assertEqual(
                    len((root / "measurements").read_text().splitlines()), 8
                )
                self.assertFalse(list((root / "remote").rglob("estimates.json")))
                self.assertFalse(list((root / "remote").rglob("performance.jsonl")))
                self.assertEqual(git("rev-parse", "HEAD"), head)
