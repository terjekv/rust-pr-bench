## Gungraun Benchmark Report

Regression threshold: **3.00%**
PR: #12 • Latest: 2026-02-27 20:30 UTC • Head: deadbee

## Summary (Latest Run • deadbee)

| Feature Set | Improved | Regressions | Accepted | Neutral | Avg Δ (bench) | Avg Δ (metrics) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| alt-impl | 1 | 0 | 0 | 0 | -10.00% | -10.00% |
| default | 0 | 1 | 0 | 0 | +10.00% | +10.00% |


<details><summary><strong>alt-impl</strong></summary>

| Benchmark | Base | Head | Delta | Status |
| --- | ---: | ---: | ---: | --- |
| alt_path | 2,000 | 1,800 | -10.00% | 🟢 improved |

Metric-level breakdowns:

<details><summary>alt_path metric breakdown (1 metrics)</summary>

| Metric | Base | Head | Delta | Status |
| --- | ---: | ---: | ---: | --- |
| <span title="bench/alt/callgrind.out">callgrind.out</span> | 2,000 | 1,800 | -10.00% | 🟢 improved |

</details>


</details>

<details><summary><strong>default</strong></summary>

| Benchmark | Base | Head | Delta | Status |
| --- | ---: | ---: | ---: | --- |
| fast_path | 1,000 | 1,100 | +10.00% | 🔴 regression |

Metric-level breakdowns:

<details><summary>fast_path metric breakdown (1 metrics)</summary>

| Metric | Base | Head | Delta | Status |
| --- | ---: | ---: | ---: | --- |
| <span title="bench/default/callgrind.out">callgrind.out</span> | 1,000 | 1,100 | +10.00% | 🔴 regression |

</details>


</details>

### Unaccepted Regressions Above Threshold

- `default` / `fast_path`: +10.00%


## PR History (last 2 runs)

| Commit | Date (UTC) | Summary | Avg Δ (bench) | Avg Δ (metrics) | Regressions? |
| --- | --- | --- | ---: | ---: | --- |
| deadbee | 2026-02-27 20:30 UTC | 1 improved / 1 reg / 0 neutral | +0.00% | +0.00% | yes |
| abc1234 | 2026-02-26 19:00 UTC | 1 improved / 0 reg / 0 neutral | -5.00% | -5.00% | no |


<!-- gungraun-history: {"history":[{"backend":"gungraun","commit":"deadbeefcafebabe","run_at":"2026-02-27 20:30 UTC","pr_number":12,"summary":{"improved":1,"regressions":1,"accepted_regressions":0,"neutral":0},"avg_bench_delta_pct":0.0,"avg_metric_delta_pct":0.0,"has_regressions":true,"has_unaccepted_regressions":true},{"backend":"gungraun","commit":"abc1234","run_at":"2026-02-26 19:00 UTC","summary":{"improved":1,"regressions":0,"neutral":0},"avg_bench_delta_pct":-5.0,"avg_metric_delta_pct":-5.0,"has_regressions":false}]} -->
