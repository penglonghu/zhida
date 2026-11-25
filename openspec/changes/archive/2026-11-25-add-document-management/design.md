# Design

## Context

- 单个 Qdrant collection 同时存所有租户/知识库的数据，payload 含 `text`、`doc_name`、`chunk_index`、`tenant_id`、`kb_id`；`tenant_id`、`kb_id` 已建 KEYWORD 索引。
- `Retriever` 在进程内缓存全量 point、按 scope 的 id 列表和 BM25 索引；`add_chunks` / `clear` 会本地失效缓存，BM25 首次使用时才构建。
- 租户由 `AuthMiddleware` 写入 `request.state`，`main._scope_from()` 合并请求里的 `kb_id`；客户端不能指定租户。
- 当前没有稳定的文档 ID，文档身份实际上是 `(tenant_id, kb_id, doc_name)`。

## Goals / Non-Goals

**Goals:**

- 在现有数据模型上补齐文档级列举与删除，不引入新存储、不做数据迁移。
- 删除严格限制在当前租户 + 知识库，且删除后本地检索缓存立即一致。

**Non-Goals:**

- 文档重命名、单块编辑、上传去重/覆盖。
- 跨进程/跨实例的缓存失效广播。
- 文档级权限（继续复用租户 + 知识库隔离）。

## Decisions

### 1. 文档身份用 `doc_name`，不新增文档表

理由：payload 已有 `doc_name`，按 `(tenant_id, kb_id, doc_name)` 聚合即可满足列举与删除；新增文档表会引入双写一致性问题。代价是同名重复上传会被视为同一份文档，删除时一并移除。备选方案是给每次上传生成 `doc_id`，但需要回填历史数据，超出本次范围。

### 2. 接口形状

- `GET /documents?kb_id=`：返回 `{"documents": [{"doc_name": "...", "chunk_count": N}]}`。
- `DELETE /documents/{doc_name}?kb_id=`：返回 `{"msg": "...", "removed_chunks": N}`。

`doc_name` 放路径参数，前端用 `encodeURIComponent` 编码；服务端拒绝空值、`/`、`\`、`..`。备选方案是查询参数（`?doc_name=`），可避开路径编码问题，但语义上删除的是“某个文档资源”，路径参数更直观。租户与知识库仍走现有的 `_scope_from()`，不新增解析逻辑。

### 3. 列举直接查 Qdrant，不使用进程内缓存

`list_documents` 用 `scroll` 按 `(tenant_id, kb_id)` 过滤、只取 `doc_name` payload，在应用侧聚合计数。理由：进程缓存只在本地写入时失效，多实例部署下会读到过期结果；列举是低频操作，按 scope 扫描的代价可接受。备选方案是从 `_load_points()` 的缓存聚合（更快），但会放大既有缓存一致性问题。

### 4. 删除先计数再按过滤条件删除

先用 `count` 统计 `(tenant_id, kb_id, doc_name)` 的块数：为 0 返回 404；否则用 `FilterSelector` 删除并返回计数。理由：Qdrant 删除接口不保证返回受影响条数，先 count 才能给出可验证的响应。备选方案是先 scroll 出 id 再按 id 删除，往返次数更多，收益不大。

### 5. 给 `doc_name` 补 KEYWORD 索引

删除过滤会用到 `doc_name`，`ensure_collection` 启动时检查并补建该索引。已有部署重启后自动补齐，无需迁移脚本。

### 6. 删除后调用 `_invalidate_cache()`

本地进程的 point 缓存、scope id 列表、BM25 索引必须一起失效，保证“删除后检索一致”。其他实例的稠密检索直接查 Qdrant，结果正确；其 BM25 缓存可能短暂陈旧，属于现有架构的已知限制，不在本次范围内解决。

### 7. 前端交互

在知识库选择区下方新增文档面板：进入页面、切换知识库、上传完成、删除完成后刷新；删除前 `confirm` 二次确认；请求期间禁用按钮。文档名为空或请求失败时用现有 `showTip` 提示。

## Risks / Trade-offs

- [同名文档被当作一份] → 在规格中明确删除语义；未来若需要版本化，再引入 `doc_id` 并回填。
- [中文/特殊字符文档名的 URL 编码] → 前端统一 `encodeURIComponent`，测试用例覆盖中文名。
- [count 与 delete 之间并发写入] → 单写入方场景可接受；删除不存在的文档稳定返回 404。
- [多实例 BM25 缓存陈旧] → 稠密检索以 Qdrant 为准；在 README 标注限制，后续可加缓存失效广播或 TTL。
- [大库列举扫描成本] → 私域部署文档量有限；后续可换 Qdrant facet 或计数接口优化。

## Migration Plan

1. 发布代码；`ensure_collection` 自动补建 `doc_name` 索引。
2. 无数据迁移，历史文档按 `doc_name` 立即可见、可删除。
3. 回滚只需移除新路由与前端面板，已删除的数据不可恢复（删除是破坏性操作，前端二次确认）。
