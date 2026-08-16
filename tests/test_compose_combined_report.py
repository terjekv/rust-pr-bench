import pathlib
import subprocess
import tempfile
import unittest

from test_render_report import callgrind_results, criterion_results
from testlib import REPO_ROOT, load_script_module, read_snapshot


render_report = load_script_module("render_report_for_compose", "scripts/render_report.py")


class ComposeCombinedReportTests(unittest.TestCase):
    maxDiff = None

    def write_single_reports(self, root: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
        callgrind_markdown, _ = render_report.render_markdown(
            callgrind_results(),
            3.0,
            "gungraun",
            12,
            "deadbeefcafebabe",
            "2026-02-27 20:30 UTC",
            [],
            10,
            "gungraun-history",
            None,
            None,
            None,
            True,
        )
        criterion_markdown, _ = render_report.render_markdown(
            criterion_results(),
            10.0,
            "criterion",
            12,
            "deadbeefcafebabe",
            "2026-02-27 20:30 UTC",
            [],
            10,
            "criterion-history",
            None,
            None,
            None,
            True,
        )
        callgrind_path = root / "gungraun.md"
        criterion_path = root / "criterion.md"
        callgrind_path.write_text(callgrind_markdown, encoding="utf-8")
        criterion_path.write_text(criterion_markdown, encoding="utf-8")
        return callgrind_path, criterion_path

    def test_combined_markdown_matches_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            callgrind_path, criterion_path = self.write_single_reports(root)
            output = root / "combined.md"

            subprocess.run(
                [
                    "python3",
                    str(REPO_ROOT / "scripts" / "compose_combined_report.py"),
                    "--output",
                    str(output),
                    "--report-gungraun",
                    str(callgrind_path),
                    "--report-criterion",
                    str(criterion_path),
                    "--pr-number",
                    "12",
                    "--run-at",
                    "2026-02-27 20:30 UTC",
                    "--head-sha",
                    "deadbeefcafebabe",
                ],
                check=True,
                cwd=REPO_ROOT,
            )

            self.assertEqual(output.read_text(encoding="utf-8"), read_snapshot("compose_combined.md"))

    def test_missing_one_backend_still_renders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            callgrind_path, _ = self.write_single_reports(root)
            output = root / "combined.md"

            subprocess.run(
                [
                    "python3",
                    str(REPO_ROOT / "scripts" / "compose_combined_report.py"),
                    "--output",
                    str(output),
                    "--report-gungraun",
                    str(callgrind_path),
                ],
                check=True,
                cwd=REPO_ROOT,
            )

            markdown = output.read_text(encoding="utf-8")
            self.assertIn("## Gungraun", markdown)
            self.assertNotIn("## Criterion", markdown)

    def test_no_backend_reports_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "combined.md"

            proc = subprocess.run(
                [
                    "python3",
                    str(REPO_ROOT / "scripts" / "compose_combined_report.py"),
                    "--output",
                    str(output),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("No backend reports were generated.", proc.stderr)


if __name__ == "__main__":
    unittest.main()
