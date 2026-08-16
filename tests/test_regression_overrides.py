import unittest

from testlib import load_script_module


regression_overrides = load_script_module(
    "regression_overrides", "scripts/regression_overrides.py"
)


def directive(rules: str) -> str:
    return (
        f"""PR details\n\n```rust-pr-bench\n{{"accept_regressions": {rules}}}\n```\n"""
    )


def metadata(body: str, labels: list[str]) -> dict:
    return {
        "body": body,
        "labels": labels,
        "body_last_edited_at": "2026-07-22T10:00:00Z",
        "approval_label_applied_at": "2026-07-22T10:01:00Z",
    }


class RegressionOverrideTests(unittest.TestCase):
    def test_parses_scoped_numeric_and_unscoped_any_rules(self) -> None:
        rules = regression_overrides.parse_directives(
            directive(
                """[
                  {
                    "benchmark": "verify password",
                    "backend": "gungraun",
                    "feature": "secure mode",
                    "max_regression_pct": 35,
                    "reason": "Constant-time verification"
                  },
                  {
                    "benchmark": "tls_handshake",
                    "max_regression_pct": "any",
                    "reason": "Protocol hardening"
                  }
                ]"""
            )
        )

        self.assertEqual(rules[0]["max_regression_pct"], 35.0)
        self.assertEqual(rules[0]["feature"], "secure mode")
        self.assertEqual(rules[1]["max_regression_pct"], "any")
        self.assertNotIn("backend", rules[1])

    def test_empty_approval_label_disables_and_ignores_directives(self) -> None:
        config = regression_overrides.build_override_config(
            {"body": "```rust-pr-bench\nnot json\n```", "labels": []}, ""
        )

        self.assertFalse(config["enabled"])
        self.assertEqual(config["rules"], [])

    def test_label_controls_whether_a_rule_matches(self) -> None:
        body = directive(
            '[{"benchmark":"verify","max_regression_pct":20,"reason":"security"}]'
        )
        unapproved = regression_overrides.build_override_config(
            metadata(body, []), "performance-approved"
        )
        approved = regression_overrides.build_override_config(
            metadata(body, ["performance-approved"]), "performance-approved"
        )

        self.assertIsNone(
            regression_overrides.matching_rule(
                unapproved, "gungraun", "default", "verify"
            )
        )
        self.assertIsNotNone(
            regression_overrides.matching_rule(approved, "criterion", "other", "verify")
        )
        self.assertRegex(approved["directive_sha256"], r"^[0-9a-f]{64}$")

    def test_stale_label_does_not_approve_an_edited_directive(self) -> None:
        body = directive(
            '[{"benchmark":"verify","max_regression_pct":"any","reason":"edited"}]'
        )
        stale = metadata(body, ["performance-approved"])
        stale["body_last_edited_at"] = "2026-07-22T10:02:00Z"
        stale["approval_label_applied_at"] = "2026-07-22T10:01:00Z"

        config = regression_overrides.build_override_config(
            stale, "performance-approved"
        )

        self.assertFalse(config["approved"])
        self.assertIsNone(
            regression_overrides.matching_rule(
                config, "gungraun", "default", "verify"
            )
        )

    def test_same_timestamp_is_rejected_to_close_edit_label_races(self) -> None:
        body = directive(
            '[{"benchmark":"verify","max_regression_pct":20,"reason":"security"}]'
        )
        ambiguous = metadata(body, ["performance-approved"])
        ambiguous["body_last_edited_at"] = ambiguous["approval_label_applied_at"]

        config = regression_overrides.build_override_config(
            ambiguous, "performance-approved"
        )

        self.assertFalse(config["approved"])

    def test_scope_matching_is_exact(self) -> None:
        body = directive(
            """[{
              "benchmark":"verify",
              "backend":"criterion",
              "feature":"secure",
              "max_regression_pct":"any",
              "reason":"security"
            }]"""
        )
        config = regression_overrides.build_override_config(
            metadata(body, ["approved"]), "approved"
        )

        self.assertIsNotNone(
            regression_overrides.matching_rule(config, "criterion", "secure", "verify")
        )
        self.assertIsNone(
            regression_overrides.matching_rule(config, "criterion", "Secure", "verify")
        )
        self.assertIsNone(
            regression_overrides.matching_rule(
                config, "gungraun", "secure", "verify"
            )
        )

    def test_numeric_limit_and_any_accept_only_finite_deltas(self) -> None:
        numeric = {"max_regression_pct": 20.0}
        any_limit = {"max_regression_pct": "any"}

        self.assertTrue(regression_overrides.rule_accepts_delta(numeric, 20.0))
        self.assertFalse(regression_overrides.rule_accepts_delta(numeric, 20.01))
        self.assertTrue(regression_overrides.rule_accepts_delta(any_limit, 1000.0))
        self.assertFalse(
            regression_overrides.rule_accepts_delta(any_limit, float("inf"))
        )

    def test_rejects_malformed_or_multiple_blocks(self) -> None:
        invalid_bodies = [
            "```rust-pr-bench\n{bad}\n```",
            "```rust-pr-bench\n{}",
            directive("[]") + directive("[]"),
        ]

        for body in invalid_bodies:
            with self.subTest(body=body):
                with self.assertRaises(regression_overrides.RegressionOverrideError):
                    regression_overrides.parse_directives(body)

    def test_rejects_unknown_missing_and_invalid_fields(self) -> None:
        invalid_rules = [
            '[{"benchmark":"a","max_regression_pct":5,"reason":"x","typo":true}]',
            '[{"benchmark":"a","max_regression_pct":5}]',
            '[{"benchmark":"a","max_regression_pct":-1,"reason":"x"}]',
            '[{"benchmark":"a","max_regression_pct":true,"reason":"x"}]',
            '[{"benchmark":"a","backend":"all","max_regression_pct":5,"reason":"x"}]',
            '[{"benchmark":"","max_regression_pct":5,"reason":"x"}]',
        ]

        for rules in invalid_rules:
            with self.subTest(rules=rules):
                with self.assertRaises(regression_overrides.RegressionOverrideError):
                    regression_overrides.parse_directives(directive(rules))

    def test_rejects_overlapping_but_allows_disjoint_rules(self) -> None:
        overlapping = """[
          {"benchmark":"a","max_regression_pct":10,"reason":"broad"},
          {"benchmark":"a","backend":"criterion","max_regression_pct":20,"reason":"narrow"}
        ]"""
        disjoint = """[
          {"benchmark":"a","backend":"criterion","max_regression_pct":10,"reason":"criterion"},
          {"benchmark":"a","backend":"gungraun","max_regression_pct":20,"reason":"gungraun"}
        ]"""

        with self.assertRaises(regression_overrides.RegressionOverrideError):
            regression_overrides.parse_directives(directive(overlapping))
        self.assertEqual(
            len(regression_overrides.parse_directives(directive(disjoint))), 2
        )

    def test_gungraun_scope_matches_gungraun_results(self) -> None:
        rules = regression_overrides.parse_directives(
            directive(
                '[{"benchmark":"a","backend":"gungraun",'
                '"max_regression_pct":10,"reason":"migration"}]'
            )
        )

        self.assertEqual(rules[0]["backend"], "gungraun")
        self.assertTrue(
            regression_overrides.rule_matches_result(
                rules[0], "gungraun", "default", "a"
            )
        )


if __name__ == "__main__":
    unittest.main()
