const {test} = require('node:test');
const assert = require('node:assert/strict');
const {summarize} = require('../scripts/job_performance');

test('separates elapsed time from total runner work and artifact transfer', () => {
  const step = (name, start, end) => ({name, started_at: start, completed_at: end});
  const start = '2026-01-01T00:00:00Z';
  const minute = '2026-01-01T00:01:00Z';
  const end = '2026-01-01T00:02:00Z';
  const jobs = [{status: 'completed', started_at: start, completed_at: end,
    steps: [step('Precompile benchmark executables', start, minute), step('Upload benchmark executables', minute, end)]},
  {status: 'completed', started_at: start, completed_at: end,
    steps: [step('Run benchmark pair', start, end)]},
  {status: 'in_progress', steps: [step('Run benchmark pair', start, end)]}];
  const summary = summarize(jobs);
  assert.equal(summary.jobs, 2);
  assert.equal(summary.elapsed_seconds, 120);
  assert.equal(summary.runner_seconds, 240);
  assert.equal(summary.artifact_transfer_seconds, 60);
});
