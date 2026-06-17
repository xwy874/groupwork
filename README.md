# Werewolf LLM Gateway (成员3 — Backend & Routing)

Unified async client that lets every agent in the Werewolf project talk to **any** model
(DeepSeek / MiniMax / GLM / Qwen) through one OpenAI-compatible endpoint, and
**guarantees** the reply parses into a shared Pydantic schema so 成员1's state machine
never has to parse natural language.

Covers Day 1–14 of the plan.

## Endpoint

| key | value |
|---|---|
| Base URL | `https://models.sjtu.edu.cn/api/v1` |
| Auth | Bearer token via `LLM_API_KEY` |
| Protocol | OpenAI `/chat/completions`, `response_format={"type":"json_object"}`, SSE streaming |

## Role → Model routing

| Role | Model | Reason |
|---|---|---|
| `villager` | `minimax-m2.7` | cheap, fast, plenty good for honest reasoning |
| `werewolf` / `seer` / `witch` / `hunter` / `guard` | `qwen3.5-27b` | better at deception / hidden-info reasoning |
| `moderator` | `deepseek-chat` | host narration |

Override any of these in `.env`:

```env
MODEL_VILLAGER=minimax-m2.7
MODEL_WEREWOLF=qwen3.5-27b
```

## Features by week

### Day 1–7 — gateway + structured output (`client.py`, `schemas.py`)

- `httpx.AsyncClient`, `BASE_URL`/`API_KEY` from `.env`
- One-call role→model dispatch
- Native `response_format={"type":"json_object"}`
- Pydantic `AgentResponse` schema; on validation failure the gateway re-prompts the model with the error up to `LLM_MAX_RETRIES` times → 100% schema-valid output.

### Day 8–9 — concurrency + 429 retry (`client.py`)

- `asyncio.Semaphore(LLM_MAX_CONCURRENCY)` caps in-flight requests so 5 agents firing at once don't trigger RPM limits.
- `tenacity.AsyncRetrying` with `wait_random_exponential` (exponential backoff + jitter), retrying **only** on `429`, `5xx`, `httpx.TimeoutException`, `httpx.NetworkError`. `4xx` other than 429 raise `FatalHTTPError` immediately — no wasted retries on auth/bad-request.

### Day 10–12 — sliding window + summary buffer (`context.py`)

```python
from werewolf_gateway import LLMGateway, SlidingWindowContext

async with LLMGateway() as gw:
    ctx = SlidingWindowContext(system="<role prompt>", keep_turns=2)
    for prompt in daily_prompts:
        ctx.add_user(prompt)
        resp = await gw.chat_json(role="werewolf", messages=ctx.messages())
        ctx.add_assistant(resp.model_dump_json())
        await ctx.maintain(gw)        # folds older turns into a summary
```

The oldest turns get compressed by the cheap summary model (`LLM_CTX_SUMMARY_MODEL`, default `minimax-m2.7`), so the prompt size **plateaus** instead of growing each round. Demo shows ~500 approx tokens after 5 days versus ~1500+ without pruning.

### Day 13–14 — streaming (`client.py:chat_stream`)

```python
async for delta in gw.chat_stream(role="moderator", messages=msgs):
    print(delta, end="", flush=True)
```

Plain SSE parser, yields content deltas as strings — drop-in for 成员5's CLI.

### Safety: `max_tokens` + timeout circuit breaker

- Per-request `timeout=30s` (`LLM_TIMEOUT`); a stuck connection cannot hang the game loop.
- Every payload's `max_tokens` is clamped to `LLM_MAX_TOKENS_HARD_CAP` (default 2048). Even if a teammate accidentally passes `max_tokens=999999`, the runaway-output bill is bounded.

### Optional: `instructor` adapter (`instructor_adapter.py`)

If you'd rather use the [instructor](https://github.com/jxnl/instructor) library:

```bash
pip install instructor openai
```

```python
from werewolf_gateway.instructor_adapter import ask
from werewolf_gateway import AgentResponse

resp: AgentResponse = ask("werewolf", [{"role":"user","content":"vote now"}])
```

Same Pydantic schema, sync API. Kept optional so the core has zero extra deps beyond httpx/pydantic/tenacity.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env       # already filled with the SJTU key

# Day 1-7 baseline
python -m werewolf_gateway.test_smoke         # connectivity + JSON validation
python -m werewolf_gateway.example            # 2-round speak->vote pipeline

# Day 8-9
python -m werewolf_gateway.demo_concurrent    # 5 agents in parallel, semaphore-gated

# Day 10-12
python -m werewolf_gateway.demo_context       # sliding window + summary buffer

# Day 13-14
python -m werewolf_gateway.demo_stream        # typewriter narration
```

## Public API for teammates

```python
from werewolf_gateway import LLMGateway, AgentResponse, SlidingWindowContext

async with LLMGateway() as gw:
    resp: AgentResponse = await gw.chat_json(
        role="werewolf",                        # role name OR raw model alias
        messages=[
            {"role": "system", "content": "<成员2's role prompt>"},
            {"role": "user",   "content": "<state machine's turn payload>"},
        ],
        max_tokens=400,                         # auto-clamped to hard cap
    )
    # resp.action / resp.target / resp.speech / resp.thought / resp.confidence

    # Or stream for UI:
    async for delta in gw.chat_stream(role="moderator", messages=msgs):
        ...
```

## Files

```
werewolf_gateway/
├── __init__.py            public surface
├── config.py              .env loader, model catalogue, role→model table, hard caps
├── schemas.py             AgentResponse Pydantic model + JSON contract string
├── client.py              LLMGateway: tenacity retries, semaphore, JSON repair, streaming
├── context.py             SlidingWindowContext + async summary buffer
├── instructor_adapter.py  optional instructor integration
├── example.py             2-round speak→vote demo (Day 1-7)
├── test_smoke.py          per-role connectivity check
├── demo_concurrent.py     parallel 5-agent demo (Day 8-9)
├── demo_context.py        sliding-window pruning demo (Day 10-12)
├── demo_stream.py         streaming demo (Day 13-14)
├── requirements.txt
├── .env / .env.example
└── README.md
```

## 12-player support

The defaults are now sized for the 12-seat advanced game (4 wolves + 4 villagers + seer/witch/hunter/guard).

| concern | what we did |
|---|---|
| 12 day-vote calls fire at once | `LLM_MAX_CONCURRENCY=10` + `LLM_MAX_CONCURRENCY_PER_MODEL=6` (8 of 12 agents share Qwen — per-model cap stops them from saturating one model) |
| Long games inflate prompts | `LLM_CTX_KEEP_TURNS=3` + summary buffer (`SlidingWindowContext`) |
| One slow agent blocks the vote | `GameSession.gather(..., timeout=30.0)` — per-seat timeout, no exception escapes |
| Trust-analysis (成员4) needs traces | `LLM_LOG_DIR=./logs` → JSONL with `{call_id, latency_ms, usage, response, seat, role, game_id}` |

### `GameSession` API for 成员1

```python
from werewolf_gateway import LLMGateway, GameSession

async with LLMGateway() as gw:
    game = GameSession(gw, game_id="g001")
    for seat, role, sysprompt in roster:        # 12 entries
        game.add_agent(seat, role, sysprompt)

    # one private night action
    seer_resp = await game.ask(seat=9, prompt="Pick a seat to inspect.")

    # 12-way day vote, fault tolerant
    votes = await game.gather(
        seats=game.alive_seats,
        prompt_builder=lambda s: f"Living: {game.alive_seats}. Cast your vote.",
        timeout=30.0,
    )
    # votes is dict[seat -> AgentResponse]; missing seats = timeout/failure

    game.kill(eliminated_seat)
```

### Verified live (33s for one full Night+Day on the 12-seat board)

```
--- NIGHT --- (8.6s, 7 parallel calls: 4 wolves + seer + witch + guard)
--- DAY  --- (25-30s, 12 parallel votes)
total : 33.6s, 25 calls, 7808 prompt tokens, 5603 completion tokens
```

### Run it

```bash
python -m werewolf_gateway.demo_12players
```



| var | default | meaning |
|---|---|---|
| `LLM_TIMEOUT` | `30` | per-request hard timeout (s) |
| `LLM_MAX_RETRIES` | `5` | tenacity attempts on 429/5xx/timeout |
| `LLM_MAX_CONCURRENCY` | `10` | global in-flight cap |
| `LLM_MAX_CONCURRENCY_PER_MODEL` | `6` | per-model in-flight cap |
| `LLM_MAX_TOKENS_DEFAULT` | `512` | when caller doesn't pass `max_tokens` |
| `LLM_MAX_TOKENS_HARD_CAP` | `2048` | absolute ceiling — runaway protection |
| `LLM_CTX_KEEP_TURNS` | `3` | recent turns kept verbatim |
| `LLM_CTX_SUMMARY_MODEL` | `minimax-m2.7` | model used to compress old turns |
| `LLM_LOG_DIR` | `` (off) | JSONL call-log directory; empty = disabled |
