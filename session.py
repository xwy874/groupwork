"""GameSession: 12 AgentSession bundles wired to a single LLMGateway.

Goal: give 成员1's state machine a one-line interface for any agent action,
without leaking gateway internals (semaphores, context pruning, logging).

Typical use:

    async with LLMGateway() as gw:
        game = GameSession(gw, game_id="g001")
        for seat, role, sysprompt in roster:
            game.add_agent(seat, role, sysprompt)

        # Single-agent ask (e.g. seer's night check)
        resp = await game.ask(seat=3, prompt="Pick a seat to inspect.")

        # Parallel ask (e.g. day-vote across all 12 alive players)
        results = await game.gather(
            seats=alive_seats,
            prompt_builder=lambda s: f"Seat {s}: cast your vote.",
            timeout=20.0,
        )
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel

from .context import SlidingWindowContext
from .schemas import AgentResponse

if TYPE_CHECKING:
    from .client import LLMGateway

T = TypeVar("T", bound=BaseModel)


@dataclass
class AgentSession:
    seat: int
    role: str
    system_prompt: str
    ctx: SlidingWindowContext = field(init=False)
    alive: bool = True

    def __post_init__(self) -> None:
        self.ctx = SlidingWindowContext(system=self.system_prompt)


class GameSession:
    """One game-instance worth of agents.

    Notes:
        - Channels are intentionally NOT modeled here — the state machine (成员1) decides
          who sees what and pushes the right `prompt` into each agent. We only own
          per-agent context + concurrency + logging.
    """

    def __init__(
        self,
        gateway: "LLMGateway",
        game_id: Optional[str] = None,
    ) -> None:
        self.gateway = gateway
        self.game_id = game_id or f"g{int(time.time())}"
        self.agents: Dict[int, AgentSession] = {}

    # ---- roster ----
    def add_agent(self, seat: int, role: str, system_prompt: str) -> AgentSession:
        a = AgentSession(seat=seat, role=role, system_prompt=system_prompt)
        a.ctx.keep_turns = self.gateway.settings.ctx_keep_turns
        self.agents[seat] = a
        return a

    def kill(self, seat: int) -> None:
        if seat in self.agents:
            self.agents[seat].alive = False

    @property
    def alive_seats(self) -> List[int]:
        return sorted(s for s, a in self.agents.items() if a.alive)

    # ---- single ask ----
    async def ask(
        self,
        seat: int,
        prompt: str,
        *,
        schema: Type[T] = AgentResponse,  # type: ignore[assignment]
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> T:
        agent = self.agents[seat]
        agent.ctx.add_user(prompt)
        resp = await self.gateway.chat_json(
            role=agent.role,
            messages=agent.ctx.messages(),
            schema=schema,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        agent.ctx.add_assistant(json.dumps(resp.model_dump(), ensure_ascii=False))
        await agent.ctx.maintain(self.gateway)
        self.gateway._log({  # type: ignore[attr-defined]
            "ts": time.time(),
            "game_id": self.game_id,
            "seat": seat,
            "role": agent.role,
            "kind": "agent_response",
            "response": resp.model_dump(),
        })
        return resp

    # ---- parallel batch ----
    async def gather(
        self,
        seats: List[int],
        prompt_builder: Callable[[int], str],
        *,
        timeout: float = 30.0,
        schema: Type[T] = AgentResponse,  # type: ignore[assignment]
        max_tokens: Optional[int] = None,
        on_failure: Optional[Callable[[int, Exception], T]] = None,
    ) -> Dict[int, T]:
        """Run `ask` for many seats in parallel.

        - `timeout` applies to each individual seat (not the whole batch).
        - If a seat fails or times out and `on_failure` is provided, its return
          value is substituted; otherwise the seat is dropped from the result map.
          Either way, no exception escapes — so a slow agent never blocks the day vote.
        """

        async def _one(seat: int) -> tuple[int, Optional[T]]:
            try:
                resp = await asyncio.wait_for(
                    self.ask(
                        seat=seat,
                        prompt=prompt_builder(seat),
                        schema=schema,
                        max_tokens=max_tokens,
                    ),
                    timeout=timeout,
                )
                return seat, resp
            except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
                if on_failure is not None:
                    return seat, on_failure(seat, e)
                self.gateway._log({  # type: ignore[attr-defined]
                    "ts": time.time(),
                    "game_id": self.game_id,
                    "seat": seat,
                    "kind": "agent_failure",
                    "error": repr(e),
                })
                return seat, None

        results = await asyncio.gather(*(_one(s) for s in seats))
        return {s: r for s, r in results if r is not None}

    # ---- bookkeeping ----
    def stats_snapshot(self) -> Dict[str, int]:
        return dict(self.gateway.stats)
