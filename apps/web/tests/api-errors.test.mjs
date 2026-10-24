import { test } from "node:test";
import assert from "node:assert/strict";
import { createAdminUser, getCurrentUser } from "../lib/api.ts";

async function withResponse(status, body, run) {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify(body), { status });
  try {
    await run();
  } finally {
    globalThis.fetch = original;
  }
}

test("expired login is detected by HTTP status rather than translated error text", async () => {
  await withResponse(401, { detail: "Not authenticated" }, async () => {
    assert.equal(await getCurrentUser(), null);
  });
});

test("forbidden or unavailable service is never mistaken for missing login", async () => {
  for (const status of [403, 503]) {
    await withResponse(status, { detail: "请先登录" }, async () => {
      await assert.rejects(getCurrentUser(), /请先登录/);
    });
  }
});

test("validation errors show readable field messages instead of object coercion", async () => {
  await withResponse(
    422,
    {
      detail: [
        { loc: ["body", "username"], msg: "Invalid username" },
        { loc: ["body", "password"], msg: "Password is too long" },
      ],
    },
    async () => {
      await assert.rejects(
        createAdminUser({
          username: "!",
          password: "x",
          display_name: "QA",
          role: "user",
          knowledge_base_ids: [],
        }),
        (error) => {
          assert.match(error.message, /username: Invalid username/);
          assert.match(error.message, /password: Password is too long/);
          assert.doesNotMatch(error.message, /\[object Object\]/);
          return true;
        },
      );
    },
  );
});

test("unexpected error payloads retain an actionable HTTP status", async () => {
  for (const detail of [{ unexpected: true }, [], null]) {
    await withResponse(503, { detail }, async () => {
      await assert.rejects(getCurrentUser(), /服务返回 503/);
    });
  }
});
