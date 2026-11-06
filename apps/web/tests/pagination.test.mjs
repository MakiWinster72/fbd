import { test } from "node:test";
import assert from "node:assert/strict";
import { paginate } from "../lib/pagination.ts";

const items = Array.from({ length: 19 }, (_, index) => index + 1);
test("import lists show eight entries per page, without losing entries", () => {
  assert.deepEqual(paginate(items, 1).items, items.slice(0, 8));
  assert.deepEqual(paginate(items, 2).items, items.slice(8, 16));
  assert.deepEqual(paginate(items, 3).items, [17, 18, 19]);
  assert.equal(paginate(items, 1).pages, 3);
});
test("pagination clamps after filtering or deleting the last page", () => {
  assert.equal(paginate(items.slice(0, 2), 3).page, 1);
  assert.equal(paginate(items, -2).page, 1);
  assert.deepEqual(paginate([], 20), { page: 1, pages: 1, items: [] });
});
