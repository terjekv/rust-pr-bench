import json
import pathlib
import tempfile
import unittest
from unittest import mock

from testlib import load_script_module


precompile_benchmarks = load_script_module(
    "precompile_benchmarks", "scripts/precompile_benchmarks.py"
)


class PrecompileBenchmarksTests(unittest.TestCase):
    def test_base_only_writes_a_distinct_build_identity(self) -> None:
        for peer, expected in (
            (("compiler", "deps"), False),
            (("compiler", "other"), True),
        ):
            with self.subTest(peer=peer), tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp)
                precompile_benchmarks.LOCAL_DOWNLOADS.clear()
                precompile_benchmarks.LOCAL_BUILDS.clear()
                with (
                    mock.patch.object(
                        precompile_benchmarks, "git_checkout"
                    ) as checkout,
                    mock.patch.object(
                        precompile_benchmarks,
                        "native_target_cpu_requested",
                        return_value=False,
                    ),
                    mock.patch.object(
                        precompile_benchmarks,
                        "build_identity",
                        return_value=("compiler", "deps"),
                    ),
                    mock.patch.object(
                        precompile_benchmarks,
                        "precompile_case",
                        return_value={"precompiled": True},
                    ),
                    mock.patch.object(
                        precompile_benchmarks.Cache, "restore", autospec=True
                    ),
                    mock.patch.object(
                        precompile_benchmarks.Cache, "save", autospec=True
                    ) as save,
                ):
                    precompile_benchmarks.compile_group(
                        root,
                        root,
                        "base",
                        [{"id": "case", "benchmark_name": "bench"}],
                        root / "output",
                        side="base",
                        peer_identity=peer,
                        target_dir=root / "target",
                    )
                    self.assertEqual(save.call_args_list[0].args[0].writer, expected)
                    checkout.assert_called_once_with(root, "base")

    def test_native_builds_never_restore_portable_target_caches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            with (
                mock.patch.object(precompile_benchmarks, "git_checkout"),
                mock.patch.object(
                    precompile_benchmarks,
                    "native_target_cpu_requested",
                    return_value=True,
                ),
                mock.patch.object(precompile_benchmarks, "build_identity") as identity,
                mock.patch.object(precompile_benchmarks.Cache, "restore") as restore,
            ):
                result = precompile_benchmarks.compile_group(
                    root,
                    root,
                    "head",
                    [{"id": "case", "benchmark_name": "bench"}],
                    root / "output",
                )
                self.assertEqual(result[0]["reason"], "target-cpu=native")
                restore.assert_not_called()
                identity.assert_not_called()

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

        self.assertIsNone(
            precompile_benchmarks.find_benchmark_executable(output, "wanted")
        )

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
                (runtime / "release" / "application").read_text(encoding="utf-8"),
                "binary",
            )
            self.assertEqual(
                (runtime / "release" / "deps" / "libdynamic.so").read_text(
                    encoding="utf-8"
                ),
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
