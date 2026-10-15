# 整屏聊天与导航重设计影响分析

## Target
仅规划 e01s02，基线 d816758；产品代码未改。用户已确认聊天首屏＋证据次屏、柔性吸附；仅移除 topbar，保留 site-masthead。

## Purpose / Callers / Contracts
- apps/web/components/chat-workspace.tsx：协调问答、消息、引用、导航；由 authenticated-workspace.tsx 调用。保持同一 useConversationHistory 实例、URL恢复、权限、busy、原请求和引用来源契约。
- apps/web/components/workspace-navigation.tsx：桌面侧栏和手机抽屉共用；两处均由 ChatWorkspace 挂载。保持角色过滤、历史分页/错误/删除/退出动作、唯一控件ID与成功打开后关闭抽屉。
- apps/web/components/language-provider.tsx：app/layout.tsx 全站使用，输出 site-masthead、语言错误行及 skip-link。只允许为工作台提供测量/作用域，不把其他页面改成整屏滚动。
- apps/web/components/modal.tsx：导航/引用/确认等共用。保持原生dialog、背景inert、关闭与焦点恢复，不改全局弹层契约。
- apps/web/app/globals.css：全站共享。新规则必须工作台作用域，不全局锁body或为html增加snap。

## Actual findings
- chat-workspace.tsx:181–207：transcript独立自动跟随；<=760引用打开弹层，其余focus+scrollIntoView。需协调第二屏与卡片滚动，不能一条scrollIntoView把多个祖先都拉走。
- chat-workspace.tsx:288–415：conversation-panel含heading/transcript/composer；evidence-panel是后续兄弟。可沿用，不复制证据状态。
- globals.css:905、1043：transcript现有min-height/max-height会阻止整屏flex收缩，必须局部覆盖。
- globals.css:877、1032：masthead高度随宽度与语言变化，不能硬编码84px；语言错误行也占高度。
- globals.css:1101：手机悬浮入口带workspace-shell底部80px预留。新整屏必须用内部独立导航栏位替代外部撑高，否则首屏必超视窗。

## Affected Stories
e01s01保持历史交付及未覆盖记录。e01s02是用户新增交互设计变更，不重开旧多轮任务；其布局要求替代e01s01中“不强制整屏”和“保留topbar”条款，其功能契约继续生效。

## Coverage
已有apps/web/tests/workspace-navigation.qa.mjs：中英六宽度、角色标签、抽屉开关/焦点/跨断点、部分会话/退出失败；尚无首屏几何、嵌套滚动、长证据、软键盘/引用跨屏验证。chat-proxy.test.mjs不覆盖UI。

## Risk: High
共享CSS、全局masthead测量、三类滚动容器与焦点联动可能破坏可用性。先建立专用隔离回归，再改布局。不得通过删掉旧断言伪造通过。

## Recommended action
按同目录e01s02计划实施；不新增包、不改后端、不写真实数据。子代理探索因上下文超过路由预算未启动；以上为主代理直接读取源码所得，不声称独立复核。release-plan、SCOPE_LATEST、GLOSSARY_LATEST、tech-stack及timing/sync脚本当前不存在，记录缺失而非虚构已运行。
