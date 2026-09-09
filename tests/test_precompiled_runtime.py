import json
import pathlib
import tempfile
import unittest
from unittest import mock

from testlib import load_script_module

runtime = load_script_module("precompiled_runtime", "scripts/precompiled_runtime.py")


class PrecompiledRuntimeTests(unittest.TestCase):
    def test_runtime_is_replaced_for_each_side_and_binary_path_is_absolute(self):
        with tempfile.TemporaryDirectory(dir=pathlib.Path.cwd()) as temporary:
            root = pathlib.Path(temporary)
            target = root / "target"
            for side in ("head", "base"):
                group = root / side
                case = group / "case"
                case.mkdir(parents=True)
                (case / "benchmark").write_text("benchmark")
                (group / "build.json").write_text(
                    json.dumps({"target_dir": str(target)})
                )
                library = group / "_runtime/custom-profile/helper"
                library.parent.mkdir(parents=True)
                library.write_text(side)
                with mock.patch.object(
                    runtime.subprocess,
                    "check_output",
                    side_effect=["host: target-triple\n", "/sysroot"],
                ):
                    command = runtime.command_for(
                        case.relative_to(pathlib.Path.cwd()), "--bench", "cargo bench"
                    )
                self.assertIn(str(case / "benchmark"), command)
                self.assertEqual((target / "custom-profile/helper").read_text(), side)

    def test_incomplete_download_falls_back_to_cargo(self):
        with tempfile.TemporaryDirectory() as temporary:
            case = pathlib.Path(temporary) / "case"
            case.mkdir()
            (case / "benchmark").write_text("benchmark")
            self.assertEqual(
                runtime.command_for(case, "--bench", "cargo bench"), "cargo bench"
            )
