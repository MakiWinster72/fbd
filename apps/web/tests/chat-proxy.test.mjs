import assert from 'node:assert/strict';
import { test } from 'node:test';
import { POST } from '../app/rag-api/api/v1/chat/route.ts';

function request() {
  return new Request('http://localhost/rag-api/api/v1/chat', {
    method: 'POST', headers: { cookie: 'session=qa', 'content-type': 'application/json' },
    body: JSON.stringify({ question: 'hi', knowledge_base_id: 'qa' }),
  });
}

test('forwards request body and authentication without caching', async (t) => {
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.match(String(url), /\/api\/v1\/chat$/);
    assert.equal(options.headers.cookie, 'session=qa');
    assert.equal(options.cache, 'no-store');
    assert.equal(JSON.parse(options.body).question, 'hi');
    return Response.json({ answer: 'Hello', grounded: false, sources: [] });
  });
  const response = await POST(request());
  assert.equal(response.status, 200);
  assert.equal((await response.json()).answer, 'Hello');
});

for (const status of [401, 403, 503]) {
  test(`preserves upstream ${status} and error details`, async (t) => {
    t.mock.method(globalThis, 'fetch', async () => Response.json({ detail: 'upstream failure' }, { status }));
    const response = await POST(request());
    assert.equal(response.status, status);
    assert.equal((await response.json()).detail, 'upstream failure');
  });
}

test('network failure returns a safe actionable JSON error', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => { throw new Error('private upstream information'); });
  const response = await POST(request());
  assert.equal(response.status, 502);
  const text = await response.text();
  assert.ok(!text.includes('private upstream'));
  assert.ok(JSON.parse(text).detail);
});
