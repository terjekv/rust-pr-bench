import json
import pathlib
import subprocess
import tempfile
import unittest

from testlib import REPO_ROOT


PR_BODY = """Security hardening changes the expected cost.

```rust-pr-bench
{
  "accept_regressions": [
    {
      "benchmark": "verify_password",
      "backend": "gungraun",
      "feature": "default",
      "max_regression_pct": 25,
      "reason": "Constant-time password verification"
    }
  ]
}
```
"""


class RegressionOverrideIntegrationTests(unittest.TestCase):
    def run_report(
        self,
        labels: list[str],
        *,
        body_last_edited_at: str = "2026-07-22T10:00:00Z",
        approval_label_applied_at: str = "2026-07-22T10:01:00Z",
    ) -> tuple[str, dict]:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            artifacts = root / "artifacts" / "case"
            artifacts.mkdir(parents=True)
            (artifacts / "result.json").write_text(
                json.dumps(
                    {
                        "backend": "gungraun",
                        "benchmark_name": "verify_password",
                        "feature_name": "default",
                        "base_total": 1000,
                        "head_total": 1200,
                        "delta_pct": 20.0,
                        "base_metrics": [{"metric": "instructions", "value": 1000}],
                        "head_metrics": [{"metric": "instructions", "value": 1200}],
                        "base_missing": False,
                        "head_missing": False,
                        "comparison_statistic": "summary",
                        "metric_unit": "events",
                    }
                ),
                encoding="utf-8",
            )
            metadata = root / "pr.json"
            metadata.write_text(
                json.dumps(
                    {
                        "body": PR_BODY,
                        "labels": labels,
                        "body_last_edited_at": body_last_edited_at,
                        "approval_label_applied_at": approval_label_applied_at,
                    }
                ),
                encoding="utf-8",
            )
            overrides = root / "overrides.json"
            report = root / "report.md"
            summary = root / "summary.json"

            subprocess.run(
                [
                    "python3",
                    str(REPO_ROOT / "scripts" / "regression_overrides.py"),
                    "--pr-metadata",
                    str(metadata),
                    "--approval-label",
                    "performance-approved",
                    "--output",
                    str(overrides),
                ],
                cwd=REPO_ROOT,
                check=True,
            )
            subprocess.run(
                [
                    "python3",
                    str(REPO_ROOT / "scripts" / "render_report.py"),
                    "--artifacts-dir",
                    str(root / "artifacts"),
                    "--threshold",
                    "3",
                    "--backend",
                    "gungraun",
                    "--markdown-output",
                    str(report),
                    "--summary-output",
                    str(summary),
                    "--head-sha",
                    "deadbeef",
                    "--regression-overrides-input",
                    str(overrides),
                ],
                cwd=REPO_ROOT,
                check=True,
            )

            return (
                report.read_text(encoding="utf-8"),
                json.loads(summary.read_text(encoding="utf-8")),
            )

    def test_approved_pr_body_exception_flows_through_user_facing_cli(self) -> None:
        markdown, summary = self.run_report(["performance-approved"])

        self.assertTrue(summary["has_regressions"])
        self.assertFalse(summary["has_unaccepted_regressions"])
        self.assertEqual(summary["accepted_regressions"], 1)
        self.assertIn("🟠 accepted regression", markdown)
        self.assertIn("Constant-time password verification", markdown)

    def test_missing_approval_label_keeps_the_same_regression_actionable(self) -> None:
        markdown, summary = self.run_report([])

        self.assertTrue(summary["has_regressions"])
        self.assertTrue(summary["has_unaccepted_regressions"])
        self.assertEqual(summary["accepted_regressions"], 0)
        self.assertIn("Awaiting Approval", markdown)
        self.assertIn("🔴 regression", markdown)

    def test_label_applied_before_body_edit_keeps_regression_actionable(self) -> None:
        markdown, summary = self.run_report(
            ["performance-approved"],
            body_last_edited_at="2026-07-22T10:02:00Z",
            approval_label_applied_at="2026-07-22T10:01:00Z",
        )

        self.assertTrue(summary["has_unaccepted_regressions"])
        self.assertEqual(summary["accepted_regressions"], 0)
        self.assertIn("Awaiting Approval", markdown)

    def test_malformed_user_directive_fails_with_an_actionable_cli_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            metadata = root / "pr.json"
            metadata.write_text(
                json.dumps(
                    {
                        "body": "```rust-pr-bench\n{not json}\n```",
                        "labels": ["performance-approved"],
                    }
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    "python3",
                    str(REPO_ROOT / "scripts" / "regression_overrides.py"),
                    "--pr-metadata",
                    str(metadata),
                    "--approval-label",
                    "performance-approved",
                    "--output",
                    str(root / "overrides.json"),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("invalid rust-pr-bench JSON", proc.stderr)


if __name__ == "__main__":
    unittest.main()
