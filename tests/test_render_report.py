import pathlib
import tempfile
import unittest

from testlib import load_script_module, read_snapshot


render_report = load_script_module("render_report", "scripts/render_report.py")


def callgrind_results() -> list[dict]:
    return [
        {
            "backend": "gungraun",
            "benchmark_name": "fast_path",
            "feature_name": "default",
            "base_total": 1000,
            "head_total": 1100,
            "delta_pct": 10.0,
            "base_metrics": [{"metric": "bench/default/callgrind.out", "value": 1000}],
            "head_metrics": [{"metric": "bench/default/callgrind.out", "value": 1100}],
            "base_missing": False,
            "head_missing": False,
            "comparison_statistic": "summary",
            "metric_unit": "events",
        },
        {
            "backend": "gungraun",
            "benchmark_name": "alt_path",
            "feature_name": "alt-impl",
            "base_total": 2000,
            "head_total": 1800,
            "delta_pct": -10.0,
            "base_metrics": [{"metric": "bench/alt/callgrind.out", "value": 2000}],
            "head_metrics": [{"metric": "bench/alt/callgrind.out", "value": 1800}],
            "base_missing": False,
            "head_missing": False,
            "comparison_statistic": "summary",
            "metric_unit": "events",
        },
    ]


def criterion_results() -> list[dict]:
    return [
        {
            "backend": "criterion",
            "benchmark_name": "criterion_fast",
            "feature_name": "default",
            "base_total": 1000.0,
            "head_total": 1050.0,
            "delta_pct": 5.0,
            "base_metrics": [{"metric": "workload/small", "value": 1000.0}],
            "head_metrics": [{"metric": "workload/small", "value": 1050.0}],
            "base_missing": False,
            "head_missing": False,
            "comparison_statistic": "median",
            "metric_unit": "ns",
        },
        {
            "backend": "criterion",
            "benchmark_name": "criterion_slow",
            "feature_name": "default",
            "base_total": 2000.0,
            "head_total": 1800.0,
            "delta_pct": -10.0,
            "base_metrics": [{"metric": "workload/medium", "value": 2000.0}],
            "head_metrics": [{"metric": "workload/medium", "value": 1800.0}],
            "base_missing": False,
            "head_missing": False,
            "comparison_statistic": "median",
            "metric_unit": "ns",
        },
    ]


class RenderReportTests(unittest.TestCase):
    maxDiff = None

    def test_callgrind_markdown_matches_snapshot(self) -> None:
        markdown, summary = render_report.render_markdown(
            callgrind_results(),
            3.0,
            "gungraun",
            12,
            "deadbeefcafebabe",
            "2026-02-27 20:30 UTC",
            [
                {
                    "backend": "gungraun",
                    "commit": "abc1234",
                    "run_at": "2026-02-26 19:00 UTC",
                    "summary": {"improved": 1, "regressions": 0, "neutral": 0},
                    "avg_bench_delta_pct": -5.0,
                    "avg_metric_delta_pct": -5.0,
                    "has_regressions": False,
                }
            ],
            10,
            "gungraun-history",
            None,
            None,
            None,
            False,
        )

        self.assertEqual(markdown, read_snapshot("render_gungraun.md"))
        self.assertTrue(summary["has_regressions"])
        self.assertEqual(summary["latest"]["commit"], "deadbeefcafebabe")

    def test_criterion_markdown_matches_snapshot(self) -> None:
        markdown, summary = render_report.render_markdown(
            criterion_results(),
            10.0,
            "criterion",
            12,
            "feedfacecafebeef",
            "2026-02-27 20:31 UTC",
            [
                {
                    "backend": "criterion",
                    "commit": "abc1234",
                    "run_at": "2026-02-26 19:00 UTC",
                    "summary": {"improved": 0, "regressions": 0, "neutral": 2},
                    "avg_bench_delta_pct": 0.5,
                    "avg_metric_delta_pct": 0.5,
                    "has_regressions": False,
                }
            ],
            10,
            "criterion-history",
            None,
            None,
            None,
            False,
        )

        self.assertEqual(markdown, read_snapshot("render_criterion.md"))
        self.assertFalse(summary["has_regressions"])
        self.assertEqual(summary["latest"]["commit"], "feedfacecafebeef")

    def test_no_results_preserves_history_marker_and_matches_snapshot(self) -> None:
        markdown, summary = render_report.render_markdown(
            [],
            3.0,
            "gungraun",
            7,
            "deadbeef",
            "2026-02-27 20:22 UTC",
            [
                {
                    "backend": "gungraun",
                    "commit": "abc1234",
                    "run_at": "2026-02-27 20:00 UTC",
                    "summary": {"improved": 1, "regressions": 0, "neutral": 2},
                    "avg_bench_delta_pct": -1.2,
                    "avg_metric_delta_pct": -0.4,
                    "has_regressions": False,
                }
            ],
            10,
            "gungraun-history",
            None,
            None,
            None,
            False,
        )

        self.assertEqual(markdown, read_snapshot("render_no_results.md"))
        self.assertIn("<!-- gungraun-history:", markdown)
        self.assertEqual(summary["history"][0]["backend"], "gungraun")
        self.assertEqual(summary["history"][0]["commit"], "abc1234")

    def test_missing_entries_are_listed(self) -> None:
        results = [
            {
                "backend": "criterion",
                "benchmark_name": "missing_case",
                "feature_name": "default",
                "base_total": 0,
                "head_total": 0,
                "delta_pct": float("nan"),
                "base_metrics": [],
                "head_metrics": [],
                "base_missing": True,
                "head_missing": False,
                "comparison_statistic": "mean",
                "metric_unit": "ns",
            }
        ]

        markdown, _ = render_report.render_markdown(
            results,
            10.0,
            "criterion",
            None,
            None,
            None,
            [],
            10,
            "criterion-history",
            None,
            None,
            None,
            False,
        )

        self.assertIn("### Skipped Benchmarks (Missing in Base/Head)", markdown)
        self.assertIn("missing in base", markdown)

    def test_unpaired_metrics_are_unknown_and_excluded_from_metric_average(self) -> None:
        entry = {
            "benchmark_name": "expanded_target",
            "base_metrics": [
                {"metric": "existing", "value": 100},
                {"metric": "removed", "value": 50},
            ],
            "head_metrics": [
                {"metric": "existing", "value": 110},
                {"metric": "added", "value": 500},
            ],
        }

        self.assertEqual(render_report.collect_metric_deltas(entry), [10.0])
        breakdown = "\n".join(render_report.render_metric_breakdown(entry, 3.0))
        self.assertIn(">added</span> | 0 | 500 | n/a | ⚪ unknown |", breakdown)
        self.assertIn(">removed</span> | 50 | 0 | n/a | ⚪ unknown |", breakdown)

    def test_moved_and_error_entries_are_listed(self) -> None:
        results = [
            {
                "backend": "gungraun",
                "benchmark_name": "crates/new/parser_callgrind",
                "feature_name": "default",
                "base_total": 0,
                "head_total": 0,
                "delta_pct": float("nan"),
                "base_metrics": [],
                "head_metrics": [],
                "base_missing": False,
                "head_missing": False,
                "base_error": True,
                "head_error": False,
                "base_error_code": 101,
                "base_error_stage": "setup",
                "moved": True,
                "move_source": "crates/old",
                "move_target": "crates/new",
                "comparison_statistic": "summary",
                "metric_unit": "events",
            }
        ]

        markdown, _ = render_report.render_markdown(
            results,
            3.0,
            "gungraun",
            None,
            None,
            None,
            [],
            10,
            "gungraun-history",
            None,
            None,
            None,
            False,
        )

        self.assertIn("### Moved Benchmarks", markdown)
        self.assertIn("`crates/old` -> `crates/new`", markdown)
        self.assertIn("### Benchmark Errors", markdown)
        self.assertIn("base setup exit 101", markdown)

    def test_failed_entries_are_excluded_from_summary_and_history_aggregates(self) -> None:
        failed = {
            "backend": "gungraun",
            "benchmark_name": "failed",
            "feature_name": "default",
            "base_total": 100,
            "head_total": 0,
            "delta_pct": -100.0,
            "base_metrics": [{"metric": "instructions", "value": 100}],
            "head_metrics": [],
            "base_missing": False,
            "head_missing": False,
            "head_error": True,
            "comparison_statistic": "summary",
            "metric_unit": "events",
        }
        successful = dict(callgrind_results()[0])
        successful["feature_name"] = "default"

        _, summary = render_report.render_markdown(
            [failed, successful], 3.0, "gungraun", None, "head", None, [], 10,
            "gungraun-history", None, None, None, False,
        )

        latest = summary["latest"]
        self.assertEqual(
            latest["summary"],
            {"improved": 0, "regressions": 1, "accepted_regressions": 0, "neutral": 0},
        )
        self.assertEqual(latest["avg_bench_delta_pct"], 10.0)
        self.assertEqual(latest["avg_metric_delta_pct"], 10.0)

    def test_approved_regression_is_visible_but_not_actionable(self) -> None:
        overrides = {
            "enabled": True,
            "approved": True,
            "approval_label": "performance-approved",
            "rules": [
                {
                    "benchmark": "fast_path",
                    "backend": "gungraun",
                    "feature": "default",
                    "max_regression_pct": 15.0,
                    "reason": "Constant-time security fix",
                }
            ],
        }

        markdown, summary = render_report.render_markdown(
            callgrind_results(), 3.0, "gungraun", None, "head", None, [], 10,
            "gungraun-history", None, None, None, False, overrides,
        )

        self.assertTrue(summary["has_regressions"])
        self.assertFalse(summary["has_unaccepted_regressions"])
        self.assertEqual(summary["accepted_regressions"], 1)
        self.assertEqual(summary["latest"]["summary"]["accepted_regressions"], 1)
        self.assertIn("🟠 accepted regression", markdown)
        self.assertIn("### Accepted Regressions", markdown)
        self.assertIn("Constant-time security fix", markdown)
        self.assertNotIn("### Unaccepted Regressions", markdown)

    def test_approved_rule_over_its_limit_remains_actionable(self) -> None:
        overrides = {
            "enabled": True,
            "approved": True,
            "approval_label": "performance-approved",
            "rules": [
                {
                    "benchmark": "fast_path",
                    "max_regression_pct": 5.0,
                    "reason": "Expected small security cost",
                }
            ],
        }

        markdown, summary = render_report.render_markdown(
            callgrind_results(), 3.0, "gungraun", None, None, None, [], 10,
            "gungraun-history", None, None, None, False, overrides,
        )

        self.assertTrue(summary["has_regressions"])
        self.assertTrue(summary["has_unaccepted_regressions"])
        self.assertEqual(summary["accepted_regressions"], 0)
        self.assertIn("requested limit +5.00% exceeded", markdown)

    def test_unapproved_and_unused_rules_are_reported(self) -> None:
        rule = {
            "benchmark": "does_not_exist",
            "max_regression_pct": "any",
            "reason": "Future security work\nwith context",
        }
        unapproved = {
            "enabled": True,
            "approved": False,
            "approval_label": "performance-approved",
            "rules": [rule],
        }
        approved = {**unapproved, "approved": True}

        unapproved_markdown, unapproved_summary = render_report.render_markdown(
            callgrind_results(), 3.0, "gungraun", None, None, None, [], 10,
            "gungraun-history", None, None, None, False, unapproved,
        )
        approved_markdown, _ = render_report.render_markdown(
            callgrind_results(), 3.0, "gungraun", None, None, None, [], 10,
            "gungraun-history", None, None, None, False, approved,
        )

        self.assertTrue(unapproved_summary["has_unaccepted_regressions"])
        self.assertIn("Awaiting Approval", unapproved_markdown)
        self.assertIn("Future security work with context", unapproved_markdown)
        self.assertIn("Unused Approved Regression Exceptions", approved_markdown)

    def test_summary_and_history_templates_can_be_overridden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary_template = pathlib.Path(tmp) / "summary.md.tmpl"
            history_template = pathlib.Path(tmp) / "history.md.tmpl"
            summary_template.write_text("## Summary Override\n\n$summary_rows\n", encoding="utf-8")
            history_template.write_text("## History Override\n\n$history_rows\n", encoding="utf-8")

            markdown, _ = render_report.render_markdown(
                callgrind_results(),
                3.0,
                "gungraun",
                None,
                None,
                None,
                [],
                10,
                "gungraun-history",
                None,
                str(summary_template),
                str(history_template),
                False,
            )

        self.assertIn("## Summary Override", markdown)
        self.assertIn("## History Override", markdown)


if __name__ == "__main__":
    unittest.main()
