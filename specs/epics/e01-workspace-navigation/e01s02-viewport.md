# 整屏聊天、分屏吸附与轻量导航 — 执行模型交接

## 1. 状态
仅计划，禁止把本次交接当成实现完成。基线 d816758，分支 feat/knowledge-base-ui-cleanup。用户要切换模型执行；本轮不修改产品代码、不构建、不部署。type: feat；risk: P1；BCP: 4；maturity: 3（Countable）。

## 2. 用户已确认的范围
聊天首屏＋证据次屏，柔性吸附；只移除工作台 topbar，保留全局 site-masthead。桌面仍有左侧导航；“满视窗”指主内容剩余宽度及扣除masthead后的可用高度，不是覆盖导航与品牌栏。手机保留左下角悬浮入口及抽屉。不做强制逐屏翻页。

## 3. 当前真实基线
已实现260px侧栏、手机<=1000抽屉、<=760引用弹层；服务标签仅admin显示（66c5c31）；手机悬浮入口（d816758）。聊天仍被上下留白、topbar和transcript高度限制分割，侧栏每项近似独立卡片。旧e01s01文档中部分“未实现”描述为历史记录，不得据此回退代码。执行前git status核实最新修改。

## 4. 模块目的
ChatWorkspace负责问答状态、消息及引用协调；WorkspaceNavigation只呈现现有操作；LanguageProvider负责语言偏好和全局masthead。布局属于表现层，不添加第二套会话hook或复制账号状态。

## 5. 调用关系
AuthenticatedWorkspace→ChatWorkspace→WorkspaceNavigation（桌面/抽屉）及Modal；app/layout.tsx→LanguageProvider→页面。全局CSS由所有页面使用。详见specs/IMPACT_LATEST.md。

## 6. 不可变契约
保持API、权限和用户数据不变；URL恢复、切库清空、分页去重、busy/restoring、过期响应保护、删除确认、退出失败保留页面、语言持久化不变。保留单一消息与引用状态；普通用户无管理入口或service-pill；无KB账号仍能进入/account。不改变后端健康检查或模型逻辑。

## 7. Requirements — MODIFIED：首屏与高度
**Before:** workspace-shell有外部margin/border/card，topbar占高度；transcript使用多组min/max-height。
**After:** 保留site-masthead且首页主体紧接其下，工作区无额外纵向外边距；工作台专属frame高度为可见视窗减去实际masthead及语言错误行高度。conversation-panel在该frame首屏完整占满主区。默认高度用100dvh，ResizeObserver测真实上方占用，必要时VisualViewport resize/scroll处理软键盘；不硬编码84px。清理观察器/监听器与局部CSS变量，跨路由不能残留。桌面主区占剩余宽度，不把长行拉满：消息正文最大约840px居中，面板本身铺满。
聊天采用grid rows auto minmax(0,1fr) auto；transcript min-height:0、max-height:none、overflow:auto，输入区不随消息滚走。紧凑标题保留可访问h1，移除重复kicker/宣传文案；欢迎态建议问题最多一行横滚或紧凑折行，不挤掉消息区。

## 8. Requirements — MODIFIED：滚动和证据次屏
**Before:** 页面普通长滚动，证据紧跟聊天；引用scrollIntoView可能推动多个祖先。
**After:** masthead位于frame外正常流；frame内部桌面侧栏不参与主区翻屏。主区专属scroller使用scroll-snap-type:y proximity，conversation-panel和evidence-panel各为一页、scroll-snap-align:start；不对html/body全局加snap，不使用mandatory/scroll-snap-stop:always，不拦截wheel/touch进行翻页。
消息和证据列表各自内部滚动，overscroll-behavior:contain防长内容意外翻屏。提供明确“查看证据／返回对话”按钮，避免嵌套滚动无法到达下一屏；可在分区边缘/空白处柔性翻屏。snap只作用于两页，不作用于每条消息。空证据仍有第二屏占位，不在回答返回时凭空改变页数。低高度/200%缩放/键盘打开时可降级取消snap并允许局部滚动，优先保证输入、发送、返回可达，不能强求几何而裁内容。

## 9. Requirements — REMOVED：工作台topbar
**Before:** 工作台内重复品牌印章、标题、导航触发器和管理员状态标签。
**After:** (removed) 删除该topbar DOM，减少重复层级；保留site-masthead原品牌/语言/工作台链接。h1移至聊天紧凑标题。手机悬浮触发器移出被删除容器，保持aria-expanded/haspopup及焦点恢复；管理员状态压缩到侧栏底部文本行，不展示给普通用户，不创建第二份健康请求。

## 10. Requirements — MODIFIED：导航视觉
**Before:** 米白独立圆角卡片，所有链接有描边，分隔线和说明密集，账号占大量纵向空间。
**After:** 与主区同高的轻量导航轨道：桌面248px（可在240–260px内微调但需记录最终值），无外层大卡片、无重阴影，仅右侧1px分隔。顶部资料库标签/选择器＋一个新建主按钮；中部会话列表占剩余高度滚动；底部管理员入口、账号/退出紧凑排列。只给当前会话浅色底和2px强调线，普通项透明无边框；刷新、加载更多、删除仍可发现但不争主视觉。历史说明缩为低对比短句，不删隐私含义。长名两行截断，完整名称保留无障碍名称；不新增搜索/置顶/分组等无后端支持功能。
视觉token建议：背景#F7F9FA、主面板#FFFFFF、文字#172C43、次级#657681、分隔#DFE6EA、强调#246455。使用现有字体栈，不下载字体；导航13px/正文15–16px、8/12/16/24间距、控件圆角8px。hover轻微底色、focus清晰，禁用态不能仅靠颜色。

## 11. Requirements — ADDED：手机输入与悬浮入口避让
<=1000继续抽屉，左下悬浮入口>=48px，保留safe-area。整屏不能沿用workspace-shell外部80px padding增加总高度；改在首屏内部底部设置导航专用栏位（至少64px＋安全区），输入区在其上，按钮不能覆盖textarea、发送或引用入口。证据屏亦预留底部操作位。键盘打开时仍可收起/打开导航、发送且无遮挡；不能仅用桌面resize模拟就宣称真机键盘验证。输入控件字体>=16px避免iOS自动放大。抽屉仍使用原生Modal，无业务状态复制。

## 12. Requirements — MODIFIED：引用和动效
**Before:** >760直接source.focus＋scrollIntoView；<=760打开mobile-evidence-sheet。
**After:** 保留<=760引用弹层，返回恢复触发引用焦点；>760先让主scroller到证据页，再只滚动evidence-list到目标卡片并focus preventScroll，避免祖先滚动级联。768–1000是导航抽屉但引用次屏，两个断点不得混为一谈。新增“返回对话”保留transcript位置和草稿。自动跟随只滚transcript，用户正在证据页不能被新回答抢回首屏。prefers-reduced-motion下取消smooth和强制吸附；切会话成功显式返回聊天，恢复失败不改变所在页。

## 13. 文件与抽象边界
重点：apps/web/components/chat-workspace.tsx、workspace-navigation.tsx、app/globals.css、lib/i18n.ts。按需language-provider.tsx仅提供masthead尺寸/路由作用域，不改语言持久化。不改通用Modal与use-conversation-history行为，除非有明确失败用例。
优先局部ref/effect或CSS grid，不创建全局布局store。若抽出useWorkspaceViewport：Reason for Depth——封装动态可见视窗、上方占用测量及清理，给布局提供单一可用高度，避免多个组件互相写尺寸；若只有一处简单测量则保持内联。不引入通用导航框架。

## 14. 依赖与前置资料
[OK] 已有React/Next/TypeScript和已安装agent-browser，不升级、不新增npm包。没有新外部API；新增依赖必须再次获批。执行前遵守apps/web/AGENTS.md读取安装版Next相关CSS/layout文档。本轮只规划，没有更改Next代码。
缺失release-plan.yaml、product/SCOPE_LATEST.yaml、GLOSSARY_LATEST.yaml、tech-stack.md、bp-timing.sh、sync-status-from-epics.sh，不能伪称已运行。局部epic是交接载体，不构造项目总体路线图。

## 15. 风险与反合理化
影响High，功能故事P1。主要风险：全局CSS泄漏、硬编码高度、语言错误行挤压、键盘遮挡、嵌套滚动锁死、引用抢滚、浮动入口挡输入。反合理化：build通过≠整屏可用；position:fixed断言≠无遮挡；把所有滚轮转成翻页≠吸附体验。必须用几何、交互、截图证明，不删除旧失败断言来过关。

## 16. 步骤 → verify
所有命令在仓库根执行；浏览器测试用3100自有隔离服务、mock API且阻止未匹配/rag-api请求，禁止真实写入。新增workspace-viewport.qa.mjs是执行阶段第一项产物，目前不存在；其各mode必须分别断言对应场景，不允许像旧脚本一样参数被忽略。支持--self-test验证fixture路由、清理、故意失败非零退出，再添加行为红测。
1. 编写隔离自启动/清理测试驱动和mock契约，未知API拒绝、ready有截止时间、端口占用则失败不杀别人的进程 → verify: `node apps/web/tests/workspace-viewport.qa.mjs --self-test`
2. 保留masthead、删除topbar并完成动态首屏尺寸、消息内部滚动和输入稳定布局 → verify: `node apps/web/tests/workspace-viewport.qa.mjs --geometry`
3. 实现两屏柔性吸附、显式往返、长证据与引用定位/焦点、reduced-motion → verify: `node apps/web/tests/workspace-viewport.qa.mjs --scroll`
4. 重设计共用导航、迁移admin状态、保留角色/会话/账号行为，处理悬浮入口与键盘避让 → verify: `node apps/web/tests/workspace-viewport.qa.mjs --navigation`
5. 类型、既有代理单测及生产构建回归 → verify: `cd apps/web && npm run check && node --test tests/chat-proxy.test.mjs && npm run build`
6. 新构建下完整矩阵及旧侧栏套件，生成报告和截图供实际阅读 → verify: `node apps/web/tests/workspace-viewport.qa.mjs --all`
驱动各mode在测试前构建或验证构建与源码一致，再自启3100；--all启动服务后调用已有workspace-navigation.qa.mjs，最后清理。只更新旧测试中已获授权改变的布局预期，保留权限/焦点/会话断言。测试命令为待实现验收接口，计划阶段不执行、不标passing。

## 17. 验收标准
- 1440×900、1280×800、1001×800、1000×800、768×1024、390×844、360×800，中英各一次；另360×568和844×390低高度降级。普通用户及管理员；长资料库名/账号名/会话名、空/200轮、错误行、大段回答/多页证据。
- 正常高度下：conversation面板顶边贴可用区域顶，底边到视窗底，几何误差<=2px；第二屏起点为主scroller一页高度（误差<=2px），全局masthead可见，topbar不存在，无横溢出。输入框/发送矩形与悬浮入口不相交，末条消息可完整滚入可视区。
- transcript滚动不改变主scroller页；证据列表滚动不抢聊天；点击往返、引用、键盘Tab/Shift-Tab均可达；Esc/遮罩/跨断点释放锁与恢复焦点。测试webkit或真机若不可用必须记录限制，不伪造覆盖。
- 保留普通用户service-pill=0、admin=1（新位置）、无KB账号入口；刷新/分页去重、打开失败保留抽屉、删除确认/失败、新建/切库/URL恢复/忙碌禁用/退出成功失败回归。
- 手机浏览器地址栏变化、软键盘及200%缩放：高度重新计算、不裁切输入；低高度可降级非snap，但必须可滚动可退出。
- 回归登录、账号、四个管理员页面；不引入全局snap/body锁。截图包含聊天首屏、证据次屏、手机导航关闭及打开、长内容、英文，必须实际读取检查。

## 18. 范围外
不改后端/RAG/资料、不修模型503、不扩大系统路线图、不删全局masthead、不新增导航功能、不回退已上线角色或悬浮功能。旧全站验收缺口仍保留，不能以本故事覆盖全部历史待办。

## 19. 交付与部署
先测试后提交，提交冒号后中文，显式暂存；.gitignore已忽略运行产物，不提交截图/日志/用户资料。构建后仅恢复确认属生成改动的apps/web/next-env.d.ts。
实施完成需验证3000首页200及/rag-api/api/v1/auth/me未登录401，直连8080也是401；首页200不证明后端正常（此前8080未启动导致代理500）。服务task ID不稳定，先核实端口与归属，只停止自己拥有的旧前端，不误停后端。实际运行验收记录放specs/verifications/，不能只给出“构建成功”。

## 20. 门禁与执行交接
先运行 `bash scripts/lib/plan-consistency-check.sh specs/epics/e01-workspace-navigation/`；本仓库是局部结构检查，不验证实现或浏览器。新的tasks全为failing，只有对应verify退出0及证据齐全才passing。
已有feature branch，下一位执行者读本文件、e01s02-tasks.yaml和IMPACT_LATEST.md，确认无新增用户变更后从测试夹具开始develop-tdd。尚未获本轮产品实现授权：此计划供用户切换模型后执行。子代理探索因输入预算未启动，主代理已直接读源码，无独立审查结论。全局state.yaml不存在，不伪造中央生命周期；局部handoff记录于epic.yaml。用户需最后审阅步骤与验收范围。
