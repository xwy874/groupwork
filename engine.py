"""engine.py — 12人狼人杀状态机引擎（成员1：系统架构与状态机管理）

成员1的两项核心职责在本文件中实现：

1. 信息隔离（谁能看到什么）——架构强制，而非靠日志切片碰巧隐藏。
   - public_log:   仅白天公开事件，所有存活玩家可见。
   - wolf_channel: 狼队夜间讨论，仅狼人可见。
   - 预言家查验结果 / 女巫刀口与用药 / 守卫守护记录 —— 仅注入对应角色自己的 prompt。
   每个玩家收到的 prompt 由 `_build_view(seat, phase)` 统一组装，是唯一信息来源，
   秘密信息永不写入 public_log。

2. 状态流转（多轮发言顺序、阶段切换、全局状态更新）——见 Phase 枚举与 run() 主循环。

事件回调钩子（为成员4数据分析 / 成员5 UI 预留）：
   构造时传入 observer（实现 GameObserver 协议或一个 emit(event) 可调用对象），
   引擎在关键节点 emit 结构化 GameEvent。observer 为 None 时退化为 print。

依赖（成员3提供）：
    from werewolf_gateway import LLMGateway, GameSession, AgentResponse

用法：
    async with LLMGateway() as gw:
        game = GameSession(gw, game_id="my_game")
        engine = WerewolfGameEngine(game)        # 默认随机分配12人角色
        await engine.run()
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Set, Tuple

from .schemas import AgentResponse
from .session import GameSession


# ========== 角色与阵营 ==========
class Faction(Enum):
    WEREWOLF = "werewolf"
    GOD = "god"          # 神职：预言家/女巫/猎人/守卫
    VILLAGER = "villager"


ROLE_FACTION = {
    "werewolf": Faction.WEREWOLF,
    "villager": Faction.VILLAGER,
    "seer": Faction.GOD,
    "witch": Faction.GOD,
    "hunter": Faction.GOD,
    "guard": Faction.GOD,
}

GOD_ROLES = {"seer", "witch", "hunter", "guard"}

# 标准12人配置：4狼 + 4平民 + 预言家 + 女巫 + 猎人 + 守卫
ROLE_SET = ["werewolf"] * 4 + ["villager"] * 4 + ["seer", "witch", "hunter", "guard"]

DEFAULT_ROSTER: List[Tuple[int, str]] = [
    (1, "werewolf"), (2, "werewolf"), (3, "werewolf"), (4, "werewolf"),
    (5, "villager"), (6, "villager"), (7, "villager"), (8, "villager"),
    (9, "seer"), (10, "witch"), (11, "hunter"), (12, "guard"),
]


def generate_random_roster(rng: Optional[random.Random] = None) -> List[Tuple[int, str]]:
    """随机分配角色给 1-12 号座位，返回 [(seat, role), ...]。

    传入 rng（random.Random 实例）可获得可复现的分配，便于成员4复盘与成员1测试。
    """
    r = rng or random
    seats = list(range(1, 13))
    roles = r.sample(ROLE_SET, len(ROLE_SET))
    return list(zip(seats, roles))


# ========== 事件（为成员4/5预留的集成接口）==========
class EventType(str, Enum):
    GAME_START = "game_start"
    PHASE_CHANGE = "phase_change"
    NIGHT_ACTION = "night_action"      # 夜间秘密行动（仅记录，UI 可选择是否展示给观察者）
    DEATH = "death"
    SPEECH = "speech"                  # 公开发言
    VOTE_CAST = "vote_cast"            # 单人投票
    VOTE_RESULT = "vote_result"        # 投票结算
    ROLE_REVEAL = "role_reveal"        # 出局后亮明身份
    CHAIN_OF_THOUGHT = "chain_of_thought"  # 思维链（私密，UI 观察台可展示）
    PUBLIC_LOG = "public_log"          # 一条公开日志
    WOLF_CHAT = "wolf_chat"            # 狼队内部发言（私密）
    SHERIFF = "sheriff"                # 警长竞选/当选/警徽流转
    GAME_OVER = "game_over"


@dataclass
class GameEvent:
    type: EventType
    day: int
    phase: str
    payload: Dict
    ts: float = field(default_factory=time.time)


# observer 既可以是带 emit(event) 方法的对象，也可以是一个 emit 函数
ObserverLike = Callable[[GameEvent], None]


# ========== 游戏状态 ==========
@dataclass
class PlayerState:
    seat: int
    role: str
    alive: bool = True
    # 女巫
    witch_antidote_used: bool = False
    witch_poison_used: bool = False
    # 守卫
    guard_last_protected: Optional[int] = None
    # 猎人
    hunter_can_shoot: bool = True          # 被毒死时置 False
    hunter_shot_triggered: bool = False
    # 预言家（私密查验历史，仅本人可见）
    seer_checks: List[Tuple[int, int, str]] = field(default_factory=list)  # (day, target, result)

    @property
    def faction(self) -> Faction:
        return ROLE_FACTION[self.role]


class Phase(Enum):
    INIT = "init"
    NIGHT_GUARD = "night_guard"
    NIGHT_WEREWOLF = "night_werewolf"
    NIGHT_SEER = "night_seer"
    NIGHT_WITCH = "night_witch"
    NIGHT_RESOLVE = "night_resolve"
    DAY_ANNOUNCE = "day_announce"
    DAY_SHERIFF_ELECT = "day_sheriff_elect"   # 仅首日警长竞选
    DAY_LAST_WORDS = "day_last_words"
    DAY_SPEAK = "day_speak"
    DAY_VOTE = "day_vote"
    DAY_VOTE_RESOLVE = "day_vote_resolve"
    GAME_OVER = "game_over"


# 合法状态转移表：phase -> 允许转入的下一个 phase 集合。
# 让 Phase 成为真正受约束的状态机——非法跳转直接抛 IllegalTransition，
# 在重构/他人接手时挡住“白天直接跳投票”“夜晚漏结算”这类 bug。
LEGAL_TRANSITIONS: Dict["Phase", Set["Phase"]] = {}


def _build_transition_table() -> Dict["Phase", Set["Phase"]]:
    P = Phase
    return {
        P.INIT: {P.NIGHT_GUARD},
        # 夜晚链：每个夜间阶段总会 _set_phase（即使该角色不在场），顺序固定
        P.NIGHT_GUARD: {P.NIGHT_WEREWOLF},
        P.NIGHT_WEREWOLF: {P.NIGHT_SEER},
        P.NIGHT_SEER: {P.NIGHT_WITCH},
        P.NIGHT_WITCH: {P.NIGHT_RESOLVE},
        # 结算后：进入白天公告（可能随后直接 game_over）
        P.NIGHT_RESOLVE: {P.DAY_ANNOUNCE},
        # 白天公告后：首日→警长竞选；之后→遗言/发言；或夜间屠边后→game_over
        P.DAY_ANNOUNCE: {P.DAY_SHERIFF_ELECT, P.DAY_LAST_WORDS, P.DAY_SPEAK, P.GAME_OVER},
        P.DAY_SHERIFF_ELECT: {P.DAY_LAST_WORDS, P.DAY_SPEAK, P.GAME_OVER},
        P.DAY_LAST_WORDS: {P.DAY_SPEAK, P.GAME_OVER},
        P.DAY_SPEAK: {P.DAY_VOTE},
        P.DAY_VOTE: {P.DAY_VOTE_RESOLVE},
        # 投票结算后：回到下一夜，或分出胜负
        P.DAY_VOTE_RESOLVE: {P.NIGHT_GUARD, P.GAME_OVER},
        P.GAME_OVER: set(),
    }


LEGAL_TRANSITIONS = _build_transition_table()


class IllegalTransition(RuntimeError):
    """尝试了状态机不允许的阶段跳转。"""


def _skip_response() -> AgentResponse:
    """容错占位：当某玩家调用失败/超时时使用，保证状态机不崩。"""
    return AgentResponse(thought="", speech="", action="skip", target=None, confidence=0.0)


# 推理模型（如 deepseek-v4-pro）会先消耗 token 推理，再写正文。
# 引擎各阶段传的 max_tokens 是“正文预算”，这里统一加一份推理余量，
# 避免推理吃光预算导致 content 为空（即“玩家行动异常 / chat_json failed”根因）。
# 非推理模型用不到这份余量，多给的上限也不会被用满，无副作用。
REASONING_HEADROOM = 1024

# 单次 agent 调用的硬超时（秒）。夜间顺序调用（狼人定刀/预言家查验等）走的是
# game.ask 直连，只有 httpx 层超时；某些“假活”连接会让 httpx 超时失效、整局卡死。
# 这里在 asyncio 层再加一道兜底：单次调用超过此值即当失败 skip，绝不拖死整局。
# 取值需覆盖推理模型最慢一次的耗时（实测 v4-pro 单次 20-30s），留足余量。
PER_CALL_TIMEOUT = 90.0


# ========== 状态机核心 ==========
class WerewolfGameEngine:
    """12人狼人杀状态机。

    Args:
        game:     已绑定 LLMGateway 的 GameSession（成员3）。
        roster:   [(seat, role), ...]；None 则随机生成。
        observer: 事件回调；可为带 emit(event) 的对象、emit 函数，或 None。
        rng:      随机源；传入可复现整局（角色分配、平票裁决、容错兜底）。
        max_days: 安全上限，防止异常情况下无限循环。
    """

    def __init__(
        self,
        game: GameSession,
        roster: Optional[List[Tuple[int, str]]] = None,
        *,
        observer: Optional[ObserverLike] = None,
        rng: Optional[random.Random] = None,
        max_days: int = 20,
    ) -> None:
        self.game = game
        self.rng = rng or random.Random()
        self.observer = observer
        self.max_days = max_days

        if roster is None:
            roster = generate_random_roster(self.rng)
        self.roster = roster

        self.players: Dict[int, PlayerState] = {}
        self.alive_seats: Set[int] = set()
        self.day = 0
        self.phase: Phase = Phase.INIT

        # 公开日志：仅白天公开信息，所有存活玩家可见
        self.public_log: List[str] = []
        # 狼队内部频道：仅狼人可见
        self.wolf_channel: List[str] = []

        # 警长（警徽流）
        self.sheriff: Optional[int] = None          # 当前警徽持有者座位，None 表示无警长
        self.sheriff_elected: bool = False          # 是否已举行过竞选（仅首日一次）
        self._pending_badge_from: Optional[int] = None  # 待处理的警徽流转（警长在夜晚死亡时置位）

        # 当晚行动暂存（每晚开头重置）
        self._reset_night_state()
        self.night_deaths: List[int] = []        # 上一夜公开的死亡名单
        self.winner: Optional[Faction] = None

        # 真相记录（ground truth）：供成员4 把对话日志与真实行动对齐做信任度量化。
        # 每条是一个 dict，按发生顺序追加。秘密信息也记在这里——它只对引擎/分析者可见，
        # 绝不通过 _build_view 泄漏给玩家。
        self.ground_truth: List[Dict] = []

        self._init_players()

    # ---------- 初始化 ----------
    def _reset_night_state(self) -> None:
        self.wolf_kill_target: Optional[int] = None
        self.witch_save: bool = False
        self.witch_poison_target: Optional[int] = None
        self.guard_protect_target: Optional[int] = None

    def _init_players(self) -> None:
        # 先建好全部 PlayerState，再统一生成 system prompt —— 修复狼队友初始化 bug：
        # 草稿里 prompt 在循环中生成，导致每个狼只能看到比自己先入座的队友。
        for seat, role in self.roster:
            self.players[seat] = PlayerState(seat=seat, role=role, alive=True)
            self.alive_seats.add(seat)

        for seat, role in self.roster:
            sys_prompt = self._build_system_prompt(seat, role)
            self.game.add_agent(seat, role, sys_prompt)

    def _wolf_seats(self, alive_only: bool = False) -> List[int]:
        return sorted(
            s for s, p in self.players.items()
            if p.role == "werewolf" and (p.alive or not alive_only)
        )

    def _build_system_prompt(self, seat: int, role: str) -> str:
        """生成角色 system prompt。成员2可在此基础上注入更精细的性格/隐藏目标。"""
        base = f"你是 {seat} 号玩家，本局身份是【{role}】。"
        if role == "werewolf":
            teammates = [s for s in self._wolf_seats() if s != seat]
            base += (
                f" 你的狼人队友是 {teammates} 号（这是只有狼队知道的秘密）。"
                " 夜晚你与队友共同决定刀杀目标；白天你要伪装成好人，隐藏身份、误导好人投票。"
            )
        elif role == "seer":
            base += " 你是预言家。每晚可查验一名玩家，系统私下告诉你他是【狼人】还是【好人】。白天你要善用查验信息引导好人。"
        elif role == "witch":
            base += " 你是女巫，拥有一瓶解药和一瓶毒药。每晚系统会私下告诉你刀口是谁。解药可救人（不能自救），毒药可毒杀一人，每晚至多用一瓶。"
        elif role == "hunter":
            base += " 你是猎人。被狼刀或被投票出局时可开枪带走一名玩家；但若被女巫毒死则无法开枪。"
        elif role == "guard":
            base += " 你是守卫。每晚可守护一名玩家（不能连续两晚守护同一人），被守护者免疫当晚狼刀；但同夜既被守护又被女巫解药会“奶穿”致死。"
        else:  # villager
            base += " 你是平民，没有特殊能力。通过发言与投票找出狼人。"
        base += " 始终以要求的 JSON 格式回复。"
        return base

    # ---------- 事件与日志 ----------
    def _emit(self, etype: EventType, **payload) -> None:
        if self.observer is None:
            return
        event = GameEvent(type=etype, day=self.day, phase=self.phase.value, payload=payload)
        emit = getattr(self.observer, "emit", None)
        try:
            if callable(emit):
                emit(event)
            elif callable(self.observer):
                self.observer(event)
        except Exception:
            # observer 不应影响游戏主流程
            pass

    def _log_public(self, msg: str) -> None:
        """公开日志：所有存活玩家在白天可见。"""
        self.public_log.append(msg)
        print(f"[GAME] {msg}")
        self._emit(EventType.PUBLIC_LOG, message=msg)

    def _log_wolf(self, msg: str) -> None:
        """狼队内部频道：仅狼人可见。"""
        self.wolf_channel.append(msg)
        print(f"[WOLF] {msg}")
        self._emit(EventType.WOLF_CHAT, message=msg)

    # ---------- 信息隔离层 ----------
    def _public_today(self) -> str:
        """取今天公开日志（含昨夜死亡公告起）。秘密信息从不在此出现。"""
        marker = f"【第{self.day}天】"
        idx = next((i for i in range(len(self.public_log) - 1, -1, -1)
                    if marker in self.public_log[i]), None)
        lines = self.public_log[idx:] if idx is not None else self.public_log[-20:]
        return "\n".join(lines) if lines else "（暂无公开信息）"

    def _build_view(self, seat: int, *, include_wolf_channel: bool = False,
                    private_note: str = "") -> str:
        """组装某玩家有权看到的上下文。这是该玩家 prompt 的唯一信息来源。

        - 所有人：今天的公开日志 + 存活名单
        - 狼人（include_wolf_channel）：额外附狼队频道
        - 角色私密信息（查验结果/刀口/用药/守护）：由 private_note 注入，调用方按权限传入
        """
        parts = [
            f"当前存活玩家：{sorted(self.alive_seats)}。",
            f"当前警长：{self.sheriff if self.sheriff else '无'}（警长投票计 1.5 票）。",
            f"公开信息：\n{self._public_today()}",
        ]
        if include_wolf_channel and self.players[seat].role == "werewolf":
            chat = "\n".join(self.wolf_channel) if self.wolf_channel else "（暂无）"
            parts.append(f"【狼队私密频道】\n{chat}")
        if private_note:
            parts.append(f"【你的私密信息】{private_note}")
        return "\n\n".join(parts)

    # ---------- 单个/批量询问（容错） ----------
    async def _ask(self, seat: int, prompt: str, *, max_tokens: Optional[int] = None) -> AgentResponse:
        # 给推理模型留出推理余量，避免推理吃光正文预算导致空 content。
        budget = (max_tokens + REASONING_HEADROOM) if max_tokens is not None else None
        try:
            # asyncio 层硬超时兜底：防止“假活”连接让 httpx 超时失效、整局卡死。
            resp = await asyncio.wait_for(
                self.game.ask(seat, prompt, max_tokens=budget),
                timeout=PER_CALL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            self._log_public(f"玩家{seat}行动超时（>{PER_CALL_TIMEOUT:.0f}s），按 skip 处理。")
            return _skip_response()
        except Exception as e:
            self._log_public(f"玩家{seat}行动异常（{e}），按 skip 处理。")
            return _skip_response()
        # 思维链事件（私密）——供成员5观察台展示
        if resp.thought:
            self._emit(EventType.CHAIN_OF_THOUGHT, seat=seat,
                       role=self.players[seat].role, thought=resp.thought)
        return resp

    async def _gather_alive(self, prompt_builder: Callable[[int], str],
                            timeout: float = 30.0) -> Dict[int, AgentResponse]:
        alive = sorted(self.alive_seats)
        if not alive:
            return {}
        results = await self.game.gather(
            seats=alive,
            prompt_builder=prompt_builder,
            timeout=timeout,
            on_failure=lambda seat, e: _skip_response(),
        )
        for seat, resp in results.items():
            if resp.thought:
                self._emit(EventType.CHAIN_OF_THOUGHT, seat=seat,
                           role=self.players[seat].role, thought=resp.thought)
        return results

    def _set_phase(self, phase: Phase) -> None:
        # 状态机合法性校验：拦截非法阶段跳转。同阶段重入（no-op）允许。
        if phase != self.phase and phase not in LEGAL_TRANSITIONS.get(self.phase, set()):
            raise IllegalTransition(
                f"非法状态转移：{self.phase.value} -> {phase.value}"
            )
        self.phase = phase
        self._emit(EventType.PHASE_CHANGE, phase=phase.value)

    def _record(self, kind: str, **data) -> None:
        """追加一条真相记录（ground truth），供成员4 复盘分析。"""
        entry = {"day": self.day, "phase": self.phase.value, "kind": kind, **data}
        self.ground_truth.append(entry)

    # ========== 黑夜 ==========
    async def _night_guard(self) -> None:
        self._set_phase(Phase.NIGHT_GUARD)
        guards = [s for s, p in self.players.items() if p.role == "guard" and p.alive]
        if not guards:
            return
        g = guards[0]
        last = self.players[g].guard_last_protected
        note = f"你上一晚守护了 {last} 号，今晚不能再守护他。" if last else "今晚是你第一次守护。"
        prompt = self._build_view(g, private_note=note) + (
            "\n\n请选择今晚守护的目标：action='protect'，target=座位号。"
        )
        resp = await self._ask(g, prompt, max_tokens=300)
        if resp.action == "protect" and resp.target in self.alive_seats and resp.target != last:
            self.guard_protect_target = resp.target
            self.players[g].guard_last_protected = resp.target
            self._emit(EventType.NIGHT_ACTION, actor=g, role="guard",
                       action="protect", target=resp.target)
        # 守护结果保持私密，不进 public_log

    async def _night_werewolf(self) -> None:
        """狼队两步：先顺序讨论（speak），再统一定刀（kill 多数决）。"""
        self._set_phase(Phase.NIGHT_WEREWOLF)
        wolves = self._wolf_seats(alive_only=True)
        if not wolves:
            return

        self._log_wolf(f"=== 第{self.day + 1}夜 狼队商议 ===")
        # 第一步：顺序讨论，后发言的狼能看到前面的发言
        for s in wolves:
            prompt = self._build_view(s, include_wolf_channel=True) + (
                "\n\n这是狼队夜间密谈。请发表你对今晚刀杀目标的看法。"
                " action='speak'，speech 填写你的建议（仅狼队可见）。"
            )
            resp = await self._ask(s, prompt, max_tokens=300)
            if resp.action == "speak" and resp.speech:
                self._log_wolf(f"狼{s}：{resp.speech}")

        # 第二步：每只狼正式定刀，多数决
        proposals: List[int] = []
        non_wolf_alive = [s for s in self.alive_seats if self.players[s].role != "werewolf"]
        for s in wolves:
            prompt = self._build_view(s, include_wolf_channel=True) + (
                "\n\n商议结束，请正式提交你今晚要刀杀的目标。"
                " action='kill'，target=座位号（必须是存活的非狼玩家）。"
            )
            resp = await self._ask(s, prompt, max_tokens=200)
            if resp.action == "kill" and resp.target in non_wolf_alive:
                proposals.append(resp.target)
                self._emit(EventType.NIGHT_ACTION, actor=s, role="werewolf",
                           action="kill", target=resp.target)
            elif non_wolf_alive:
                proposals.append(self.rng.choice(non_wolf_alive))

        if proposals:
            counter = Counter(proposals)
            top = max(counter.values())
            candidates = sorted(t for t, c in counter.items() if c == top)
            self.wolf_kill_target = self.rng.choice(candidates)
        elif non_wolf_alive:
            self.wolf_kill_target = self.rng.choice(non_wolf_alive)

        if self.wolf_kill_target:
            self._log_wolf(f"狼队最终决定刀杀 {self.wolf_kill_target} 号。")

    async def _night_seer(self) -> None:
        self._set_phase(Phase.NIGHT_SEER)
        seers = [s for s, p in self.players.items() if p.role == "seer" and p.alive]
        if not seers:
            return
        seer = seers[0]
        checkable = [s for s in self.alive_seats if s != seer]
        prompt = self._build_view(seer) + (
            "\n\n请选择今晚查验的玩家：action='check'，target=座位号。"
        )
        resp = await self._ask(seer, prompt, max_tokens=200)
        target = resp.target if (resp.action == "check" and resp.target in checkable) else None
        if target is None and checkable:
            target = self.rng.choice(checkable)
        if target is not None:
            result = "狼人" if self.players[target].role == "werewolf" else "好人"
            self.players[seer].seer_checks.append((self.day + 1, target, result))
            self._emit(EventType.NIGHT_ACTION, actor=seer, role="seer",
                       action="check", target=target, result=result)
            # 真相记录：查验是否“准”——seer 验狼为真，验民为真；用于成员4 判断预言家是否被悍跳带偏
            self._record("seer_check", seer=seer, target=target, result=result,
                         target_role=self.players[target].role)
            print(f"[SEER] 预言家查验 {target} -> {result}")  # 控制台调试，不进 public_log

    async def _night_witch(self) -> None:
        self._set_phase(Phase.NIGHT_WITCH)
        witches = [s for s, p in self.players.items() if p.role == "witch" and p.alive]
        if not witches:
            return
        w = witches[0]
        state = self.players[w]
        killed = self.wolf_kill_target
        # 女巫私密信息：今晚刀口 + 药剂状态
        note = (
            f"今晚狼队刀杀的目标是 {killed if killed else '无'} 号。"
            f" 解药{'（已用）' if state.witch_antidote_used else '（可用）'}，"
            f" 毒药{'（已用）' if state.witch_poison_used else '（可用）'}。"
        )
        prompt = self._build_view(w, private_note=note) + (
            "\n\n请选择：救人 action='save'（救今晚刀口，不能自救），"
            " 或毒杀 action='poison' target=座位号，或不行动 action='skip'。每晚至多用一瓶药。"
        )
        resp = await self._ask(w, prompt, max_tokens=250)

        if resp.action == "save" and not state.witch_antidote_used:
            if killed is not None and killed != w:
                self.witch_save = True
                state.witch_antidote_used = True
                self._emit(EventType.NIGHT_ACTION, actor=w, role="witch",
                           action="save", target=killed)
                print(f"[WITCH] 女巫对 {killed} 使用解药")
        elif resp.action == "poison" and not state.witch_poison_used:
            if resp.target in self.alive_seats and resp.target != w:
                self.witch_poison_target = resp.target
                state.witch_poison_used = True
                self._emit(EventType.NIGHT_ACTION, actor=w, role="witch",
                           action="poison", target=resp.target)
                print(f"[WITCH] 女巫对 {resp.target} 使用毒药")

    async def _resolve_night(self) -> None:
        """结算夜晚死亡。守护/解药/毒药交互在此判定。"""
        self._set_phase(Phase.NIGHT_RESOLVE)
        dead: Set[int] = set()
        poisoned: Set[int] = set()

        if self.wolf_kill_target is not None:
            saved = self.witch_save
            guarded = (self.guard_protect_target == self.wolf_kill_target)
            if saved and guarded:
                # 奶穿：既守又救 -> 死
                dead.add(self.wolf_kill_target)
            elif saved or guarded:
                pass  # 救活/守住
            else:
                dead.add(self.wolf_kill_target)

        if self.witch_poison_target is not None:
            dead.add(self.witch_poison_target)
            poisoned.add(self.witch_poison_target)

        self.night_deaths = sorted(dead)
        for seat in self.night_deaths:
            # 被毒死的猎人不能开枪
            if self.players[seat].role == "hunter" and seat in poisoned:
                self.players[seat].hunter_can_shoot = False
            self._kill_seat(seat, cause="night")
            # 警长死于夜晚：标记待处理的警徽流转（在天亮后处理）
            if seat == self.sheriff:
                self._pending_badge_from = seat

        # 真相记录：本夜完整结算，含真实刀口/解药/毒药/守护/最终死亡
        self._record(
            "night_resolve",
            wolf_kill_target=self.wolf_kill_target,
            witch_save=self.witch_save,
            witch_poison_target=self.witch_poison_target,
            guard_protect_target=self.guard_protect_target,
            deaths=self.night_deaths,
        )

        self._reset_night_state()

    def _kill_seat(self, seat: int, *, cause: str) -> None:
        if seat not in self.alive_seats:
            return
        self.players[seat].alive = False
        self.alive_seats.discard(seat)
        self.game.kill(seat)
        self._emit(EventType.DEATH, seat=seat, role=self.players[seat].role, cause=cause)

    # ========== 白天 ==========
    async def _day_announce(self) -> None:
        self.day += 1
        self._set_phase(Phase.DAY_ANNOUNCE)
        if self.night_deaths:
            self._log_public(f"【第{self.day}天】天亮了。昨晚死亡：{self.night_deaths} 号。")
        else:
            self._log_public(f"【第{self.day}天】天亮了。昨晚是平安夜，无人死亡。")

    # ---------- 警长机制（警徽流）----------
    async def _sheriff_election(self) -> None:
        """首日警长竞选：上警发言 -> 未上警玩家投票 -> 当选者持警徽（1.5 票）。"""
        if self.sheriff_elected:
            return
        self._set_phase(Phase.DAY_SHERIFF_ELECT)
        self.sheriff_elected = True
        alive = sorted(self.alive_seats)
        if len(alive) < 3:
            self._log_public("存活人数过少，跳过警长竞选。")
            return

        self._log_public("警长竞选开始，请玩家决定是否上警并发言。")
        self._emit(EventType.SHERIFF, stage="election_start", candidates=[])

        # 第一步：每名玩家决定是否上警；上警则公开竞选发言
        candidates: List[int] = []
        for seat in alive:
            prompt = self._build_view(seat) + (
                "\n\n警长竞选：警长在白天投票计 1.5 票，并主持发言顺序，是好人与狼人都想争夺的关键身份。"
                " 你是否上警竞选？上警请 action='speak' 并在 speech 写竞选宣言；放弃请 action='skip'。"
            )
            resp = await self._ask(seat, prompt, max_tokens=300)
            if resp.action == "speak" and resp.speech:
                candidates.append(seat)
                self._log_public(f"【上警】{seat}号竞选发言：{resp.speech}")
                self._emit(EventType.SPEECH, seat=seat, role=self.players[seat].role,
                           text=resp.speech, kind="sheriff_campaign")

        if not candidates:
            self._log_public("无人上警，本局无警长。")
            self._emit(EventType.SHERIFF, stage="no_sheriff", candidates=[])
            self._record("sheriff_election", candidates=[], sheriff=None)
            return

        if len(candidates) == 1:
            self.sheriff = candidates[0]
            self._log_public(f"仅 {self.sheriff} 号上警，自动当选警长。")
            self._emit(EventType.SHERIFF, stage="elected", sheriff=self.sheriff,
                       candidates=candidates)
            self._record("sheriff_election", candidates=candidates, sheriff=self.sheriff)
            return

        # 第二步：未上警玩家投票（上警者不投）
        voters = [s for s in alive if s not in candidates]
        if not voters:
            voters = alive[:]  # 全员上警的极端情况：全体投

        def elect_prompt(seat: int) -> str:
            return self._build_view(seat) + (
                f"\n\n请从警长候选人 {candidates} 中投票选出警长："
                " action='vote'，target=候选人座位号；弃票用 action='no_vote'。"
            )

        votes = await self.game.gather(
            seats=voters, prompt_builder=elect_prompt, timeout=25.0,
            on_failure=lambda seat, e: _skip_response(),
        )
        tally: Counter = Counter()
        for seat, resp in votes.items():
            if resp.action == "vote" and resp.target in candidates:
                tally[resp.target] += 1
                self._emit(EventType.VOTE_CAST, voter=seat, target=resp.target, kind="sheriff")

        if not tally:
            self.sheriff = self.rng.choice(candidates)
            self._log_public(f"竞选无有效票，随机指定 {self.sheriff} 号为警长。")
        else:
            top = max(tally.values())
            leaders = sorted(t for t, c in tally.items() if c == top)
            self.sheriff = leaders[0] if len(leaders) == 1 else self.rng.choice(leaders)
            if len(leaders) > 1:
                self._log_public(f"竞选平票（{leaders}），随机裁定 {self.sheriff} 号当选警长。")
            else:
                self._log_public(f"{self.sheriff} 号以 {top} 票当选警长。")
        self._emit(EventType.SHERIFF, stage="elected", sheriff=self.sheriff,
                   candidates=candidates, tally=dict(tally))
        self._record("sheriff_election", candidates=candidates, sheriff=self.sheriff,
                     tally=dict(tally))

    async def _sheriff_badge_flow(self, dead_sheriff: int) -> None:
        """警长出局后的警徽流转：移交一名存活玩家，或撕掉警徽（本局无警长）。"""
        # 警长已不在存活名单中，targets 为其余存活者
        targets = sorted(self.alive_seats)
        self.sheriff = None  # 先卸任，避免移交期间仍计 1.5 票
        if not targets:
            self._emit(EventType.SHERIFF, stage="badge_destroyed", former=dead_sheriff)
            return
        prompt = (
            f"你（{dead_sheriff}号）原是警长，现已出局，需处理警徽。"
            f" 可移交给一名存活玩家（action='shoot'，target=座位号——此处复用 shoot 表示移交），"
            " 或撕掉警徽使本局再无警长（action='skip'）。存活玩家："
            f"{targets}。"
        )
        resp = await self._ask(dead_sheriff, prompt, max_tokens=200)
        if resp.action == "shoot" and resp.target in self.alive_seats:
            self.sheriff = resp.target
            self._log_public(f"警徽流转：{dead_sheriff}号将警徽移交给 {self.sheriff}号。")
            self._emit(EventType.SHERIFF, stage="badge_transferred",
                       former=dead_sheriff, sheriff=self.sheriff)
            self._record("sheriff_badge", former=dead_sheriff, new=self.sheriff,
                         destroyed=False)
        else:
            self._log_public(f"警徽流转：{dead_sheriff}号撕掉警徽，本局再无警长。")
            self._emit(EventType.SHERIFF, stage="badge_destroyed", former=dead_sheriff)
            self._record("sheriff_badge", former=dead_sheriff, new=None, destroyed=True)

    async def _last_words(self) -> None:
        """仅第一天的夜晚死者有遗言。"""
        if self.day != 1 or not self.night_deaths:
            return
        self._set_phase(Phase.DAY_LAST_WORDS)
        for seat in self.night_deaths:
            prompt = (
                f"你（{seat}号）昨晚死亡，现在留遗言。可指认怀疑对象、传递信息。"
                " action='speak'，speech 填写遗言。"
            )
            resp = await self._ask(seat, prompt, max_tokens=300)
            if resp.action == "speak" and resp.speech:
                self._log_public(f"【遗言】{seat}号：{resp.speech}")
                self._emit(EventType.SPEECH, seat=seat, role=self.players[seat].role,
                           text=resp.speech, kind="last_words")

    async def _day_speak(self) -> None:
        """全体存活玩家按座位号轮流公开发言。"""
        self._set_phase(Phase.DAY_SPEAK)
        self._log_public("进入自由发言环节。")
        for seat in sorted(self.alive_seats):
            player = self.players[seat]
            # 预言家把尚未公布的查验结果作为私密信息带入（是否公开由模型决策）
            private = ""
            if player.role == "seer" and player.seer_checks:
                last = player.seer_checks[-1]
                private = f"你最近一次查验：第{last[0]}夜验了 {last[1]} 号，结果是【{last[2]}】。"
            prompt = self._build_view(seat, private_note=private) + (
                "\n\n轮到你公开发言。可分析局势、指认狼人、解释自己。"
                " action='speak'，speech 填写发言内容。"
            )
            resp = await self._ask(seat, prompt, max_tokens=400)
            if resp.action == "speak" and resp.speech:
                self._log_public(f"{seat}号发言：{resp.speech}")
                self._emit(EventType.SPEECH, seat=seat, role=player.role,
                           text=resp.speech, kind="day")
            else:
                self._log_public(f"{seat}号选择沉默。")

    async def _day_vote(self) -> None:
        """全体存活玩家并行投票放逐。警长票计 1.5。"""
        self._set_phase(Phase.DAY_VOTE)
        alive = sorted(self.alive_seats)
        if len(alive) <= 2:
            self._log_public("存活人数过少，跳过投票。")
            await self._resolve_vote({})  # 仍经过结算阶段，保持状态机完整
            return

        def vote_prompt(seat: int) -> str:
            return self._build_view(seat) + (
                f"\n\n投票放逐环节，存活：{alive}。"
                " 请投出你认为是狼人的玩家：action='vote'，target=座位号；弃票用 action='no_vote'。"
            )

        votes = await self._gather_alive(vote_prompt, timeout=30.0)
        tally: Dict[int, float] = {}
        for seat, resp in votes.items():
            if resp.action == "vote" and resp.target in alive and resp.target != seat:
                weight = 1.5 if seat == self.sheriff else 1.0
                tally[resp.target] = tally.get(resp.target, 0.0) + weight
                self._emit(EventType.VOTE_CAST, voter=seat, target=resp.target,
                           weight=weight)

        await self._resolve_vote(tally)

    async def _resolve_vote(self, tally: Dict[int, float]) -> None:
        self._set_phase(Phase.DAY_VOTE_RESOLVE)
        if not tally:
            self._log_public("无人投出有效票，本轮无人出局。")
            self._emit(EventType.VOTE_RESULT, eliminated=None, tally={})
            self._record("day_vote", eliminated=None, tally={})
            return
        top = max(tally.values())
        leaders = sorted(t for t, c in tally.items() if c == top)
        # 平票：随机裁决（可由 rng 复现）。简化版未做 PK 发言。
        eliminated = leaders[0] if len(leaders) == 1 else self.rng.choice(leaders)
        if len(leaders) > 1:
            self._log_public(f"平票（{leaders} 各 {top} 票），随机裁决放逐 {eliminated} 号。")
        else:
            self._log_public(f"{eliminated} 号以 {top} 票被放逐出局。")
        self._emit(EventType.VOTE_RESULT, eliminated=eliminated, tally=dict(tally))
        self._record("day_vote", eliminated=eliminated, tally=dict(tally))
        await self._eliminate(eliminated, reason="vote")

    async def _eliminate(self, seat: int, *, reason: str) -> None:
        if seat not in self.alive_seats:
            return
        role = self.players[seat].role
        was_sheriff = (seat == self.sheriff)
        self._kill_seat(seat, cause=reason)
        self._log_public(f"{seat}号出局，身份是【{role}】。")
        self._emit(EventType.ROLE_REVEAL, seat=seat, role=role, reason=reason)
        await self._maybe_hunter_shot(seat, reason)
        # 警长在白天出局（投票/枪杀）：当场移交警徽
        if was_sheriff:
            await self._sheriff_badge_flow(seat)

    async def _maybe_hunter_shot(self, seat: int, reason: str) -> None:
        player = self.players[seat]
        if player.role != "hunter" or player.hunter_shot_triggered:
            return
        if not player.hunter_can_shoot:  # 被毒死
            self._log_public(f"猎人{seat}号被毒杀，无法开枪。")
            return
        player.hunter_shot_triggered = True
        targets = sorted(self.alive_seats)
        if not targets:
            return
        prompt = (
            f"你（猎人 {seat}号）已出局，可开枪带走一名玩家。存活：{targets}。"
            " action='shoot'，target=座位号；若放弃用 action='skip'。"
        )
        resp = await self._ask(seat, prompt, max_tokens=200)
        if resp.action == "shoot" and resp.target in self.alive_seats:
            shot = resp.target
            self._log_public(f"猎人{seat}号开枪带走 {shot} 号。")
            self._emit(EventType.NIGHT_ACTION, actor=seat, role="hunter",
                       action="shoot", target=shot)
            await self._eliminate(shot, reason="hunter_shot")
        else:
            self._log_public(f"猎人{seat}号未开枪。")

    # ========== 胜负 ==========
    def _check_game_over(self) -> bool:
        """屠边规则：狼全灭则好人胜；平民全灭或神职全灭则狼胜。"""
        alive_roles = [self.players[s].role for s in self.alive_seats]
        wolves = sum(1 for r in alive_roles if r == "werewolf")
        villagers = sum(1 for r in alive_roles if r == "villager")
        gods = sum(1 for r in alive_roles if r in GOD_ROLES)

        if wolves == 0:
            self.winner = Faction.GOD  # 好人阵营（神民）
            return True
        if villagers == 0 or gods == 0:
            self.winner = Faction.WEREWOLF
            return True
        # 屠城兜底：狼数 >= 好人数
        if wolves >= (villagers + gods):
            self.winner = Faction.WEREWOLF
            return True
        return False

    def _announce_winner(self) -> None:
        self._set_phase(Phase.GAME_OVER)
        if self.winner == Faction.WEREWOLF:
            msg = "游戏结束：狼人阵营胜利！"
        else:
            msg = "游戏结束：好人阵营胜利！所有狼人出局。"
        self._log_public(msg)
        roles = {s: p.role for s, p in self.players.items()}
        self._emit(EventType.GAME_OVER, winner=self.winner.value if self.winner else None,
                   roles=roles)

    # ========== 状态快照 / 序列化（为成员4数据分析）==========
    def to_dict(self) -> Dict:
        """导出完整游戏状态（含隐藏身份的 ground truth），JSON 可序列化。

        成员4 用它把对话日志与真相对齐：谁是狼、每晚真实刀口/查验/用药、
        警徽流转、胜负，从而量化“信任建立/欺骗/结盟”的成败。
        注意：本字典含全部隐藏信息，仅供引擎/分析者，不可发给玩家。
        """
        return {
            "game_id": getattr(self.game, "game_id", None),
            "phase": self.phase.value,
            "day": self.day,
            "winner": self.winner.value if self.winner else None,
            "roster": [{"seat": s, "role": r} for s, r in self.roster],
            "sheriff": self.sheriff,
            "players": {
                s: {
                    "seat": p.seat,
                    "role": p.role,
                    "alive": p.alive,
                    "faction": p.faction.value,
                    "witch_antidote_used": p.witch_antidote_used,
                    "witch_poison_used": p.witch_poison_used,
                    "guard_last_protected": p.guard_last_protected,
                    "hunter_can_shoot": p.hunter_can_shoot,
                    "hunter_shot_triggered": p.hunter_shot_triggered,
                    "seer_checks": [
                        {"day": d, "target": t, "result": r}
                        for (d, t, r) in p.seer_checks
                    ],
                }
                for s, p in self.players.items()
            },
            "alive_seats": sorted(self.alive_seats),
            "public_log": list(self.public_log),
            "wolf_channel": list(self.wolf_channel),
            "ground_truth": list(self.ground_truth),
            "stats": self.game.stats_snapshot(),
        }

    def save_replay(self, path: str) -> None:
        """把完整状态写成 JSON 复盘文件，供成员4 离线分析。"""
        import json
        from pathlib import Path

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        print(f"[REPLAY] 已保存复盘到 {path}")

    # ========== 主循环 ==========
    async def run(self) -> Optional[Faction]:
        self._emit(EventType.GAME_START,
                   roster={s: r for s, r in self.roster})
        self._log_public(f"游戏开始，共 {len(self.roster)} 名玩家。")

        while self.day < self.max_days:
            # —— 黑夜 ——
            await self._night_guard()
            await self._night_werewolf()
            await self._night_seer()
            await self._night_witch()
            await self._resolve_night()

            if self._check_game_over():
                # 白天来临前先报昨夜死亡，再宣布胜负，便于复盘
                await self._day_announce()
                self._announce_winner()
                break

            # —— 白天 ——
            await self._day_announce()

            # 警长在夜晚出局：天亮后先处理警徽流转
            if self._pending_badge_from is not None:
                former = self._pending_badge_from
                self._pending_badge_from = None
                await self._sheriff_badge_flow(former)

            # 首日警长竞选（仅一次）
            if not self.sheriff_elected:
                await self._sheriff_election()

            await self._last_words()

            if self._check_game_over():  # 遗言/首日结算后可能已分胜负
                self._announce_winner()
                break

            await self._day_speak()
            await self._day_vote()

            if self._check_game_over():  # 投票/猎人开枪后判定
                self._announce_winner()
                break

            self.night_deaths = []  # 进入下一夜前清空

        else:
            # 触达 max_days 上限
            self._log_public(f"达到最大天数 {self.max_days}，游戏强制结束。")
            self._set_phase(Phase.GAME_OVER)

        stats = self.game.stats_snapshot()
        self._log_public(
            f"统计：LLM调用 {stats.get('calls', 0)} 次，"
            f"prompt_tokens={stats.get('prompt_tokens', 0)}，"
            f"completion_tokens={stats.get('completion_tokens', 0)}。"
        )
        return self.winner
