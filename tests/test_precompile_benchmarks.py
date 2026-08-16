import json
import pathlib
import tempfile
import unittest

from testlib import load_script_module


precompile_benchmarks = load_script_module(
    "precompile_benchmarks", "scripts/precompile_benchmarks.py"
)


class PrecompileBenchmarksTests(unittest.TestCase):
    def test_find_benchmark_executable_prefers_named_bench_target(self) -> None:
        messages = [
            {
                "reason": "compiler-artifact",
                "target": {"name": "other", "kind": ["bench"]},
                "executable": "/tmp/other",
            },
            {
                "reason": "compiler-artifact",
                "target": {"name": "wanted", "kind": ["bench"]},
                "executable": "/tmp/wanted",
            },
        ]
        output = "\n".join(json.dumps(message) for message in messages)

        executable = precompile_benchmarks.find_benchmark_executable(output, "wanted")

        self.assertEqual(executable, pathlib.Path("/tmp/wanted"))

    def test_find_benchmark_executable_ignores_non_bench_artifacts(self) -> None:
        output = json.dumps(
            {
                "reason": "compiler-artifact",
                "target": {"name": "wanted", "kind": ["lib"]},
                "executable": "/tmp/library",
            }
        )

        self.assertIsNone(precompile_benchmarks.find_benchmark_executable(output, "wanted"))

    def test_copy_runtime_artifacts_preserves_target_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            target = root / "target"
            binary = target / "release" / "application"
            library = target / "release" / "deps" / "libdynamic.so"
            binary.parent.mkdir(parents=True)
            library.parent.mkdir(parents=True)
            binary.write_text("binary", encoding="utf-8")
            library.write_text("library", encoding="utf-8")
            output = json.dumps(
                {
                    "reason": "compiler-artifact",
                    "target": {"name": "application", "kind": ["bin"]},
                    "executable": str(binary),
                    "filenames": [str(binary), str(library)],
                }
            )
            runtime = root / "runtime"

            precompile_benchmarks.copy_runtime_artifacts(output, target, runtime)

            self.assertEqual(
                (runtime / "release" / "application").read_text(encoding="utf-8"), "binary"
            )
            self.assertEqual(
                (runtime / "release" / "deps" / "libdynamic.so").read_text(encoding="utf-8"),
                "library",
            )

    def test_native_target_cpu_is_detected_in_commands_and_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            workdir = repo / "crate"
            workdir.mkdir()

            self.assertTrue(
                precompile_benchmarks.native_target_cpu_requested(
                    [{"compile_command": "cargo bench --config target-cpu=native"}],
                    workdir,
                    repo,
                    {},
                )
            )
            self.assertTrue(
                precompile_benchmarks.native_target_cpu_requested(
                    [], workdir, repo, {"RUSTFLAGS": "-C target-cpu=native"}
                )
            )

    def test_native_target_cpu_is_detected_in_cargo_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            workdir = repo / "crate"
            cargo_config = repo / ".cargo" / "config.toml"
            workdir.mkdir()
            cargo_config.parent.mkdir()
            cargo_config.write_text(
                '[build]\nrustflags = ["-C", "target-cpu=native"]\n', encoding="utf-8"
            )

            self.assertTrue(
                precompile_benchmarks.native_target_cpu_requested([], workdir, repo, {})
            )

    def test_portable_build_does_not_disable_precompilation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            workdir = repo / "crate"
            workdir.mkdir()

            self.assertFalse(
                precompile_benchmarks.native_target_cpu_requested(
                    [{"compile_command": "cargo bench --bench portable"}],
                    workdir,
                    repo,
                    {"RUSTFLAGS": "-C opt-level=3"},
                )
            )


if __name__ == "__main__":
    unittest.main()
