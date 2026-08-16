import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from testlib import REPO_ROOT, load_script_module


expand_matrix = load_script_module("expand_matrix", "scripts/expand_matrix.py")


class ExpandMatrixTests(unittest.TestCase):
    def test_normalize_backend_selection_accepts_canonical_names(self) -> None:
        self.assertEqual(expand_matrix.normalize_backend_selection("all"), "all")
        self.assertEqual(
            expand_matrix.normalize_backend_selection("gungraun"),
            "gungraun",
        )

    def test_discover_benchmarks_routes_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            benches = repo / "crate" / "benches"
            benches.mkdir(parents=True)
            (benches / "alpha_callgrind.rs").write_text("// callgrind\n", encoding="utf-8")
            (benches / "alpha_gungraun.rs").write_text("// gungraun\n", encoding="utf-8")
            (benches / "alpha_iai_callgrind.rs").write_text(
                "// iai-callgrind\n", encoding="utf-8"
            )
            (benches / "beta_criterion.rs").write_text("// criterion\n", encoding="utf-8")
            (benches / "shared.rs").write_text("// both\n", encoding="utf-8")

            discovered = expand_matrix.discover_benchmarks(repo, "crate", "all")
            pairs = {(item["name"], item["backend"]) for item in discovered}

            self.assertEqual(
                pairs,
                {
                    ("alpha_callgrind", "gungraun"),
                    ("alpha_gungraun", "gungraun"),
                    ("alpha_iai_callgrind", "gungraun"),
                    ("beta_criterion", "criterion"),
                    ("shared", "gungraun"),
                    ("shared", "criterion"),
                },
            )

    def test_explicit_legacy_backend_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "gungraun.*criterion"):
            expand_matrix.expand_benchmark_entry(
                {"name": "legacy", "bench": "legacy", "backend": "iai-callgrind"},
                "gungraun",
            )

    def test_discover_benchmarks_finds_workspace_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            (repo / "Cargo.toml").write_text(
                '[workspace]\nmembers = ["crates/*"]\n',
                encoding="utf-8",
            )
            for crate_name in ("alpha", "beta"):
                crate = repo / "crates" / crate_name
                (crate / "benches").mkdir(parents=True)
                (crate / "Cargo.toml").write_text(
                    f'[package]\nname = "{crate_name}"\nversion = "0.1.0"\nedition = "2021"\n',
                    encoding="utf-8",
                )
                (crate / "benches" / "shared_callgrind.rs").write_text("// bench\n", encoding="utf-8")

            discovered = expand_matrix.discover_benchmarks(repo, ".", "gungraun")

            self.assertEqual(
                {(item["name"], item["manifest_path"]) for item in discovered},
                {
                    ("crates/alpha/shared_callgrind", "crates/alpha/Cargo.toml"),
                    ("crates/beta/shared_callgrind", "crates/beta/Cargo.toml"),
                },
            )

    def test_discover_benchmarks_honors_globbed_workspace_excludes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            (repo / "Cargo.toml").write_text(
                '[workspace]\nmembers = ["crates/*"]\nexclude = ["crates/example"]\n',
                encoding="utf-8",
            )
            for crate_name in ("included", "example"):
                crate = repo / "crates" / crate_name
                (crate / "benches").mkdir(parents=True)
                (crate / "Cargo.toml").write_text(
                    f'[package]\nname = "{crate_name}"\nversion = "0.1.0"\nedition = "2021"\n',
                    encoding="utf-8",
                )
                (crate / "benches" / "shared_callgrind.rs").write_text("// bench\n", encoding="utf-8")

            discovered = expand_matrix.discover_benchmarks(repo, ".", "gungraun")

            self.assertEqual([item["name"] for item in discovered], ["crates/included/shared_callgrind"])

    def test_build_command_appends_criterion_args_only_for_criterion(self) -> None:
        spec = {"name": "bench_a", "bench": "bench_a"}
        feature_set = {"name": "default", "features": "", "no_default_features": False}

        callgrind_command = expand_matrix.build_command(
            spec, feature_set, "", "gungraun", "--noplot --sample-size 10"
        )
        criterion_command = expand_matrix.build_command(
            spec, feature_set, "", "criterion", "--noplot --sample-size 10"
        )

        self.assertEqual(callgrind_command, "cargo bench --bench bench_a")
        self.assertEqual(
            criterion_command,
            "cargo bench --bench bench_a -- --noplot --sample-size 10",
        )

    def test_cargo_args_are_before_criterion_separator(self) -> None:
        spec = {"name": "bench_a", "bench": "bench_a"}
        feature_set = {"name": "default", "features": "", "no_default_features": False}

        command = expand_matrix.build_command(
            spec, feature_set, "--release", "criterion", "--noplot --sample-size 10"
        )

        self.assertEqual(
            command,
            "cargo bench --bench bench_a --release -- --noplot --sample-size 10",
        )

    def test_split_benchmark_command_separates_compile_and_runtime_args(self) -> None:
        split = expand_matrix.split_benchmark_command(
            "cargo bench --bench bench_a --release -- --noplot --sample-size 10"
        )

        self.assertEqual(
            split,
            (
                "cargo bench --bench bench_a --release --no-run --message-format json",
                "--bench --noplot --sample-size 10",
            ),
        )

    def test_split_benchmark_command_adds_cargos_implicit_bench_argument(self) -> None:
        self.assertEqual(
            expand_matrix.split_benchmark_command("cargo bench --bench bench_a"),
            (
                "cargo bench --bench bench_a --no-run --message-format json",
                "--bench",
            ),
        )

    def test_native_target_cpu_disables_cross_runner_precompilation(self) -> None:
        feature_sets = [{"name": "default", "features": "", "no_default_features": False}]
        benchmarks = [{"name": "native", "bench": "native", "backend": "criterion"}]

        matrix = expand_matrix.make_matrix(
            benchmarks,
            feature_sets,
            "--config 'build.rustflags=[\"-C\",\"target-cpu=native\"]'",
            "--noplot",
        )
        build_matrix = expand_matrix.make_build_matrix(matrix)

        self.assertFalse(matrix["include"][0]["precompile"])
        self.assertFalse(build_matrix["include"][0]["enabled"])

    def test_split_benchmark_command_rejects_opaque_custom_command(self) -> None:
        self.assertIsNone(expand_matrix.split_benchmark_command("./scripts/run-benchmark bench_a"))

    def test_build_matrix_groups_benchmarks_by_feature_and_revision(self) -> None:
        feature_sets = [{"name": "default", "features": "", "no_default_features": False}]
        benchmarks = [
            {"name": "bench_a", "bench": "bench_a", "backend": "gungraun"},
            {"name": "bench_b", "bench": "bench_b", "backend": "gungraun"},
        ]

        benchmark_matrix = expand_matrix.make_matrix(benchmarks, feature_sets, "", "--noplot")
        build_matrix = expand_matrix.make_build_matrix(benchmark_matrix)

        self.assertTrue(all(item["precompile"] for item in benchmark_matrix["include"]))
        self.assertEqual({item["side"] for item in build_matrix["include"]}, {"head", "base"})
        self.assertEqual([len(item["cases"]) for item in build_matrix["include"]], [2, 2])

    def test_custom_command_uses_disabled_precompile_matrix(self) -> None:
        feature_sets = [{"name": "default", "features": "", "no_default_features": False}]
        benchmarks = [{"name": "custom", "command": "./bench.sh", "backend": "criterion"}]

        benchmark_matrix = expand_matrix.make_matrix(benchmarks, feature_sets, "", "--noplot")
        build_matrix = expand_matrix.make_build_matrix(benchmark_matrix)

        self.assertFalse(benchmark_matrix["include"][0]["precompile"])
        self.assertFalse(build_matrix["include"][0]["enabled"])

    def test_build_matrix_shares_application_build_across_backends(self) -> None:
        feature_sets = [{"name": "default", "features": "", "no_default_features": False}]
        benchmarks = [
            {"name": "callgrind", "bench": "callgrind", "backend": "gungraun"},
            {"name": "criterion", "bench": "criterion", "backend": "criterion"},
        ]

        benchmark_matrix = expand_matrix.make_matrix(benchmarks, feature_sets, "", "--noplot")
        build_matrix = expand_matrix.make_build_matrix(benchmark_matrix)

        self.assertEqual(len(build_matrix["include"]), 2)
        self.assertEqual([len(item["cases"]) for item in build_matrix["include"]], [2, 2])

    def test_pair_moved_benchmarks_adds_base_spec_for_unique_move(self) -> None:
        head = [
            {
                "name": "crates/new/parser_callgrind",
                "bench": "parser_callgrind",
                "backend": "gungraun",
                "repo_crate": "crates/new",
                "package_name": "parser",
            }
        ]
        base = [
            {
                "name": "crates/old/parser_callgrind",
                "bench": "parser_callgrind",
                "backend": "gungraun",
                "repo_crate": "crates/old",
                "package_name": "parser",
                "manifest_path": "crates/old/Cargo.toml",
            }
        ]

        paired = expand_matrix.pair_moved_benchmarks(head, base)

        self.assertTrue(paired[0]["moved"])
        self.assertEqual(paired[0]["base"]["manifest_path"], "crates/old/Cargo.toml")
        self.assertEqual(paired[0]["move_source"], "crates/old")
        self.assertEqual(paired[0]["move_target"], "crates/new")

    def test_pair_moved_benchmarks_requires_source_to_be_absent_from_head(self) -> None:
        head = [
            {"bench": "parser_callgrind", "backend": "gungraun", "repo_crate": "crates/old"},
            {"bench": "parser_callgrind", "backend": "gungraun", "repo_crate": "crates/new"},
        ]
        base = [
            {"bench": "parser_callgrind", "backend": "gungraun", "repo_crate": "crates/old"}
        ]

        paired = expand_matrix.pair_moved_benchmarks(head, base)

        self.assertNotIn("moved", paired[1])
        self.assertNotIn("base", paired[1])

    def test_pair_moved_benchmarks_uses_a_base_source_only_once(self) -> None:
        head = [
            {"bench": "parser_callgrind", "backend": "gungraun", "repo_crate": "crates/new-a"},
            {"bench": "parser_callgrind", "backend": "gungraun", "repo_crate": "crates/new-b"},
        ]
        base = [
            {"bench": "parser_callgrind", "backend": "gungraun", "repo_crate": "crates/old"}
        ]

        paired = expand_matrix.pair_moved_benchmarks(head, base)

        self.assertTrue(paired[0]["moved"])
        self.assertNotIn("moved", paired[1])
        self.assertNotIn("base", paired[1])

    def test_make_matrix_emits_distinct_base_command_for_move(self) -> None:
        feature_sets = [{"name": "default", "features": "", "no_default_features": False}]
        benchmarks = [
            {
                "name": "crates/new/parser_callgrind",
                "bench": "parser_callgrind",
                "backend": "gungraun",
                "manifest_path": "crates/new/Cargo.toml",
                "base": {
                    "bench": "parser_callgrind",
                    "manifest_path": "crates/old/Cargo.toml",
                },
                "moved": True,
                "move_source": "crates/old",
                "move_target": "crates/new",
            }
        ]

        matrix = expand_matrix.make_matrix(benchmarks, feature_sets, "", "--noplot")
        item = matrix["include"][0]

        self.assertEqual(
            item["head_command"],
            "cargo bench --bench parser_callgrind --manifest-path crates/new/Cargo.toml",
        )
        self.assertEqual(
            item["base_command"],
            "cargo bench --bench parser_callgrind --manifest-path crates/old/Cargo.toml",
        )
        self.assertTrue(item["moved"])

    def test_make_matrix_does_not_inherit_head_manifest_for_move_from_root(self) -> None:
        feature_sets = [{"name": "default", "features": "", "no_default_features": False}]
        benchmarks = [
            {
                "name": "crates/parser/parser_callgrind",
                "bench": "parser_callgrind",
                "backend": "gungraun",
                "manifest_path": "crates/parser/Cargo.toml",
                "base": {
                    "name": "parser_callgrind",
                    "bench": "parser_callgrind",
                    "backend": "gungraun",
                    "repo_crate": ".",
                },
                "moved": True,
                "move_source": ".",
                "move_target": "crates/parser",
            }
        ]

        matrix = expand_matrix.make_matrix(benchmarks, feature_sets, "", "--noplot")
        item = matrix["include"][0]

        self.assertEqual(
            item["head_command"],
            "cargo bench --bench parser_callgrind --manifest-path crates/parser/Cargo.toml",
        )
        self.assertEqual(item["base_command"], "cargo bench --bench parser_callgrind")

    def test_cli_auto_detects_moved_workspace_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            (repo / "Cargo.toml").write_text(
                '[workspace]\nmembers = ["crates/*"]\n',
                encoding="utf-8",
            )
            old_crate = repo / "crates" / "old"
            (old_crate / "benches").mkdir(parents=True)
            (old_crate / "Cargo.toml").write_text(
                '[package]\nname = "parser"\nversion = "0.1.0"\nedition = "2021"\n',
                encoding="utf-8",
            )
            (old_crate / "benches" / "parser_callgrind.rs").write_text("// old\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "-c", "commit.gpgsign=false", "commit", "-m", "base"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            base_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

            new_crate = repo / "crates" / "new"
            (new_crate / "benches").mkdir(parents=True)
            (new_crate / "Cargo.toml").write_text(
                '[package]\nname = "parser"\nversion = "0.1.0"\nedition = "2021"\n',
                encoding="utf-8",
            )
            (new_crate / "benches" / "parser_callgrind.rs").write_text("// new\n", encoding="utf-8")
            shutil.rmtree(old_crate)
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "-c", "commit.gpgsign=false", "commit", "-m", "head"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            head_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            output = repo / "matrix.json"

            subprocess.run(
                [
                    "python3",
                    str(REPO_ROOT / "scripts" / "expand_matrix.py"),
                    "--repo-path",
                    str(repo),
                    "--working-directory",
                    ".",
                    "--benchmarks-json",
                    "[]",
                    "--feature-sets-json",
                    '[{"name":"default","features":""}]',
                    "--backend",
                    "gungraun",
                    "--auto-discover",
                    "--auto-detect-moved-benchmarks",
                    "--head-sha",
                    head_sha,
                    "--base-sha",
                    base_sha,
                    "--output",
                    str(output),
                ],
                check=True,
                cwd=REPO_ROOT,
            )

            item = json.loads(output.read_text(encoding="utf-8"))["include"][0]
            self.assertTrue(item["moved"])
            self.assertIn("--manifest-path crates/new/Cargo.toml", item["head_command"])
            self.assertIn("--manifest-path crates/old/Cargo.toml", item["base_command"])

    def test_command_placeholders_expand_feature_flags(self) -> None:
        spec = {"command": "cargo bench {features} {no_default_features_flag}"}
        feature_set = {"name": "feat", "features": "simd", "no_default_features": True}

        command = expand_matrix.build_command(spec, feature_set, "", "gungraun", "--noplot")

        self.assertEqual(command, "cargo bench simd --no-default-features")

    def test_cli_accepts_dash_prefixed_argument_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "matrix.json"
            subprocess.run(
                [
                    "python3",
                    str(REPO_ROOT / "scripts" / "expand_matrix.py"),
                    "--repo-path",
                    str(REPO_ROOT),
                    "--working-directory",
                    "examples/sample-rust-app",
                    "--benchmarks-json",
                    '["sample_callgrind_bench"]',
                    "--feature-sets-json",
                    '[{"name":"default","features":""}]',
                    "--backend",
                    "criterion",
                    "--criterion-cli-args",
                    "--noplot --sample-size 30",
                    "--cargo-args",
                    "--release",
                    "--output",
                    str(output),
                ],
                check=True,
                cwd=REPO_ROOT,
            )

            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["include"]), 1)
            self.assertEqual(
                payload["include"][0]["command"],
                "cargo bench --bench sample_callgrind_bench --release -- --noplot --sample-size 30",
            )


if __name__ == "__main__":
    unittest.main()
