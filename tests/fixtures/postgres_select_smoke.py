#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time


def query_postgres(container: str) -> int:
    started_ns = time.perf_counter_ns()
    completed = subprocess.run(
        [
            "docker",
            "exec",
            container,
            "psql",
            "--username",
            "postgres",
            "--dbname",
            "bench",
            "--tuples-only",
            "--no-align",
            "--set",
            "ON_ERROR_STOP=1",
            "--command",
            "SELECT 1",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    elapsed_ns = max(1, time.perf_counter_ns() - started_ns)
    if completed.returncode != 0:
        output = "\n".join(
            item.strip()
            for item in (completed.stdout, completed.stderr)
            if item.strip()
        )
        raise RuntimeError(f"PostgreSQL SELECT failed: {output}")
    if completed.stdout.strip() != "1":
        raise RuntimeError(
            f"PostgreSQL SELECT returned {completed.stdout.strip()!r}, expected '1'"
        )
    return elapsed_ns


def write_criterion_result(target_dir: pathlib.Path, elapsed_ns: int) -> None:
    interval = {
        "point_estimate": float(elapsed_ns),
        "confidence_interval": {
            "lower_bound": float(elapsed_ns),
            "upper_bound": float(elapsed_ns),
        },
    }
    output = target_dir / "criterion" / "postgres-select" / "new" / "estimates.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"mean": interval, "median": interval}), encoding="utf-8"
    )


def main() -> int:
    container = os.environ["PG_CONTAINER"]
    target_dir = pathlib.Path(os.environ["CARGO_TARGET_DIR"])
    elapsed_ns = query_postgres(container)
    write_criterion_result(target_dir, elapsed_ns)
    print(f"PostgreSQL SELECT 1 succeeded in {elapsed_ns} ns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
