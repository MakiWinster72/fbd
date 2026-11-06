import { execFileSync } from "node:child_process";
import assert from "node:assert/strict";

const session = `knowledge-imports-${process.pid}`;
function browser(...args) {
  return execFileSync("agent-browser", ["--session", session, ...args], { encoding: "utf8", timeout: 30000 }).trim();
}
function evaluate(code) {
  return JSON.parse(browser("eval", "-b", Buffer.from(`(() => eval(${JSON.stringify(code)}))()`).toString("base64")));
}
function route(path, body) {
  browser("network", "route", `**/api/v1/${path}`, "--body", JSON.stringify(body));
}
try {
  browser("open", "http://localhost:3000/login");
  route("auth/me", { id: "qa", username: "qa-admin", role: "admin", active: true });
  const bases = [{ id: "qa", name: "测试知识库" }];
  route("knowledge-bases", bases);
  route("admin/knowledge-bases", bases);
  route("admin/mineru-status", { reachable: true });
  route("admin/model-profiles", [{ id: "embed", name: "测试向量模型", model_name: "test", purpose: "embedding", is_active: true }]);
  route("admin/documents?*", Array.from({ length: 19 }, (_, i) => ({ document_id: `doc-${i}`, document_name: `文档-${i}.pdf`, chunk_count: 2, page_count: 1 })));
  route("admin/document-imports", Array.from({ length: 23 }, (_, i) => ({ id: `job-${i}`, file_name: `任务-${i}.pdf`, knowledge_base_id: "qa", status: i === 22 ? "failed" : "completed", total_chunks: 2, completed_chunks: 2, source_available: true, error: i === 22 ? "模拟失败" : null })));
  browser("open", "http://localhost:3000/admin/documents");
  browser("wait", "--text", "文档-0.pdf");
  assert.equal(evaluate('document.querySelectorAll(".intake-jobs:not([hidden]) .intake-job-row").length'), 8);
  assert.equal(evaluate('document.querySelector(".intake-advanced").open'), false);
  assert.equal(evaluate('!!document.querySelector(".intake-stage-rail")'), false);
  evaluate('document.querySelector(".intake-jobs:not([hidden]) .intake-pagination button:last-child").click(); true');
  browser("wait", "--text", "文档-8.pdf");
  evaluate('document.querySelector(".intake-record-tabs button:last-child").click(); true');
  browser("wait", "--text", "任务-0.pdf");
  assert.equal(evaluate('document.querySelectorAll(".intake-jobs:not([hidden]) .intake-job-row").length'), 8);
  evaluate('document.querySelector(".intake-jobs:not([hidden]) .intake-pagination button:last-child").click(); true');
  browser("wait", "--text", "任务-8.pdf");
  browser("select", ".intake-job-filters select", "failed");
  browser("wait", "--text", "任务-22.pdf");
  assert.equal(evaluate('document.querySelectorAll(".intake-jobs:not([hidden]) .intake-job-row").length'), 1);
  assert.equal(evaluate('document.querySelector(".intake-jobs:not([hidden]) .intake-pagination button:last-child").disabled'), true);
  for (const width of [1440, 375]) {
    browser("set", "viewport", String(width), "900");
    assert.equal(evaluate('document.documentElement.scrollWidth > window.innerWidth'), false, `overflow at ${width}px`);
    browser("screenshot", `/tmp/knowledge-imports-${width}.png`);
  }
  evaluate('const transfer = new DataTransfer(); for (let i = 0; i < 24; i++) transfer.items.add(new File(["测试正文"], `待导入-${i}.txt`, {type: "text/plain"})); const input = document.querySelector("input[type=file]"); input.files = transfer.files; input.dispatchEvent(new Event("change", {bubbles:true})); true');
  browser("wait", "--text", "待导入-0.txt");
  assert.ok(evaluate('document.querySelector(".intake-file-list").clientHeight') <= 260);
  assert.equal(evaluate('document.querySelector(".intake-review-button").disabled'), false);
  evaluate('document.querySelector(".intake-queue-panel button").click(); true');
  browser("wait", "--fn", '!document.querySelector(".intake-queue-panel")');
  browser("network", "unroute", "**/api/v1/admin/document-imports");
  route("admin/document-imports", { id: "uploaded", file_name: "单击导入.txt", knowledge_base_id: "qa", status: "completed", total_chunks: 1, completed_chunks: 1 });
  evaluate('const transfer = new DataTransfer(); transfer.items.add(new File(["测试正文"], "单击导入.txt", {type:"text/plain"})); const input = document.querySelector("input[type=file]"); input.files = transfer.files; input.dispatchEvent(new Event("change", {bubbles:true})); true');
  browser("wait", "--text", "单击导入.txt");
  evaluate('document.querySelector(".intake-review-button").click(); true');
  browser("wait", "--fn", 'document.querySelector(".intake-jobs:not([hidden]) .intake-job-row strong")?.textContent === "单击导入.txt"');
  assert.equal(evaluate('!!document.querySelector(".intake-queue-panel")'), false);
  console.log("通过：文档/任务切换、8条分页、筛选重置、高级设置折叠、375/1440px无溢出、24个待选文件有界滚动、单击提交后显示任务（模拟API）。");
} finally {
  browser("close");
}
