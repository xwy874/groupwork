"""Day 13-14 demo: typewriter-style streaming (for 成员5's CLI/UI).

Run:
    python -m werewolf_gateway.demo_stream
"""
from __future__ import annotations

import asyncio
import sys

from werewolf_gateway import LLMGateway


async def main() -> None:
    messages = [
        {
            "role": "system",
            "content": "You are a dramatic Werewolf game moderator. Narrate vividly.",
        },
        {
            "role": "user",
            "content": "Open Day 1 in 4 short sentences. Mention the village waking up.",
        },
    ]
    async with LLMGateway() as gw:
        async for delta in gw.chat_stream(role="moderator", messages=messages, max_tokens=300):
            sys.stdout.write(delta)
            sys.stdout.flush()
        print()


if __name__ == "__main__":
    asyncio.run(main())
