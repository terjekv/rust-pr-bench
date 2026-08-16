const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const assert = require("node:assert/strict");

const helper = require("../scripts/github_pr_comment");

function makeGithub(
  comments,
  pullRequest = { body: "", labels: [] },
  timeline = [],
  lastEditedAt = null,
) {
  const state = {
    comments,
    pullRequest,
    timeline,
    lastEditedAt,
    graphqlQueries: [],
    pullRequests: [],
    updates: [],
    creates: [],
    deletes: [],
  };

  const github = {
    state,
    graphql: async (query, variables) => {
      state.graphqlQueries.push({ query, variables });
      return {
        repository: { pullRequest: { lastEditedAt: state.lastEditedAt } },
      };
    },
    rest: {
      pulls: {
        get: async (payload) => {
          state.pullRequests.push(payload);
          return { data: state.pullRequest };
        },
      },
      issues: {
        listComments: async () => {},
        listEventsForTimeline: async () => {},
        updateComment: async (payload) => {
          state.updates.push(payload);
        },
        createComment: async (payload) => {
          state.creates.push(payload);
        },
        deleteComment: async (payload) => {
          state.deletes.push(payload);
        },
      },
    },
  };
  github.paginate = async (endpoint) =>
    endpoint === github.rest.issues.listEventsForTimeline
      ? state.timeline
      : state.comments;
  return github;
}

function makeContext() {
  return {
    repo: { owner: "octo", repo: "bench" },
    payload: { pull_request: { number: 3 } },
  };
}

test("loadHistory extracts embedded history payload", async () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "bench-history-"));
  const historyPath = path.join(tempDir, "history.json");
  const github = makeGithub([
    {
      body: '<!-- rust-pr-bench -->\n<!-- criterion-history: {"history":[{"commit":"abc1234"}]} -->',
    },
  ]);

  await helper.loadHistory({
    github,
    context: makeContext(),
    core: { info() {} },
    historyPath,
    historyKey: "criterion-history",
    markers: ["<!-- rust-pr-bench -->", "<!-- criterion-bench -->"],
  });

  assert.deepEqual(JSON.parse(fs.readFileSync(historyPath, "utf8")), {
    history: [{ commit: "abc1234" }],
  });
});

test("loadHistory ignores malformed history payloads", async () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "bench-history-"));
  const historyPath = path.join(tempDir, "history.json");
  const github = makeGithub([
    {
      body: "<!-- rust-pr-bench -->\n<!-- criterion-history: not-json -->",
    },
  ]);

  await helper.loadHistory({
    github,
    context: makeContext(),
    core: { info() {} },
    historyPath,
    historyKey: "criterion-history",
    markers: ["<!-- rust-pr-bench -->"],
  });

  assert.equal(fs.existsSync(historyPath), false);
});

test("loadHistory prefers new history and normalizes legacy backend entries", async () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "bench-history-"));
  const historyPath = path.join(tempDir, "history.json");
  const github = makeGithub([
    {
      body:
        '<!-- iai-callgrind-bench -->\n' +
        '<!-- iai-callgrind-history: {"history":[{"backend":"iai-callgrind","commit":"old"}]} -->',
    },
    {
      body:
        '<!-- gungraun-bench -->\n' +
        '<!-- gungraun-history: {"history":[{"backend":"iai","commit":"new"}]} -->',
    },
  ]);

  await helper.loadHistory({
    github,
    context: makeContext(),
    core: { info() {} },
    historyPath,
    historyKeys: ["gungraun-history", "iai-callgrind-history"],
    markers: ["<!-- gungraun-bench -->", "<!-- iai-callgrind-bench -->"],
    canonicalBackend: "gungraun",
  });

  assert.deepEqual(JSON.parse(fs.readFileSync(historyPath, "utf8")), {
    history: [{ backend: "gungraun", commit: "new" }],
  });
});

test("loadHistory falls back from absent new key to legacy history", async () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "bench-history-"));
  const historyPath = path.join(tempDir, "history.json");
  const github = makeGithub([
    {
      body:
        '<!-- iai-callgrind-bench -->\n' +
        '<!-- iai-callgrind-history: {"history":[{"backend":"callgrind","commit":"old"}]} -->',
    },
  ]);

  await helper.loadHistory({
    github,
    context: makeContext(),
    core: { info() {} },
    historyPath,
    historyKeys: ["gungraun-history", "iai-callgrind-history"],
    markers: ["<!-- gungraun-bench -->", "<!-- iai-callgrind-bench -->"],
    canonicalBackend: "gungraun",
  });

  assert.deepEqual(JSON.parse(fs.readFileSync(historyPath, "utf8")), {
    history: [{ backend: "gungraun", commit: "old" }],
  });
});

test("loadPullRequestMetadata fetches the current body and label names", async () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "bench-pr-metadata-"));
  const metadataPath = path.join(tempDir, "pr.json");
  const github = makeGithub([], {
    body: "PR body from the API",
    labels: [{ name: "performance-approved" }, "backport"],
  }, [
    {
      event: "labeled",
      label: { name: "performance-approved" },
      created_at: "2026-07-22T09:55:00Z",
    },
    {
      event: "labeled",
      label: { name: "different-label" },
      created_at: "2026-07-22T10:10:00Z",
    },
    {
      event: "labeled",
      label: { name: "performance-approved" },
      created_at: "2026-07-22T10:05:00Z",
    },
  ], "2026-07-22T10:00:00Z");

  await helper.loadPullRequestMetadata({
    github,
    context: makeContext(),
    metadataPath,
    approvalLabel: "performance-approved",
  });

  assert.deepEqual(JSON.parse(fs.readFileSync(metadataPath, "utf8")), {
    body: "PR body from the API",
    labels: ["performance-approved", "backport"],
    body_last_edited_at: "2026-07-22T10:00:00Z",
    approval_label_applied_at: "2026-07-22T10:05:00Z",
  });
  assert.deepEqual(github.state.pullRequests, [
    { owner: "octo", repo: "bench", pull_number: 3 },
  ]);
  assert.equal(github.state.graphqlQueries.length, 1);
  assert.deepEqual(github.state.graphqlQueries[0].variables, {
    owner: "octo",
    repo: "bench",
    number: 3,
  });
});

test("loadPullRequestMetadata is a no-op outside pull request context", async () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "bench-pr-metadata-"));
  const metadataPath = path.join(tempDir, "pr.json");
  const github = makeGithub([]);

  await helper.loadPullRequestMetadata({
    github,
    context: { repo: { owner: "octo", repo: "bench" }, payload: {} },
    metadataPath,
    approvalLabel: "performance-approved",
  });

  assert.equal(fs.existsSync(metadataPath), false);
  assert.equal(github.state.pullRequests.length, 0);
});

test("upsertReport updates an existing matching comment", async () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "bench-report-"));
  const reportPath = path.join(tempDir, "report.md");
  fs.writeFileSync(reportPath, "# report\n");
  const github = makeGithub([{ id: 11, body: "<!-- criterion-bench -->\nold" }]);

  await helper.upsertReport({
    github,
    context: makeContext(),
    reportPath,
    primaryMarker: "<!-- criterion-bench -->",
    markers: ["<!-- criterion-bench -->"],
    deleteExtras: false,
  });

  assert.equal(github.state.updates.length, 1);
  assert.equal(github.state.creates.length, 0);
  assert.match(github.state.updates[0].body, /# report/);
});

test("upsertReport replaces a legacy marker with the canonical marker", async () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "bench-report-"));
  const reportPath = path.join(tempDir, "report.md");
  fs.writeFileSync(reportPath, "# migrated report\n");
  const github = makeGithub([
    { id: 11, body: "<!-- iai-callgrind-bench -->\nold" },
  ]);

  await helper.upsertReport({
    github,
    context: makeContext(),
    reportPath,
    primaryMarker: "<!-- gungraun-bench -->",
    markers: ["<!-- gungraun-bench -->", "<!-- iai-callgrind-bench -->"],
    deleteExtras: false,
  });

  assert.equal(github.state.updates.length, 1);
  assert.equal(github.state.creates.length, 0);
  assert.match(github.state.updates[0].body, /^<!-- gungraun-bench -->/);
  assert.doesNotMatch(
    github.state.updates[0].body,
    /<!-- iai-callgrind-bench -->/,
  );
});

test("upsertReport deletes extra legacy comments in consolidated mode", async () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "bench-report-"));
  const reportPath = path.join(tempDir, "report.md");
  fs.writeFileSync(reportPath, "# combined report\n");
  const github = makeGithub([
    { id: 10, body: "<!-- rust-pr-bench -->\nold" },
    { id: 11, body: "<!-- iai-callgrind-bench -->\nold" },
    { id: 12, body: "<!-- criterion-bench -->\nold" },
  ]);

  await helper.upsertReport({
    github,
    context: makeContext(),
    reportPath,
    primaryMarker: "<!-- rust-pr-bench -->",
    markers: [
      "<!-- rust-pr-bench -->",
      "<!-- iai-callgrind-bench -->",
      "<!-- criterion-bench -->",
    ],
    deleteExtras: true,
  });

  assert.equal(github.state.updates.length, 1);
  assert.equal(github.state.deletes.length, 2);
  assert.deepEqual(
    github.state.deletes.map((item) => item.comment_id).sort(),
    [11, 12],
  );
});

test("upsertReport is a no-op outside pull request context", async () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "bench-report-"));
  const reportPath = path.join(tempDir, "report.md");
  fs.writeFileSync(reportPath, "# report\n");
  const github = makeGithub([]);

  await helper.upsertReport({
    github,
    context: { repo: { owner: "octo", repo: "bench" }, payload: {} },
    reportPath,
    primaryMarker: "<!-- criterion-bench -->",
    markers: ["<!-- criterion-bench -->"],
    deleteExtras: false,
  });

  assert.equal(github.state.updates.length, 0);
  assert.equal(github.state.creates.length, 0);
});
