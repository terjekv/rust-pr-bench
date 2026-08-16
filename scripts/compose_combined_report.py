#!/usr/bin/env python3
import argparse
import pathlib
from string import Template

TEMPLATE_DIR = pathlib.Path(__file__).resolve().parent / "templates"


def load_template_text(default_filename: str, template_path: str | None) -> str:
    if template_path:
        path = pathlib.Path(template_path).resolve()
    else:
        path = TEMPLATE_DIR / default_filename
    return path.read_text(encoding="utf-8")


def shift_heading_depth(lines: list[str], depth: int) -> list[str]:
    shifted: list[str] = []
    for line in lines:
        if line.startswith("#"):
            marker_len = len(line) - len(line.lstrip("#"))
            if marker_len > 0 and (len(line) == marker_len or line[marker_len] == " "):
                shifted.append(("#" * depth) + line)
                continue
        shifted.append(line)
    return shifted


def normalize_report_body(report_path: pathlib.Path) -> str:
    text = report_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if lines and lines[0].startswith("## "):
        lines = lines[1:]
    while lines and not lines[0].strip():
        lines = lines[1:]
    return "\n".join(shift_heading_depth(lines, 1)).strip()


def build_run_meta_block(pr_number: int | None, run_at: str | None, head_sha: str | None) -> str:
    if pr_number is None and not run_at and not head_sha:
        return ""
    pr_part = f"PR: #{pr_number}" if pr_number is not None else "PR: n/a"
    run_part = f"Latest: {run_at}" if run_at else "Latest: n/a"
    head_part = f"Head: {head_sha[:7]}" if head_sha else "Head: n/a"
    return f"{pr_part} • {run_part} • {head_part}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--report-gungraun",
        "--report-callgrind",
        dest="report_gungraun",
    )
    parser.add_argument("--report-criterion")
    parser.add_argument("--template-path")
    parser.add_argument("--section-template-path")
    parser.add_argument("--pr-number", type=int)
    parser.add_argument("--run-at")
    parser.add_argument("--head-sha")
    args = parser.parse_args()

    sections: list[str] = []
    section_template = Template(
        load_template_text("report_combined_backend_section.md.tmpl", args.section_template_path)
    )
    if args.report_gungraun:
        path = pathlib.Path(args.report_gungraun)
        if path.exists():
            backend_body = normalize_report_body(path)
            sections.append(
                section_template.safe_substitute(
                    backend_title="Gungraun",
                    backend_body=backend_body,
                ).strip()
            )
    if args.report_criterion:
        path = pathlib.Path(args.report_criterion)
        if path.exists():
            backend_body = normalize_report_body(path)
            sections.append(
                section_template.safe_substitute(
                    backend_title="Criterion",
                    backend_body=backend_body,
                ).strip()
            )

    if not sections:
        raise SystemExit("No backend reports were generated.")

    template = Template(load_template_text("report_combined.md.tmpl", args.template_path))
    run_meta_line = build_run_meta_block(args.pr_number, args.run_at, args.head_sha)
    run_meta_block = f"{run_meta_line}\n\n" if run_meta_line else ""
    markdown = template.safe_substitute(
        run_meta_block=run_meta_block,
        sections="\n\n".join(sections),
    ).rstrip() + "\n"
    pathlib.Path(args.output).write_text(markdown, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
