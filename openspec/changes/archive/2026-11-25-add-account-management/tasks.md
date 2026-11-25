# Tasks

## 1. 账号存储层

- [x] 1.1 新增测试用 fake Redis（内存实现 `hgetall/hset/hsetnx/hincrby/scan_iter/del/exists/ping`），先写失败测试覆盖用户读取、列举、新增、重名、`--force` 覆盖、改密、改租户、删除、版本自增
- [x] 1.2 实现 `core/user_store.py`：key 规则、`get_user`、`list_users`、`add_user`、`set_password`、`set_tenant`、`remove_user`，验证：`pytest -q tests/test_user_store.py` 全绿
- [x] 1.3 实现 `seed_from_env()`：仅当账号库为空时从 `AUTH_USERS` 用 `HSETNX` 导入，验证：单测覆盖空库导入、非空库不覆盖、重复调用幂等

## 2. 密码策略

- [x] 2.1 在 `core/auth.py` 增加 `validate_password(password, username)` 与内置弱口令集合，先写测试覆盖 9 位、常见口令、与用户名相同、项目名，验证：单测全绿
- [x] 2.2 `scripts/hash_password.py` 改为调用 `validate_password`（不再单独写 8 位判断），验证：短密码被拒且提示来自同一规则

## 3. Token 账号版本

- [x] 3.1 `create_session_token` 增加 `v` 参数写入 payload；`verify_session_token` 要求 `v` 存在，先写测试覆盖「含 v 可解」「缺 v 无效」「签名错误无效」「过期无效」
- [x] 3.2 核对所有 token 签发/校验调用点（`main.login`、`middleware`），验证：全仓库无遗漏的旧签名调用

## 4. 认证链路与启动

- [x] 4.1 `AuthMiddleware._apply_auth` 改 async：按 token 用户名实时查账号并校验版本；账号不存在或版本不一致返回 401，验证：接口测试
- [x] 4.2 Redis 异常时 Cookie 认证 fail-closed 返回 503，但 API Key 仍可通过；公共路径（`/login`、`/me`）不整体拦截，验证：接口测试
- [x] 4.3 `POST /login` 改 async，从账号存储校验密码并以当前版本签发 token；存储异常返回 503，验证：接口测试
- [x] 4.4 增加 FastAPI lifespan：认证开启时执行一次 `seed_from_env()`，失败只记日志，验证：单测/启动日志
- [x] 4.5 `/ready` 在 `auth_enabled` 时也检查 Redis；`/health` 保持 200，验证：接口测试

## 5. 运维 CLI 与审计

- [x] 5.1 新增 `scripts/manage_users.py`：`add` / `list` / `passwd` / `set-tenant` / `remove`，退出码 0/2/3/4/5/1，验证：CLI 函数级测试与手工命令
- [x] 5.2 审计日志：成功与失败各记一条到 `service.log`，只含用户名/租户/动作/结果，验证：日志断言不含密码或哈希
- [x] 5.3 `list` 输出不包含密码哈希，验证：CLI 测试断言

## 6. 测试与验收

- [x] 6.1 `tests/` 接口测试：改密 / 改租户 / 删号后旧 Cookie 立即 401，未变更会话仍 200
- [x] 6.2 接口测试：错误密码 401、不存在用户名与错误密码同样表现、旧格式 token 401、过期 token 401
- [x] 6.3 接口测试：重名冲突、账号不存在、空参数、弱密码的失败语义
- [x] 6.4 真实 Redis 集成测试（不可达自动跳过、独立前缀并在结束清理）：热更新、种子导入、版本失效
- [x] 6.5 手工验证：容器内 `manage_users.py` 建账号 → 登录 → 改密 → 旧会话 401 → 新密码登录；删号后 401

## 7. 文档与门禁

- [x] 7.1 更新 `README.md` / `.env.example`：账号迁移说明（AUTH_USERS 仅作种子）、CLI 用法（容器内执行）、旧 token 需重新登录、Redis 备份范围
- [x] 7.2 回归：认证关闭开发态、API Key 调用、问答与文档管理接口不受影响
- [x] 7.3 运行 `ruff check .`、`python -m compileall -q core scripts main.py config.py schemas.py`、`openspec validate add-account-management --strict`，验证：全部通过

## Workflow follow-up

- 实现完成后运行 `openspec validate add-account-management --strict`。
- 评审通过后归档：`openspec archive add-account-management`。
