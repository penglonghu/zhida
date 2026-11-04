import asyncio

from core.llm import llm_client


async def main():
    print("===== 同步调用测试 =====")
    res = await llm_client.chat("1+1等于几？")
    print(res)

    print("\n===== 流式调用测试 =====")
    async for c in llm_client.stream_chat("简单介绍RAG是什么"):
        print(c, end="")
    print()


if __name__ == "__main__":
    asyncio.run(main())
