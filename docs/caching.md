# Build caching and performance

Both interfaces cache Cargo downloads, Cargo build outputs, and exact-version
Gungraun/IAI runner installations by default. Cargo still runs after a cache
restore and decides which artifacts are fresh. Every benchmark measurement,
including base measurements, runs again. The project's build profile, LTO,
optimization settings and compiler flags are preserved.

| Input | Default | Effect |
|---|---|---|
| `cache` | `true` | Restore and, when permitted, save persistent caches. |
| `cache_save` | `true` | Allow elected writers to save. Set `false` for restore-only CI. |
| `cache_namespace` | `default` | Partition caches, or share compatible entries with other jobs in this repository. |
| `compile_only` | `false` | Build the head revision without running benchmarks or lifecycle hooks. A base revision is unnecessary. |

The composite action also exposes `performance_path`, a JSON file containing its
build/cache observations. Cache failures are reported and builds continue.
Outside GitHub Actions the composite entrypoint still reuses compilation locally;
persistent caching is reported as unavailable.

## Warm caches on main

Add a build on `push` to the default branch. PR-created caches belong to that
PR's merge ref; checking out main inside a PR job does **not** move its cache to
main's scope. A main-branch warming build makes entries available to subsequent
PRs. See [GitHub's cache scope rules](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching#restrictions-for-accessing-a-cache).

```yaml
name: Warm benchmark dependencies
on:
  push:
    branches: [main]
  workflow_dispatch:
permissions:
  contents: read
  pull-requests: write
jobs:
  warm:
    uses: terjekv/rust-pr-bench/.github/workflows/rust-pr-bench.yml@v1
    with:
      backend: criterion
      benchmarks_json: '["my_benchmark"]'
      cache_namespace: ci-bench
      compile_only: true
      comment_mode: never
```

Use the same namespace, toolchain, working directory, benchmark specifications,
feature sets and Cargo arguments in the PR workflow. `compile_only` requires
separable, portable `cargo bench` commands; it fails when requested cases cannot
be precompiled. Full custom commands retain their original execution behavior
in ordinary comparisons.

The warming build can instead be a step in the caller's existing main CI job:

```yaml
- uses: actions/checkout@v7
  with:
    fetch-depth: 0
- uses: terjekv/rust-pr-bench@v1
  with:
    backend: criterion
    benchmarks_json: '["my_benchmark"]'
    cache_namespace: ci-bench
    compile_only: "true"
    comment_mode: never
```

These examples require a release containing the caching inputs; when evaluating
an unreleased change, pin both interfaces to its commit.

## What makes builds compatible

Keys include the cache schema and namespace, resolved `rustc -vV`, OS,
architecture, runner image, effective Cargo commands (features, target, profile,
package/manifest selection and build flags), compiler environment and Cargo
configuration. The dependency suffix hashes manifests, lockfiles and toolchain
files from the selected revision. There is no source commit hash in dependency
keys. A lockfile change can restore an older entry within the same build
configuration; Cargo validates and rebuilds changed dependencies.

Build targets live at
`$GITHUB_WORKSPACE/.rust-pr-bench-cache/<namespace-hash>/target`, shared by
compatible sequential builds. Both interfaces use the same archive paths.
GitHub's [cache version](https://github.com/actions/cache#cache-version) includes
paths and compression, so using the same textual namespace with an unrelated
`target/` or `Swatinem/rust-cache` configuration does not automatically share an
entry. Ordinary debug CI artifacts also do not replace optimized benchmark
dependencies. Use the warming interface above to populate compatible entries.

Cargo downloads are cached separately from compiled outputs. Credentials,
benchmark measurements and service state are excluded. Compiled target caching
is disabled for `target-cpu=native`, including inherited Cargo configuration.
The reusable workflow builds those benchmarks on their execution runners. The
composite action may precompile them locally because compilation and execution
stay on the same runner.

The composite action compiles cases grouped by revision before measuring. Each
case keeps its own measurement directory and its existing setup/readiness/
teardown lifecycle. Executables and discovered runtime binaries/libraries are
copied for each side, and activated immediately before that side runs. The
reusable workflow retains its existing artifact distribution and fallback to
in-job compilation when an executable is unavailable.

## Save policy

Within a reusable-workflow matrix, head owns each build entry. Base saves only
when its dependency/build identity differs from head's. Feature groups have
separate entries. The first build group owns Cargo download saves, and the first
Gungraun measurement case owns runner saves. Other jobs restore only. The
composite action applies the same build policy sequentially and can save the
runner versions it uses. Versions only exercised by other reusable-workflow
cases may need a dedicated warming comparison to populate their runner entry.

Separate simultaneous workflow runs can still try to create the same entry;
GitHub's cache reservation arbitrates this without failing the benchmark. Set
`cache_save: false` in read-only consumers if a designated main CI job owns all
writes. A save denied by the service is recorded rather than treated as a saved
entry. Caches remain scoped to the caller repository; unrelated repositories
do not share physical GitHub cache storage.

## Evaluate the benefit

Reports have a collapsible build/cache table. It distinguishes exact restores,
fallback restores, misses, errors, disabled caching and local reuse. It includes
restore/save outcomes and durations, uncompressed cache contents, compilation
time and fresh/rebuilt Cargo compiler-artifact counts. Raw `performance.jsonl`
files also contain requested/matched keys, hashes of individual build-key components,
and compressed archive size when the
toolkit reports it. These files accompany reusable-workflow report artifacts.
When the job API is accessible, reusable reports also show elapsed time and
summed runner time for completed benchmark jobs in the workflow run, plus
binary-artifact transfer step durations. These exclude queueing and report
creation; if the caller invokes several benchmark workflows, this timing summary
covers their completed benchmark jobs together.

Treat a cache hit followed by widespread rebuilding as a diagnostic result,
not a successful optimization. Compare:

1. A cold build, including cache upload costs.
2. A repeat build with unchanged dependencies.
3. A new PR restoring a main-branch cache.
4. A dependency, feature or build-flag change.

Compare elapsed workflow time and total runner minutes as well as compilation
time. Cache transfer can outweigh compilation savings for small projects.
`Cache Self-Test` exercises a cold save and a restore in a separate job;
the repository's main workflow warms the sample project's benchmark cache.

Complete executable reuse across runs remains a separate optimization: a Cargo
target cache is **not** used to bypass Cargo or execute an unchecked historical
baseline. That would require a source/build identity and a complete runtime-file
manifest, including project-specific generated assets and external inputs.
