"""Day 10-12 demo: sliding-window context with async summary buffer.

Simulates 5 days of one werewolf's diary. With keep_turns=2, days 1..3 get
folded into a running summary by the cheap MiniMax model, while days 4..5
stay verbatim. Watch the approx-token count plateau instead of exploding.

Run:
    python -m werewolf_gateway.demo_context
"""
from __future__ import annotations

import asyncio

from werewolf_gateway import AgentResponse, LLMGateway, SlidingWindowContext


SYSTEM = (
    "You are Player 1, a WEREWOLF in a 6-player Werewolf game. "
    "Living seats are tracked in the user message. Be deceptive but not obvious."
)


DAILY_PROMPTS = [
    "Day 1: living=[1,2,3,4,5,6]. Give a one-line speech and vote.",
    "Day 2: living=[1,2,3,4,5]. P6 was voted out (villager). Speak and vote.",
    "Day 3: living=[1,2,3,4]. P5 was killed at night. Speak and vote.",
    "Day 4: living=[1,2,3]. P4 was voted out (your teammate). Speak and vote.",
    "Day 5: living=[1,2]. Final round — make your case.",
]


async def main() -> None:
    async with LLMGateway() as gw:
        ctx = SlidingWindowContext(system=SYSTEM, keep_turns=gw.settings.ctx_keep_turns)
        for day, prompt in enumerate(DAILY_PROMPTS, start=1):
            ctx.add_user(prompt)
            resp: AgentResponse = await gw.chat_json(
                role="werewolf",
                messages=ctx.messages(),
                max_tokens=250,
            )
            ctx.add_assistant(resp.model_dump_json())
            await ctx.maintain(gw)
            print(
                f"Day {day}: action={resp.action} target={resp.target} "
                f"| approx_tokens={ctx.approx_token_count():>4} "
                f"| turns_kept={len([1 for t in ctx._turns if t.user or t.assistant])}"
            )

        print("\n--- final running summary ---")
        print(ctx.summary or "(none yet)")


if __name__ == "__main__":
    asyncio.run(main())
