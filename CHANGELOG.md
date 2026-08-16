# Changelog

All notable changes to Rust PR Bench will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Add a root composite action for sequential Gungraun and Criterion base-versus-head comparisons.
- Add a parallel reusable workflow with precompiled benchmark artifacts and dynamic fan-out.
- Add sticky PR reports, regression outputs, configurable gates, and label-approved exceptions.
- Preserve exact runner interoperability with an `iai-callgrind 0.16.1` benchmark on an older
  revision without exposing legacy backend aliases in the new API.

### Changed

- Establish `terjekv/rust-pr-bench` as the new canonical project identity.
- Start a clean `v1` public API using only `gungraun`, `criterion`, and `all` backend values.

### Removed

- Remove the deprecated `iai-callgrind`, `iai`, and `callgrind` public backend aliases.
- Remove `regression_threshold_pct_iai_callgrind` and the legacy compatibility workflow path.

## Project history

Rust PR Bench originated in
[`terjekv/github-action-iai-callgrind`](https://github.com/terjekv/github-action-iai-callgrind).
That repository retains the historical changelog and existing `v1`–`v3` compatibility releases.
