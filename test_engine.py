"""test_engine.py — 成员1 状态机引擎离线自测（不调真实 LLM，不花 token）。

用 FakeGameSession 顶替成员3的 GameSession：按 prompt 里要求的 action 返回
合法的脚本化响应，从而独立验证引擎的两大职责——

  1. 信息隔离：秘密信息（狼队频道、刀口、预言家查验结果）绝不泄漏给无权玩家，
     也绝不进入 public_log。
  2. 状态流转：整局能从开局推进到 game_over，规则（猎人开枪/女巫/守卫/胜负）正确。

运行：
    python -m werewolf_gateway.test_engine      # 直接跑，打印 PASS/FAIL
    pytest werewolf_gateway/test_engine.py       # 或用 pytest
"""

from __future__ import annotations

import asyncio
import re
from typing import Callable, Dict, List

from .engine import (
    EventType,
    Faction,
    GameEvent,
    IllegalTransition,
    LEGAL_TRANSITIONS,
    Phase,
    WerewolfGameEngine,
)
from .schemas import AgentResponse

_ALIVE_RE = re.compile(r"存活[^：:]*[：:]\s*\[([0-9,\s]*)\]")


def _parse_alive(prompt: str) -> List[int]:
    m = _ALIVE_RE.search(prompt)
    if not m:
        return []
    return [int(x) for x in re.findall(r"\d+", m.group(1))]


class FakeGameSession:
    """最小化的 GameSession 替身：记录每个座位收到的所有 prompt，脚本化作答。"""

    def __init__(self) -> None:
        self.agents: Dict[int, str] = {}          # seat -> role
        self.system_prompts: Dict[int, str] = {}
        self.prompts: Dict[int, List[str]] = {}   # seat -> 收到过的所有 prompt
        self.alive: Dict[int, bool] = {}
        self._stats = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    # --- 引擎依赖的接口 ---
    def add_agent(self, seat: int, role: str, system_prompt: str):
        self.agents[seat] = role
        self.system_prompts[seat] = system_prompt
        self.prompts[seat] = []
        self.alive[seat] = True

    def kill(self, seat: int) -> None:
        self.alive[seat] = False

    def stats_snapshot(self) -> Dict[str, int]:
        return dict(self._stats)

    async def ask(self, seat: int, prompt: str, *, max_tokens=None, **kw) -> AgentResponse:
        self.prompts[seat].append(prompt)
        self._stats["calls"] += 1
        return self._scripted(seat, prompt)

    async def gather(self, seats, prompt_builder: Callable[[int], str], *,
                     timeout=30.0, on_failure=None, **kw) -> Dict[int, AgentResponse]:
        out = {}
        for s in seats:
            out[s] = await self.ask(s, prompt_builder(s))
        return out

    # --- 脚本化“策略”（成员2真实 prompt 的占位）---
    def _scripted(self, seat: int, prompt: str) -> AgentResponse:
        alive = [x for x in _parse_alive(prompt) if x != seat] or [
            s for s in self.alive if self.alive.get(s) and s != seat
        ]
        target = min(alive) if alive else None

        def has(token: str) -> bool:
            return token in prompt

        if has("action='protect'"):
            return AgentResponse(thought="守一个", speech="", action="protect", target=target)
        if has("action='kill'"):
            return AgentResponse(thought="刀一个", speech="", action="kill", target=target)
        if has("action='check'"):
            return AgentResponse(thought="验一个", speech="", action="check", target=target)
        if has("action='save'") or has("action='poison'"):
            # 女巫固定 skip，保证刀口生效、便于观测死亡流转
            return AgentResponse(thought="先不动药", speech="", action="skip", target=None)
        if has("action='shoot'"):
            return AgentResponse(thought="带走一个", speech="", action="shoot", target=target)
        if has("action='vote'"):
            return AgentResponse(thought="投一个", speech="", action="vote", target=target)
        # 默认发言
        return AgentResponse(thought=f"{seat}号的内心想法", speech=f"我是{seat}号，我觉得有狼。",
                             action="speak", target=None)


class RecordingObserver:
    def __init__(self) -> None:
        self.events: List[GameEvent] = []

    def emit(self, event: GameEvent) -> None:
        self.events.append(event)


# 固定阵容，断言可预测：1-4狼，5-8民，9预言家,10女巫,11猎人,12守卫
FIXED_ROSTER = [
    (1, "werewolf"), (2, "werewolf"), (3, "werewolf"), (4, "werewolf"),
    (5, "villager"), (6, "villager"), (7, "villager"), (8, "villager"),
    (9, "seer"), (10, "witch"), (11, "hunter"), (12, "guard"),
]


async def _run() -> None:
    fake = FakeGameSession()
    obs = RecordingObserver()
    engine = WerewolfGameEngine(fake, roster=FIXED_ROSTER, observer=obs)
    winner = await engine.run()

    # ---------- 1. 状态机推进 ----------
    assert engine.phase.value == "game_over", f"未到 game_over，停在 {engine.phase}"
    assert winner in (Faction.WEREWOLF, Faction.GOD), f"胜方异常：{winner}"
    assert any(e.type == EventType.GAME_OVER for e in obs.events), "缺少 GAME_OVER 事件"
    print(f"[OK] 状态机推进到 game_over，胜方 = {winner.value}")

    # ---------- 2. 信息隔离：public_log 不含秘密 ----------
    public_blob = "\n".join(engine.public_log)
    assert "狼队私密频道" not in public_blob, "狼队频道泄漏进 public_log！"
    assert "解药" not in public_blob and "毒药" not in public_blob, "女巫私密信息泄漏！"
    # 刀口决定只在狼队频道，public_log 里不应出现“刀杀 N 号”这种夜间措辞
    assert "刀杀" not in public_blob, "夜间刀口措辞泄漏进 public_log！"
    print("[OK] public_log 不含任何秘密信息")

    # ---------- 3. 信息隔离：非狼玩家从未收到狼队频道 ----------
    wolves = {1, 2, 3, 4}
    leaked = []
    for seat, prompts in fake.prompts.items():
        blob = "\n".join(prompts)
        if seat not in wolves and "狼队私密频道" in blob:
            leaked.append(seat)
    assert not leaked, f"非狼玩家收到了狼队频道：{leaked}"
    print("[OK] 狼队频道仅狼人可见")

    # ---------- 4. 信息隔离：预言家查验结果仅预言家可见 ----------
    seer = 9
    leaked_seer = []
    for seat, prompts in fake.prompts.items():
        if seat == seer:
            continue
        blob = "\n".join(prompts)
        if "你最近一次查验" in blob:
            leaked_seer.append(seat)
    assert not leaked_seer, f"预言家查验结果泄漏给：{leaked_seer}"
    print("[OK] 预言家查验结果仅本人可见")

    # ---------- 5. 信息隔离：女巫刀口信息仅女巫可见 ----------
    witch = 10
    leaked_witch = []
    for seat, prompts in fake.prompts.items():
        if seat == witch:
            continue
        blob = "\n".join(prompts)
        if "狼队刀杀的目标" in blob:
            leaked_witch.append(seat)
    assert not leaked_witch, f"女巫刀口信息泄漏给：{leaked_witch}"
    print("[OK] 女巫刀口信息仅本人可见")

    # ---------- 6. 狼队友信息正确（修复初始化 bug）----------
    # 每只狼的 system prompt 都应列出其余三只狼
    for w in wolves:
        sp = fake.system_prompts[w]
        others = sorted(wolves - {w})
        assert str(others) in sp, f"狼{w}的队友列表错误：{sp}"
    print("[OK] 狼队友初始化正确（每只狼都看到其余三只）")

    # ---------- 7. 事件钩子产出 ----------
    kinds = {e.type for e in obs.events}
    for needed in (EventType.GAME_START, EventType.PHASE_CHANGE,
                   EventType.DEATH, EventType.SPEECH, EventType.GAME_OVER):
        assert needed in kinds, f"缺少事件类型 {needed}"
    print(f"[OK] 事件钩子产出 {len(obs.events)} 条事件，覆盖关键节点")

    # ---------- 8. 警长机制（警徽流）----------
    assert engine.sheriff_elected, "未举行警长竞选"
    assert any(e.type == EventType.SHERIFF for e in obs.events), "缺少 SHERIFF 事件"
    # 竞选产生过警长（脚本里全员上警 -> 必有当选）
    assert any(e.payload.get("stage") == "elected"
               for e in obs.events if e.type == EventType.SHERIFF), "无人当选警长"
    # ground_truth 里有竞选记录
    assert any(g["kind"] == "sheriff_election" for g in engine.ground_truth), \
        "ground_truth 缺少 sheriff_election"
    print("[OK] 警长竞选 + 警徽流转事件/记录齐全")

    # ---------- 9. 状态快照 / 序列化 ----------
    import json
    snap = engine.to_dict()
    json.dumps(snap, ensure_ascii=False)  # 必须 JSON 可序列化
    assert snap["winner"] == winner.value
    assert snap["phase"] == "game_over"
    assert len(snap["players"]) == 12
    # ground_truth 含夜间真实结算，供成员4 对齐
    assert any(g["kind"] == "night_resolve" for g in snap["ground_truth"]), \
        "snapshot 缺少 night_resolve 真相记录"
    # 真相里能拿到隐藏身份
    assert all("role" in p for p in snap["players"].values())
    print(f"[OK] to_dict() 可序列化，含 {len(snap['ground_truth'])} 条真相记录")

    # ---------- 10. 状态转移合法性校验 ----------
    # 引擎能正常跑通本身就证明所有转移合法；这里再正向验证非法转移会被拦截。
    bad = WerewolfGameEngine(FakeGameSession(), roster=FIXED_ROSTER)
    raised = False
    try:
        bad._set_phase(Phase.DAY_VOTE)  # INIT -> DAY_VOTE 非法
    except IllegalTransition:
        raised = True
    assert raised, "非法状态转移未被拦截"
    print("[OK] 非法状态转移被 IllegalTransition 拦截")

    print("\n=== 全部断言通过 ===")


def test_full_game():
    """pytest 入口。"""
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
