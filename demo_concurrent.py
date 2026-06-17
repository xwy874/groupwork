"""Day 8-9 demo: 5 agents speak in parallel — gated by the semaphore.

Run:
    python -m werewolf_gateway.demo_concurrent
"""
from __future__ import annotations

import asyncio
import time

from werewolf_gateway import AgentResponse, LLMGateway


PLAYERS = [
    {"seat": 1, "role": "werewolf"},
    {"seat": 2, "role": "villager"},
    {"seat": 3, "role": "seer"},
    {"seat": 4, "role": "villager"},
    {"seat": 5, "role": "werewolf"},
]


def messages_for(p):
    return [
        {
            "role": "system",
            "content": (
                f"You are Player {p['seat']}, role={p['role']}, in a 5-player Werewolf game. "
                "Living seats: [1,2,3,4,5]."
            ),
        },
        {"role": "user", "content": "Day 1 — give a one-sentence opening speech and vote."},
    ]


async def one_agent(gw: LLMGateway, p) -> tuple[int, AgentResponse]:
    resp = await gw.chat_json(role=p["role"], messages=messages_for(p), max_tokens=300)
    return p["seat"], resp


async def main() -> None:
    async with LLMGateway() as gw:
        gw.settings.max_concurrency = 3  # showcase: only 3 of 5 fly at once
        # Reset semaphore to honor the new value
        import asyncio as _a
        gw._sem = _a.Semaphore(3)

        print(f"sending {len(PLAYERS)} requests in parallel; semaphore={gw.settings.max_concurrency}")
        t0 = time.perf_counter()
        results = await asyncio.gather(*(one_agent(gw, p) for p in PLAYERS))
        elapsed = time.perf_counter() - t0
        print(f"done in {elapsed:.1f}s\n")
        for seat, r in sorted(results):
            print(f"P{seat:>2} action={r.action} target={r.target} | {r.speech[:80]}")


if __name__ == "__main__":
    asyncio.run(main())
