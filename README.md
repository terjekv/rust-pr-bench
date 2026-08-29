# Rust PR Bench

Rust PR Bench compares Rust benchmark results between a base and head revision using
[Gungraun](https://gungraun.github.io/gungraun/),
[Criterion](https://bheisler.github.io/criterion.rs/book/), or both. It produces a job summary,
can maintain a sticky pull-request report, and can fail CI when a regression exceeds a configured
threshold.

This repository is the successor to
[`terjekv/github-action-iai-callgrind`](https://github.com/terjekv/github-action-iai-callgrind).
The original repository remains available for existing `v1`–`v3` reusable-workflow consumers.

## Choose an interface

Rust PR Bench provides two interfaces backed by the same comparison and reporting code:

- The root action is the simplest interface and is suitable for GitHub Marketplace. It runs all
  selected benchmark cases sequentially in one caller-owned job.
- The reusable workflow precompiles and fans benchmark cases out across a dynamic job matrix. Use
  it for larger suites where parallel execution is worth the additional jobs and artifacts.

Input syntax differs between the interfaces:

- Root composite-action inputs are strings. Quote booleans and numbers, such as `"true"` and
  `"3"`.
- Reusable-workflow inputs use the types declared by `workflow_call`. Write booleans and numbers
  without quotes, such as `true` and `3`.

## Root action

The action requires an Ubuntu runner and a full checkout so both revisions are available. The
caller controls job permissions; `pull-requests: write` is needed only when PR comments are enabled.
Comment updates are best-effort: fork pull requests normally receive a read-only token, so the
report remains available in the job summary when GitHub does not permit the action to update the PR.

```yaml
name: Rust PR Bench

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write

jobs:
  bench:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0

      - name: Compare benchmarks
        id: bench
        uses: terjekv/rust-pr-bench@v1
        with:
          # Composite-action inputs are strings.
          backend: all
          auto_discover: "true"
          regression_threshold_pct_gungraun: "3"
          regression_threshold_pct_criterion: "10"
          fail_on_regression: "true"
```

## Parallel reusable workflow

Call the reusable workflow directly from a job. The workflow owns its Ubuntu jobs, matrix,
precompiled benchmark artifacts, report artifact, and PR comment.

```yaml
name: Rust PR Bench

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write

jobs:
  bench:
    uses: terjekv/rust-pr-bench/.github/workflows/rust-pr-bench.yml@v1
    with:
      # Reusable-workflow booleans and numbers are typed values.
      backend: all
      auto_discover: true
      feature_sets_json: >-
        [
          {"name":"default","features":""},
          {"name":"simd","features":"simd"}
        ]
      regression_threshold_pct_gungraun: 3
      regression_threshold_pct_criterion: 10
      fail_on_regression: true
```

## Features

- Compares pull-request head and base revisions using isolated Git worktrees.
- Supports Gungraun instruction/event counts, Criterion wall-clock measurements, or both.
- Discovers standalone and workspace benchmark targets or accepts explicit commands.
- Tests multiple Cargo feature sets and honors benchmark target `required-features`.
- Forms target-level deltas only from metric identities present in both revisions; added or removed
  benchmark functions remain visible as unknown metrics without skewing the aggregate.
- Supports benchmarks moved between workspace members.
- Selects the exact Gungraun runner required by each benchmark executable.
- Can execute an `iai-callgrind 0.16.1` benchmark from an older base revision during migration.
- Publishes Markdown summaries and sticky PR comments with bounded history.
- Exposes raw and unaccepted regression signals separately.
- Supports label-approved, auditable exceptions for intentional regressions.

## Inputs

The action and reusable workflow share the inputs below. As noted above, action values are strings;
reusable-workflow booleans and numbers use their native YAML types.

| Input | Default | Description |
| --- | --- | --- |
| `backend` | `gungraun` | `gungraun`, `criterion`, or `all` |
| `benchmarks_json` | `[]` | Explicit benchmark specifications |
| `auto_discover` | `true` | Discover `benches/*.rs` when no explicit specifications are supplied |
| `auto_detect_moved_benchmarks` | `false` | Pair uniquely moved workspace benchmarks across revisions |
| `feature_sets_json` | default feature set | Feature combinations to test |
| `working_directory` | `.` | Cargo project or workspace directory |
| `toolchain` | `stable` | Rust toolchain |
| `cargo_args` | empty | Extra Cargo arguments |
| `criterion_cli_args` | `--noplot` | Criterion bench-binary arguments |
| `criterion_statistic` | `mean` | `mean` or `median` |
| `base_sha` | PR base SHA | Explicit base revision |
| `head_sha` | PR head SHA | Root-action-only explicit head revision |
| `regression_threshold_pct` | `3` | Generic regression threshold |
| `regression_threshold_pct_gungraun` | `-1` | Gungraun override; `-1` uses the generic threshold |
| `regression_threshold_pct_criterion` | `-1` | Criterion override; `-1` uses the generic threshold |
| `fail_on_regression` | `false` | Fail for an unaccepted regression above its threshold |
| `regression_override_label` | empty | Label required to approve PR-body exceptions |
| `comment_mode` | `always` | `always`, `on-regression`, or `never` |

The reusable workflow resolves its helper scripts from the exact called-workflow commit. It also
exposes `action_repository` and `action_ref` overrides for testing a fork or pull-request revision
of the workflow implementation.

## Outputs

Both interfaces expose:

- `has_regressions`: at least one measurement exceeded its configured threshold, including an
  accepted regression.
- `has_unaccepted_regressions`: at least one threshold regression lacked an approved exception.

The root action additionally exposes:

- `had_errors`: at least one benchmark command failed.
- `report_path`: absolute path to the generated Markdown report for later workflow steps.

## Benchmark configuration

When `benchmarks_json` is empty and `auto_discover` is enabled, benchmark filenames route cases:

- A name containing `gungraun`, `iai_callgrind`, or `callgrind`, but not `criterion`, selects
  Gungraun.
- A name containing `criterion`, but no Callgrind-family marker, selects Criterion.
- Other names run for every selected backend.

Explicit entries may be strings or objects:

```yaml
benchmarks_json: >-
  [
    {"name":"parser-events","bench":"parser_gungraun","backend":"gungraun"},
    {
      "name":"parser-time",
      "bench":"parser_criterion",
      "backend":"criterion",
      "criterion_args":"--noplot --sample-size 80 --measurement-time 6"
    }
  ]
```

Object fields include:

- `name`: report label.
- `bench`: Cargo benchmark target.
- `backend`: `gungraun` or `criterion`.
- `command`: complete command override.
- `manifest_path`, `package`, and `args`: Cargo command helpers.
- `required_features`: benchmark-specific Cargo features that are always enabled.
- `criterion_args`: per-benchmark Criterion arguments.
- `head`, `base`, `head_command`, and `base_command`: explicit mappings when a benchmark moved or
  changed names.

For example, a Gungraun benchmark can compare against an older IAI-Callgrind target without making
IAI-Callgrind part of the new public backend API:

```json
[
  {
    "name": "parser-migration",
    "bench": "parser_gungraun",
    "backend": "gungraun",
    "base": {"bench": "parser_iai_callgrind"}
  }
]
```

## Feature sets

`feature_sets_json` is an array of names or objects:

```json
[
  {"name":"default","features":""},
  {"name":"simd","features":"simd"},
  {"name":"minimal","features":"serde","no_default_features":true}
]
```

Autodiscovery reads `required-features` from each Cargo `[[bench]]` target and combines them with
the selected feature set. Declare target-specific requirements there instead of placing them in a
global feature set, which applies to every discovered benchmark. Workspace members without those
target requirements are then left unchanged. Explicit benchmark objects can provide the equivalent
`required_features` string or array.

## Intentional regression exceptions

Set `regression_override_label` to require an explicit approval label. A PR can then declare
bounded exceptions in one fenced block:

````markdown
```rust-pr-bench
{
  "accept_regressions": [
    {
      "benchmark": "verify_password",
      "backend": "gungraun",
      "feature": "constant-time",
      "max_regression_pct": 25,
      "reason": "Constant-time verification"
    }
  ]
}
```
````

The approval label must have been applied after the most recent PR-body edit. Editing the directive
invalidates an older approval. Reports always show the measured regression; approval only changes
whether it is considered unaccepted by the CI gate.

## Migrating from `github-action-iai-callgrind`

Existing callers may remain on the original repository and its `v1`–`v3` tags. Migration is
explicit because GitHub Actions does not follow repository-rename redirects.

For the parallel workflow, change only the repository and major version first:

```diff
- uses: terjekv/github-action-iai-callgrind/.github/workflows/rust-pr-bench.yml@v3
+ uses: terjekv/rust-pr-bench/.github/workflows/rust-pr-bench.yml@v1
```

Then replace deprecated public values:

- `backend: iai-callgrind`, `iai`, or `callgrind` → `backend: gungraun`
- `regression_threshold_pct_iai_callgrind` → `regression_threshold_pct_gungraun`

The original compatibility workflow path does not exist in this repository. Use
`.github/workflows/rust-pr-bench.yml` or migrate to the root action.

## Development

Run the local tests with:

```bash
actionlint
python3 -m unittest discover -s tests -v
node --test tests/test_github_pr_comment.js
cargo clippy --manifest-path examples/sample-rust-app/Cargo.toml \
  --all-targets --all-features -- -D warnings
```

The sample application in `examples/sample-rust-app` exercises Gungraun, Criterion, mixed runner
versions, both public interfaces, and regression outputs in CI.

## License

Rust PR Bench is licensed under the [MIT License](LICENSE).
