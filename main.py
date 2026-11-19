import sys
import anyio
from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
)

sys.stdout.reconfigure(encoding="utf-8")


async def main():
    options = ClaudeAgentOptions(
        system_prompt="너는 친절한 한국어 비서야",
        tools=[],
        setting_sources=[],  # CLAUDE.md 등 프로젝트 설정을 읽지 않음
    )

    async with ClaudeSDKClient(options=options) as client:
        while True:
            user = input("You: ").strip()
            if user in ("exit", "quit"):
                break
            if not user:
                continue

            await client.query(user)
            print("Claude: ", end="")
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            print(block.text, end="")
            print()


anyio.run(main)
