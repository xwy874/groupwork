"""Minimal connectivity + JSON-mode test. Run before handing off to 成员1/成员2.

    python -m werewolf_gateway.test_smoke
"""
from __future__ import annotations

import asyncio

from werewolf_gateway import AgentResponse, LLMGateway
from werewolf_gateway.config import DEFAULT_ROLE_MODEL, GatewaySettings


async def check_role(gw: LLMGateway, role: str) -> None:
    msgs = [
        {
            "role": "system",
            "content": f"You are a {role} in a Werewolf game, seat 3. Living seats: [1,2,3,4,5].",
        },
        {
            "role": "user",
            "content": "It is the day phase. Cast your vote against the most suspicious player.",
        },
    ]
    resp: AgentResponse = await gw.chat_json(role=role, messages=msgs, max_tokens=300)
    model = gw.settings.resolve_model(role)
    print(f"[{role:>9}] -> {model:<18} action={resp.action} target={resp.target} "
          f"speech={resp.speech[:60]!r}")


async def main() -> None:
    settings = GatewaySettings()
    print(f"base_url = {settings.base_url}")
    print("role -> model mapping:")
    for r, m in settings.role_model.items():
        print(f"  {r:>9} -> {settings.resolve_model(r)}")
    print()

    async with LLMGateway(settings) as gw:
        for role in ("villager", "werewolf", "seer"):
            try:
                await check_role(gw, role)
            except Exception as e:  # noqa: BLE001
                print(f"[{role}] FAILED: {e}")


if __name__ == "__main__":
    asyncio.run(main())
