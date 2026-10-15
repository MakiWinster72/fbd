# 工作区导航影响分析

## Target
apps/web/components/chat-workspace.tsx 与 apps/web/app/globals.css；按需修改 language-provider.tsx、authenticated-workspace.tsx、i18n.ts。

## Dependents
- authenticated-workspace.tsx 调用 ChatWorkspace；必须保留无KB时账号入口。
- use-conversation-history.ts 提供唯一会话状态及异步竞态保护，导航复用而不是复制。
- modal.tsx 提供dialog、背景inert、关闭与焦点恢复，手机导航复用且不改通用样式。
- language-provider.tsx 被全站使用，导航调整必须限制路由作用域。
- globals.css 被全站引用，新样式必须局部命名避免管理页回归。

## Affected Stories
仓库此前没有specs/；本次局部计划e01s01，不推断旧路线图编号对应关系。

## Test Coverage
apps/web/tests/chat-proxy.test.mjs 只覆盖代理，不能证明导航布局及交互正确。
缺口：桌面/手机、中英、权限、会话切换、恢复与分页、抽屉焦点及断点、退出失败、无KB账号入口；计划新增隔离浏览器mock验证。

## Risk: High
缺少导航自动浏览器覆盖且涉及全站CSS、账号入口与会话切换；故事实施优先补测试。当前只完成静态影响分析，没有执行新UI测试。

## Recommended action
按 specs/epics/e01-workspace-navigation/e01s01-sidebar.md 交接，先确认门禁工具缺失处理，再实施；不得将生产build代替浏览器验收。
