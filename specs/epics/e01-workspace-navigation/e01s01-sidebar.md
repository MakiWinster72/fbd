# 工作区左侧导航重设计 — 模型交接计划

## 1. 状态
实施中。最初为只读交接计划；用户随后通过“开始”（m07500）授权实施。风险 P1，BCP 4。尚未完成全部浏览器验收，不得标记交付。

## 2. 目标
拆解 topbar-meta：知识库和历史会话属于工作上下文，进入左栏；管理入口和账号放左栏下部；服务状态留在主内容标题附近。减少重复导航，不改问答和权限逻辑。

## 3. 当前基线
apps/web/components/chat-workspace.tsx 的 topbar-meta 当前包含 service-pill、collection-label、user-menu；history-controls 在对话标题下面。最近已改为 evidence-panel 位于对话区底部，并调整 history-controls，以上变更尚未提交，位于 apps/web/app/globals.css 与 apps/web/components/chat-workspace.tsx。执行前检查 git diff，保留这些用户已认可的改动；不要恢复整个文件。
当前生产前端运行于3000，后台任务 b15ded367；不要依赖该ID必然仍有效，部署前核实归属。不得添加或删除 .pi/、data/mineru/。

## 4. 模块目的
ChatWorkspace 负责有知识库访问权用户的问答、会话切换和证据展示。导航只呈现其状态与动作，不创建第二套会话状态。

## 5. 调用者
apps/web/components/authenticated-workspace.tsx 调用 ChatWorkspace。apps/web/components/language-provider.tsx 提供全站导航及语言。apps/web/components/use-conversation-history.ts 维护会话状态；apps/web/components/modal.tsx 提供原生 dialog 焦点与关闭行为。

## 6. 不可变契约
保持 user/KB 授权、URL conversation 参数恢复、切换KB重置当前会话、分页去重及异步过期响应防护。继续使用现有 busy/restoring/listing 和 hook guard；不得为布局改动绕过限制。删除沿用确认流程，历史与引用不丢失。底部证据区与手机引用弹层保留。

## 7. MODIFIED：工作区结构
**Before:** 标题旁堆叠状态、知识库、账号与管理员链接；历史控件占主内容上部。
**After:** 桌面左栏宽260px，主区 minmax(0,1fr)，间距24px；主区仍是标题/状态→对话→输入→底部证据。左栏正常文档流，不悬浮、不 fixed；不强制占满视口高度。保留现有工作台最大宽度，不能为容纳侧栏造成页面横向溢出。

## 8. MODIFIED：知识库与历史
**Before:** 桌面知识库在 topbar，手机另有 mobile-kb-selector；历史通过主区 select 选择。
**After:** 左栏顶端显示知识库标签和唯一选择器，单库显示名称。新建会话按钮位于其下；历史改为纵向按钮列表，标题最多两行，完整标题保留可访问名称，显示轮数，当前项 aria-current=true。不添加未经接口支持的搜索、时间分组、置顶、重命名。保留刷新、加载更多、删除当前会话与原有确认流程。当前会话不在分页 items 时补入一次，不能重复。

## 9. MODIFIED：账号与管理
**Before:** 显示名、用户中心、文件导入、管理后台和退出挤在 topbar-meta。
**After:** 左栏下部独立分组显示管理员入口，仅 admin 可见；账号区显示名、用户中心、退出。管理员链接保持原目标 /admin/documents 和 /admin/models。账号非悬浮，不能遮挡历史。退出调用原 logout，新增 submitting/error 状态，失败留在页面显示本地化错误，成功跳 /login，防重复提交。

## 10. MODIFIED：全站导航
**Before:** site-masthead 全站展示工作台、用户中心及语言。
**After:** 仅工作台路由 / 隐藏重复的用户中心链接（左栏提供），保留品牌、工作台返回入口、语言。其他页面和无知识库空权限页面必须仍可访问用户中心；推荐不改 LanguageProvider 的权限数据流，由空权限页面明确保留入口。登录页行为不扩展。不要把账号数据再复制到语言Context。

## 11. ADDED：移动端抽屉
宽度 <=1000px 改为单列，标题内正常流“工作区导航”按钮打开左侧抽屉（无悬浮按钮）。复用 Modal，宽 min(320px, calc(100vw - 32px))，可滚动，关闭按钮 data-initial-focus。Escape、遮罩关闭、Tab焦点约束、关闭后恢复触发按钮焦点。桌面侧栏和手机抽屉不能同时存在可操作的重复ID。断点跨越时关闭抽屉并恢复body滚动。打开会话成功后关闭；失败留在抽屉显示错误，不能将仅返回void的 history.open 当成成功信号：优先观察 active.id/restoring/error，或明确扩展 open 的结果契约并补测试。切换KB及新建按现有guard成功后关闭；忙碌时禁用变更上下文。

## 12. 状态与可访问性
覆盖初次加载、空历史、分页、错误重试、长标题、无权限、恢复中、回答中、删除中；errors role=alert，服务状态使用文本不能仅靠颜色。点击目标>=44px，清晰focus-visible，支持reduced-motion。手机动效只用于抽屉短促滑入，不能影响关闭与焦点。英文不可固定中文，所有新增文字进入 i18n.ts。

## 13. 文件与抽象
修改 chat-workspace.tsx、globals.css、i18n.ts；按需 language-provider.tsx、authenticated-workspace.tsx。新增 workspace-navigation.tsx 作为无业务状态呈现组件：Reason for Depth——桌面侧栏与手机抽屉共享同一权限、控件和状态渲染，避免复制两套可漂移的导航。use-conversation-history.ts 的 open(id) 扩展为 Promise<boolean>：仅成功恢复且请求未过期时返回 true，受 guard、过期响应或失败均返回 false。手机抽屉据此只在成功后关闭，保留失败提示；原权限及请求版本保护不变。Modal 默认不改，只通过 className 定制。无需全局导航store、通用菜单框架或后端API。

## 14. 依赖审查
[OK] 当前React、Next、TypeScript继续使用，不升级。浏览器验收使用现有 agent-browser；不添加UI框架。若执行者要安装Playwright等新包，先说明必要性并获用户同意。

## 15. 影响与风险
全局CSS可能影响管理员页面；所有新规则限定 workspace-navigation / workspace-layout / workspace-drawer，不改通用 .modal 默认规则。language-provider 的变更需回归登录、账号、管理员页面。危险点：复制hook、重复挂载导航导致ID冲突、断点后body仍锁定、切换失败误关抽屉、隐藏空权限用户的账号入口。

## 16. 实施顺序与可执行验证
1. 增加隔离浏览器回归脚本 apps/web/tests/workspace-navigation.qa.mjs，使用本机已安装agent-browser CLI驱动并mock API，内置启动隔离Next端口及finally清理；不得使用真实用户写请求 → verify: `node apps/web/tests/workspace-navigation.qa.mjs --baseline`
2. 抽取导航呈现组件并实现桌面左栏，保留所有会话行为 → verify: `node apps/web/tests/workspace-navigation.qa.mjs --desktop`
3. 实现手机抽屉及断点/焦点/忙碌错误处理 → verify: `node apps/web/tests/workspace-navigation.qa.mjs --mobile`
4. 完成本地化、角色与空权限入口、退出错误交互 → verify: `node apps/web/tests/workspace-navigation.qa.mjs --states`
5. 回归类型、聊天代理、构建 → verify: `cd apps/web && npm run check && node --test tests/chat-proxy.test.mjs && npm run build`
6. 使用新构建运行完整隔离回归并生成截图与报告 → verify: `node apps/web/tests/workspace-navigation.qa.mjs --all`
这些qa命令是待新增测试的目标接口，目前不存在、不能标绿。测试脚本必须断言失败退出非零，不用仅打印CSS类名充当行为验收。

## 17. 验收标准
1280/1440桌面左栏，768/1000/360手机抽屉；1001边界为桌面。中英各测，无横溢出、正文/输入/证据不被遮挡。普通用户没有管理链接，admin正常；单/多KB正确。旧会话可打开、切库仅呈现对应历史、新建清空当前选择、分页不重复、失败可重试、删除需确认且成功后刷新。回答中禁止切库/切会话/删除。抽屉Escape、遮罩、焦点恢复、跨断点解锁均通过。URL恢复、退出成功及失败、无KB账号入口均测试。桌面点击引用定位到底部证据，手机引用弹层仍正常。登录/账号/三个管理页面无布局回归。浏览器录制/截图须实际查看，不能只跑build。

## 18. 范围外
不修复上游模型503，不调整召回/重排阈值，不重做知识库生命周期，不推进历史第50项，不新增功能菜单，不修改后端、不访问真实文档内容。

## 19. 交付与运行验证
显式暂存本故事文件，提交描述冒号后中文。先检查并保留既有未提交改动归属。生产更新必须完成build、核对并恢复next-env.d.ts纯生成改动、只停止自己确认的旧前端、重启3000，再验证HTTP和真实浏览器布局；仅源码改完不宣称上线。隔离QA须自动清理，避免误停API8080。

## 20. 规划门禁与交接
仓库尚无specs历史capsule、release-plan.yaml、SCOPE_LATEST.yaml、GLOSSARY_LATEST.yaml、tech-stack.md及scripts/lib/plan-consistency-check.sh；本目录是本次新建的局部交接载体，不代表整个项目第一个业务史诗。用户m07500已授权实施此局部计划。现已新增局部一致性检查脚本，检查20节、BCP、任务ID、风险/严重度、验证命令与文件关联，实际执行0 CRITICAL/0 HIGH/0 MED；不是完整bigpowers规划引擎，不代表运行验收通过。无新增依赖审批需求。计划任务全部failing表示尚未验收，并非已有测试失败。已确认布局方向，步骤和完整验收范围待用户审阅。授权实施不等于验收完成；所有未覆盖的运行检查须继续保留failing。
