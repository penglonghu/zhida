# Proposal

## Why

账号目前只能通过环境变量 `AUTH_USERS` 配置：改一个账号要改 `.env` 并重启容器，密码哈希要手工跑脚本生成，多账号交付时运维成本高且容易出错。更关键的是会话安全：登录 token 是无状态 HMAC，只含用户名与过期时间，没有任何版本信息——改密码后旧 token 在 8 小时有效期内仍可继续访问，这是交付客户时不可接受的缺口。随着多租户、多账号交付，需要让账号可在线维护，并保证密码轮换、租户调整、删号能立即让旧会话失效。

## What Changes

- 账号数据从环境变量迁移到 Redis：按用户名存储密码哈希、租户与版本号；运维修改后下一次请求生效，无需重启。
- 新增运维 CLI `scripts/manage_users.py`，提供 `add` / `list` / `passwd` / `set-tenant` / `remove` 子命令；仅在本机 shell 执行，不新增 HTTP 管理端点。
- 首次启动时若 Redis 中没有任何账号，则从 `AUTH_USERS` 导入作为种子；导入后以 Redis 数据为准，环境变量不再覆盖已有账号。
- 密码策略：新建与轮换密码最少 10 位，并拒绝弱口令（常见口令、与用户名相同、项目名等）；黑名单内置在代码中，本期不做外部配置；不满足时 CLI 返回错误与原因，不写入存储。
- 账号操作语义：`add` 遇重名默认拒绝（409），需 `--force` 才覆盖；`passwd` / `set-tenant` / `remove` 目标不存在返回 404；用户名或租户为空返回 400。
- 会话绑定账号版本：登录 token 增加账号版本号；每次受保护请求校验账号仍存在且版本一致；改密码、改租户后旧 token 立即失效（401），删号后立即失效（401）。
- 认证开启且 Redis 不可用时 fail-closed：登录与受保护请求返回 503；`/health` 保持 200，`/ready` 如实报告 Redis 状态。
- 所有账号变更写入现有 `service.log` 审计日志（时间、目标账号、租户、动作），不记录密码明文或哈希。
- 保持不变：API Key 调用仍由 `TENANT_API_KEYS` 决定租户；`AUTH_ENABLED=false` 的开发态行为不变；不做旧 token 向下兼容（上线前一次性切换，用户重新登录）。

## Capabilities

### New Capabilities

- `account-management`: 账号的在线增删改、密码轮换、租户绑定、密码策略、会话立即失效，以及认证存储不可用时的失败语义。

### Modified Capabilities

（无）

## Impact

- `core/auth.py`：token 增加账号版本；新增密码策略校验与弱口令黑名单。
- 新增 `core/user_store.py`：Redis 账号读写（首次种子导入、版本自增、列举）。
- `core/middleware.py`：认证时按 token 中的用户名读取账号并校验版本；Redis 异常 fail-closed。
- `main.py`：登录改为读账号存储并以当前版本签发 token；登出不变。
- 新增 `scripts/manage_users.py`；`scripts/hash_password.py` 对齐 10 位策略。
- `config.py` / `.env.example` / `README.md`：说明账号迁移、`AUTH_USERS` 仅作种子、运维命令。
- 测试：账号存储单测（增删改、版本、并发自增）、密码策略、CLI 参数与退出码；接口测试覆盖改密后旧 Cookie 401、删号 401、改租户即时生效、重名 409、不存在 404、弱密码 400、Redis 故障 503。

## Non-goals

- 不做管理员角色与 Web 管理页面（本次只有具备服务器 shell 权限的运维能管理账号）。
- 不做自助注册、找回密码、邮箱/短信验证、单点登录。
- 不做细粒度 RBAC（仍是一账号一租户）。
- API Key 的生命周期管理不在本次范围（仍由环境变量配置）。
