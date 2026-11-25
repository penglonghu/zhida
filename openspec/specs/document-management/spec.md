# document-management Specification

## Purpose

让用户在当前租户和知识库范围内查看已有文档，并删除单份文档，而不必清空整个知识库。删除只作用于指定范围，不影响其他租户、知识库或其他文档。

## Requirements

### Requirement: 列出当前知识库的文档

系统 SHALL 返回当前 `(tenant_id, kb_id)` 范围内的文档名及每个文档的文本块数量。同名文档的多个文本块 SHALL 聚合为一条记录，结果 SHALL 按文档名稳定排序。空知识库 SHALL 返回空列表而非错误。

#### Scenario: 空知识库返回空列表

- **WHEN** 用户对没有任何文档的知识库调用 `GET /documents`
- **THEN** 系统返回 200，文档列表为空数组

#### Scenario: 同名文档聚合块数

- **WHEN** 知识库中存在文档 A 的 3 个文本块和文档 B 的 1 个文本块，用户调用 `GET /documents`
- **THEN** 系统返回两条记录：A 的块数为 3，B 的块数为 1

#### Scenario: 按租户与知识库隔离列举

- **WHEN** 租户 T1 的知识库 KB1 中有文档 A，租户 T2 的知识库 KB1 中有文档 B，T1 用户调用 `GET /documents`
- **THEN** 系统只返回文档 A，不返回文档 B

### Requirement: 删除指定文档

系统 SHALL 删除当前 `(tenant_id, kb_id)` 范围内指定文档名的全部文本块，并返回被删除的块数。删除操作 SHALL NOT 影响其他文档或其他租户、知识库中的同名文档。

#### Scenario: 成功删除整份文档

- **WHEN** 用户对存在的文档 A 调用 `DELETE /documents/A`
- **THEN** 系统返回 200 和删除的块数，随后文档列表中不再包含 A

#### Scenario: 同名重复上传的块一并删除

- **WHEN** 同一文档名被重复上传过多次，用户调用 `DELETE /documents/<文档名>`
- **THEN** 该文档名下的全部文本块都被删除

#### Scenario: 删除不存在的文档

- **WHEN** 用户对知识库中不存在的文档名调用 `DELETE /documents/<文档名>`
- **THEN** 系统返回 404，且知识库内容保持不变

#### Scenario: 不误删其他租户的同名文档

- **WHEN** T1 与 T2 的知识库中都有同名文档 A，T1 用户调用 `DELETE /documents/A`
- **THEN** 只删除 T1 范围内的 A，T2 的 A 不受影响

### Requirement: 文档名参数校验

系统 SHALL 拒绝空文档名，以及包含路径分隔符（`/`、`\`）或 `..` 的文档名，并返回 400。

#### Scenario: 拒绝非法文档名

- **WHEN** 用户调用 `DELETE /documents/..` 或传入空文档名
- **THEN** 系统返回 400，且不删除任何数据

### Requirement: 删除后检索一致

文档删除成功后，后续问答检索 SHALL NOT 再返回该文档的文本块。

#### Scenario: 删除后不再被检索命中

- **WHEN** 用户删除文档 A 后再次提出一个原本会命中 A 的问题
- **THEN** 答案的参考来源中不包含文档 A
