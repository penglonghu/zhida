"""集中式配置：所有环境变量在此统一定义、校验、加载。

取代原来散落在各模块里的 load_dotenv() + os.getenv()：
- 类型化：字段声明即类型，int/float/bool 自动转换
- 启动即校验：配置缺失或类型错误，import 时就报错，而非运行中才炸
- 单点加载：实例化 Settings 时读一次 .env / 环境变量
- 字段即文档：看完这个类，就知道系统全部配置项
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",          # 相对启动时的工作目录；Docker 里由 compose 注入环境变量
        env_file_encoding="utf-8",
        case_sensitive=False,     # 环境变量 LLM_API_URL 自动匹配字段 llm_api_url
        extra="ignore",           # 忽略未声明的环境变量（如 PATH、HOME）
    )

    # ===== LLM 网关（DeepSeek OpenAI 兼容） =====
    llm_api_url: str
    llm_api_key: str
    llm_model_name: str = "deepseek-flash"
    llm_timeout: int = 60000       # 单位：毫秒（生成需时较长，3s 太短会频繁超时）
    llm_max_retry: int = 3
    llm_retry_delay_base: int = 2  # 预留：S1 指数退避基数
    llm_max_output_tokens: int = 2048

    # ===== 提示词 =====
    prompt_dir: str = "./prompts"
    prompt_reload_on_each_call: bool = True

    # ===== 服务 =====
    service_host: str = "0.0.0.0"
    service_port: int = 8000
    log_level: str = "INFO"

    # ===== Embedding =====
    embed_model_name: str = "./models/bge-small-zh"
    embed_device: str = "mps"      # auto / cpu / mps / cuda

    # ===== 文本分块 =====
    chunk_size: int = 500
    chunk_overlap: int = 80

    # ===== Qdrant 向量库 =====
    qdrant_host: str = "127.0.0.1"
    qdrant_port: int = 6333
    qdrant_collection_name: str = "zhida"
    vector_dim: int = 512

    # ===== 检索 =====
    retrieve_top_k: int = 5       # 合同/制度类长文块高度相似，3 条常装不进答案块
    list_query_top_k: int = 10            # 列举型问题（哪些/全部/几个）用更大的召回，避免漏经历/条目
    retrieval_mode: str = "hybrid"        # dense / hybrid
    hybrid_dense_candidates: int = 20     # 混合检索：稠密召回候选数
    hybrid_keyword_candidates: int = 20   # 混合检索：关键词召回候选数
    rrf_k: int = 60                        # RRF 融合的平滑常数
    rrf_dense_weight: float = 1.0          # RRF 稠密信号权重
    rrf_keyword_weight: float = 3.0        # RRF 关键词信号权重（精确 token 应更可信）

    # ===== 多轮对话 =====
    query_rewrite_enabled: bool = True     # 追问时先改写为独立查询再检索
    conversation_max_turns: int = 5        # 每个会话最多保留的问答轮数（省多轮 token）
    conversation_ttl_seconds: int = 3600   # 会话空闲过期时间（秒）

    # ===== 引用溯源 =====
    citation_verify_enabled: bool = False  # 引用校验：额外一次 LLM 调用校验 [n] 引用是否被资料支持（增延迟，默认关）

    # ===== 多租户 / 知识库隔离 =====
    default_tenant_id: str = "default"     # 未认证/未指定时的租户，兼容单租户部署
    default_kb_id: str = "default"         # 未指定时的知识库
    tenant_api_keys: str = ""              # "tenantA=keyA,tenantB=keyB"；SERVICE_API_KEY 映射到 default 租户

    # ===== 浏览器登录（Cookie 会话）=====
    auth_users: str = ""                   # "alice:<密码哈希>:tenantA,bob:<哈希>:tenantB"
    session_secret: str = ""               # 登录 Cookie 的 HMAC 签名密钥（必填，随机长串）
    session_ttl_seconds: int = 28800       # 登录有效期 8 小时
    cookie_secure: bool = False            # HTTPS 部署置 true
    cookie_name: str = "zhida_session"

    # ===== 安全可观测 =====
    auth_enabled: bool = False             # 是否开启认证（私有化交付时开，POC 先关）
    service_api_key: str = ""              # 程序调用密钥（映射 default 租户）
    rate_limit_enabled: bool = True        # 是否开启限流
    rate_limit_max_requests: int = 60      # 窗口内最大请求数
    rate_limit_window_seconds: int = 60    # 限流窗口（秒）

    # ===== Redis（多实例高可用）=====
    redis_enabled: bool = False            # 会话/限流共享存储：False=进程内存（单实例够用），True=Redis（多实例横向扩展）
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 0
    redis_prefix: str = "zhida"            # key 前缀：多环境/多租户隔离，避免 key 冲突


settings = Settings()