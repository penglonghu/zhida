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
    llm_model_name: str = "deepseek-chat"
    llm_timeout: int = 3000        # 单位：毫秒
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
    retrieve_top_k: int = 3
    retrieval_mode: str = "hybrid"        # dense / hybrid
    hybrid_dense_candidates: int = 20     # 混合检索：稠密召回候选数
    hybrid_keyword_candidates: int = 20   # 混合检索：关键词召回候选数
    rrf_k: int = 60                        # RRF 融合的平滑常数


settings = Settings()