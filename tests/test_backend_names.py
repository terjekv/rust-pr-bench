import subprocess
import unittest

from testlib import REPO_ROOT, load_script_module


backend_names = load_script_module("backend_names", "scripts/backend_names.py")


class BackendNamesTests(unittest.TestCase):
    def test_canonical_backend_names(self) -> None:
        self.assertEqual(backend_names.normalize_backend("gungraun"), "gungraun")
        self.assertEqual(backend_names.normalize_backend("criterion"), "criterion")
        self.assertEqual(backend_names.normalize_backend_selection("all"), "all")

    def test_selection_cli_emits_canonical_gungraun(self) -> None:
        completed = subprocess.run(
            [
                "python3",
                str(REPO_ROOT / "scripts" / "backend_names.py"),
                "--selection",
                "gungraun",
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        self.assertEqual(completed.stdout.strip(), "gungraun")

    def test_legacy_and_unknown_backends_are_rejected(self) -> None:
        for backend in ("iai-callgrind", "iai", "callgrind", "perf", "any"):
            with self.subTest(backend=backend):
                with self.assertRaisesRegex(ValueError, "gungraun.*criterion"):
                    backend_names.normalize_backend_selection(backend)


if __name__ == "__main__":
    unittest.main()
