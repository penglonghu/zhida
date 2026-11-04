import os
import time
from dotenv import load_dotenv
import requests

load_dotenv()

class LLMClient:
    def __init__(self):
        self.api_url = os.getenv("LLM_API_URL")
        self.api_key = os.getenv("LLM_API_KEY")
        self.model_name = os.getenv("LLM_MODEL_NAME")
        self.timeout = int(os.getenv("LLM_TIMEOUT", 3000)) / 1000
        self.max_retry = int(os.getenv("LLM_MAX_RETRY", 3))

        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        self.system_prompt = """
        你是知识库问答助手，严格遵守下面全部规则：
        1. 只能使用参考资料原文内容，没有相关信息固定输出：【当前资料中没有该问题相关内容，无法回答】，禁止编造。
        2. 输出格式强制要求：
        - 一级标题 # 单独一行，前后保留换行；
        - 二级标题 ## 单独一行；
        - 列表项以 `- ` 开头，每条单独一行；
        - 不同板块之间必须空一行（连续两个换行符 \n\n）；
        - 重点文字使用 **文字** 加粗；
        - 你输出的字符串里，必须真实写入换行符 \n，不能省略换行；
        - 禁止所有文字挤在同一行。

        输出样例：
        # 中国股票策略研报观点汇总
        研报日期：2026年7月23日｜覆盖机构：J.P.Morgan、MorganStanley

        ## 一、两家机构核心观点对比
        **J.P.Morgan（摩根大通）**
        - 核心观点：AI引发的去杠杆后，政策托底信号（PolicyPut）显现，2Q业绩将成为催化剂
        - 市场偏好：中性偏多，结构性强于总量
        - 关键风险：内需疲弱、房地产拖累、消费降级

        **MorganStanley（摩根士丹利）**
        - 核心观点：A股情绪在政策真空与IPO担忧中降温，但港股更具吸引力
        - 市场偏好：短期看港股，长期看A股科技
        - 关键风险：全球波动、IPO流动性虹吸、政策力度不及预期
        """

    def chat(self, user_query: str, temperature: float = 0.1) -> str:
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_query}
            ],
            "temperature": temperature,
            "stream": False
        }
        for attempt in range(self.max_retry):
            try:
                resp = requests.post(
                    self.api_url,
                    headers=self.headers,
                    json=payload,
                    timeout=self.timeout
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except Exception as e:
                if attempt == self.max_retry - 1:
                    raise e
                time.sleep(1)

    def stream_chat(self, user_query: str, temperature: float = 0.1):
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_query}
            ],
            "temperature": temperature,
            "stream": True
        }
        for attempt in range(self.max_retry):
            try:
                resp = requests.post(
                    self.api_url,
                    headers=self.headers,
                    json=payload,
                    timeout=self.timeout,
                    stream=True
                )
                resp.raise_for_status()
                # SSE解析
                for line in resp.iter_lines():
                    if not line:
                        continue
                    line = line.decode("utf-8")
                    if line.startswith("data: "):
                        chunk_data = line[6:]
                        if chunk_data == "[DONE]":
                            break
                        import json
                        obj = json.loads(chunk_data)
                        delta = obj["choices"][0]["delta"].get("content")
                        if delta:
                            yield delta
                return
            except Exception as e:
                if attempt == self.max_retry - 1:
                    raise e
                time.sleep(1)


llm_client = LLMClient()