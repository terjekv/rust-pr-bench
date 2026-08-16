#!/usr/bin/env python3
import argparse
import html
import json
import math
import pathlib
import sys
from collections import defaultdict
from string import Template
from typing import Any, Iterable

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
TEMPLATE_DIR = SCRIPT_DIR / "templates"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from regression_overrides import (  # noqa: E402
    matching_rule,
    rule_accepts_delta,
    rule_matches_result,
)
from backend_names import GUNGRAUN_BACKEND, normalize_backend  # noqa: E402


def load_results(artifacts_dir: pathlib.Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for path in sorted(artifacts_dir.rglob("result.json")):
        with path.open("r", encoding="utf-8") as handle:
            results.append(json.load(handle))
    return results


def classify(delta_pct: float, threshold: float) -> tuple[str, bool]:
    if math.isnan(delta_pct) or math.isinf(delta_pct):
        return ("⚪ unknown", False)
    if delta_pct > threshold:
        return ("🔴 regression", True)
    if delta_pct < -0.5:
        return ("🟢 improved", False)
    if delta_pct > 0.5:
        return ("🟡 slight regression", False)
    return ("⚪ neutral", False)


def backend_title(backend: str) -> str:
    if backend == "criterion":
        return "Criterion Benchmark Report"
    return "Gungraun Benchmark Report"


def build_run_meta_block(pr_number: int | None, run_at: str | None, head_sha: str | None) -> str:
    if pr_number is None and not run_at and not head_sha:
        return ""
    pr_part = f"PR: #{pr_number}" if pr_number is not None else "PR: n/a"
    run_part = f"Latest: {run_at}" if run_at else "Latest: n/a"
    head_part = f"Head: {head_sha[:7]}" if head_sha else "Head: n/a"
    return f"{pr_part} • {run_part} • {head_part}\n"


def load_template_text(default_filename: str, template_path: str | None) -> str:
    if template_path:
        path = pathlib.Path(template_path).resolve()
    else:
        path = TEMPLATE_DIR / default_filename
    return path.read_text(encoding="utf-8")


def fmt_number(value: float | int) -> str:
    numeric = float(value)
    if not math.isfinite(numeric):
        return "n/a"
    rounded = round(numeric)
    if abs(numeric - rounded) < 1e-9:
        return f"{int(rounded):,}"
    return f"{numeric:,.2f}"


def fmt_pct(value: float) -> str:
    return f"{value:+.2f}%"


def fmt_pct_or_na(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "n/a"
    return fmt_pct(value)


def metric_delta(base_value: float, head_value: float) -> float:
    if base_value == 0.0:
        return 0.0 if head_value == 0 else float("inf")
    return ((head_value - base_value) / base_value) * 100.0


def avg(values: Iterable[float]) -> float | None:
    items = [value for value in values if math.isfinite(value)]
    if not items:
        return None
    return sum(items) / len(items)


def collect_metric_deltas(entry: dict[str, Any]) -> list[float]:
    base_metrics = {item["metric"]: float(item["value"]) for item in entry.get("base_metrics", [])}
    head_metrics = {item["metric"]: float(item["value"]) for item in entry.get("head_metrics", [])}
    metric_names = set(base_metrics.keys()) | set(head_metrics.keys())
    deltas: list[float] = []
    for metric_name in metric_names:
        base_value = base_metrics.get(metric_name, 0)
        head_value = head_metrics.get(metric_name, 0)
        deltas.append(metric_delta(base_value, head_value))
    return deltas


def accepted_override(
    entry: dict[str, Any],
    threshold: float,
    backend: str,
    override_config: dict[str, Any],
) -> dict[str, Any] | None:
    if (
        entry.get("head_error")
        or entry.get("base_error")
        or entry.get("head_missing")
        or entry.get("base_missing")
    ):
        return None
    _, is_regression = classify(float(entry["delta_pct"]), threshold)
    if not is_regression:
        return None
    rule = matching_rule(
        override_config,
        backend,
        str(entry["feature_name"]),
        str(entry["benchmark_name"]),
    )
    if rule is None or not rule_accepts_delta(rule, float(entry["delta_pct"])):
        return None
    return rule


def compute_feature_summary(
    entries: list[dict[str, Any]],
    threshold: float,
    backend: str,
    override_config: dict[str, Any],
) -> tuple[int, int, int, int, float | None, float | None, bool, bool]:
    improved = 0
    regressions = 0
    accepted_regressions = 0
    neutral = 0
    has_regressions = False
    has_unaccepted_regressions = False
    bench_deltas: list[float] = []
    metric_deltas: list[float] = []

    for entry in entries:
        if entry.get("head_error") or entry.get("base_error"):
            continue
        elif entry.get("head_missing") or entry.get("base_missing"):
            continue
        else:
            status, is_regression = classify(entry["delta_pct"], threshold)
            bench_deltas.append(float(entry["delta_pct"]))
            metric_deltas.extend(collect_metric_deltas(entry))
        if is_regression:
            regressions += 1
            has_regressions = True
            if accepted_override(entry, threshold, backend, override_config) is not None:
                accepted_regressions += 1
            else:
                has_unaccepted_regressions = True
        elif status.startswith("🟢"):
            improved += 1
        else:
            neutral += 1

    return (
        improved,
        regressions,
        accepted_regressions,
        neutral,
        avg(bench_deltas),
        avg(metric_deltas),
        has_regressions,
        has_unaccepted_regressions,
    )


def inline_text(value: Any) -> str:
    return html.escape(" ".join(str(value).split()))


def code_text(value: Any) -> str:
    return f"<code>{inline_text(value)}</code>"


def override_limit_text(rule: dict[str, Any]) -> str:
    maximum = rule["max_regression_pct"]
    if maximum == "any":
        return "any finite regression"
    return f"+{float(maximum):.2f}%"


def override_scope_text(rule: dict[str, Any]) -> str:
    backend = rule.get("backend") or "all backends"
    feature = rule.get("feature") or "all feature sets"
    return "{backend} / {feature} / {benchmark}".format(
        backend=code_text(backend),
        feature=code_text(feature),
        benchmark=code_text(rule["benchmark"]),
    )


def render_metric_breakdown(entry: dict[str, Any], threshold: float) -> list[str]:
    lines: list[str] = []
    if entry.get("head_error") or entry.get("base_error"):
        labels = []
        if entry.get("head_error"):
            labels.append("head error")
        if entry.get("base_error"):
            labels.append("base error")
        reason_text = " and ".join(labels) if labels else "error"
        lines.append(f"<details><summary>{entry['benchmark_name']} metric breakdown (error)</summary>")
        lines.append("")
        lines.append(f"Skipped metric breakdown ({reason_text}).")
        lines.append("")
        lines.append("</details>")
        return lines

    if entry.get("head_missing") or entry.get("base_missing"):
        reasons = []
        if entry.get("head_missing"):
            reasons.append("head missing")
        if entry.get("base_missing"):
            reasons.append("base missing")
        reason_text = " and ".join(reasons) if reasons else "missing"
        lines.append(
            f"<details><summary>{entry['benchmark_name']} metric breakdown (missing)</summary>"
        )
        lines.append("")
        lines.append(f"Skipped metric breakdown ({reason_text}).")
        lines.append("")
        lines.append("</details>")
        return lines

    base_metrics = {item["metric"]: float(item["value"]) for item in entry.get("base_metrics", [])}
    head_metrics = {item["metric"]: float(item["value"]) for item in entry.get("head_metrics", [])}
    metric_names = sorted(set(base_metrics.keys()) | set(head_metrics.keys()))
    short_metric_names = make_unique_metric_labels(metric_names)

    lines.append(
        f"<details><summary>{entry['benchmark_name']} metric breakdown ({len(metric_names)} metrics)</summary>"
    )
    lines.append("")
    lines.append("| Metric | Base | Head | Delta | Status |")
    lines.append("| --- | ---: | ---: | ---: | --- |")
    for metric_name in metric_names:
        base_value = base_metrics.get(metric_name, 0)
        head_value = head_metrics.get(metric_name, 0)
        delta_pct = metric_delta(base_value, head_value)
        status, _ = classify(delta_pct, threshold)
        lines.append(
            "| {metric} | {base} | {head} | {delta} | {status} |".format(
                metric=short_metric_names[metric_name],
                base=fmt_number(base_value),
                head=fmt_number(head_value),
                delta=fmt_pct(delta_pct) if math.isfinite(delta_pct) else "n/a",
                status=status if math.isfinite(delta_pct) else "⚪ unknown",
            )
        )
    lines.append("")
    lines.append("</details>")
    return lines


def make_unique_metric_labels(metric_names: list[str]) -> dict[str, str]:
    if not metric_names:
        return {}

    segments = {name: [seg for seg in name.split("/") if seg] for name in metric_names}
    max_segments = max((len(parts) for parts in segments.values()), default=1)
    lengths = {name: 1 for name in metric_names}

    while True:
        labels: dict[str, list[str]] = {}
        for name in metric_names:
            parts = segments[name]
            length = min(lengths[name], len(parts))
            short = "/".join(parts[-length:]) if parts else name
            labels.setdefault(short, []).append(name)

        collisions = {short: names for short, names in labels.items() if len(names) > 1}
        if not collisions:
            break

        progressed = False
        for names in collisions.values():
            for name in names:
                if lengths[name] < max_segments:
                    lengths[name] += 1
                    progressed = True
        if not progressed:
            break

    rendered: dict[str, str] = {}
    for name in metric_names:
        parts = segments[name]
        length = min(lengths[name], len(parts))
        short = "/".join(parts[-length:]) if parts else name
        rendered[name] = f'<span title="{name}">{short}</span>'
    return rendered


def render_markdown(
    results: list[dict[str, Any]],
    threshold: float,
    backend: str,
    pr_number: int | None,
    head_sha: str | None,
    run_at: str | None,
    history: list[dict[str, Any]],
    max_history: int,
    history_marker_key: str,
    template_path: str | None,
    summary_template_path: str | None,
    history_template_path: str | None,
    omit_run_meta: bool,
    override_config: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    backend = normalize_backend(backend)
    override_config = override_config or {
        "enabled": False,
        "approved": False,
        "approval_label": "",
        "rules": [],
    }
    comparison_statistic = "summary"
    metric_unit = ""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    has_regressions = False
    has_unaccepted_regressions = False
    summary_rows: list[str] = []
    feature_sections: list[str] = []
    total_improved = 0
    total_regressions = 0
    total_accepted_regressions = 0
    total_neutral = 0
    all_bench_deltas: list[float] = []
    all_metric_deltas: list[float] = []

    if results:
        backend = normalize_backend(results[0].get("backend", backend))
        comparison_statistic = str(results[0].get("comparison_statistic") or "summary")
        metric_unit = str(results[0].get("metric_unit") or "")

        for item in results:
            grouped[item["feature_name"]].append(item)

        for feature_name in sorted(grouped.keys()):
            (
                improved,
                regressions,
                accepted_regressions,
                neutral,
                avg_bench_delta,
                avg_metric_delta,
                feature_has_regressions,
                feature_has_unaccepted_regressions,
            ) = compute_feature_summary(
                grouped[feature_name], threshold, backend, override_config
            )
            if feature_has_regressions:
                has_regressions = True
            if feature_has_unaccepted_regressions:
                has_unaccepted_regressions = True
            total_improved += improved
            total_regressions += regressions
            total_accepted_regressions += accepted_regressions
            total_neutral += neutral
            for entry in grouped[feature_name]:
                if (
                    entry.get("head_error")
                    or entry.get("base_error")
                    or entry.get("head_missing")
                    or entry.get("base_missing")
                ):
                    continue
                all_bench_deltas.append(float(entry["delta_pct"]))
                all_metric_deltas.extend(collect_metric_deltas(entry))
            section_lines: list[str] = []

            section_lines.append(f"<details><summary><strong>{feature_name}</strong></summary>")
            section_lines.append("")
            section_lines.append("| Benchmark | Base | Head | Delta | Status |")
            section_lines.append("| --- | ---: | ---: | ---: | --- |")

            sorted_entries = sorted(grouped[feature_name], key=lambda e: e["benchmark_name"])
            for entry in sorted_entries:
                if entry.get("head_error") or entry.get("base_error"):
                    status = "🔴 error"
                elif entry.get("head_missing") or entry.get("base_missing"):
                    status = "⚪ missing"
                else:
                    status, _ = classify(entry["delta_pct"], threshold)
                    if accepted_override(entry, threshold, backend, override_config) is not None:
                        status = "🟠 accepted regression"
                section_lines.append(
                    "| {bench} | {base} | {head} | {delta} | {status} |".format(
                        bench=entry["benchmark_name"],
                        base=fmt_number(float(entry["base_total"])),
                        head=fmt_number(float(entry["head_total"])),
                        delta=fmt_pct_or_na(float(entry["delta_pct"])),
                        status=status,
                    )
                )

            section_lines.append("")
            section_lines.append("Metric-level breakdowns:")
            section_lines.append("")
            for entry in sorted_entries:
                section_lines.extend(render_metric_breakdown(entry, threshold))
                section_lines.append("")

            section_lines.append("")
            section_lines.append("</details>")

            summary_rows.append(
                "| {feature} | {improved} | {regressions} | {accepted} | {neutral} | {bench_avg} | {metric_avg} |".format(
                    feature=feature_name,
                    improved=improved,
                    regressions=regressions,
                    accepted=accepted_regressions,
                    neutral=neutral,
                    bench_avg=fmt_pct_or_na(avg_bench_delta),
                    metric_avg=fmt_pct_or_na(avg_metric_delta),
                )
            )
            feature_sections.append("\n".join(section_lines))

    regressions_block = ""
    accepted_lines: list[str] = []
    unaccepted_lines: list[str] = []
    if has_regressions:
        for entry in sorted(results, key=lambda e: e["delta_pct"], reverse=True):
            if entry.get("head_error") or entry.get("base_error"):
                continue
            _, is_regression = classify(entry["delta_pct"], threshold)
            if not is_regression:
                continue
            rule = matching_rule(
                override_config,
                backend,
                str(entry["feature_name"]),
                str(entry["benchmark_name"]),
            )
            accepted = rule is not None and rule_accepts_delta(
                rule, float(entry["delta_pct"])
            )
            line = "- `{feature}` / `{bench}`: {delta}".format(
                feature=entry["feature_name"],
                bench=entry["benchmark_name"],
                delta=fmt_pct(float(entry["delta_pct"])),
            )
            if accepted and rule is not None:
                accepted_lines.append(
                    "{line} (limit: {limit}; reason: {reason})".format(
                        line=line,
                        limit=override_limit_text(rule),
                        reason=inline_text(rule["reason"]),
                    )
                )
            else:
                if rule is not None:
                    line += " (requested limit {limit} exceeded; reason: {reason})".format(
                        limit=override_limit_text(rule),
                        reason=inline_text(rule["reason"]),
                    )
                unaccepted_lines.append(line)
        if unaccepted_lines:
            regressions_block += (
                "### Unaccepted Regressions Above Threshold\n\n"
                + "\n".join(unaccepted_lines)
                + "\n\n"
            )
        if accepted_lines:
            regressions_block += (
                "### Accepted Regressions\n\n"
                + "Approved by label {label}.\n\n".format(
                    label=code_text(override_config.get("approval_label") or "")
                )
                + "\n".join(accepted_lines)
                + "\n\n"
            )

    overrides_block = ""
    applicable_rules = [
        rule
        for rule in override_config.get("rules", [])
        if rule.get("backend") in {None, backend}
    ]
    if applicable_rules and not override_config.get("approved"):
        request_lines = [
            "- {scope}: limit {limit}; reason: {reason}".format(
                scope=override_scope_text(rule),
                limit=override_limit_text(rule),
                reason=inline_text(rule["reason"]),
            )
            for rule in applicable_rules
        ]
        overrides_block = (
            "### Regression Exception Requests (Awaiting Approval)\n\n"
            + "These requests do not affect the gate until label {label} is present.\n\n".format(
                label=code_text(override_config.get("approval_label") or "")
            )
            + "\n".join(request_lines)
            + "\n\n"
        )
    elif applicable_rules and override_config.get("approved"):
        matched_rule_ids: set[int] = set()
        for entry in results:
            if (
                entry.get("head_error")
                or entry.get("base_error")
                or entry.get("head_missing")
                or entry.get("base_missing")
            ):
                continue
            _, is_regression = classify(float(entry["delta_pct"]), threshold)
            if not is_regression:
                continue
            for rule in applicable_rules:
                if rule_matches_result(
                    rule,
                    backend,
                    str(entry["feature_name"]),
                    str(entry["benchmark_name"]),
                ):
                    matched_rule_ids.add(id(rule))
        unused_rules = [
            rule for rule in applicable_rules if id(rule) not in matched_rule_ids
        ]
        if unused_rules:
            unused_lines = [
                "- {scope}: limit {limit}; reason: {reason}".format(
                    scope=override_scope_text(rule),
                    limit=override_limit_text(rule),
                    reason=inline_text(rule["reason"]),
                )
                for rule in unused_rules
            ]
            overrides_block = (
                "### Unused Approved Regression Exceptions\n\n"
                + "\n".join(unused_lines)
                + "\n\n"
            )

    moved_entries = [entry for entry in results if entry.get("moved") or entry.get("move_ambiguous")]
    moved_block = ""
    if moved_entries:
        moved_lines: list[str] = []
        for entry in sorted(moved_entries, key=lambda e: e["benchmark_name"]):
            if entry.get("moved"):
                moved_lines.append(
                    "- `{feature}` / `{bench}`: compared moved benchmark `{source}` -> `{target}`".format(
                        feature=entry["feature_name"],
                        bench=entry["benchmark_name"],
                        source=entry.get("move_source") or "base",
                        target=entry.get("move_target") or "head",
                    )
                )
            else:
                candidates = ", ".join(str(item) for item in entry.get("move_candidates", []))
                moved_lines.append(
                    "- `{feature}` / `{bench}`: move detection was ambiguous ({candidates})".format(
                        feature=entry["feature_name"],
                        bench=entry["benchmark_name"],
                        candidates=candidates or "no unique candidate",
                    )
                )
        moved_block = "### Moved Benchmarks\n\n" + "\n".join(moved_lines) + "\n\n"

    error_entries = [entry for entry in results if entry.get("head_error") or entry.get("base_error")]
    error_block = ""
    if error_entries:
        error_lines: list[str] = []
        for entry in sorted(error_entries, key=lambda e: e["benchmark_name"]):
            reasons = []
            if entry.get("head_error"):
                reasons.append(f"head exit {entry.get('head_error_code')}")
            if entry.get("base_error"):
                reasons.append(f"base exit {entry.get('base_error_code')}")
            error_lines.append(
                "- `{feature}` / `{bench}`: {reason}".format(
                    feature=entry["feature_name"],
                    bench=entry["benchmark_name"],
                    reason=", ".join(reasons),
                )
            )
        error_block = "### Benchmark Errors\n\n" + "\n".join(error_lines) + "\n\n"

    missing_entries = [entry for entry in results if entry.get("head_missing") or entry.get("base_missing")]
    missing_block = ""
    if missing_entries:
        missing_lines: list[str] = []
        for entry in sorted(missing_entries, key=lambda e: e["benchmark_name"]):
            reasons = []
            if entry.get("head_missing"):
                reasons.append("head")
            if entry.get("base_missing"):
                reasons.append("base")
            reason_text = " & ".join(reasons) if reasons else "missing"
            missing_lines.append(
                "- `{feature}` / `{bench}`: missing in {reason}".format(
                    feature=entry["feature_name"],
                    bench=entry["benchmark_name"],
                    reason=reason_text,
                )
            )
        missing_block = (
            "### Skipped Benchmarks (Missing in Base/Head)\n\n" + "\n".join(missing_lines) + "\n\n"
        )

    avg_bench_delta_all = avg(all_bench_deltas)
    avg_metric_delta_all = avg(all_metric_deltas)
    latest_entry = {
        "backend": backend,
        "commit": head_sha or "",
        "run_at": run_at or "",
        "pr_number": pr_number,
        "summary": {
            "improved": total_improved,
            "regressions": total_regressions,
            "accepted_regressions": total_accepted_regressions,
            "neutral": total_neutral,
        },
        "avg_bench_delta_pct": avg_bench_delta_all,
        "avg_metric_delta_pct": avg_metric_delta_all,
        "has_regressions": has_regressions,
        "has_unaccepted_regressions": has_unaccepted_regressions,
    }

    def history_entry_key(item: dict[str, Any]) -> str:
        item_backend = normalize_backend(item.get("backend", backend))
        commit = str(item.get("commit") or "")
        return f"{item_backend}:{commit}"

    new_history: list[dict[str, Any]] = []
    seen: set[str] = set()
    if results:
        new_history = [latest_entry]
        seen.add(history_entry_key(latest_entry))
    for item in history:
        item_backend = normalize_backend(item.get("backend", backend))
        if item_backend != backend:
            continue
        key = history_entry_key(item)
        if not key or key in seen:
            continue
        normalized_item = dict(item)
        normalized_item["backend"] = item_backend
        new_history.append(normalized_item)
        seen.add(key)
        if len(new_history) >= max_history:
            break

    history_rows: list[str] = []
    for item in new_history:
        summary = item.get("summary", {})
        accepted_count = summary.get("accepted_regressions", 0)
        regression_text = f"{summary.get('regressions', 0)} reg"
        if accepted_count:
            regression_text += f" ({accepted_count} accepted)"
        summary_text = "{improved} improved / {regressions} / {neutral} neutral".format(
            improved=summary.get("improved", 0),
            regressions=regression_text,
            neutral=summary.get("neutral", 0),
        )
        history_rows.append(
            "| {commit} | {run_at} | {summary} | {bench_avg} | {metric_avg} | {has_regressions} |".format(
                commit=(item.get("commit") or "")[:7],
                run_at=item.get("run_at") or "n/a",
                summary=summary_text,
                bench_avg=fmt_pct_or_na(item.get("avg_bench_delta_pct")),
                metric_avg=fmt_pct_or_na(item.get("avg_metric_delta_pct")),
                has_regressions="yes" if item.get("has_regressions") else "no",
            )
        )
    if not history_rows:
        history_rows.append("| n/a | n/a | 0 improved / 0 reg / 0 neutral | n/a | n/a | no |")

    history_payload = json.dumps({"history": new_history}, separators=(",", ":"))
    template = Template(load_template_text("report_single.md.tmpl", template_path))

    comparison_block = ""
    if backend == "criterion":
        unit_text = f" ({metric_unit})" if metric_unit else ""
        comparison_block = f"Comparison statistic: **{comparison_statistic}**{unit_text}\n"

    run_meta_block = "" if omit_run_meta else build_run_meta_block(pr_number, run_at, head_sha)

    summary_rows_text = (
        "\n".join(summary_rows) if summary_rows else "| n/a | 0 | 0 | 0 | 0 | n/a | n/a |"
    )
    feature_sections_text = "\n\n".join(feature_sections).strip()
    if not feature_sections_text:
        feature_sections_text = "No benchmark results were found."
    if feature_sections_text:
        feature_sections_text += "\n\n"
    history_rows_text = "\n".join(history_rows)
    summary_suffix = f" • {head_sha[:7]}" if head_sha else ""
    summary_section = (
        Template(load_template_text("report_single_summary.md.tmpl", summary_template_path))
        .safe_substitute(summary_suffix=summary_suffix, summary_rows=summary_rows_text)
        .strip()
        + "\n"
    )
    history_section = (
        Template(load_template_text("report_single_history.md.tmpl", history_template_path))
        .safe_substitute(history_count=str(len(new_history)), history_rows=history_rows_text)
        .strip()
        + "\n"
    )

    markdown = template.safe_substitute(
        report_title=backend_title(backend),
        threshold_text=f"{threshold:.2f}%",
        comparison_block=comparison_block,
        run_meta_block=run_meta_block,
        summary_section=summary_section,
        feature_sections=feature_sections_text,
        regressions_block=regressions_block,
        overrides_block=overrides_block,
        missing_block=moved_block + error_block + missing_block,
        history_section=history_section,
        history_marker_key=history_marker_key,
        history_payload=history_payload,
    ).rstrip() + "\n"

    summary_payload = {
        "has_regressions": has_regressions,
        "has_unaccepted_regressions": has_unaccepted_regressions,
        "accepted_regressions": total_accepted_regressions,
        "count": len(results),
        "backend": backend,
        "latest": latest_entry,
        "history": new_history,
    }
    return (markdown, summary_payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts-dir", required=True)
    parser.add_argument("--threshold", required=True, type=float)
    parser.add_argument("--backend", default=GUNGRAUN_BACKEND)
    parser.add_argument("--markdown-output", required=True)
    parser.add_argument("--summary-output", required=True)
    parser.add_argument("--history-input")
    parser.add_argument("--history-key", default="gungraun-history")
    parser.add_argument("--template-path")
    parser.add_argument("--summary-template-path")
    parser.add_argument("--history-template-path")
    parser.add_argument("--head-sha")
    parser.add_argument("--run-at")
    parser.add_argument("--pr-number", type=int)
    parser.add_argument("--max-history", type=int, default=10)
    parser.add_argument("--omit-run-meta", action="store_true")
    parser.add_argument("--regression-overrides-input")
    args = parser.parse_args()

    results = load_results(pathlib.Path(args.artifacts_dir))
    history: list[dict[str, Any]] = []
    if args.history_input:
        history_path = pathlib.Path(args.history_input)
        if history_path.exists():
            history_data = json.loads(history_path.read_text(encoding="utf-8"))
            if isinstance(history_data, dict):
                history = history_data.get("history", [])
            elif isinstance(history_data, list):
                history = history_data

    override_config: dict[str, Any] | None = None
    if args.regression_overrides_input:
        override_path = pathlib.Path(args.regression_overrides_input)
        if override_path.exists():
            loaded_overrides = json.loads(override_path.read_text(encoding="utf-8"))
            if not isinstance(loaded_overrides, dict):
                raise ValueError("regression overrides input must be a JSON object")
            override_config = loaded_overrides

    markdown, summary_payload = render_markdown(
        results,
        args.threshold,
        args.backend,
        args.pr_number,
        args.head_sha,
        args.run_at,
        history,
        args.max_history,
        args.history_key,
        args.template_path,
        args.summary_template_path,
        args.history_template_path,
        args.omit_run_meta,
        override_config,
    )

    pathlib.Path(args.markdown_output).write_text(markdown, encoding="utf-8")
    pathlib.Path(args.summary_output).write_text(json.dumps(summary_payload), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
