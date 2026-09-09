# Build caching and performance

Both interfaces cache Cargo downloads, Cargo build outputs, and exact-version
Gungraun/IAI runner installations by default. Cargo still runs after a cache
restore and decides which artifacts are fresh, unless opt-in executable reuse
finds a verified exact match. Every benchmark measurement,
including base measurements, runs again. The project's build profile, LTO,
optimization settings and compiler flags are preserved.

| Input | Default | Effect |
|---|---|---|
| `cache` | `true` | Restore and, when permitted, save persistent caches. |
| `cache_save` | `true` | Allow elected writers to save. Set `false` for restore-only CI. |
| `cache_namespace` | `default` | Partition caches, or share compatible entries with other jobs in this repository. |
| `cache_binaries` | `false` | Reuse exact executables across runs; requires reproducible builds. |
| `binary_cache_key` | empty | Additional identity for external build inputs and custom environment. |
| `binary_cache_paths` | `[]` | JSON array of repository-relative runtime files/directories to bundle. |
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

## Reuse complete executables

Set `cache_binaries: true` in **both** the warming build and the comparison:

```yaml
with:
  cache_namespace: ci-bench
  cache_binaries: true
  # Version inputs that the action cannot discover automatically:
  binary_cache_key: native-libraries-v3
  # Optional generated assets outside Cargo's target directory:
  binary_cache_paths: '["generated/benchmark-data"]'
```

On an exact hit, the precompile step restores a bundle, verifies it, and skips
Cargo and linking entirely. It also skips Cargo download/target-cache transfers.
The reusable workflow still distributes executables through its normal artifacts.
Both revisions run fresh measurements, including setup/readiness/teardown hooks.
`compile_only` continues to skip measurements and hooks intentionally.

The executable identity includes the checked-out commit, tracked source contents,
tags, dependency/build identity, full compile commands and selected benchmark cases,
Cargo version, Rust sysroot, source/target/Cargo-home paths, runner-dispatch paths,
bundle implementation, declared runtime paths, and `binary_cache_key`. Build
identity already covers resolved rustc, OS/architecture, runner image, features,
profiles, compiler flags and Cargo configuration. There are no restore prefixes;
even a partial match of the primary key is rejected. Base and head use the same
key for the same source/build identity, so an unchanged baseline can reuse a
previous head build. Distinct revisions own distinct entries; head owns equal
base/head pairs. `cache` and `cache_save` apply to executable caches too.

A bundle contains benchmark executables, Cargo-reported helper binaries and shared
libraries, complete build-script `OUT_DIR` trees, and declared runtime assets.
Each file has a relative path, size, SHA-256 checksum and executable-bit marker.
The complete file set and identity are checked after restore and again before
execution, including after artifact distribution. Empty directories and execute
bits are restored when artifact transport drops them. Missing, changed, unexpected,
unsafe or unsupported files reject the bundle and fall back to compilation.
Cache checksums detect incomplete/corrupt bundles; they do not replace GitHub's
cache access controls or attest to the publisher's trustworthiness.

The composite action uses a stable private worktree inside the namespace's cache
directory so embedded `CARGO_MANIFEST_DIR`, `OUT_DIR` and helper paths remain valid.
It removes the worktree after execution and protects it against simultaneous use
on the same runner. Use distinct namespaces for concurrent actions on one runner.
The reusable workflow already has a stable checkout path. Different checkout
layouts, runner images or toolchain paths deliberately produce different executable
keys; sharing dependency caches does not guarantee sharing executable bundles.

This option assumes reproducible compilation. Build scripts, proc macros and native
tools can read arbitrary files, environment variables, system libraries, clocks,
or network data. The action cannot infer all of those inputs. Include their
versions/content hashes in `binary_cache_key`, including custom compile-time
variables and self-hosted runner images/tool installations. If a build intentionally
embeds a timestamp or random value that must change every run, leave executable
reuse disabled. Changing runtime-only measurement settings does not require a
new executable key.

`binary_cache_paths` entries are relative to the repository root (not
`working_directory`). They must exist after compilation, before lifecycle setup;
they may name regular files or directories, without globbing. They are restored
to the same source paths separately before each side's setup hook. Include only
runtime assets: never secrets, measurement output, or mutable service state.
Cargo's generated `OUT_DIR` files are bundled automatically. See the
[Cargo build-script contract](https://doc.rust-lang.org/cargo/reference/build-scripts.html#outputs-of-the-build-script).

A committed lockfile and clean tracked sources are required. Submodules, external
path dependencies/source symlinks, Cargo path overrides, native CPU builds and
unsupported runtime links/files disable reuse. Missing declared assets prevent a
save. Inputs are checked again after compilation; a changed lockfile or generated
tracked source is never published under the earlier identity. Projects with these
constraints retain the ordinary compilation path and dependency caches.

The report labels successful hits `skipped (N executables reused)` and records
`rejected` or `ineligible` outcomes with reasons in `performance.jsonl`. Old build
timings and benchmark results are never replayed from a bundle. A `contended` save
means another job reserved the entry; `error` denotes another cache-service failure.
The executable self-tests cover cold/warm jobs through both public interfaces,
including fresh Gungraun and Criterion measurements after compilation is skipped.
