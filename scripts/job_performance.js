const fs = require('node:fs');

function seconds(start, end) {
  return Math.max(0, (Date.parse(end) - Date.parse(start)) / 1000) || 0;
}

function summarize(jobs) {
  const completed = jobs.filter(job => job.status === 'completed' && job.steps?.some(step =>
    ['Precompile benchmark executables', 'Run benchmark pair'].includes(step.name)));
  if (!completed.length) return null;
  return {
    kind: 'jobs',
    label: 'Completed Rust PR Bench jobs in this workflow run',
    jobs: completed.length,
    elapsed_seconds: seconds(
      new Date(Math.min(...completed.map(job => Date.parse(job.started_at)))).toISOString(),
      new Date(Math.max(...completed.map(job => Date.parse(job.completed_at)))).toISOString()),
    runner_seconds: completed.reduce((sum, job) => sum + seconds(job.started_at, job.completed_at), 0),
    artifact_transfer_seconds: completed.flatMap(job => job.steps).filter(step =>
      /^(Upload benchmark executables|Download precompiled (head|base) benchmark)$/.test(step.name)
    ).reduce((sum, step) => sum + seconds(step.started_at, step.completed_at), 0),
  };
}

async function collect({github, context, core, output}) {
  try {
    const jobs = await github.paginate(github.rest.actions.listJobsForWorkflowRun, {
      ...context.repo, run_id: context.runId, filter: 'latest', per_page: 100
    });
    const summary = summarize(jobs);
    if (summary) fs.appendFileSync(output, JSON.stringify(summary) + '\n');
  } catch {
    core.info('Job/transfer timings unavailable; build and cache observations remain available.');
  }
}

module.exports = {summarize, collect};
