# Design

## Context

- 认证现状：`AuthMiddleware.__init__` 在启动时用 `parse_users(settings.auth_users)` 缓存账号 `{username: (password_hash, tenant)}`；`core/middleware.py` 的 `_apply_auth` 从缓存取租户。改账号必须重启。
- Token：`core/auth.py` 的 `create_session_token` 只签 `{u, exp}`；`verify_session_token` 只校验签名与过期。
- 登录：`main.py` 的 `POST /login` 是同步函数，每次请求都 `parse_users(settings.auth_users)`。
- Redis：`core/redis_client.py` 提供懒加载 `redis.asyncio` 单例；`redis_enabled` 目前只控制会话/限流。账号存储在认证开启时**始终**依赖 Redis。
- 日志：`core/logging.py` 的 `setup_logging` 写 `logs/service.log`（RotatingFileHandler）；容器内 `logs/` 已挂载。
- 测试：`tests/` 用 `TestClient` + monkeypatch/stub；CI 只做 ruff + 编译 + 导入冒烟，不跑完整 pytest。

## Goals / Non-Goals

**Goals:**

- 账号增删改、密码轮换、租户绑定在线生效，无需重启。
- 改密 / 改租户 / 删号后旧 token 立即失效。
- 认证依赖的存储不可用时 fail-closed，且探测语义仍正确。

**Non-Goals:**

- 管理员角色 / Web 管理页 / RBAC。
- 自助注册、找回密码、SSO。
- API Key 生命周期管理（仍走 `TENANT_API_KEYS`）。

## Decisions

### 1. 账号存储结构：每用户一个 HASH + 版本自增

- Key：`{redis_prefix}:auth:user:{username}`，字段 `password_hash`、`tenant`、`version`、`updated_at`。
- 版本用 `HINCRBY ... version 1` 原子自增，用于 token 失效；删除用 `DEL`。
- 列举用 `SCAN MATCH {prefix}:auth:user:*`；账号数量级小，SCAN 可接受，避免再维护索引 SET。
- 理由：单 Hash 便于「只改租户」「只改密码」局部更新与原子版本自增；双写一个 SET 索引会多一处一致性负担。

### 2. 每请求实时查账号，不做进程内缓存

- `AuthMiddleware._apply_auth` 改为 `async`：解析 token → 按用户名 `HGETALL` → 校验存在性与 `version` 一致。
- 不做短 TTL 缓存：任何缓存都会把「立即失效」退化成「缓存期内仍有效」，违背本次核心目标。
- 代价：每次 Cookie 认证多一次内网 Redis 往返（亚毫秒级），可接受。

### 3. 故障语义：Cookie 认证 fail-closed，API Key 不受影响

- Cookie 路径查存储抛异常时，不立即返回，而是记录故障并继续尝试 API Key；API Key 命中则放行，否则返回 503。
- 公共路径（`/login`、`/me` 等）不因存储故障整体拦截；`/me` 在故障时按未认证返回，登录接口自身返回 503。
- `AUTH_ENABLED=false`：完全不访问账号存储，保持 `X-Tenant-Id` 开发态。
- 中间件顺序保持 `RateLimit -> Auth`：限流仍 fail-open，限流自身异常不改变 Auth 的 503 语义。
- `/ready`：在 `redis_enabled or auth_enabled` 时 ping Redis；`/health` 永远 200。

### 4. Token 格式：增加账号版本，旧 token 一律失效

- payload 改为 `{u, v, exp}`；`verify_session_token` 要求 `v` 存在，缺失即无效。
- 版本不匹配、账号已删除、版本缺失 → 401，迫使重新登录。
- 不保留兼容分支：上线一次性切换，文档写明用户需重新登录。

### 5. 种子导入：仅空库导入，HSETNX 幂等

- 启动时（FastAPI lifespan）若 `SCAN` 到 0 个账号，则解析 `AUTH_USERS` 并逐用户 `HSETNX`。
- `HSETNX` 保证多 worker 并发启动时不覆盖已写入账号；已有账号时不再导入。
- 导入失败（Redis 不可用）只记录错误，不阻止进程启动；请求期按 fail-closed 处理。
- `AUTH_USERS` 的格式错误项沿用 `parse_users` 的「跳过」策略。

### 6. 密码策略放在 `core/auth.py`

- 新增 `validate_password(password, username) -> Optional[str]`，返回中文错误原因或 `None`。
- 规则：长度 ≥ 10；不在内置弱口令集合；不等于用户名、不包含用户名（长度≥3 时）；不等于项目名 `zhida`。
- `scripts/hash_password.py` 复用同一函数（替换现有 8 位检查）。

### 7. CLI：在容器内执行，审计写入同一 `service.log`

- `scripts/manage_users.py` 子命令：`add` / `list` / `passwd` / `set-tenant` / `remove`。
- 推荐运行方式：`docker compose exec zhida python scripts/manage_users.py ...`，保证 Redis 地址与日志目录和主进程一致；宿主机直跑需自行对齐 `REDIS_HOST` 与 CWD。
- 退出码约定：`0` 成功；`2` 参数非法；`3` 不存在；`4` 冲突；`5` 弱密码；`1` 存储或未知错误。
- 审计日志用 `logging.getLogger("audit")`，经 `setup_logging()` 写入 `service.log`；成功与失败都记，字段只含用户名、租户、动作、结果，绝不记录密码或哈希。

### 8. 兼容与迁移

- 旧 token（无 `v`）全部失效，用户重新登录；已有 `AUTH_USERS` 在空库时自动导入。
- Redis 数据纳入备份范围（`scripts/backup.sh` 已有 Redis SAVE）。
- 不需要独立迁移脚本。

## Risks / Trade-offs

- **每次认证多一次 Redis 调用**：换来即时失效；若未来 QPS 很高，可考虑用账号版本号做 key 的本地只读缓存 + pub/sub 失效广播，但本次不做。
- **CLI 运行位置错误**：宿主机直跑会连到错误的 Redis / 写错日志文件；README 明确要求容器内执行。
- **Redis 部分损坏**：空库才自动导入，非空但缺账号时不会自动补齐，需人工 `add`；文档写明。
- **登录接口改异步**：`POST /login` 从 `def` 改 `async def`，需回归登录与 Cookie 行为。
