"""
werewolf_game.py - 完整12人狼人杀状态机（支持随机角色分配）

成员1（系统架构与状态机管理）使用本文件驱动游戏。
依赖：
    from werewolf_gateway import LLMGateway, GameSession, AgentResponse

用法：
    async with LLMGateway() as gw:
        game = GameSession(gw, game_id="my_game")
        engine = WerewolfGameEngine(game)  # 默认随机分配角色
        await engine.run()
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

from werewolf_gateway import AgentResponse, GameSession, LLMGateway


# ========== 角色与阵营 ==========
class Faction(Enum):
    WEREWOLF = "werewolf"
    VILLAGER = "villager"
    SEER = "seer"
    WITCH = "witch"
    HUNTER = "hunter"
    GUARD = "guard"


ROLE_FACTION = {
    "werewolf": Faction.WEREWOLF,
    "villager": Faction.VILLAGER,
    "seer": Faction.SEER,
    "witch": Faction.WITCH,
    "hunter": Faction.HUNTER,
    "guard": Faction.GUARD,
}

ALIVE_ROLES = ["werewolf", "villager", "seer", "witch", "hunter", "guard"]
GOOD_FACTIONS = {Faction.VILLAGER, Faction.SEER, Faction.WITCH, Faction.HUNTER, Faction.GUARD}
EVIL_FACTIONS = {Faction.WEREWOLF}

# 标准12人角色配置（4狼、4平民、预言家、女巫、猎人、守卫）
ROLE_SET = ["werewolf"] * 4 + ["villager"] * 4 + ["seer", "witch", "hunter", "guard"]

# 保留默认固定阵容作为参考，但默认使用随机分配
DEFAULT_ROSTER = [
    (1, "werewolf"), (2, "werewolf"), (3, "werewolf"), (4, "werewolf"),
    (5, "villager"), (6, "villager"), (7, "villager"), (8, "villager"),
    (9, "seer"), (10, "witch"), (11, "hunter"), (12, "guard"),
]


def generate_random_roster() -> List[Tuple[int, str]]:
    """随机分配角色给1-12号座位，返回列表[(seat, role), ...]"""
    seats = list(range(1, 13))
    roles = random.sample(ROLE_SET, 12)  # 随机打乱
    return list(zip(seats, roles))


# ========== 游戏状态 ==========
@dataclass
class PlayerState:
    seat: int
    role: str
    alive: bool = True
    # 特殊能力使用记录
    witch_antidote_used: bool = False
    witch_poison_used: bool = False
    guard_last_protected: Optional[int] = None
    hunter_shot_triggered: bool = False   # 是否已开枪
    # 预言家上次查验结果（私密）
    seer_last_check_target: Optional[int] = None
    seer_last_check_result: Optional[str] = None
    # 是否已告知预言家查验结果
    seer_result_acknowledged: bool = False


class Phase(Enum):
    INIT = "init"
    NIGHT_WEREWOLF = "night_werewolf"
    NIGHT_WITCH = "night_witch"
    NIGHT_SEER = "night_seer"
    NIGHT_GUARD = "night_guard"
    NIGHT_RESOLVE = "night_resolve"   # 结算死亡
    DAY_START = "day_start"
    DAY_SHERRIF_ELECT = "sherrif_elect"   # 仅首日
    DAY_SPEAK = "day_speak"
    DAY_VOTE = "day_vote"
    DAY_VOTE_RESOLVE = "day_vote_resolve"
    GAME_OVER = "game_over"


# ========== 状态机核心 ==========
class WerewolfGameEngine:
    def __init__(self, game: GameSession, roster: List[Tuple[int, str]] = None):
        """
        game: GameSession实例，已绑定gateway
        roster: 座位号与角色列表，默认为随机生成（若为None）
        """
        self.game = game
        # 如果没有传入roster，则随机生成角色分配
        if roster is None:
            roster = generate_random_roster()
            print(f"[初始化] 随机角色分配：{roster}")
        self.roster = roster
        self.players: Dict[int, PlayerState] = {}
        self.alive_seats: Set[int] = set()
        self.day = 0
        self.sheriff: Optional[int] = None          # 警长座位
        self.sheriff_elected: bool = False          # 是否已举行过警长竞选
        self.night_deaths: List[int] = []           # 当晚死亡玩家
        self.public_log: List[str] = []             # 全局公开日志（所有玩家可见）
        self.phase: Phase = Phase.INIT

        # 夜晚行动暂存
        self.wolf_kill_target: Optional[int] = None
        self.witch_save_target: Optional[int] = None   # 被救的人
        self.witch_poison_target: Optional[int] = None
        self.seer_check_target: Optional[int] = None
        self.seer_check_result: Optional[str] = None
        self.guard_protect_target: Optional[int] = None

        # 女巫信息：每晚被狼刀的目标
        self.night_wolf_target: Optional[int] = None

        # 狼人夜间讨论记录（仅狼人可见）
        self.wolf_chat_log: List[str] = []

        # 初始化玩家
        self._init_players()

    def _init_players(self):
        for seat, role in self.roster:
            # 生成系统提示（成员2提供的角色提示词可在这里替换）
            sys_prompt = self._build_system_prompt(seat, role)
            self.game.add_agent(seat, role, sys_prompt)
            self.players[seat] = PlayerState(seat=seat, role=role, alive=True)
            self.alive_seats.add(seat)

    def _build_system_prompt(self, seat: int, role: str) -> str:
        """生成角色系统提示。成员2可在此处替换为更精细的Prompt。"""
        base = f"你是玩家{seat}号，角色：{role}。"
        if role == "werewolf":
            teammates = [s for s, p in self.players.items() if p.role == "werewolf" and s != seat]
            base += f" 你的狼队友是：{teammates}。你可以在白天伪装成好人，夜晚与队友协商杀人。"
        elif role == "seer":
            base += " 你每晚可以查验一名玩家的身份，系统会告诉你他是狼人还是好人。"
        elif role == "witch":
            base += " 你有一瓶解药和一瓶毒药，每晚可以救人或毒人，不能自救。"
        elif role == "hunter":
            base += " 如果你被放逐出局，你可以开枪带走一名玩家；若被毒死则不能开枪。"
        elif role == "guard":
            base += " 你每晚可以守护一名玩家（不能连续两晚同一人），被守护者免疫狼刀，但被女巫救+守护会奶穿。"
        else:
            base += " 你没有特殊能力，通过发言和投票找出狼人。"
        base += " 请始终按JSON格式回复。"
        return base

    # ========== 辅助方法 ==========
    def _log_public(self, msg: str):
        """添加一条公开日志，所有玩家在白天都能看到"""
        self.public_log.append(msg)
        print(f"[GAME] {msg}")

    def _log_wolf_chat(self, msg: str):
        """记录狼人夜间内部聊天（不公开）"""
        self.wolf_chat_log.append(msg)
        print(f"[WOLF_CHAT] {msg}")

    async def _ask_player(self, seat: int, prompt: str, extra_context: str = "") -> AgentResponse:
        """封装单个玩家询问，支持额外的上下文（如私密信息）"""
        full_prompt = prompt
        if extra_context:
            full_prompt = f"{extra_context}\n\n{prompt}"
        try:
            return await self.game.ask(seat, full_prompt)
        except Exception as e:
            self._log_public(f"玩家{seat}行动失败: {e}，跳过本回合")
            # 返回一个默认的skip响应
            return AgentResponse(
                thought="",
                speech="",
                action="skip",
                target=None,
                confidence=0.0
            )

    async def _gather_alive(self, prompt_builder, timeout=30.0) -> Dict[int, AgentResponse]:
        """并行询问所有存活玩家，自动容错"""
        alive = list(self.alive_seats)
        if not alive:
            return {}
        return await self.game.gather(
            seats=alive,
            prompt_builder=prompt_builder,
            timeout=timeout,
            on_failure=lambda seat, e: AgentResponse(
                thought="",
                speech="",
                action="skip",
                target=None,
                confidence=0.0
            )
        )

    def _get_public_log_since_day_start(self) -> str:
        """获取从当天开始到现在的所有公开日志（用于白天发言）"""
        # 找到最近一天开始的日志索引
        day_marker = f"【白天第{self.day}天】"
        lines = self.public_log
        # 倒序查找，找到最后一条当天开始标记
        idx = len(lines) - 1
        while idx >= 0 and day_marker not in lines[idx]:
            idx -= 1
        if idx >= 0:
            recent_logs = lines[idx:]
        else:
            recent_logs = lines[-20:]  # 保底取最后20条
        return "\n".join(recent_logs)

    # ========== 黑夜阶段 ==========
    async def _night_werewolf(self):
        """
        狼人夜间讨论（顺序发言，所有狼人可见彼此讨论内容）
        最终根据所有狼人提出的目标进行多数决，决定刀人目标
        """
        self.phase = Phase.NIGHT_WEREWOLF
        self._log_public("【黑夜阶段】狼人请闭眼，商讨杀人目标。")

        wolf_seats = [s for s, p in self.players.items() if p.role == "werewolf" and p.alive]
        if not wolf_seats:
            self.wolf_kill_target = None
            return

        # 顺序询问每个狼人，让他们看到之前的讨论记录
        wolf_proposals = []  # 记录每个狼人最终提议的目标
        for seat in wolf_seats:
            # 构建讨论历史
            chat_history = "\n".join(self.wolf_chat_log) if self.wolf_chat_log else "暂无讨论记录。"
            prompt = (
                f"当前存活玩家：{list(self.alive_seats)}。\n"
                f"狼人内部讨论记录：\n{chat_history}\n\n"
                "请发表你的意见，并提出今晚你建议杀害的目标。请回复action='speak'，speech填写你的发言，"
                "同时你必须指定action='kill'，target=座位号。\n"
                "格式示例：{\"action\": \"speak\", \"speech\": \"我建议杀3号\", \"action2\": \"kill\", \"target\": 3}\n"
                "注意：最终系统会统计所有狼人的提议，按多数决确定最终目标。"
            )
            resp = await self._ask_player(seat, prompt)
            # 记录发言到狼人日志
            if resp.action == "speak" and resp.speech:
                self._log_wolf_chat(f"狼人{seat}说：{resp.speech}")
            # 提取提议目标（可能从 resp 的某个字段，这里简单从 action/target 或 speech 中解析）
            # 为了简化，我们期望狼人在回复中同时包含 action='kill' 和 target
            # 如果 resp 没有直接给出，可以尝试从 speech 中解析，这里按最简方式：
            if hasattr(resp, 'target') and resp.target and resp.target in self.alive_seats and resp.target not in wolf_seats:
                wolf_proposals.append(resp.target)
            else:
                # 若没有合法目标，则随机选一个非狼人（避免程序崩溃）
                non_wolves = [s for s in self.alive_seats if s not in wolf_seats]
                wolf_proposals.append(random.choice(non_wolves) if non_wolves else None)

        # 统计提议，多数决
        if wolf_proposals:
            from collections import Counter
            counter = Counter(wolf_proposals)
            # 移除 None
            if None in counter:
                del counter[None]
            if counter:
                max_count = max(counter.values())
                candidates = [t for t, c in counter.items() if c == max_count]
                # 平局取座位号最大者
                self.wolf_kill_target = max(candidates)
            else:
                # 所有提议无效，随机杀非狼人
                non_wolves = [s for s in self.alive_seats if s not in wolf_seats]
                self.wolf_kill_target = random.choice(non_wolves) if non_wolves else None
        else:
            non_wolves = [s for s in self.alive_seats if s not in wolf_seats]
            self.wolf_kill_target = random.choice(non_wolves) if non_wolves else None

        self.night_wolf_target = self.wolf_kill_target
        self._log_public(f"狼人决定杀害玩家{self.wolf_kill_target}")

    async def _night_witch(self):
        """女巫行动"""
        self.phase = Phase.NIGHT_WITCH
        witch_seat = [s for s, p in self.players.items() if p.role == "witch" and p.alive]
        if not witch_seat or self.night_wolf_target is None:
            return
        witch = witch_seat[0]
        state = self.players[witch]

        # 构造女巫信息
        prompt = (
            f"今晚狼人杀害了玩家{self.night_wolf_target}。你有一瓶解药{'已使用' if state.witch_antidote_used else '可用'}，"
            f"一瓶毒药{'已使用' if state.witch_poison_used else '可用'}。"
            "请选择：救他（action='save'，target=被救者），毒杀某人（action='poison'，target=目标），或什么都不做（action='skip'）。"
            "注意：不能自救，且每夜只能使用一瓶药。"
        )
        resp = await self._ask_player(witch, prompt)
        if resp.action == "save" and not state.witch_antidote_used:
            if resp.target == self.night_wolf_target:
                self.witch_save_target = self.night_wolf_target
                state.witch_antidote_used = True
                self._log_public(f"女巫使用了解药，救下了{self.night_wolf_target}")
            else:
                self._log_public("女巫解药使用无效（只能救被刀者），未生效")
        elif resp.action == "poison" and not state.witch_poison_used:
            if resp.target in self.alive_seats and resp.target != witch:
                self.witch_poison_target = resp.target
                state.witch_poison_used = True
                self._log_public(f"女巫使用了毒药，毒杀{resp.target}")
            else:
                self._log_public("女巫毒药目标无效，未使用")
        else:
            self._log_public("女巫未使用任何药物")

    async def _night_seer(self):
        """预言家查验"""
        self.phase = Phase.NIGHT_SEER
        seer_seat = [s for s, p in self.players.items() if p.role == "seer" and p.alive]
        if not seer_seat:
            return
        seer = seer_seat[0]
        state = self.players[seer]
        prompt = (
            f"当前存活玩家：{list(self.alive_seats)}。请选择一名玩家查验其身份。"
            "回复action='check'，target=座位号。"
        )
        resp = await self._ask_player(seer, prompt)
        if resp.action == "check" and resp.target in self.alive_seats:
            self.seer_check_target = resp.target
            target_role = self.players[resp.target].role
            self.seer_check_result = "狼人" if target_role == "werewolf" else "好人"
            # 保存结果到玩家状态，等待白天告知
            state.seer_last_check_target = resp.target
            state.seer_last_check_result = self.seer_check_result
            state.seer_result_acknowledged = False
            # 不公开打印结果，仅记录调试
            print(f"[预言家] 查验{resp.target}，结果为{self.seer_check_result}")
        else:
            self._log_public("预言家查验无效")

    async def _night_guard(self):
        """守卫守护"""
        self.phase = Phase.NIGHT_GUARD
        guard_seat = [s for s, p in self.players.items() if p.role == "guard" and p.alive]
        if not guard_seat:
            return
        guard = guard_seat[0]
        state = self.players[guard]
        last = state.guard_last_protected
        prompt = (
            f"当前存活玩家：{list(self.alive_seats)}。"
            f"{'你昨晚守护了'+str(last)+'，' if last else ''}不能连续两晚守护同一人。"
            "请选择守护目标：action='protect'，target=座位号。"
        )
        resp = await self._ask_player(guard, prompt)
        if resp.action == "protect" and resp.target in self.alive_seats and resp.target != last:
            self.guard_protect_target = resp.target
            state.guard_last_protected = resp.target
            self._log_public(f"守卫守护了{resp.target}")
        else:
            self._log_public("守卫未守护或守护无效")

    async def _resolve_night(self):
        """结算夜晚死亡"""
        self.phase = Phase.NIGHT_RESOLVE
        dead_set = set()

        # 狼刀目标
        if self.night_wolf_target:
            # 检查是否被守护或女巫救
            saved_by_witch = (self.witch_save_target == self.night_wolf_target)
            protected_by_guard = (self.guard_protect_target == self.night_wolf_target)

            # 奶穿：同时被救和守护 -> 死
            if saved_by_witch and protected_by_guard:
                dead_set.add(self.night_wolf_target)
                self._log_public(f"奶穿！玩家{self.night_wolf_target}同时被救和守护，依然死亡。")
            elif saved_by_witch:
                pass  # 被救活
            elif protected_by_guard:
                pass  # 被守护免死
            else:
                dead_set.add(self.night_wolf_target)

        # 女巫毒药
        if self.witch_poison_target:
            dead_set.add(self.witch_poison_target)

        self.night_deaths = list(dead_set)
        for seat in self.night_deaths:
            if seat in self.alive_seats:
                self.players[seat].alive = False
                self.alive_seats.remove(seat)
                self._log_public(f"玩家{seat}在夜晚死亡。")

        # 清除临时变量
        self.wolf_kill_target = None
        self.witch_save_target = None
        self.witch_poison_target = None
        self.guard_protect_target = None
        self.night_wolf_target = None

    # ========== 白天阶段 ==========
    async def _day_start(self):
        self.phase = Phase.DAY_START
        self.day += 1
        death_info = f"昨晚死亡玩家：{self.night_deaths if self.night_deaths else '无人死亡'}"
        self._log_public(f"【白天第{self.day}天】{death_info}")

    async def _sheriff_election(self):
        """首日警长竞选"""
        if self.sheriff_elected:
            return
        self.phase = Phase.DAY_SHERRIF_ELECT
        self._log_public("开始警长竞选，请玩家上警发言。")
        # 简化：所有存活玩家均有机会上警，每人发言后未上警玩家投票
        alive = list(self.alive_seats)
        if not alive:
            return

        # 第一步：上警发言顺序（按座位号）
        candidates = []
        for seat in alive:
            prompt = f"你是否愿意上警竞选警长？如果愿意，请发表你的竞选发言（action='speak'，speech填写发言内容）。如果不愿意，回复action='skip'。"
            resp = await self._ask_player(seat, prompt)
            if resp.action == "speak":
                candidates.append(seat)
                speech = resp.speech
                self._log_public(f"玩家{seat}上警发言：{speech}")

        if not candidates:
            self._log_public("无人上警，本局无警长")
            self.sheriff_elected = True
            return

        # 第二步：未上警玩家投票
        voters = [s for s in alive if s not in candidates]
        if not voters:
            # 全部上警，则全体投票（包括上警者？通常上警者不投票，简化处理：全体投）
            voters = alive[:]

        # 并行投票
        async def voter_prompt(seat):
            return f"请从警长候选人{candidates}中投票，回复action='vote'，target=座位号。"
        votes = await self.game.gather(seats=voters, prompt_builder=voter_prompt, timeout=20.0)
        tally = {}
        for resp in votes.values():
            if resp.action == "vote" and resp.target in candidates:
                tally[resp.target] = tally.get(resp.target, 0) + 1
        if tally:
            max_votes = max(tally.values())
            winners = [s for s, c in tally.items() if c == max_votes]
            if len(winners) == 1:
                self.sheriff = winners[0]
                self._log_public(f"玩家{self.sheriff}当选警长，拥有1.5票投票权。")
            else:
                # 平票：再次发言+投票（简化：随机）
                self.sheriff = random.choice(winners)
                self._log_public(f"平票，随机选定警长{self.sheriff}")
        else:
            self._log_public("无人投票，本局无警长")
        self.sheriff_elected = True

    async def _day_speak(self):
        """全体轮流发言（所有玩家都能看到完整的公开日志）"""
        self.phase = Phase.DAY_SPEAK
        self._log_public("开始公开发言环节")
        # 按座位号顺序发言
        for seat in sorted(self.alive_seats):
            player = self.players[seat]
            # 构建公开日志（从当天开始到现在）
            public_context = self._get_public_log_since_day_start()
            # 为预言家单独提供上一晚的查验结果（如果尚未告知）
            extra_info = ""
            if player.role == "seer" and not player.seer_result_acknowledged and player.seer_last_check_target is not None:
                extra_info = f"【私密信息】昨晚你查验了{player.seer_last_check_target}，结果是{player.seer_last_check_result}。"
                player.seer_result_acknowledged = True  # 标记已告知
            prompt = (
                f"当前存活玩家：{list(self.alive_seats)}。\n"
                f"公开日志（从今天开始）：\n{public_context}\n"
                f"{extra_info}\n"
                "请发表你的看法，可以指认狼人、解释行为等。回复action='speak'，speech填写发言内容。"
            )
            resp = await self._ask_player(seat, prompt)
            if resp.action == "speak":
                speech = resp.speech
                self._log_public(f"玩家{seat}发言：{speech}")
            else:
                self._log_public(f"玩家{seat}未发言")

    async def _day_vote(self):
        """投票放逐"""
        self.phase = Phase.DAY_VOTE
        self._log_public("开始投票放逐")
        alive = list(self.alive_seats)
        if len(alive) <= 2:
            self._log_public("剩余人数过少，跳过投票")
            return

        # 构造投票提示，同时提供最近的公开日志作为参考
        public_context = self._get_public_log_since_day_start()
        def vote_prompt(seat):
            sheriff_note = "你作为警长，票数为1.5。" if seat == self.sheriff else "你有一票。"
            return f"当前存活：{alive}。\n公开日志（从今天开始）：\n{public_context}\n{sheriff_note}\n请投票放逐一名玩家。回复action='vote'，target=座位号。"

        votes_dict = await self._gather_alive(vote_prompt, timeout=30.0)
        tally = {}
        for seat, resp in votes_dict.items():
            if resp.action == "vote" and resp.target in alive:
                weight = 1.5 if seat == self.sheriff else 1
                tally[resp.target] = tally.get(resp.target, 0.0) + weight
            else:
                # 弃票或无效，不计入
                pass
        if not tally:
            self._log_public("无人投票，平局结束？")
            return

        max_votes = max(tally.values())
        eliminated = [s for s, v in tally.items() if v == max_votes]
        if len(eliminated) > 1:
            self._log_public(f"平票，候选人{eliminated}进入PK发言")
            # PK环节：平票玩家再次发言，其余玩家重新投票（简化：随机放逐）
            eliminated = [random.choice(eliminated)]
        eliminated_seat = eliminated[0]
        self._log_public(f"玩家{eliminated_seat}被放逐出局")

        # 放逐出局
        await self._eliminate(eliminated_seat, reason="vote")

    async def _eliminate(self, seat: int, reason: str = "vote"):
        """处理玩家出局（放逐或夜晚死亡后的额外触发，如猎人开枪）"""
        if seat not in self.alive_seats:
            return
        player = self.players[seat]
        player.alive = False
        self.alive_seats.remove(seat)
        self._log_public(f"玩家{seat}出局，身份是{player.role}")

        # 猎人开枪（仅当被放逐时）
        if player.role == "hunter" and reason == "vote" and not player.hunter_shot_triggered:
            player.hunter_shot_triggered = True
            self._log_public(f"猎人{seat}发动技能，开枪带走一人。")
            # 让猎人选择目标
            prompt = f"你被放逐出局，可以开枪带走一名玩家。存活玩家：{list(self.alive_seats)}。回复action='shoot'，target=座位号。"
            resp = await self._ask_player(seat, prompt)
            if resp.action == "shoot" and resp.target in self.alive_seats:
                await self._eliminate(resp.target, reason="hunter_shot")
            else:
                self._log_public("猎人未开枪或目标无效")

    # ========== 遗言 ==========
    async def _handle_last_words(self):
        """处理遗言：只有第一晚死亡的玩家有遗言"""
        if self.day == 1 and self.night_deaths:
            # 第一晚死亡的玩家可以有遗言（简化：每个死亡玩家依次发言）
            for seat in self.night_deaths:
                if not self.players[seat].alive:
                    prompt = f"你已死亡，请留遗言。你可以指认狼人、表达遗憾等。回复action='speak'，speech填写遗言。"
                    resp = await self._ask_player(seat, prompt)
                    if resp.action == "speak":
                        self._log_public(f"遗言（玩家{seat}）：{resp.speech}")
        else:
            self._log_public("无遗言环节")

    # ========== 胜负判定 ==========
    def _check_game_over(self) -> Tuple[bool, Optional[str]]:
        """返回 (是否结束, 胜利阵营描述)"""
        alive_roles = [self.players[s].role for s in self.alive_seats]
        wolf_alive = any(r == "werewolf" for r in alive_roles)
        villager_alive = any(r == "villager" for r in alive_roles)
        special_alive = any(r in ["seer", "witch", "hunter", "guard"] for r in alive_roles)

        if not wolf_alive:
            return True, "好人阵营胜利！所有狼人出局。"
        if not villager_alive or not special_alive:
            return True, "狼人阵营胜利！"
        return False, None

    # ========== 主循环 ==========
    async def run(self):
        self._log_public("游戏开始！角色分配完成。")
        # 主循环
        while True:
            # 夜晚阶段（按顺序）
            await self._night_werewolf()
            await self._night_witch()
            await self._night_seer()
            await self._night_guard()
            await self._resolve_night()

            # 白天阶段
            await self._day_start()
            await self._handle_last_words()

            if not self.sheriff_elected:
                await self._sheriff_election()

            await self._day_speak()
            await self._day_vote()

            # 胜负判定
            ended, winner_msg = self._check_game_over()
            if ended:
                self._log_public(winner_msg)
                self.phase = Phase.GAME_OVER
                break

            # 重置夜晚临时变量
            self.night_deaths = []
            # 如果狼人全灭，会在下一轮开头判定，实际上已经在上面的判定中结束

        # 最终统计
        stats = self.game.stats_snapshot()
        self._log_public(f"游戏结束，总调用次数{stats['calls']}，消耗token：{stats['prompt_tokens']}+{stats['completion_tokens']}")


# ========== 运行入口 ==========
async def main():
    async with LLMGateway() as gw:
        game = GameSession(gw, game_id="12p_game")
        engine = WerewolfGameEngine(game)  # 默认随机分配角色
        await engine.run()


if __name__ == "__main__":
    asyncio.run(main())