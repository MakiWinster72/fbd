import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const page = readFileSync(
  new URL("../app/admin/users/page.tsx", import.meta.url),
  "utf8",
);
const list = readFileSync(
  new URL("../app/admin/users/user-list.tsx", import.meta.url),
  "utf8",
);
const dialogs = readFileSync(
  new URL("../app/admin/users/user-dialogs.tsx", import.meta.url),
  "utf8",
);
const navigation = readFileSync(
  new URL("../components/workspace-navigation.tsx", import.meta.url),
  "utf8",
);

test("admin user directory retains authorization, independent loading and search", () => {
  assert.match(page, /user\.role !== "admin"/);
  assert.match(page, /listAdminUsers\(\)/);
  assert.match(page, /listAdminKnowledgeBases\(\)/);
  assert.doesNotMatch(page, /Promise\.all/);
  assert.match(
    page,
    /user\.username\.toLocaleLowerCase\(\)\.includes\(keyword\)/,
  );
  assert.match(page, /usersStatus === "error"/);
  assert.match(page, /basesStatus === "error"/);
});

test("directory exposes actions without allowing self-disable", () => {
  assert.match(list, /<table/);
  assert.match(list, /user-directory-mobile/);
  assert.match(list, /disabled=\{user\.id === currentUserId\}/);
  assert.match(navigation, /href="\/admin\/users"/);
});

test("dialogs reuse existing mutation APIs with pending protection", () => {
  for (const name of [
    "createAdminUser",
    "setAdminUserKnowledgeBases",
    "resetAdminUserPassword",
    "setAdminUserStatus",
  ])
    assert.match(dialogs, new RegExp(`${name}\\(`));
  assert.match(dialogs, /if \(submitting\) return/);
  assert.match(dialogs, /if \(!submitting\) onClose\(\)/);
});
