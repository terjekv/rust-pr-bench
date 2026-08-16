#!/usr/bin/env python3
import argparse
import math


UNSET = -1.0


def resolve_gungraun_threshold(
    generic: float,
    gungraun_specific: float = UNSET,
) -> float:
    values = {
        "regression_threshold_pct": float(generic),
        "regression_threshold_pct_gungraun": float(gungraun_specific),
    }
    for name, value in values.items():
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
        if name == "regression_threshold_pct" and value < 0:
            raise ValueError(f"{name} must be non-negative")
        if name != "regression_threshold_pct" and value < 0 and value != UNSET:
            raise ValueError(f"{name} must be non-negative or -1")

    specific = values["regression_threshold_pct_gungraun"]
    if specific != UNSET:
        return specific
    return values["regression_threshold_pct"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generic", required=True, type=float)
    parser.add_argument("--gungraun", default=UNSET, type=float)
    args = parser.parse_args()

    try:
        threshold = resolve_gungraun_threshold(
            args.generic,
            args.gungraun,
        )
    except ValueError as error:
        parser.error(str(error))
    print(f"{threshold:g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
