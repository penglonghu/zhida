# account-management Specification

## Purpose

让运维无需重启即可维护登录账号（增、删、改密、改租户），并在密码轮换、租户调整、删号后立即让旧会话失效。账号是认证数据，因此认证存储不可用时有明确的失败语义。

约定：CLI 用非零退出码 + stderr 消息表达「重名 / 不存在 / 参数非法」；HTTP 端使用真实状态码（401 / 503）。本 spec 中的「返回 409/404/400」在 CLI 场景下等价于「非零退出并说明原因」。

## Requirements

### Requirement: 账号存入 Redis 并热更新

系统 SHALL 将账号（用户名、密码哈希、租户、版本号）存储在 Redis，账号变更 SHALL 在下一次请求生效，无需重启服务。系统 SHALL 仅当 Redis 中没有任何账号时，从 `AUTH_USERS` 导入种子；Redis 已有账号时 SHALL NOT 用环境变量覆盖。

#### Scenario: 修改密码后无需重启即生效

- **WHEN** 运维通过 CLI 修改某账号密码，随后该账号用旧密码登录
- **THEN** 登录失败；用新密码登录成功，期间服务未重启

#### Scenario: 空账号库时从环境变量导入种子

- **WHEN** 服务启动时 Redis 中没有任何账号，且 `AUTH_USERS` 配置了 alice
- **THEN** alice 可在账号库中查到并正常登录

#### Scenario: Redis 已有账号时环境变量不覆盖

- **WHEN** Redis 中已有 alice，且运维通过 CLI 把 alice 的租户改为 tenantB，随后重启服务（`AUTH_USERS` 中 alice 仍为 tenantA）
- **THEN** alice 的租户仍为 tenantB

### Requirement: 账号增删改语义

系统 SHALL 提供 `add` / `list` / `passwd` / `set-tenant` / `remove` 子命令。`add` 遇已存在用户名 SHALL 默认拒绝且不修改数据，仅在指定 `--force` 时覆盖。`passwd`、`set-tenant`、`remove` 的目标账号不存在时 SHALL 返回 404 语义（非零退出并说明）。用户名或租户为空 SHALL 返回 400 语义。`list` SHALL NOT 输出密码哈希。

#### Scenario: 新增账号成功

- **WHEN** 运维对不存在的用户名执行 `add`，提供合规密码与租户
- **THEN** 命令成功退出，该账号可立即登录

#### Scenario: 重名新增被拒绝

- **WHEN** 运维对已存在的用户名执行 `add` 且未加 `--force`
- **THEN** 命令以非零状态退出并说明账号已存在，原账号的密码与租户保持不变

#### Scenario: --force 覆盖已有账号

- **WHEN** 运维对已存在的用户名执行 `add --force`
- **THEN** 原账号被新密码与新租户覆盖

#### Scenario: 操作不存在的账号

- **WHEN** 运维对不存在的用户名执行 `passwd`、`set-tenant` 或 `remove`
- **THEN** 命令以非零状态退出并说明账号不存在，账号库保持不变

#### Scenario: 空用户名或空租户

- **WHEN** 运维传入空用户名或空租户
- **THEN** 命令以非零状态退出并说明参数非法，账号库保持不变

#### Scenario: 列举不泄露哈希

- **WHEN** 运维执行 `list`
- **THEN** 输出包含用户名与租户，且不包含密码哈希

### Requirement: 密码策略

新建与轮换密码 SHALL 至少 10 位，且 SHALL 拒绝弱口令（常见口令、与用户名相同、项目名等）。校验失败时 SHALL NOT 写入账号库。

#### Scenario: 长度不足被拒绝

- **WHEN** 运维为账号设置 9 位密码
- **THEN** 命令以非零状态退出并说明长度要求，账号库保持不变

#### Scenario: 常见弱口令被拒绝

- **WHEN** 运维设置 `password123`、`1234567890`，或与用户名相同的密码
- **THEN** 命令以非零状态退出并说明密码过弱，账号库保持不变

### Requirement: 会话与账号版本绑定

登录 token SHALL 携带账号版本号。受保护请求 SHALL 校验 token 中的用户名仍存在且版本与当前账号一致；不一致或账号不存在 SHALL 返回 401。`passwd`、`set-tenant` 与 `remove` SHALL 使该账号已签发的 token 立即失效。旧格式（无版本号）token SHALL 一律视为无效。

#### Scenario: 改密后旧会话立即失效

- **WHEN** 用户 alice 已登录，运维修改 alice 的密码，随后 alice 用旧 Cookie 访问受保护接口
- **THEN** 系统返回 401；alice 用新密码重新登录后可正常访问

#### Scenario: 改租户后旧会话立即失效

- **WHEN** 用户 alice 已登录，运维把 alice 的租户从 tenantA 改为 tenantB，随后 alice 用旧 Cookie 访问
- **THEN** 系统返回 401；重新登录后请求作用域为 tenantB

#### Scenario: 删号后会话立即失效

- **WHEN** 用户 alice 已登录，运维删除 alice，随后 alice 用旧 Cookie 访问
- **THEN** 系统返回 401

#### Scenario: 账号未变更时会话保持有效

- **WHEN** 用户登录后，账号未发生任何变更，且在有效期内访问受保护接口
- **THEN** 请求正常通过

#### Scenario: 旧格式 token 被拒绝

- **WHEN** 请求携带改造前签发的、不含版本号的 token
- **THEN** 系统返回 401，要求重新登录

#### Scenario: 过期 token 被拒绝

- **WHEN** 请求携带已超过有效期（`SESSION_TTL_SECONDS`）的 token
- **THEN** 系统返回 401，要求重新登录

### Requirement: 登录失败语义

密码错误 SHALL 返回 401 且 SHALL NOT 下发会话 Cookie；用户名不存在 SHALL 与密码错误表现一致，不泄露账号是否存在。

#### Scenario: 密码错误

- **WHEN** 用户提交存在的用户名与错误密码
- **THEN** 系统返回 401，响应中不包含会话 Cookie

#### Scenario: 用户名不存在不泄露信息

- **WHEN** 用户提交不存在的用户名
- **THEN** 系统返回与「密码错误」相同的状态码和错误信息

### Requirement: 认证存储不可用时 fail-closed

认证开启且账号存储不可用时，系统 SHALL 拒绝登录与受保护的 Cookie 认证请求，返回 503；`/health` SHALL 保持 200，`/ready` SHALL 报告不健康。API Key 认证 SHALL NOT 依赖账号存储，存储不可用时仍可通过认证。

#### Scenario: 账号存储故障时登录失败

- **WHEN** Redis 不可用，用户提交登录请求
- **THEN** 系统返回 503，不下发会话 Cookie

#### Scenario: 账号存储故障时受保护请求被拒

- **WHEN** Redis 不可用，用户携带之前有效的 Cookie 访问受保护接口
- **THEN** 系统返回 503，而不是放行

#### Scenario: 探针语义保持正确

- **WHEN** Redis 不可用
- **THEN** `/health` 返回 200，`/ready` 返回 503 并指出 Redis 不健康

#### Scenario: API Key 调用不受影响

- **WHEN** Redis 不可用，请求使用有效的 API Key 调用受保护接口
- **THEN** 认证通过（认证环节不依赖账号存储）

#### Scenario: 开启认证但无任何可用账号

- **WHEN** 认证开启，账号库为空且未配置任何 API Key，请求访问受保护接口
- **THEN** 系统拒绝请求（fail-closed），不因「没有账号」而放行

### Requirement: 认证关闭时行为不变

`AUTH_ENABLED=false` 时，系统 SHALL 保持现有开发态行为：不查询账号存储，允许 `X-Tenant-Id` 指定租户。

#### Scenario: 开发态不依赖账号存储

- **WHEN** 认证关闭且 Redis 中没有账号，请求携带 `X-Tenant-Id: dev`
- **THEN** 请求被放行，作用域租户为 dev

### Requirement: 账号变更审计日志

每次账号变更操作（成功或失败）SHALL 在 `service.log` 记录一条审计日志，包含时间、目标账号、动作与结果；SHALL NOT 记录密码明文或哈希。

#### Scenario: 成功变更可追溯

- **WHEN** 运维成功执行 `add`、`passwd`、`set-tenant` 或 `remove`
- **THEN** `service.log` 中存在对应审计记录，且记录中不含密码明文或哈希

#### Scenario: 失败操作被记录

- **WHEN** 运维执行因重名或弱密码而失败的变更
- **THEN** `service.log` 中存在失败审计记录，且记录中不含密码明文或哈希
