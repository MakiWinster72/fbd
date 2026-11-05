- Markdown（`.md`）和纯文本（`.txt`）直接入库，无需 MinerU；支持 UTF-8（含 BOM）及带 BOM 的 UTF-16，保留 Markdown 正文、表格与代码；文本文件统一使用逻辑第 1 页作为引用页码；
- 按登录账号隔离的会话历史，支持刷新恢复、逐轮引用、新建与删除；
多个 API 进程处理文档时，必须连接**同一业务数据库**，并共享同一个 `RAG_DATA_DIR`（上传原件与规范化切块）。文档导入、重试、删除和重新向量化使用数据库租约协调；运行中的进程每 15 秒续租，崩溃后租约最长约 120 秒失效，再次启动、查询任务列表或重试时会将过期任务标记为失败。部署前先运行上面的 Alembic 迁移。租约不能为 Milvus 写入提供跨系统事务：数据库长期不可用或进程在写入中途失去租约时，仍需人工核对索引状态。导入时会先持久化索引提交意图，再写 Milvus，并原子替换本地切块缓存；对于失败或中断的**新文档**，服务启动、查询任务或重试时会按意图清理不完整索引。已有文档的重新嵌入失败不会删除旧文档，但极端故障下新旧向量可能混杂，仍需人工核验后重建。


建议使用该稳定入口，而不是直接填写 Python 模块路径；这样以后调整内部包结构时，启动命令无需变化。生产环境去掉 `--reload`。
uv run knowledge-rag serve --host 127.0.0.1 --port 8080 --reload
浏览器通过 Next.js rewrite 访问 FastAPI（大文件上传使用流式转发路由），无需额外开放 CORS。失败导入的原件保存在 `RAG_DATA_DIR/uploads/<job_id>/` 供重试；在管理员页面选择“清理失败原件”后无法再重试该任务，但不会删除已入库文档。生产环境启用 HTTPS 时，应设置：
文件导入与文档管理：http://127.0.0.1:3000/admin/documents
GET  /api/v1/admin/documents?knowledge_base_id=default
GET  /api/v1/admin/document-imports
POST /api/v1/admin/document-imports
POST /api/v1/admin/document-imports/{job_id}/retry
POST /api/v1/admin/document-imports/{job_id}/discard
## 会话历史

登录用户可在工作台选择历史会话、恢复消息、新建会话和删除当前会话。首问自动创建会话，浏览器 URL 的 `conversation` 参数支持刷新恢复；会话列表按知识库过滤。上线前运行 `uv run alembic upgrade head`，使用已有的业务数据库保存历史。

- `POST /api/v1/conversations`：创建；`GET /api/v1/conversations`：列表（`knowledge_base_id`、`limit`、`offset`）。
- `GET /api/v1/conversations/{id}`：详情；`DELETE /api/v1/conversations/{id}`：删除全部轮次。
- `POST /api/v1/chat` 可带 `conversation_id`，仅成功生成并完成保存后返回答案；失败不保存部分轮次，最多 200 轮。
- 只有会话所属账号可读写或删除，管理员也不能访问他人的会话。每次读取及保存检查当前知识库权限；撤权后隐藏历史，但所属账号仍可删除。
- 保存事务通过数据库锁与授权替换、账号及知识库状态修改串行化，取得锁后重新校验权限，不在模型调用期间持锁。撤权/停用先提交则不保存或返回该答案；保存先取得锁则允许先提交，随后的撤权会隐藏历史。SQLite 使用写事务，PostgreSQL 使用用户行锁与知识库共享锁，不依赖同进程互斥。
- 可运行 `uv run python scripts/check_conversation_concurrency.py` 验证两个连接在授权检查后和父行更新后的交错。需要 Docker 及已缓存的 `postgres:17-alpine`；脚本不拉取镜像，使用随机凭据、仅回环端口及临时容器存储，结束时删除容器。普通测试默认跳过 PostgreSQL；显式 `KNOWLEDGE_TEST_POSTGRES_URL` 仅可指向专用本机 `knowledge_history_qa_` 数据库，测试会清理其表，勿用于任何真实数据库。
- 每轮保存问题、答案、语言、依据状态及文件名/页码/摘要快照，不保存完整检索原文。早先轮次的引用不会被新答案替换；旧摘要并非当前索引状态或重新验证过的证据。
- 不带 `conversation_id` 的旧请求以及开发模式仍是无状态问答；带会话 ID 时只使用服务端保存且当前有权读取的同会话历史，不接受客户端伪造历史。

### 有限多轮上下文

- 读取最近最多 6 轮，每个历史问题最多 2,000 字符、历史答案最多 1,000 字符，总计不超过 12,000 字符；预算不够时丢弃较旧轮次，不跨过中间轮次拼接。未有依据的旧答案不进入上下文，旧引用快照不参与检索。
- 追问先调用当前聊天模型消解为独立检索问题，再在当前知识库重新检索。历史仅用于确定主题或指代，不直接传给最终回答模型作为证据；本轮引用编号与依据均来自本轮检索。
- 解析输出无效、指代不明、改变当前显式文件/页码限定，或页码无法落实到唯一已索引文件时要求澄清；没有新证据仍拒答。页码检索不允许悄悄退回全库搜索。模型调用异常返回通用 503；超时返回带重试提示的 504，避免暴露历史或供应商错误细节，并且不保存该轮。
- 完整换题由解析提示要求保留原问题；显式列举文档仍走确定性目录查询，仅匹配完整清单或数量请求，不将“第二份文档的预算”等内容问题当作目录。解析提示不是语义正确性或抗提示注入保证，复杂追问仍可能需要用户写明对象。
- 原始提问照常保存；刷新、重新登录或服务重启后可继续追问。每次追问可能额外消耗一次模型调用；不在模型执行期间持数据库锁，保存仍执行上述当前权限检查。

### 聊天等待与超时

一轮回答可能依次调用生成、证据核验，以及最多一次修复和复核；追问还会增加指代消解。OpenAI Compatible 聊天模型每次 HTTP 调用默认使用 60 秒 socket 超时，可在模型配置的 `options.timeout` 中设置正整数秒数。这不是整轮请求的总时限；检索和多次模型调用仍会累加。核验或修复超时不会返回未经核验的答案，也不会误报成“知识库依据不足”，而是返回 HTTP 504，供用户重试。

SiliconFlow 的证据核验请求默认发送 `enable_thinking: false`，减少推理模型在结构化核验上的等待；普通回答生成及其他供应商不自动改变思考模式。核验使用程序预先编号的回答段落与原文片段，模型返回编号而非复写文本，避免换行或引文复写差异导致误拒答；本地仍严格校验每段覆盖、全部引用、原文片段编号及核验结论。旧式逐字引文响应仍按原规则严格验证。模型配置可显式设置 `options.enable_thinking` 覆盖默认行为，也支持传递 `thinking_budget` 和 `reasoning_effort`（需供应商及模型支持）。

默认保留向量相关性门槛 `RAG_RETRIEVAL_MIN_SCORE`，重排分数只用于排序，不把不同模型的分数统一当成以 0.5 为界的相关概率。`RAG_RETRIEVAL_RERANK_MIN_SCORE` 未设置或留空时不启用重排硬门槛；只有使用当前模型和真实语料完成校准后才应显式设置 `(0, 1]` 的值。显式设置时仍使用独立重排门槛；模型回答始终必须基于检索证据并带有效引用，证据不足仍拒答。

## 回答质量评测

使用独立标注题集验证 Recall@K、MRR、引用、拒答与端到端延迟；支持在线 Cookie 登录和离线响应评分。真实资料题集与报告放在忽略目录 `data/evaluation/`，报告权限为 `0600`，示例仅使用合成资料。

```bash
uv run knowledge-rag evaluate data/evaluation/questions.jsonl \
  --base-url http://127.0.0.1:8080 --username evaluation-user \
  --output data/evaluation/baseline.json
```

默认交互输入密码。自动化可用 `--password-env` 指定环境变量名；非本机 API 必须 HTTPS。指标定义、标注规范、离线格式、退出码与引用语义核对限制见 [评测说明](evaluation/README.md)。


真实服务隔离验收（须先启动 MinerU、Milvus，并配置可用的活动模型）：

```bash
uv run --extra milvus --extra test python scripts/e2e_smoke.py
```

验收使用**合成 PDF/DOCX** 和临时 SQLite、临时 `RAG_DATA_DIR`、独立 Milvus Collection，不修改已有知识库；合成文本会发送至当前配置的 Embedding/Chat 服务。涵盖上传、MinerU 解析、检索问答、失败重试及失败原件清理。完成后自动删除测试 Collection 与临时目录；若进程被强制中断，需检查并清理 `rag_e2e_*` Collection 和 `/tmp/rag-e2e-*`。
uv run --with pytest --extra test pytest -q
npm --prefix apps/web run check
npm --prefix apps/web run build
