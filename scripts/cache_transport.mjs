// Run in a child of a JavaScript action so the official toolkit receives the
// runner's cache-service credentials. Never print the request environment.
import fs from 'node:fs';
import * as cache from '@actions/cache';

export async function operate(request, client = cache) {
  if (request.operation === 'restore') {
    const matched = await client.restoreCache(request.paths, request.key, request.restore_keys);
    return {status: matched === request.key ? 'exact' : matched ? 'fallback' : 'miss', matched_key: matched || ''};
  }
  const id = await client.saveCache(request.paths, request.key);
  return {status: id >= 0 ? 'saved' : 'not-saved'};
}

if (process.argv[2] && process.argv[3]) {
  const [requestPath, resultPath] = process.argv.slice(2);
  Promise.resolve().then(() => operate(JSON.parse(fs.readFileSync(requestPath, 'utf8'))))
    .catch(error => ({status: 'error', error: error.name || 'CacheError'}))
    .then(result => fs.writeFileSync(resultPath, JSON.stringify(result)));
}
