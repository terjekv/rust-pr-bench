#!/usr/bin/env python3
import argparse
import datetime
import hashlib
import json
import math
import pathlib
import re
import sys
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from backend_names import normalize_backend  # noqa: E402
DIRECTIVE_OPEN_RE = re.compile(r"^```rust-pr-bench[ \t]*$", re.MULTILINE)
DIRECTIVE_BLOCK_RE = re.compile(
    r"^```rust-pr-bench[ \t]*\r?\n(?P<payload>.*?)^```[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
ROOT_FIELDS = {"accept_regressions"}
RULE_FIELDS = {
    "benchmark",
    "backend",
    "feature",
    "max_regression_pct",
    "reason",
}


class RegressionOverrideError(ValueError):
    pass


def _reject_json_constant(value: str) -> None:
    raise RegressionOverrideError(f"invalid JSON number: {value}")


def _non_empty_string(value: Any, field: str, rule_number: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RegressionOverrideError(
            f"regression override rule {rule_number} field '{field}' must be a non-empty string"
        )
    return value


def _rules_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left["benchmark"] != right["benchmark"]:
        return False
    backend_overlaps = (
        left.get("backend") is None
        or right.get("backend") is None
        or left.get("backend") == right.get("backend")
    )
    feature_overlaps = (
        left.get("feature") is None
        or right.get("feature") is None
        or left.get("feature") == right.get("feature")
    )
    return backend_overlaps and feature_overlaps


def parse_directives(pr_body: str) -> list[dict[str, Any]]:
    openings = list(DIRECTIVE_OPEN_RE.finditer(pr_body))
    if not openings:
        return []

    blocks = list(DIRECTIVE_BLOCK_RE.finditer(pr_body))
    if len(openings) != len(blocks):
        raise RegressionOverrideError(
            "rust-pr-bench directive block is not closed with a line containing exactly ```"
        )
    if len(blocks) != 1:
        raise RegressionOverrideError(
            "exactly one rust-pr-bench directive block is allowed"
        )

    try:
        payload = json.loads(
            blocks[0].group("payload"),
            parse_constant=_reject_json_constant,
        )
    except json.JSONDecodeError as error:
        raise RegressionOverrideError(f"invalid rust-pr-bench JSON: {error}") from error

    if not isinstance(payload, dict):
        raise RegressionOverrideError("rust-pr-bench JSON must be an object")
    unknown_root_fields = set(payload) - ROOT_FIELDS
    if unknown_root_fields:
        names = ", ".join(sorted(unknown_root_fields))
        raise RegressionOverrideError(f"unknown rust-pr-bench field(s): {names}")
    if set(payload) != ROOT_FIELDS:
        raise RegressionOverrideError(
            "rust-pr-bench JSON must contain 'accept_regressions'"
        )

    raw_rules = payload["accept_regressions"]
    if not isinstance(raw_rules, list):
        raise RegressionOverrideError("'accept_regressions' must be an array")

    rules: list[dict[str, Any]] = []
    for index, raw_rule in enumerate(raw_rules, start=1):
        if not isinstance(raw_rule, dict):
            raise RegressionOverrideError(
                f"regression override rule {index} must be an object"
            )
        unknown_rule_fields = set(raw_rule) - RULE_FIELDS
        if unknown_rule_fields:
            names = ", ".join(sorted(unknown_rule_fields))
            raise RegressionOverrideError(
                f"regression override rule {index} has unknown field(s): {names}"
            )

        required = {"benchmark", "max_regression_pct", "reason"}
        missing = required - set(raw_rule)
        if missing:
            names = ", ".join(sorted(missing))
            raise RegressionOverrideError(
                f"regression override rule {index} is missing required field(s): {names}"
            )

        rule: dict[str, Any] = {
            "benchmark": _non_empty_string(raw_rule["benchmark"], "benchmark", index),
            "reason": _non_empty_string(raw_rule["reason"], "reason", index),
        }
        if "backend" in raw_rule:
            backend = _non_empty_string(raw_rule["backend"], "backend", index)
            try:
                backend = normalize_backend(backend)
            except ValueError as error:
                raise RegressionOverrideError(
                    f"regression override rule {index} backend must be one of: "
                    "criterion or gungraun"
                ) from error
            rule["backend"] = backend
        if "feature" in raw_rule:
            rule["feature"] = _non_empty_string(raw_rule["feature"], "feature", index)

        maximum = raw_rule["max_regression_pct"]
        if maximum == "any":
            rule["max_regression_pct"] = "any"
        elif isinstance(maximum, bool) or not isinstance(maximum, (int, float)):
            raise RegressionOverrideError(
                f"regression override rule {index} 'max_regression_pct' must be "
                "a non-negative number or 'any'"
            )
        elif not math.isfinite(float(maximum)) or float(maximum) < 0:
            raise RegressionOverrideError(
                f"regression override rule {index} 'max_regression_pct' must be non-negative"
            )
        else:
            rule["max_regression_pct"] = float(maximum)

        for previous_index, previous in enumerate(rules, start=1):
            if _rules_overlap(previous, rule):
                raise RegressionOverrideError(
                    f"regression override rules {previous_index} and {index} overlap"
                )
        rules.append(rule)

    return rules


def directive_digest(rules: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        {"accept_regressions": rules},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _github_timestamp(value: Any, field: str) -> datetime.datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RegressionOverrideError(f"pull request metadata field '{field}' must be a string")
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RegressionOverrideError(
            f"pull request metadata field '{field}' is not a valid timestamp"
        ) from error
    if parsed.tzinfo is None:
        raise RegressionOverrideError(
            f"pull request metadata field '{field}' must include a timezone"
        )
    return parsed


def build_override_config(
    pr_metadata: dict[str, Any] | None, approval_label: str
) -> dict[str, Any]:
    label = approval_label.strip()
    if not label:
        return {
            "enabled": False,
            "approved": False,
            "approval_label": "",
            "directive_sha256": "",
            "rules": [],
        }

    metadata = pr_metadata or {}
    body = metadata.get("body") or ""
    if not isinstance(body, str):
        raise RegressionOverrideError("pull request body must be a string")
    raw_labels = metadata.get("labels") or []
    if not isinstance(raw_labels, list) or not all(
        isinstance(item, str) for item in raw_labels
    ):
        raise RegressionOverrideError("pull request labels must be an array of strings")

    rules = parse_directives(body)
    body_last_edited_at = _github_timestamp(
        metadata.get("body_last_edited_at"), "body_last_edited_at"
    )
    label_applied_at = _github_timestamp(
        metadata.get("approval_label_applied_at"), "approval_label_applied_at"
    )
    label_is_current = label in raw_labels
    approval_is_newer = label_applied_at is not None and (
        body_last_edited_at is None or label_applied_at > body_last_edited_at
    )

    return {
        "enabled": True,
        "approved": label_is_current and approval_is_newer,
        "approval_label": label,
        "directive_sha256": directive_digest(rules),
        "body_last_edited_at": metadata.get("body_last_edited_at"),
        "approval_label_applied_at": metadata.get("approval_label_applied_at"),
        "rules": rules,
    }


def matching_rule(
    config: dict[str, Any], backend: str, feature: str, benchmark: str
) -> dict[str, Any] | None:
    if not config.get("enabled") or not config.get("approved"):
        return None
    for rule in config.get("rules", []):
        if rule_matches_result(rule, backend, feature, benchmark):
            return rule
    return None


def rule_matches_result(
    rule: dict[str, Any], backend: str, feature: str, benchmark: str
) -> bool:
    if rule["benchmark"] != benchmark:
        return False
    normalized_backend = normalize_backend(backend)
    if (
        rule.get("backend") is not None
        and normalize_backend(rule["backend"]) != normalized_backend
    ):
        return False
    if rule.get("feature") is not None and rule["feature"] != feature:
        return False
    return True


def rule_accepts_delta(rule: dict[str, Any], delta_pct: float) -> bool:
    if not math.isfinite(delta_pct):
        return False
    maximum = rule["max_regression_pct"]
    return maximum == "any" or delta_pct <= float(maximum)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr-metadata")
    parser.add_argument("--approval-label", default="")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    metadata: dict[str, Any] | None = None
    if args.pr_metadata:
        metadata_path = pathlib.Path(args.pr_metadata)
        if not metadata_path.exists():
            parser.error(f"pull request metadata file does not exist: {metadata_path}")
        try:
            loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            parser.error(f"invalid pull request metadata JSON: {error}")
        if not isinstance(loaded, dict):
            parser.error("pull request metadata must be a JSON object")
        metadata = loaded

    try:
        config = build_override_config(metadata, args.approval_label)
    except RegressionOverrideError as error:
        parser.error(str(error))
    pathlib.Path(args.output).write_text(json.dumps(config), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
