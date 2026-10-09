# 回答质量评测

`knowledge-rag evaluate` 对独立 JSONL 评测集逐题调用已有登录与问答 API，不改知识库、模型配置或索引。真实问题、答案、证据与报告可能敏感，请放在忽略目录 `data/evaluation/`；报告权限为 `0600`。示例 `examples.jsonl` **仅展示格式**，其中合成文件不属于当前知识库，不能直接当作真实服务合格基线。

## 标注

每行包含唯一 `id`、`question`、`knowledge_base_id`、`expect_grounded`；可选 `locale` 为 `zh-CN` 或 `en`。

- 可回答题必须给 `expected_sources`：`document_id` 或精确 `document_name`，可选正数 `page_start`/`page_end`。同时给 ID 和名字时需同时匹配；页码区间按重叠匹配。
- `answer_contains` 是可选的答案关键词（全部须出现，忽略大小写、空白和全半角差异）；不是语义判定。
- 无依据题使用 `expect_grounded: false`，不填预期证据与答案关键词。
- 不填参考答案或不要凭模型现有输出反向标注，应人工从原文确定答案和页码。建议覆盖人名、设备编号、金额、表格、跨文件问题、英文、无依据题和诱导题；保持评测集与调参集分离。

## 在线评测

先启动 API，并使用有目标知识库权限的账号：

```sh
uv run knowledge-rag evaluate data/evaluation/questions.jsonl \
  --base-url http://127.0.0.1:8080 --username evaluation-user \
  --output data/evaluation/baseline.json --top-k 6
```

密码交互输入，不放命令行。自动化时用 `--password-env RAG_EVAL_PASSWORD` 从现有环境变量读取。非本机地址必须 HTTPS；不允许带凭据的 URL，也不跟随登录重定向。结束尝试退出评测会话。在线运行会把问题及检索证据送至当前配置的模型；运行前确认该服务被允许处理资料。

## 离线评分

响应文件每行 `{"id":"题目ID","response":{"answer":"答案【1】","grounded":true,"sources":[{"index":1,"document_id":"文档ID","document_name":"文件.pdf","page_start":1,"page_end":1,"excerpt":"原文"}]}}`，IDs 必须与评测集完全一致：

```sh
uv run knowledge-rag evaluate data/evaluation/questions.jsonl \
  --responses data/evaluation/responses.jsonl \
  --output data/evaluation/offline.json
```

离线模式不调用服务，不报告伪造的模型延迟。

## 指标与边界

- `recall_at_k`：Top K 来源覆盖的预期证据比例；每题等权平均。返回不足 K 时按真实返回数量评估，不补齐，也不另外增大后端召回数量。
- `mrr`：第一个相关证据的倒数排名的平均值；无命中为 0。
- `citation_validity`：回答中的 `【编号】` 能否对应返回证据。
- `citation_evidence_precision`：引用编号对应的文件/页码是否在标注证据中。**这不验证每句话是否被原文支持**，语义正确性与完整性仍需人工复核报告中的答案和原文摘录。
- `refusal_accuracy`：无依据题是否 `grounded=false` 且没有证据/引用；另报告 grounded 标志准确率与可选关键词命中率。
- 在线报告端到端 `latency_mean_ms` 和最近秩 P95（包含失败请求耗时）；离线延迟为 null。
- 服务错误单列 `errors`，不偷偷丢弃；质量均值只对成功响应计算，应同时查看样本数。没有适用样本的指标为 null，而不是 100%。

可回答题严格通过条件：grounded 正确、预期证据全部召回、至少一个引用且所有引用有效并匹配标注、可选关键词全部出现；无依据题按拒答条件判定。退出码：0 表示无服务错误且通过率达到 `--min-pass-rate`（默认 1.0）；2 表示不达标或逐题服务错误；1 表示输入、认证或报告写入错误。阈值可调，但降低阈值不等于修复检索质量。

## 首次私有真实基线

已按原文独立标注并通过真实登录/问答 API 执行 12 题。原始标注及逐题答案保存在忽略目录，不提交私密资料。初始结果：服务错误 0，严格通过 1/12，Recall@6 和 MRR 均为 0.4；引用编号有效率 1.0，但文件/页码证据匹配率仅约 0.389。平均端到端延迟约 8.85 秒、P95 约 21.07 秒。**这不是合格的回答质量声明。**

基线暴露出指定文件召回偏移，以及模型正文已拒答却仍被后端标记 `grounded=true` 并返回来源的问题。保持原始标注与失败报告，不靠降低阈值或按模型输出改答案来提高分数；这些问题另列修复任务，修复后对原评测集复验。后续还需扩大样本、补充人工语义审核及保留独立测试集。
