import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";

const session = `knowledge-users-${process.pid}`;
const base = process.env.QA_BASE_URL || "http://127.0.0.1:3100";
assert.equal(base, "http://127.0.0.1:3100");
const ab = (...args) =>
  execFileSync("agent-browser", ["--session", session, ...args], {
    encoding: "utf8",
    timeout: 35000,
  });
const evaluate = (code) =>
  ab("eval", "-b", Buffer.from(code).toString("base64"));
const probe = (code) =>
  JSON.parse(JSON.parse(evaluate(`JSON.stringify(${code})`).trim()));
const admin = {
  id: "qa-admin",
  username: "qa-admin",
  display_name: "QA Admin",
  role: "admin",
  active: true,
  preferred_locale: "zh-CN",
  knowledge_base_ids: [],
};
const member = {
  ...admin,
  id: "qa-user",
  username: "alice",
  display_name: "Alice",
  role: "user",
  knowledge_base_ids: ["qa-base"],
};
const route = (path, body) =>
  ab("network", "route", `**/rag-api${path}`, "--body", JSON.stringify(body));

try {
  ab("open", "about:blank");
  route("/api/v1/auth/me", admin);
  route("/api/v1/admin/users", [admin, member]);
  route("/api/v1/admin/knowledge-bases", [
    {
      id: "qa-base",
      name: "QA Base",
      description: "Synthetic data",
      active: true,
    },
  ]);
  ab("open", `${base}/admin/users`);
  ab("wait", ".user-directory-table tbody tr");
  assert.equal(
    probe("document.querySelectorAll('.user-directory-table tbody tr').length"),
    2,
  );
  // All mutations are handled in-browser. Never forward synthetic writes to a backend.
  evaluate(
    `window.__qaWrites = []; window.fetch = ((original) => async (input, init) => { if (init?.method && init.method !== 'GET') { window.__qaWrites.push({url:String(input), ...init}); return new Response(JSON.stringify({detail:'QA mutation rejected'}), {status:503, headers:{'Content-Type':'application/json'}}); } return original(input, init); })(window.fetch.bind(window));`,
  );
  ab("fill", "input[type=search]", "alice");
  assert.equal(
    probe("document.querySelectorAll('.user-directory-table tbody tr').length"),
    1,
  );
  ab("click", ".user-directory-filters label:nth-child(2) select");
  ab("press", "ArrowDown");
  ab("press", "Enter");
  assert.equal(
    probe("document.querySelector('.user-directory-filters select').value"),
    "admin",
  );
  ab(
    "wait",
    "--fn",
    "document.querySelector('.user-directory-table') === null",
  );
  assert.equal(
    probe("document.querySelector('.user-directory-table') === null"),
    true,
  );
  ab("click", ".user-directory-filters label:nth-child(2) select");
  ab("press", "Home");
  ab("press", "Enter");
  ab("wait", ".user-directory-table tbody tr");
  ab("click", ".user-directory-table tbody tr:first-child button:first-child");
  ab("wait", "dialog[open]");
  assert.equal(
    probe("document.querySelector('dialog input[type=checkbox]').checked"),
    true,
  );
  ab("uncheck", "dialog input[type=checkbox]");
  ab("press", "Escape");
  ab("click", ".user-directory-table tbody tr:first-child button:first-child");
  ab("wait", "dialog[open]");
  assert.equal(
    probe("document.querySelector('dialog input[type=checkbox]').checked"),
    true,
  );
  ab("click", "dialog button[type=submit]");
  ab("wait", "dialog [role=alert]");
  assert.equal(probe("window.__qaWrites.length"), 1);
  assert.equal(probe("!!document.querySelector('dialog[open]')"), true);
  ab("press", "Escape");
  ab("click", "input[type=search]");
  ab("press", "Control+a");
  ab("press", "Backspace");
  assert.equal(probe("document.querySelector('input[type=search]').value"), "");
  ab(
    "wait",
    "--fn",
    "document.querySelectorAll('.user-directory-table tbody tr').length === 2",
  );
  assert.equal(
    probe(
      "document.querySelector('.user-directory-table tbody tr button:last-child').disabled",
    ),
    true,
  );
  for (const width of [1440, 390, 360]) {
    ab("set", "viewport", String(width), "900");
    assert.equal(
      probe("document.documentElement.scrollWidth > innerWidth"),
      false,
      `Overflow at ${width}px`,
    );
    assert.equal(
      probe(
        "getComputedStyle(document.querySelector('.user-directory-mobile')).display !== 'none'",
      ),
      width <= 760,
    );
  }
  console.log(
    "PASS: directory loading, search, role filter, authorization draft reset, failed mutation, self-disable guard, desktop/mobile layout",
  );
} finally {
  ab("close");
}
