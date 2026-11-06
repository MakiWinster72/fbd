import { execFileSync } from "node:child_process";
import assert from "node:assert/strict";

const session = `knowledge-account-${process.pid}`;
const browser = (...args) => execFileSync("agent-browser", ["--session", session, ...args], { encoding: "utf8", timeout: 30000 }).trim();
const evaluate = (code) => JSON.parse(browser("eval", "-b", Buffer.from(code).toString("base64")));
const route = (path, body) => browser("network", "route", `**/api/v1/${path}`, "--body", JSON.stringify(body));
try {
  browser("open", "http://localhost:3000/login");
  const user = { id: "account-qa", username: "qa-member", display_name: "账户测试", role: "user", active: true, preferred_locale: "zh-CN" };
  route("auth/me", user);
  browser("open", "http://localhost:3000/account");
  browser("wait", "--text", "账户测试");
  assert.equal(evaluate('document.querySelectorAll(".account-section").length'), 3);
  assert.equal(evaluate('document.querySelector(".account-profile-form button").disabled'), true);
  for (const width of [320, 375, 768, 1440]) {
    browser("set", "viewport", String(width), "900");
    assert.equal(evaluate('document.documentElement.scrollWidth > innerWidth'), false, `overflow at ${width}`);
    browser("screenshot", `/tmp/knowledge-account-${width}.png`);
  }
  browser("fill", "#display-name", "新显示名称");
  assert.equal(evaluate('document.querySelector(".account-profile-form button").disabled'), false);
  browser("fill", "#current-password", "current-test-password");
  browser("fill", "#new-password", "new-test-password");
  browser("fill", "#confirm-password", "different-test-password");
  evaluate('document.querySelector(".account-section-security button").click(); true');
  browser("wait", "--text", "两次新密码不一致");
  assert.equal(evaluate('document.querySelector("#confirm-password").getAttribute("aria-invalid")'), "true");
  console.log("通过：账户概览、三项设置、未修改禁用保存、编辑启用保存、密码不一致校验、320/375/768/1440px无溢出（模拟身份，无真实写入）。");
} finally {
  browser("close");
}
