import unittest

from testlib import REPO_ROOT


def extract_job_block(workflow_text: str, job_name: str) -> str:
    lines = workflow_text.splitlines()
    in_jobs = False
    collecting = False
    block: list[str] = []

    for line in lines:
        if not in_jobs:
            if line == "jobs:":
                in_jobs = True
            continue
        if not collecting:
            if line == f"  {job_name}:":
                collecting = True
                block.append(line)
            continue
        if line.startswith("  ") and not line.startswith("    "):
            break
        block.append(line)

    if not block:
        raise AssertionError(f"job '{job_name}' not found")
    return "\n".join(block)


class WorkflowContractTests(unittest.TestCase):
    def test_root_metadata_defines_the_marketplace_action(self) -> None:
        metadata = (REPO_ROOT / "action.yml").read_text(encoding="utf-8")

        self.assertIn("name: Rust PR Bench", metadata)
        self.assertIn("  using: composite", metadata)
        self.assertIn("branding:", metadata)
        self.assertIn("scripts/action_entrypoint.py", metadata)
        self.assertIn("actions/setup-python@v7", metadata)
        self.assertIn("has_regressions:", metadata)
        self.assertIn("has_unaccepted_regressions:", metadata)
        self.assertIn("had_errors:", metadata)
        self.assertNotIn("regression_threshold_pct_iai_callgrind", metadata)
        self.assertNotIn("backend: iai-callgrind", metadata)

    def test_root_action_loads_history_runs_and_comments(self) -> None:
        metadata = (REPO_ROOT / "action.yml").read_text(encoding="utf-8")

        self.assertIn("helper.loadHistory", metadata)
        self.assertIn("helper.loadPullRequestMetadata", metadata)
        self.assertIn("helper.upsertReport", metadata)
        self.assertIn("inputs.comment_mode == 'always'", metadata)
        self.assertIn("steps.bench.outputs.should_fail == 'true'", metadata)

    def test_workflow_uses_new_repository_identity(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/rust-pr-bench.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn('default: "terjekv/rust-pr-bench"', workflow)
        self.assertNotIn("github-action-iai-callgrind", workflow)
        self.assertNotIn("regression_threshold_pct_iai_callgrind", workflow)
        self.assertFalse(
            (REPO_ROOT / ".github/workflows/iai-callgrind-pr-bench.yml").exists()
        )

    def test_report_job_directly_depends_on_prepare_matrix(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/rust-pr-bench.yml").read_text(
            encoding="utf-8"
        )
        report = extract_job_block(workflow, "report")

        self.assertIn("    needs:", report)
        self.assertIn("      - prepare-matrix", report)
        self.assertIn("      - benchmark", report)
        self.assertIn("    if: always() && needs.prepare-matrix.result == 'success'", report)

    def test_workflow_precompiles_and_fans_out_benchmarks(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/rust-pr-bench.yml").read_text(
            encoding="utf-8"
        )
        precompile = extract_job_block(workflow, "precompile")
        benchmark = extract_job_block(workflow, "benchmark")

        self.assertIn("scripts/precompile_benchmarks.py", precompile)
        self.assertIn("actions/upload-artifact@v7", precompile)
        self.assertIn("strategy:", benchmark)
        self.assertIn("needs.prepare-matrix.outputs.matrix", benchmark)
        self.assertIn("actions/download-artifact@v8", benchmark)
        self.assertIn("scripts/run_pair.py", benchmark)

    def test_workflow_keeps_old_runner_binary_interoperability(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/rust-pr-bench.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("scripts/iai-callgrind-runner-dispatch", workflow)
        self.assertIn("scripts/gungraun-runner-dispatch", workflow)
        self.assertIn("taiki-e/install-action@v2", workflow)

    def test_workflow_pins_helpers_to_the_called_workflow_commit(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/rust-pr-bench.yml").read_text(
            encoding="utf-8"
        )
        fallback = (
            "ref: ${{ inputs.action_ref != '' && inputs.action_ref || "
            "github.workflow_sha }}"
        )

        self.assertEqual(workflow.count(fallback), 4)
        self.assertNotIn("ref: ${{ inputs.action_ref }}", workflow)

    def test_workflow_wires_thresholds_and_regression_exceptions(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/rust-pr-bench.yml").read_text(
            encoding="utf-8"
        )
        report = extract_job_block(workflow, "report")

        self.assertIn("regression_threshold_pct_gungraun:", workflow)
        self.assertIn("regression_threshold_pct_criterion:", workflow)
        self.assertIn("scripts/resolve_threshold.py", report)
        self.assertIn("loadPullRequestMetadata", report)
        self.assertIn("scripts/regression_overrides.py", report)
        self.assertIn("has_unaccepted_regressions:", workflow)
        self.assertIn(
            "inputs.fail_on_regression && steps.render_overall.outputs.has_unaccepted_regressions == 'true'",
            report,
        )

    def test_sample_workflow_exercises_both_public_interfaces(self) -> None:
        sample = (REPO_ROOT / ".github/workflows/sample-self-test.yml").read_text(
            encoding="utf-8"
        )
        action_job = extract_job_block(sample, "action-sample-all")
        workflow_job = extract_job_block(sample, "workflow-sample-all")

        self.assertIn("fetch-depth: 0", action_job)
        self.assertIn("uses: ./", action_job)
        self.assertIn("steps.bench.outputs.report_path", action_job)
        self.assertIn("uses: ./.github/workflows/rust-pr-bench.yml", workflow_job)
        self.assertIn(
            "action_repository: ${{ github.event.pull_request.head.repo.full_name }}",
            workflow_job,
        )

    def test_sample_workflow_lints_actions_and_checks_main_pushes(self) -> None:
        sample = (REPO_ROOT / ".github/workflows/sample-self-test.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("  push:\n    branches:\n      - main", sample)
        self.assertIn("docker://rhysd/actionlint:1.7.12", sample)
        self.assertEqual(sample.count("if: github.event_name == 'pull_request'"), 3)

    def test_sample_preserves_mixed_runner_comparison_coverage(self) -> None:
        sample = (REPO_ROOT / ".github/workflows/sample-self-test.yml").read_text(
            encoding="utf-8"
        )
        mixed = extract_job_block(sample, "workflow-mixed-runner-compatibility")

        self.assertIn('"base":{"bench":"sample_iai_callgrind_compat_bench"}', mixed)
        self.assertIn('"bench":"sample_callgrind_bench"', mixed)


if __name__ == "__main__":
    unittest.main()
