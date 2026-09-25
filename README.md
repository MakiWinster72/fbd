多个 API 进程处理文档时，必须连接**同一业务数据库**，并共享同一个 `RAG_DATA_DIR`（上传原件与规范化切块）。文档导入、重试、删除和重新向量化使用数据库租约协调；运行中的进程每 15 秒续租，崩溃后租约最长约 120 秒失效，再次启动、查询任务列表或重试时会将过期任务标记为失败。部署前先运行上面的 Alembic 迁移。租约不能为 Milvus 写入提供跨系统事务：数据库长期不可用或进程在写入中途失去租约时，仍需人工核对索引状态。


建议使用该稳定入口，而不是直接填写 Python 模块路径；这样以后调整内部包结构时，启动命令无需变化。生产环境去掉 `--reload`。
uv run knowledge-rag serve --host 127.0.0.1 --port 8080 --reload
浏览器通过 Next.js rewrite 访问 FastAPI（大文件上传使用流式转发路由），无需额外开放 CORS。失败导入的原件保存在 `RAG_DATA_DIR/uploads/<job_id>/` 供重试；在管理员页面选择“清理失败原件”后无法再重试该任务，但不会删除已入库文档。生产环境启用 HTTPS 时，应设置：
文件导入与文档管理：http://127.0.0.1:3000/admin/documents
GET  /api/v1/admin/documents?knowledge_base_id=default
GET  /api/v1/admin/document-imports
POST /api/v1/admin/document-imports
POST /api/v1/admin/document-imports/{job_id}/retry
POST /api/v1/admin/document-imports/{job_id}/discard

真实服务隔离验收（须先启动 MinerU、Milvus，并配置可用的活动模型）：

```bash
uv run --extra milvus --extra test python scripts/e2e_smoke.py
```

验收使用**合成 PDF/DOCX** 和临时 SQLite、临时 `RAG_DATA_DIR`、独立 Milvus Collection，不修改已有知识库；合成文本会发送至当前配置的 Embedding/Chat 服务。涵盖上传、MinerU 解析、检索问答、失败重试及失败原件清理。完成后自动删除测试 Collection 与临时目录；若进程被强制中断，需检查并清理 `rag_e2e_*` Collection 和 `/tmp/rag-e2e-*`。
uv run --with pytest --extra test pytest -q
npm --prefix apps/web run check
npm --prefix apps/web run build
