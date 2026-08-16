## Criterion Benchmark Report

Regression threshold: **10.00%**
Comparison statistic: **median** (ns)
PR: #12 • Latest: 2026-02-27 20:31 UTC • Head: feedfac

## Summary (Latest Run • feedfac)

| Feature Set | Improved | Regressions | Accepted | Neutral | Avg Δ (bench) | Avg Δ (metrics) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| default | 1 | 0 | 0 | 1 | -2.50% | -2.50% |


<details><summary><strong>default</strong></summary>

| Benchmark | Base | Head | Delta | Status |
| --- | ---: | ---: | ---: | --- |
| criterion_fast | 1,000 | 1,050 | +5.00% | 🟡 slight regression |
| criterion_slow | 2,000 | 1,800 | -10.00% | 🟢 improved |

Metric-level breakdowns:

<details><summary>criterion_fast metric breakdown (1 metrics)</summary>

| Metric | Base | Head | Delta | Status |
| --- | ---: | ---: | ---: | --- |
| <span title="workload/small">small</span> | 1,000 | 1,050 | +5.00% | 🟡 slight regression |

</details>

<details><summary>criterion_slow metric breakdown (1 metrics)</summary>

| Metric | Base | Head | Delta | Status |
| --- | ---: | ---: | ---: | --- |
| <span title="workload/medium">medium</span> | 2,000 | 1,800 | -10.00% | 🟢 improved |

</details>


</details>


## PR History (last 2 runs)

| Commit | Date (UTC) | Summary | Avg Δ (bench) | Avg Δ (metrics) | Regressions? |
| --- | --- | --- | ---: | ---: | --- |
| feedfac | 2026-02-27 20:31 UTC | 1 improved / 0 reg / 1 neutral | -2.50% | -2.50% | no |
| abc1234 | 2026-02-26 19:00 UTC | 0 improved / 0 reg / 2 neutral | +0.50% | +0.50% | no |


<!-- criterion-history: {"history":[{"backend":"criterion","commit":"feedfacecafebeef","run_at":"2026-02-27 20:31 UTC","pr_number":12,"summary":{"improved":1,"regressions":0,"accepted_regressions":0,"neutral":1},"avg_bench_delta_pct":-2.5,"avg_metric_delta_pct":-2.5,"has_regressions":false,"has_unaccepted_regressions":false},{"backend":"criterion","commit":"abc1234","run_at":"2026-02-26 19:00 UTC","summary":{"improved":0,"regressions":0,"neutral":2},"avg_bench_delta_pct":0.5,"avg_metric_delta_pct":0.5,"has_regressions":false}]} -->
