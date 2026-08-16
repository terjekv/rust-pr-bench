import subprocess
import unittest

from testlib import REPO_ROOT, load_script_module


resolve_threshold = load_script_module(
    "resolve_threshold", "scripts/resolve_threshold.py"
)


class ResolveThresholdTests(unittest.TestCase):
    def test_specific_threshold_overrides_generic(self) -> None:
        resolve = resolve_threshold.resolve_gungraun_threshold
        self.assertEqual(resolve(3), 3)
        self.assertEqual(resolve(3, gungraun_specific=5), 5)

    def test_cli_prints_resolved_threshold(self) -> None:
        completed = subprocess.run(
            [
                "python3",
                str(REPO_ROOT / "scripts" / "resolve_threshold.py"),
                "--generic=3",
                "--gungraun=4",
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        self.assertEqual(completed.stdout.strip(), "4")

    def test_generic_threshold_cannot_use_specific_input_sentinel(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "regression_threshold_pct must be non-negative",
        ):
            resolve_threshold.resolve_gungraun_threshold(-1)


if __name__ == "__main__":
    unittest.main()
