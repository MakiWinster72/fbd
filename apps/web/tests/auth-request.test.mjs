import { test } from "node:test";
import assert from "node:assert/strict";
import { getCurrentUser } from "../lib/api.ts";

test("concurrent permission and locale checks share only the in-flight request", async () => {
  const original = globalThis.fetch;
  const originalWindow = globalThis.window;
  globalThis.window = {};
  let calls = 0;
  let resolve;
  globalThis.fetch = () => {
    calls++;
    return new Promise((done) => {
      resolve = done;
    });
  };
  try {
    const first = getCurrentUser();
    const second = getCurrentUser();
    assert.equal(
      calls,
      1,
      "duplicate auth/me requests delay page permission verification",
    );
    resolve(new Response(JSON.stringify({ id: "admin", role: "admin" })));
    assert.deepEqual(await first, await second);
    const next = getCurrentUser();
    assert.equal(
      calls,
      2,
      "do not cache settled identity across logout or account changes",
    );
    resolve(
      new Response(JSON.stringify({ detail: "请先登录" }), { status: 401 }),
    );
    assert.equal(await next, null);
  } finally {
    globalThis.fetch = original;
    if (originalWindow === undefined) delete globalThis.window;
    else globalThis.window = originalWindow;
  }
});

test("a stalled identity check times out and a fresh attempt can recover", async (context) => {
  const original = globalThis.fetch;
  const originalWindow = globalThis.window;
  globalThis.window = {};
  context.mock.timers.enable({ apis: ["setTimeout"] });
  globalThis.fetch = (_url, init) =>
    new Promise((_resolve, reject) => {
      init.signal.addEventListener("abort", () =>
        reject(new DOMException("aborted", "AbortError")),
      );
    });
  try {
    const request = getCurrentUser();
    const rejection = assert.rejects(request, /验证登录状态超时，请重试/);
    context.mock.timers.tick(10_000);
    await rejection;
    globalThis.fetch = async () =>
      new Response(JSON.stringify({ id: "recovered", role: "admin" }));
    assert.equal((await getCurrentUser()).id, "recovered");
  } finally {
    globalThis.fetch = original;
    context.mock.timers.reset();
    if (originalWindow === undefined) delete globalThis.window;
    else globalThis.window = originalWindow;
  }
});

test("server-side callers never share identity requests", async () => {
  const original = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls++;
    return new Response(JSON.stringify({ id: String(calls) }));
  };
  try {
    const [first, second] = await Promise.all([
      getCurrentUser(),
      getCurrentUser(),
    ]);
    assert.equal(calls, 2);
    assert.notEqual(first.id, second.id);
  } finally {
    globalThis.fetch = original;
  }
});
