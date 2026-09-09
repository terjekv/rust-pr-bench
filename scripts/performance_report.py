"""Render build/cache observations separately from measured regressions."""

import argparse
import html
import json
import pathlib


def collect(directory: pathlib.Path) -> list[dict]:
    records = []
    for path in sorted(directory.rglob("performance.jsonl")):
        for line in path.read_text().splitlines():
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    records.append(item)
            except ValueError:
                continue
    return records


def render(directory: pathlib.Path) -> str:
    records = collect(directory)
    if not records:
        return ""
    rows = [
        "\n<details>",
        "<summary>Build and cache performance</summary>\n",
        "| Work | Restore / save outcome | Restore / save | Contents | Compilation | Fresh / rebuilt |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for item in records:
        if item.get("kind") == "jobs":
            continue
        name = html.escape(str(item.get("label", item.get("kind", "build")))).replace(
            "|", "&#124;"
        )
        size = item.get("contents_bytes")
        size_text = f"{size / 1048576:.1f} MiB" if size is not None else "—"
        compile_seconds = item.get("compile_seconds")
        compile_text = f"{compile_seconds:.2f}s" if compile_seconds is not None else "—"
        if item.get("executables_reused"):
            compile_text = f"skipped ({item['executables_reused']} executables reused)"
        restore_time = (
            f"{item['restore_seconds']:.2f}s" if "restore_seconds" in item else "—"
        )
        save_time = f"{item['save_seconds']:.2f}s" if "save_seconds" in item else "—"
        rows.append(
            f"| {name} | {item.get('restore', '—')} / {item.get('save', '—')} | "
            f"{restore_time} / {save_time} | "
            f"{size_text} | {compile_text} | {item.get('fresh', '—')} / {item.get('rebuilt', '—')} |"
        )
    for item in records:
        if item.get("kind") == "jobs":
            rows.extend(
                [
                    "",
                    f"Completed benchmark jobs in this workflow run: {item['jobs']}; "
                    f"elapsed {item['elapsed_seconds'] / 60:.2f} min; "
                    f"total runner time {item['runner_seconds'] / 60:.2f} min; "
                    f"binary artifact transfer steps {item['artifact_transfer_seconds']:.1f}s.",
                    "These job timings exclude queueing and report generation.",
                ]
            )
    rows.extend(
        [
            "",
            "Sizes are uncompressed cache contents. Times are observed work, not time saved.",
            "Fresh/rebuilt counts are Cargo compiler artifacts, not benchmark measurements.",
            "Executable reuse skips compilation; benchmark measurements still run fresh. Contended saves mean another job reserved the same entry.",
            "Cache keys, matched keys, save outcomes and available compressed sizes are in performance.jsonl.",
            "</details>\n",
        ]
    )
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts-dir", required=True)
    parser.add_argument("--reports", nargs="+", required=True)
    args = parser.parse_args()
    section = render(pathlib.Path(args.artifacts_dir))
    for name in args.reports:
        path = pathlib.Path(name)
        if path.exists():
            with path.open("a") as handle:
                handle.write(section)


if __name__ == "__main__":
    main()
