# Tasks

## 1. 检索层

- [x] 1.1 在 `Retriever.ensure_collection` 中为 `doc_name` 补建 KEYWORD payload 索引，验证：单测断言 `get_collection().payload_schema` 包含 `doc_name`
- [x] 1.2 实现 `list_documents(tenant_id, kb_id)`：按 scope scroll 取 `doc_name` payload 并聚合块数、按文档名稳定排序，验证：pytest 覆盖空库、同名聚合、租户/知识库隔离
- [x] 1.3 实现 `delete_document(tenant_id, kb_id, doc_name)`：先 `count` 再按过滤条件删除、调用 `_invalidate_cache()`、返回删除块数，验证：pytest 覆盖整份删除、计数为 0、其他 scope 同名文档不受影响
- [x] 1.4 新增 `tests/test_documents.py`，用可注入的 fake Qdrant client 覆盖 1.2/1.3，验证：`pytest -q tests/test_documents.py` 全部通过

## 2. API 层

- [x] 2.1 在 `schemas.py` 增加 `DocumentInfo`、`DocumentListResponse`、`DocumentDeleteResponse`，验证：pytest 序列化断言 + `/openapi.json` 中出现对应 schema
- [x] 2.2 在 `main.py` 增加 `GET /documents`，复用 `_scope_from()`，验证：TestClient 返回 200 且结构为 `{"documents": [{"doc_name", "chunk_count"}]}`
- [x] 2.3 在 `main.py` 增加 `DELETE /documents/{doc_name}`：空名或含 `/`、`\`、`..` 返回 400，未找到返回 404，成功返回 200 与 `removed_chunks`，验证：TestClient 覆盖三种状态码
- [x] 2.4 在 `README.md` 的 API 一览补充两个接口与“同名聚合、删除整份文档”的语义，验证：按 README 示例执行 curl 可复现文档描述的行为

## 3. 前端

- [x] 3.1 在 `chat.html` 知识库区域下方增加文档面板与样式，验证：本地打开页面无 JS 报错、面板正常渲染
- [x] 3.2 实现列表加载与渲染，并在进入页面、切换知识库、上传完成后自动刷新，验证：浏览器中切换知识库时列表随 scope 变化
- [x] 3.3 实现删除交互：`encodeURIComponent` 编码文档名、二次确认、成功后刷新列表、失败用 `showTip` 提示，验证：删除中文名文档后列表项消失且检索不再命中
- [x] 3.4 回归现有前端主流程：同步问答、流式问答、上传、清空对话，验证：手动各走一遍均正常

## 4. 集成验证

- [x] 4.1 用真实 Qdrant 验证两租户、两知识库的列举与删除隔离，验证：集成脚本或 pytest 标记用例通过
- [x] 4.2 端到端验证：上传 → 列举 → 问答命中 → 删除 → 问答不再命中，验证：curl 或浏览器操作结果符合预期
- [x] 4.3 运行 `ruff check .`、`python -m compileall -q core scripts main.py config.py schemas.py` 和现有 CI 离线导入冒烟，验证：全部通过

## Workflow follow-up

- 实现完成后运行 `openspec validate add-document-management --strict`。
- 评审通过后归档变更：`openspec archive add-document-management`。
