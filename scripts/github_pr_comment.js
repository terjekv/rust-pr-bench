const fs = require("fs");

async function listIssueComments(github, context, issueNumber) {
  return github.paginate(github.rest.issues.listComments, {
    owner: context.repo.owner,
    repo: context.repo.repo,
    issue_number: issueNumber,
  });
}

function logInfo(core, message) {
  if (core && typeof core.info === "function") {
    core.info(message);
  }
}

function findFirstMatchingComment(comments, markers) {
  return comments.find((comment) => {
    if (!comment || !comment.body) {
      return false;
    }
    return markers.some((marker) => comment.body.includes(marker));
  });
}

function normalizeHistoryPayload(payload, canonicalBackend) {
  if (!canonicalBackend || !payload || !Array.isArray(payload.history)) {
    return payload;
  }
  const legacyBackends = new Set([
    "gungraun",
    "iai",
    "iai-callgrind",
    "callgrind",
  ]);
  return {
    ...payload,
    history: payload.history.map((entry) => {
      if (
        !entry ||
        typeof entry !== "object" ||
        !legacyBackends.has(String(entry.backend || "").toLowerCase())
      ) {
        return entry;
      }
      return { ...entry, backend: canonicalBackend };
    }),
  };
}

async function loadHistory({
  github,
  context,
  core,
  historyPath,
  historyKey,
  historyKeys,
  markers,
  canonicalBackend,
}) {
  const issueNumber = context.payload?.pull_request?.number;
  if (!issueNumber) {
    return;
  }

  const comments = await listIssueComments(github, context, issueNumber);
  const matchingComments = comments.filter(
    (comment) =>
      comment &&
      comment.body &&
      markers.some((marker) => comment.body.includes(marker)),
  );
  const keys = historyKeys || (historyKey ? [historyKey] : []);
  for (const key of keys) {
    const escapedKey = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const historyRegex = new RegExp(`<!-- ${escapedKey}: ([\\s\\S]*?) -->`);
    for (const existing of matchingComments) {
      const match = existing.body.match(historyRegex);
      if (!match) {
        continue;
      }
      try {
        const payload = normalizeHistoryPayload(
          JSON.parse(match[1]),
          canonicalBackend,
        );
        fs.writeFileSync(historyPath, JSON.stringify(payload));
        return;
      } catch (error) {
        logInfo(core, `Failed to parse history JSON for ${key}: ${error}`);
      }
    }
  }
}

async function loadPullRequestMetadata({ github, context, metadataPath, approvalLabel }) {
  const issueNumber = context.payload?.pull_request?.number;
  if (!issueNumber) {
    return;
  }

  const response = await github.rest.pulls.get({
    owner: context.repo.owner,
    repo: context.repo.repo,
    pull_number: issueNumber,
  });
  const pullRequest = response.data || {};
  const labels = (pullRequest.labels || [])
    .map((label) => (typeof label === "string" ? label : label && label.name))
    .filter((label) => typeof label === "string");
  const editResponse = await github.graphql(
    `query($owner: String!, $repo: String!, $number: Int!) {
      repository(owner: $owner, name: $repo) {
        pullRequest(number: $number) { lastEditedAt }
      }
    }`,
    {
      owner: context.repo.owner,
      repo: context.repo.repo,
      number: issueNumber,
    },
  );
  const bodyLastEditedAt =
    editResponse?.repository?.pullRequest?.lastEditedAt || null;
  const timeline = await github.paginate(
    github.rest.issues.listEventsForTimeline,
    {
      owner: context.repo.owner,
      repo: context.repo.repo,
      issue_number: issueNumber,
      per_page: 100,
    },
  );
  const labelAppliedAt = timeline
    .filter(
      (event) =>
        event?.event === "labeled" && event?.label?.name === approvalLabel,
    )
    .map((event) => event.created_at)
    .filter((createdAt) => typeof createdAt === "string")
    .sort()
    .at(-1) || null;
  fs.writeFileSync(
    metadataPath,
    JSON.stringify({
      body: typeof pullRequest.body === "string" ? pullRequest.body : "",
      labels,
      body_last_edited_at: bodyLastEditedAt,
      approval_label_applied_at: labelAppliedAt,
    }),
  );
}

async function upsertReport({
  github,
  context,
  reportPath,
  primaryMarker,
  markers,
  deleteExtras,
}) {
  const issueNumber = context.payload?.pull_request?.number;
  if (!issueNumber) {
    return;
  }

  const report = fs.readFileSync(reportPath, "utf8");
  const body = `${primaryMarker}\n${report}`;
  const comments = await listIssueComments(github, context, issueNumber);
  const matching = comments.filter(
    (comment) =>
      comment &&
      comment.body &&
      markers.some((marker) => comment.body.includes(marker))
  );

  if (matching.length > 0) {
    const [first, ...rest] = matching;
    await github.rest.issues.updateComment({
      owner: context.repo.owner,
      repo: context.repo.repo,
      comment_id: first.id,
      body,
    });

    if (deleteExtras) {
      for (const extra of rest) {
        await github.rest.issues.deleteComment({
          owner: context.repo.owner,
          repo: context.repo.repo,
          comment_id: extra.id,
        });
      }
    }
    return;
  }

  await github.rest.issues.createComment({
    owner: context.repo.owner,
    repo: context.repo.repo,
    issue_number: issueNumber,
    body,
  });
}

module.exports = {
  findFirstMatchingComment,
  loadHistory,
  loadPullRequestMetadata,
  normalizeHistoryPayload,
  upsertReport,
};
