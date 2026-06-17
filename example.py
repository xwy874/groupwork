"""Smoke-test: two-round speak→vote loop wired through the gateway.

Run:
    python -m werewolf_gateway.example
"""
from __future__ import annotations

import asyncio
import json

from werewolf_gateway import AgentResponse, LLMGateway


VILLAGER_SYS = """You are Player {seat}, a VILLAGER in a 6-player Werewolf game.
Living players: {alive}. Werewolves are unknown to you.
Your goal: find and vote out the werewolves. Be analytical, not random."""

WEREWOLF_SYS = """You are Player {seat}, a WEREWOLF in a 6-player Werewolf game.
Living players: {alive}. Your teammates: {teammates}.
Your goal: deceive villagers and survive votes. Sound like a villager."""


def build_messages(role: str, seat: int, alive, teammates, public_log):
    if role == "werewolf":
        sysmsg = WEREWOLF_SYS.format(seat=seat, alive=alive, teammates=teammates)
    else:
        sysmsg = VILLAGER_SYS.format(seat=seat, alive=alive)
    user = (
        "Public log so far:\n" + ("\n".join(public_log) if public_log else "(empty)")
        + "\n\nIt is your turn. Decide whether to `speak` (day phase) or `vote`. "
          "If voting, set `target` to a living seat number."
    )
    return [
        {"role": "system", "content": sysmsg},
        {"role": "user", "content": user},
    ]


async def run_round(gw: LLMGateway, players, public_log, phase: str):
    print(f"\n=== {phase} ===")
    for p in players:
        if not p["alive"]:
            continue
        msgs = build_messages(
            role=p["role"],
            seat=p["seat"],
            alive=[q["seat"] for q in players if q["alive"]],
            teammates=[q["seat"] for q in players if q["role"] == "werewolf" and q["seat"] != p["seat"]],
            public_log=public_log,
        )
        resp: AgentResponse = await gw.chat_json(role=p["role"], messages=msgs, max_tokens=400)
        line = f"P{p['seat']}({p['role']}): action={resp.action} target={resp.target} | {resp.speech}"
        print(line)
        if resp.speech:
            public_log.append(f"P{p['seat']}: {resp.speech}")
    return public_log


async def main():
    # 6-seat micro setup: 2 wolves, 4 villagers (no specials, this is just a pipeline check)
    players = [
        {"seat": 1, "role": "werewolf", "alive": True},
        {"seat": 2, "role": "villager", "alive": True},
        {"seat": 3, "role": "villager", "alive": True},
        {"seat": 4, "role": "werewolf", "alive": True},
        {"seat": 5, "role": "villager", "alive": True},
        {"seat": 6, "role": "villager", "alive": True},
    ]
    public_log: list[str] = []
    async with LLMGateway() as gw:
        public_log = await run_round(gw, players, public_log, "Day 1 — Speak")
        public_log = await run_round(gw, players, public_log, "Day 1 — Vote")
        public_log = await run_round(gw, players, public_log, "Day 2 — Speak")
        public_log = await run_round(gw, players, public_log, "Day 2 — Vote")

    print("\n--- final public log ---")
    print(json.dumps(public_log, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
