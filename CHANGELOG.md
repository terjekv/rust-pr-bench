# Changelog

All notable changes to Rust PR Bench will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.1] - 2026-08-30

### Fixed

- Compare target-level totals using only metric identities present in both revisions, so adding or
  removing an independent Criterion function is reported as an unknown metric instead of a false
  aggregate performance regression.

## [1.1.0] - 2026-08-29

### Added

- Read Cargo benchmark target `required-features` during autodiscovery and enable them only for
  the target that declares them.
- Allow explicit benchmark specifications to declare `required_features` as a string or array.

### Changed

- Clarify why composite-action examples quote boolean and numeric inputs while reusable-workflow
  examples use typed YAML values.

### Fixed

- Prevent workspace benchmark targets from being skipped when another package's benchmark needs
  an opt-in Cargo feature.

## [1.0.0] - 2026-08-16

### Added

- License the project under the MIT License.
- Add a stable CI verification gate and a private vulnerability reporting policy.
- Add a root composite action for sequential Gungraun and Criterion base-versus-head comparisons.
- Add a parallel reusable workflow with precompiled benchmark artifacts and dynamic fan-out.
- Add sticky PR reports, regression outputs, configurable gates, and label-approved exceptions.
- Preserve exact runner interoperability with an `iai-callgrind 0.16.1` benchmark on an older
  revision without exposing legacy backend aliases in the new API.

### Changed

- Establish `terjekv/rust-pr-bench` as the new canonical project identity.
- Start a clean `v1` public API using only `gungraun`, `criterion`, and `all` backend values.
- Make pull-request comment updates best-effort when fork token permissions are read-only.

### Security

- Pin every external GitHub Action and CI container to an immutable commit or image digest.

### Fixed

- Resolve reusable-workflow helper checkouts from the called workflow's repository and commit,
  rather than from the consuming repository's pull-request revision.

### Removed

- Remove the deprecated `iai-callgrind`, `iai`, and `callgrind` public backend aliases.
- Remove `regression_threshold_pct_iai_callgrind` and the legacy compatibility workflow path.

## Project history

Rust PR Bench originated in
[`terjekv/github-action-iai-callgrind`](https://github.com/terjekv/github-action-iai-callgrind).
That repository retains the historical changelog and existing `v1`–`v3` compatibility releases.

[Unreleased]: https://github.com/terjekv/rust-pr-bench/compare/v1.1.1...HEAD
[1.1.1]: https://github.com/terjekv/rust-pr-bench/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/terjekv/rust-pr-bench/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/terjekv/rust-pr-bench/releases/tag/v1.0.0
