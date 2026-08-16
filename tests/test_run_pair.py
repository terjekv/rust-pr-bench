import pathlib
import tempfile
import unittest

from testlib import load_script_module


run_pair = load_script_module("run_pair", "scripts/run_pair.py")


class RunPairTests(unittest.TestCase):
    def test_callgrind_metric_names_pair_old_and_new_output_roots(self) -> None:
        old = pathlib.Path("iai/sample/bench/callgrind.out.123")
        new = pathlib.Path("gungraun/sample/bench/callgrind.out.987")

        self.assertEqual(
            run_pair.normalize_callgrind_metric_name(old),
            run_pair.normalize_callgrind_metric_name(new),
        )
        self.assertEqual(
            run_pair.normalize_callgrind_metric_name(new),
            "gungraun/sample/bench/callgrind.out",
        )

    def test_collects_old_and_new_path_fixtures_under_same_metric_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp)
            old = target / "iai" / "sample" / "callgrind.out.11"
            new = target / "gungraun" / "sample" / "callgrind.out.22"
            old.parent.mkdir(parents=True)
            new.parent.mkdir(parents=True)
            old.write_text("summary: 100\n", encoding="utf-8")
            new.write_text("summary: 110\n", encoding="utf-8")

            old_name = run_pair.normalize_callgrind_metric_name(
                old.relative_to(target)
            )
            new_name = run_pair.normalize_callgrind_metric_name(
                new.relative_to(target)
            )
            self.assertEqual(old_name, new_name)

    def test_detects_both_runner_family_version_mismatches(self) -> None:
        self.assertEqual(
            run_pair.runner_version_mismatch_family(
                "iai-callgrind-runner (0.16.0) is older than iai-callgrind (0.16.1)"
            ),
            "iai-callgrind",
        )
        self.assertEqual(
            run_pair.runner_version_mismatch_family(
                "gungraun-runner (0.19.3) is older than gungraun (0.19.4)"
            ),
            "gungraun",
        )


if __name__ == "__main__":
    unittest.main()
