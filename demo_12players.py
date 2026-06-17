"""12-player load test: simulates one full Night→Day cycle of the 12-seat game.

Layout (per the spec):
    seats 1-4   werewolf  (Qwen)        -- night kill, day vote
    seats 5-8   villager  (MiniMax)     -- day vote
    seat  9     seer      (Qwen)        -- night check
    seat 10     witch     (Qwen)        -- night save/poison
    seat 11     hunter    (Qwen)        -- day vote (passive at night)
    seat 12     guard     (Qwen)        -- night protect

Run:
    python -m werewolf_gateway.demo_12players
"""
from __future__ import annotations

import asyncio
import time

from werewolf_gateway import AgentResponse, GameSession, LLMGateway


ROSTER = [
    (1,  "werewolf"), (2,  "werewolf"), (3,  "werewolf"), (4,  "werewolf"),
    (5,  "villager"), (6,  "villager"), (7,  "villager"), (8,  "villager"),
    (9,  "seer"),     (10, "witch"),    (11, "hunter"),   (12, "guard"),
]


def system_prompt(seat: int, role: str, teammates: list[int]) -> str:
    base = f"You are Player {seat} ({role}) in a 12-player Werewolf game."
    if role == "werewolf":
        base += f" Your wolf teammates: {teammates}. Living seats: 1..12."
        base += " You must blend in as a villager and not reveal your team."
    elif role == "seer":
        base += " Each night you may inspect one seat to learn if they are a wolf."
    elif role == "witch":
        base += " You hold one antidote and one poison. You cannot self-save."
    elif role == "hunter":
        base += " If voted out, you may shoot one player. If poisoned, you cannot."
    elif role == "guard":
        base += " Each night you may protect one seat (not the same person twice in a row)."
    else:
        base += " You have no special ability. Vote out wolves through reasoning."
    return base


async def night_phase(game: GameSession) -> None:
    print("\n--- NIGHT ---")
    t0 = time.perf_counter()

    # Wolves (parallel): each privately picks a kill target
    wolf_seats = [s for s, a in game.agents.items() if a.role == "werewolf" and a.alive]
    wolf_results = await game.gather(
        seats=wolf_seats,
        prompt_builder=lambda s: (
            "Night phase. Coordinate with your wolf teammates and propose a kill target "
            "(seat number 1-12, must be alive and not yourself). Use action='kill'."
        ),
        timeout=25.0,
        max_tokens=300,
    )

    # Specials (parallel): seer / witch / guard act simultaneously
    special_seats = [9, 10, 12]
    special_prompts = {
        9:  "Pick a seat to inspect tonight. Use action='check'.",
        10: ("Last night the wolves chose seat ?. You may save (action='save'), "
             "poison another seat (action='poison'), or skip (action='skip')."),
        12: "Pick a seat to protect tonight, not the same as last night. action='protect'.",
    }
    special_results = await game.gather(
        seats=special_seats,
        prompt_builder=lambda s: special_prompts[s],
        timeout=25.0,
        max_tokens=300,
    )

    elapsed = time.perf_counter() - t0
    print(f"night took {elapsed:.1f}s")
    for s in sorted(wolf_seats):
        if s in wolf_results:
            r = wolf_results[s]
            print(f"  P{s:>2} (wolf)  -> kill target={r.target}")
    for s in sorted(special_seats):
        if s in special_results:
            r = special_results[s]
            print(f"  P{s:>2} ({game.agents[s].role:>5}) -> action={r.action} target={r.target}")


async def day_phase(game: GameSession) -> None:
    print("\n--- DAY ---")
    t0 = time.perf_counter()
    alive = game.alive_seats

    # 12 parallel votes — the real load test for the gateway
    results = await game.gather(
        seats=alive,
        prompt_builder=lambda s: (
            f"Day vote. Living seats are {alive}. "
            "Cast your vote against the most suspicious player. "
            "Use action='vote' with target=<seat>."
        ),
        timeout=30.0,
        max_tokens=350,
    )

    elapsed = time.perf_counter() - t0
    print(f"day vote ({len(alive)} parallel calls) took {elapsed:.1f}s")
    tally: dict[int, int] = {}
    for s in alive:
        if s in results:
            r = results[s]
            print(f"  P{s:>2} ({game.agents[s].role:>8}) votes -> {r.target}")
            if r.target is not None:
                tally[r.target] = tally.get(r.target, 0) + 1
        else:
            print(f"  P{s:>2} ({game.agents[s].role:>8}) FAILED/TIMEOUT")
    if tally:
        top = max(tally.items(), key=lambda x: x[1])
        print(f"  -> top vote: P{top[0]} with {top[1]} votes")


async def main() -> None:
    async with LLMGateway() as gw:
        game = GameSession(gw, game_id="loadtest-12p")
        wolf_seats = [s for s, r in ROSTER if r == "werewolf"]
        for seat, role in ROSTER:
            teammates = [s for s in wolf_seats if s != seat] if role == "werewolf" else []
            game.add_agent(seat, role, system_prompt(seat, role, teammates))

        print(f"global concurrency = {gw.settings.max_concurrency}")
        print(f"per-model concurrency = {gw.settings.max_concurrency_per_model}")
        print(f"log dir = {gw.settings.log_dir!r}")

        t0 = time.perf_counter()
        await night_phase(game)
        await day_phase(game)
        total = time.perf_counter() - t0

        s = gw.stats
        print(f"\n=== SUMMARY ===")
        print(f"total wall time : {total:.1f}s")
        print(f"calls           : {s['calls']}")
        print(f"prompt_tokens   : {s['prompt_tokens']}")
        print(f"completion_tokens: {s['completion_tokens']}")


if __name__ == "__main__":
    asyncio.run(main())
