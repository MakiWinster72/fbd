import { execFileSync } from "node:child_process";
import assert from "node:assert/strict";

const session = `knowledge-layout-${process.pid}`;
const browser = (...args) => execFileSync("agent-browser", ["--session", session, ...args], { encoding: "utf8", timeout: 30000 }).trim();
const evaluate = (code) => JSON.parse(browser("eval", "-b", Buffer.from(code).toString("base64")));
const route = (path, body) => browser("network", "route", `**/api/v1/${path}`, "--body", JSON.stringify(body));
try {
  browser("open", "http://localhost:3000/login");
  const admin = { id: "layout", username: "qa-admin", display_name: "Layout QA", role: "admin", active: true, preferred_locale: "zh-CN", knowledge_base_ids: [] };
  route("auth/me", admin);
  route("knowledge-bases", []);
  route("admin/knowledge-bases", []);
  route("admin/users", [admin]);
  route("admin/model-profiles", []);
  route("admin/mineru-status", { reachable: true });
  route("admin/documents?*", []);
  route("admin/document-imports", []);
  const results = [];
  for (const path of ["/admin/knowledge-bases", "/admin/documents", "/admin/models", "/admin/users", "/account", "/login"]) {
    if (path === "/login") {
      browser("network", "unroute", "**/api/v1/auth/me");
      route("auth/me", null);
    }
    browser("open", `http://localhost:3000${path}`);
    browser("wait", "--fn", `!!document.querySelector(${JSON.stringify(path.startsWith("/admin") ? ".admin-content" : path === "/account" ? ".account-sections" : ".login-card")})`);
    for (const width of [320, 375, 768, 1440, 1920]) {
      browser("set", "viewport", String(width), "900");
      browser("wait", "--fn", `getComputedStyle(document.documentElement).getPropertyValue('--page-gutter').trim() !== '' && ${path === "/login" ? "parseFloat(getComputedStyle(document.querySelector('.login-shell')).paddingLeft) >= 16" : "true"}`);
      const geometry = evaluate(`(() => {
        const main = document.querySelector('main.login-shell .login-card, main:not(.login-shell)').getBoundingClientRect();
        const nav = document.querySelector('.admin-shell > .admin-navigation')?.getBoundingClientRect();
        const copy = document.querySelector('.page-heading-copy')?.getBoundingClientRect();
        const masthead = document.querySelector('.site-masthead');
        return {overflow: document.documentElement.scrollWidth > innerWidth, left: main.left, right: innerWidth-main.right, navLeft:nav?.left, titleLeft:copy?.left, mastheadPadding: masthead ? parseFloat(getComputedStyle(masthead).paddingLeft) : null};
      })()`);
      assert.equal(geometry.overflow, false, `${path} overflow at ${width}`);
      assert.ok(geometry.left >= 15, `${path} missing gutter at ${width}`);
      assert.ok(Math.abs(geometry.left - geometry.right) < 2, `${path} not centered at ${width}`);
      if (path.startsWith("/admin")) {
        assert.ok(Math.abs(geometry.left - geometry.navLeft) < 2, `${path} nav alignment at ${width}`);
        assert.ok(Math.abs(geometry.left - geometry.titleLeft) < 2, `${path} heading alignment at ${width}`);
        assert.ok(Math.abs(geometry.left - geometry.mastheadPadding) < 2, `${path} masthead alignment at ${width}`);
      }
      results.push({ path, width, left: geometry.left });
      if ([375, 1440].includes(width)) browser("screenshot", `/tmp/knowledge-layout-${path.replaceAll("/", "-")}-${width}.png`);
    }
  }
  console.log(JSON.stringify({ passed: true, cases: results.length, results, productionWrites: 0 }));
} finally {
  browser("close");
}
