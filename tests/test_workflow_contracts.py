import re
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
        self.assertIn(
            "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7",
            metadata,
        )
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
        self.assertIn(
            "- name: Update pull request report\n      continue-on-error: true",
            metadata,
        )
        self.assertIn("inputs.comment_mode == 'always'", metadata)
        self.assertIn("steps.bench.outputs.should_fail == 'true'", metadata)

    def test_workflow_uses_called_workflow_identity(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/rust-pr-bench.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("job.workflow_repository", workflow)
        self.assertIn("job.workflow_sha", workflow)
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
        self.assertIn(
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
            precompile,
        )
        self.assertIn("strategy:", benchmark)
        self.assertIn("needs.prepare-matrix.outputs.matrix", benchmark)
        self.assertIn(
            "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
            benchmark,
        )
        self.assertIn("scripts/run_pair.py", benchmark)

    def test_workflow_keeps_old_runner_binary_interoperability(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/rust-pr-bench.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("scripts/iai-callgrind-runner-dispatch", workflow)
        self.assertIn("scripts/gungraun-runner-dispatch", workflow)
        self.assertIn(
            "taiki-e/install-action@288e746965032cfcc232e09af2daf5f23c14d780",
            workflow,
        )

    def test_workflow_pins_helpers_to_the_called_workflow_commit(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/rust-pr-bench.yml").read_text(
            encoding="utf-8"
        )
        repository_fallback = (
            "repository: ${{ inputs.action_repository != '' && "
            "inputs.action_repository || job.workflow_repository }}"
        )
        ref_fallback = (
            "ref: ${{ inputs.action_ref != '' && inputs.action_ref || "
            "job.workflow_sha }}"
        )

        self.assertEqual(workflow.count(repository_fallback), 4)
        self.assertEqual(workflow.count(ref_fallback), 4)
        self.assertIn('default: ""', workflow)
        self.assertNotIn("github.workflow_sha", workflow)
        self.assertNotIn("ref: ${{ inputs.action_ref }}", workflow)

        actionlint_config = (REPO_ROOT / ".github/actionlint.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            r'property "workflow_(repository|sha)" is not defined in object type',
            actionlint_config,
        )

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
        self.assertNotIn("action_repository:", workflow_job)
        self.assertNotIn("action_ref:", workflow_job)

        mixed = extract_job_block(sample, "workflow-mixed-runner-compatibility")
        self.assertIn(
            "action_repository: ${{ github.event.pull_request.head.repo.full_name }}",
            mixed,
        )
        self.assertIn("action_ref: ${{ github.event.pull_request.head.sha }}", mixed)

    def test_ci_lints_actions_and_checks_main_pushes(self) -> None:
        ci = (REPO_ROOT / ".github/workflows/ci.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("  push:\n    branches:\n      - main", ci)
        self.assertIn("  verify:\n    runs-on: ubuntu-latest", ci)
        self.assertIn("docker://rhysd/actionlint@sha256:", ci)
        self.assertIn('node-version: "24"', ci)

    def test_release_workflow_extracts_only_the_version_section(self) -> None:
        release = (REPO_ROOT / ".github/workflows/release.yml").read_text(
            encoding="utf-8"
        )
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        version = "1.1.0"
        pattern = re.compile(
            rf"^## \[{re.escape(version)}\] - [^\n]*\n(?P<body>.*?)(?=^## |\Z)",
            re.MULTILINE | re.DOTALL,
        )
        match = pattern.search(changelog)

        self.assertIn('- "v*.*.*"', release)
        self.assertIn("draft: true", release)
        self.assertIn(r"(?=^## |\Z)", release)
        self.assertIsNotNone(match)
        body = match.group("body").strip() if match else ""
        self.assertIn("Read Cargo benchmark target `required-features`", body)
        self.assertNotIn("Project history", body)

    def test_pr_comment_updates_are_best_effort(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/rust-pr-bench.yml").read_text(
            encoding="utf-8"
        )

        for step_name in (
            "Upsert Gungraun PR comment",
            "Upsert Criterion PR comment",
            "Upsert consolidated PR comment (backend=all)",
        ):
            self.assertIn(
                f"- name: {step_name}\n        continue-on-error: true", workflow
            )

    def test_external_actions_and_images_are_immutably_pinned(self) -> None:
        paths = [REPO_ROOT / "action.yml"]
        paths.extend(sorted((REPO_ROOT / ".github/workflows").glob("*.yml")))

        for path in paths:
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                stripped = line.strip()
                if stripped.startswith("- uses: "):
                    reference = stripped.removeprefix("- uses: ").split()[0]
                elif stripped.startswith("uses: "):
                    reference = stripped.removeprefix("uses: ").split()[0]
                else:
                    continue
                if reference.startswith("./"):
                    continue
                if reference.startswith("docker://"):
                    self.assertRegex(
                        reference,
                        r"^docker://[^@]+@sha256:[0-9a-f]{64}$",
                        f"{path}:{line_number}",
                    )
                else:
                    self.assertRegex(
                        reference,
                        r"^[^@]+@[0-9a-f]{40}$",
                        f"{path}:{line_number}",
                    )

    def test_sample_preserves_mixed_runner_comparison_coverage(self) -> None:
        sample = (REPO_ROOT / ".github/workflows/sample-self-test.yml").read_text(
            encoding="utf-8"
        )
        mixed = extract_job_block(sample, "workflow-mixed-runner-compatibility")

        self.assertIn('"base":{"bench":"sample_iai_callgrind_compat_bench"}', mixed)
        self.assertIn('"bench":"sample_callgrind_bench"', mixed)


if __name__ == "__main__":
    unittest.main()
