import assert from 'node:assert/strict';
import {test} from 'node:test';
import {operate} from '../scripts/cache_transport.mjs';

test('distinguishes exact, fallback and missing entries', async () => {
  for (const [matched, expected] of [['key', 'exact'], ['older', 'fallback'], [undefined, 'miss']]) {
    const result = await operate({operation: 'restore', paths: ['target'], key: 'key', restore_keys: ['prefix']}, {
      restoreCache: async (paths, key, prefixes) => {
        assert.deepEqual(paths, ['target']);
        assert.equal(key, 'key');
        assert.deepEqual(prefixes, ['prefix']);
        return matched;
      }
    });
    assert.equal(result.status, expected);
  }
});

test('reports a declined save without claiming success', async () => {
  assert.deepEqual(await operate({operation: 'save', paths: [], key: 'key'}, {
    saveCache: async () => -1
  }), {status: 'not-saved'});
});
