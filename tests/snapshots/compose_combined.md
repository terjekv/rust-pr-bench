# Rust PR Bench Report

PR: #12 • Latest: 2026-02-27 20:30 UTC • Head: deadbee


## Gungraun

Regression threshold: **3.00%**

### Summary (Latest Run • deadbee)

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

#### Unaccepted Regressions Above Threshold

- `default` / `fast_path`: +10.00%


### PR History (last 1 runs)

| Commit | Date (UTC) | Summary | Avg Δ (bench) | Avg Δ (metrics) | Regressions? |
| --- | --- | --- | ---: | ---: | --- |
| deadbee | 2026-02-27 20:30 UTC | 1 improved / 1 reg / 0 neutral | +0.00% | +0.00% | yes |


<!-- gungraun-history: {"history":[{"backend":"gungraun","commit":"deadbeefcafebabe","run_at":"2026-02-27 20:30 UTC","pr_number":12,"summary":{"improved":1,"regressions":1,"accepted_regressions":0,"neutral":0},"avg_bench_delta_pct":0.0,"avg_metric_delta_pct":0.0,"has_regressions":true,"has_unaccepted_regressions":true}]} -->

## Criterion

Regression threshold: **10.00%**
Comparison statistic: **median** (ns)

### Summary (Latest Run • deadbee)

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


### PR History (last 1 runs)

| Commit | Date (UTC) | Summary | Avg Δ (bench) | Avg Δ (metrics) | Regressions? |
| --- | --- | --- | ---: | ---: | --- |
| deadbee | 2026-02-27 20:30 UTC | 1 improved / 0 reg / 1 neutral | -2.50% | -2.50% | no |


<!-- criterion-history: {"history":[{"backend":"criterion","commit":"deadbeefcafebabe","run_at":"2026-02-27 20:30 UTC","pr_number":12,"summary":{"improved":1,"regressions":0,"accepted_regressions":0,"neutral":1},"avg_bench_delta_pct":-2.5,"avg_metric_delta_pct":-2.5,"has_regressions":false,"has_unaccepted_regressions":false}]} -->
