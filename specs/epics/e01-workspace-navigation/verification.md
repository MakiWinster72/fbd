---
type: verification
context: e01s01-workspace-navigation
status: partial
---
# 左侧导航实施与验证

用户m07500已授权实施。桌面260px左栏、手机抽屉、共用导航呈现、会话操作、管理员与账号入口已实现；原证据底部布局保留。没有修改后端/API权限、模型或用户数据。

## 已运行
- npm run check：通过。
- node --test apps/web/tests/chat-proxy.test.mjs：5项通过（实际在apps/web目录执行）。
- npm run build：最新b11fd6ac7通过，next-env.d.ts纯生成改动已恢复。
- node apps/web/tests/workspace-navigation.qa.mjs --all：bd8baac78退出0。脚本当前执行核心场景，并非全部验收矩阵，所有mode参数目前运行同一核心套件。
- 1280/360/1000/1001宽度：无横向溢出，桌面260px左栏，<=1000显示抽屉入口。
- 抽屉初始关闭按钮焦点、Escape关闭及焦点恢复；会话打开成功关闭并更新URL，新建清除URL；跨断点关闭。
- 英文普通用户：不展示管理员链接，导航名称英文；退出失败保留页面并显示英文错误。
- 中文管理员：入口正确；截图已实际读取，手机抽屉背景正常、桌面底部证据保留。截图与报告位于/tmp/knowledge-sidebar-qa/，属临时验收产物，不提交。
- git diff --check及局部计划一致性检查：通过。该检查不是完整bigpowers规划引擎。

## 已处理的验收夹具问题
最初复用浏览器修改auth mock时英文切换超时；改独立英文session后通过。先前手机截图捕获了弹层动画中间帧，改为等待动画结束，并实际读取新截图确认。

## 自审
- 正确性：history.open返回Promise<boolean>，仅当前成功恢复返回true；保留请求版本/权限/忙碌guard，失败不关闭抽屉。
- 安全：无新后端能力或权限放宽、无密钥、无真实用户写请求。测试数据隔离；写操作在浏览器中返回mock失败响应。
- 性能：仍仅一套会话hook；桌面/抽屉共享呈现，不新增列表请求或模型调用。
- 类型：无any/ignore绕过；TypeScript通过。useId避免多实例ID冲突。
- 清晰度：局部导航组件，无全局store；CSS限定workspace类。未做独立第二模型评审。

## 本轮补充验收
- b12d654de退出0：中英均覆盖1440/1280/1001/1000/768/360，无横向溢出，侧栏断点正确；git diff --check通过。
- Chromium原生dialog反向Tab后activeElement可为BODY，随后Tab返回关闭按钮。新增实测：背景触发按钮不能被程序聚焦；Escape及焦点恢复仍通过。原先要求焦点始终为dialog后代的断言不准确，已校正；不宣称严格环内循环。
- 本轮仅改验收脚本和记录，无产品行为改动。

## 尚未覆盖，不能标全量完成
分页去重、列表/恢复失败重试、长标题、回答中禁用、删除确认与成功刷新、切库历史隔离、URL首次恢复；完整正反向Tab序列/遮罩关闭、reduced-motion；退出成功、无KB账号浏览器入口；桌面/手机引用交互；登录/账号/管理页面完整回归。源码已确认无KB页面保留/account入口，但不是浏览器验证。

当前qa脚本依赖单独启动127.0.0.1:3100的隔离Next服务，尚未内置启动/停止服务。project tasks仍保留未覆盖项failing，不更改其他项目的全局tuxedo任务。核心功能可单独提交，不宣称完整计划验收结束。
