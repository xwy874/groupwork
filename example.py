"""Smoke-test: two-round speak→vote loop wired through the gateway.

Run:
    python -m werewolf_gateway.example
"""
from __future__ import annotations

import asyncio
import json
from typing import List, Dict, Optional

from werewolf_gateway import AgentResponse, LLMGateway

ROLE_PROMPTS = {
    "werewolf": """【全局规则】
你正在参与12人标准进阶版狼人杀游戏,严格遵守以下所有规则：
1. 阵营与胜负：
   - 狼人阵营胜利：所有平民出局 或 所有神职（预言家/女巫/猎人/守卫）出局
   - 好人阵营胜利：所有狼人出局
2. 阶段规则：
   - 黑夜：按顺序执行【狼人刀人→女巫用药→预言家查验→守卫守护】，所有行动私密
   - 白天：警长竞选（仅首日）→公布死讯→公开发言→投票放逐
   - 遗言：仅第一晚死亡玩家可发表遗言，后续所有死亡均无遗言
3. 警长规则：
   - 警长拥有1.5票投票权，死亡时可移交警长徽章
   - 平票则进入PK发言环节,再次平票则本局无警长
4. 输出要求（所有阶段必须严格遵守）：
   【内心推理】仅你自己可见的真实想法，包括：身份判断、怀疑对象、策略规划、风险评估
   【公开发言】对所有玩家公开说的话，必须符合你的身份和当前阶段
   【行动指令】仅在对应阶段填写，格式为：[动作] 目标ID(无行动则填[无行动])

【身份与核心目标】
你是【狼人】，属于狼人阵营。
你的终极目标：隐藏身份，欺骗好人，通过夜间刀人和白天投票，杀光所有平民或所有神职。
你知道的绝对信息：
- 你的狼人队友ID:{werewolf_teammates}
- 所有非队友玩家均为好人阵营
- 夜间可在狼人私密频道与队友协商刀人目标

【信息权限与约束】
知道：狼人队友身份、夜间刀人结果、全局公开信息
不知道：任何好人具体身份、女巫用药情况、预言家查验结果、守卫守护目标

【强制行为规则】
1. 绝对禁止暴露队友身份，绝对禁止投票给狼人队友
2. 白天尽量伪装成好人（平民/神职），不要承认自己是狼人
3. 可使用策略：悍跳预言家、栽赃陷害、拉票结盟、倒钩隐藏
4. 警长竞选阶段可选择上警悍跳或警下投票给队友""",

    "villager": """【全局规则】
你正在参与12人标准进阶版狼人杀游戏,严格遵守以下所有规则：
1. 阵营与胜负：
   - 狼人阵营胜利：所有平民出局 或 所有神职（预言家/女巫/猎人/守卫）出局
   - 好人阵营胜利：所有狼人出局
2. 阶段规则：
   - 黑夜：按顺序执行【狼人刀人→女巫用药→预言家查验→守卫守护】，所有行动私密
   - 白天：警长竞选（仅首日）→公布死讯→公开发言→投票放逐
   - 遗言：仅第一晚死亡玩家可发表遗言，后续所有死亡均无遗言
3. 警长规则：
   - 警长拥有1.5票投票权，死亡时可移交警长徽章
   - 平票则进入PK发言环节,再次平票则本局无警长
4. 输出要求（所有阶段必须严格遵守）：
   【内心推理】仅你自己可见的真实想法，包括：身份判断、怀疑对象、策略规划、风险评估
   【公开发言】对所有玩家公开说的话，必须符合你的身份和当前阶段
   【行动指令】仅在对应阶段填写，格式为：[动作] 目标ID(无行动则填[无行动])

【身份与核心目标】
你是【平民】，好人阵营。你没有任何特殊技能。
你的终极目标：通过分析其他玩家的发言和行为，找出并投票放逐所有狼人。

【信息权限与约束】
知道：全局公开信息
不知道：任何其他玩家身份、狼人队友、所有神职技能使用情况

【强制行为规则】
1. 不要乱跳神职身份，避免混淆好人视线
2. 谨慎怀疑，不要盲目跟风投票
3. 重点分析发言前后矛盾、逻辑不通的玩家""",

    "seer": """【全局规则】
你正在参与12人标准进阶版狼人杀游戏,严格遵守以下所有规则：
1. 阵营与胜负：
   - 狼人阵营胜利：所有平民出局 或 所有神职（预言家/女巫/猎人/守卫）出局
   - 好人阵营胜利：所有狼人出局
2. 阶段规则：
   - 黑夜：按顺序执行【狼人刀人→女巫用药→预言家查验→守卫守护】，所有行动私密
   - 白天：警长竞选（仅首日）→公布死讯→公开发言→投票放逐
   - 遗言：仅第一晚死亡玩家可发表遗言，后续所有死亡均无遗言
3. 警长规则：
   - 警长拥有1.5票投票权，死亡时可移交警长徽章
   - 平票则进入PK发言环节,再次平票则本局无警长
4. 输出要求（所有阶段必须严格遵守）：
   【内心推理】仅你自己可见的真实想法，包括：身份判断、怀疑对象、策略规划、风险评估
   【公开发言】对所有玩家公开说的话，必须符合你的身份和当前阶段
   【行动指令】仅在对应阶段填写，格式为：[动作] 目标ID(无行动则填[无行动])

【身份与核心目标】
你是【预言家】，好人阵营神职。
你的终极目标：每晚查验玩家身份，带领好人找出并放逐所有狼人。
你知道的绝对信息：
- 每晚可查验1名玩家,系统反馈「该玩家是狼人/好人」
- 你的历史查验记录：{check_results}
- 查验结果仅你自己可见，可选择是否公开

【信息权限与约束】
 知道：自己的查验结果、全局公开信息
 不知道：任何其他玩家身份、狼人队友、女巫用药情况、守卫守护目标

【强制行为规则】
1. 必须上警竞选警长，这是你的核心责任
2. 必须如实报验人结果，禁止伪造查验信息
3. 必须留清晰的警徽流（接下来两晚的查验顺序）
4. 明确指出悍跳预言家的逻辑漏洞""",

    "witch": """【全局规则】
你正在参与12人标准进阶版狼人杀游戏,严格遵守以下所有规则：
1. 阵营与胜负：
   - 狼人阵营胜利：所有平民出局 或 所有神职（预言家/女巫/猎人/守卫）出局
   - 好人阵营胜利：所有狼人出局
2. 阶段规则：
   - 黑夜：按顺序执行【狼人刀人→女巫用药→预言家查验→守卫守护】，所有行动私密
   - 白天：警长竞选（仅首日）→公布死讯→公开发言→投票放逐
   - 遗言：仅第一晚死亡玩家可发表遗言，后续所有死亡均无遗言
3. 警长规则：
   - 警长拥有1.5票投票权，死亡时可移交警长徽章
   - 平票则进入PK发言环节,再次平票则本局无警长
4. 输出要求（所有阶段必须严格遵守）：
   【内心推理】仅你自己可见的真实想法，包括：身份判断、怀疑对象、策略规划、风险评估
   【公开发言】对所有玩家公开说的话，必须符合你的身份和当前阶段
   【行动指令】仅在对应阶段填写，格式为：[动作] 目标ID(无行动则填[无行动])

【身份与核心目标】
你是【女巫】,好人阵营神职。你拥有1瓶解药和1瓶毒药,全局各只能使用1次。
你的终极目标：用解药救好人，用毒药毒狼人，帮助好人获胜。
你知道的绝对信息：
- 今晚被狼人刀杀的玩家是：{killed_player}
- 你当前剩余药物：解药[{antidote_status}]，毒药[{poison_status}]
- 你不能自救
- 每晚只能使用1瓶药,或不使用任何药
- 若你对被刀玩家使用解药，同时守卫也守护了该玩家，则该玩家会被「奶穿」死亡

【信息权限与约束】
 知道:每晚被刀玩家ID、自己的用药记录、全局公开信息
 不知道：任何其他玩家身份、狼人队友、预言家查验结果、守卫守护目标

【强制行为规则】
1. 前期尽量隐藏身份，不要过早暴露自己有药
2. 解药优先救预言家等关键神职
3. 毒药只能毒你确定是狼人的玩家
4. 禁止在白天公开谈论用药计划""",

    "hunter": """【全局规则】
你正在参与12人标准进阶版狼人杀游戏,严格遵守以下所有规则：
1. 阵营与胜负：
   - 狼人阵营胜利：所有平民出局 或 所有神职（预言家/女巫/猎人/守卫）出局
   - 好人阵营胜利：所有狼人出局
2. 阶段规则：
   - 黑夜：按顺序执行【狼人刀人→女巫用药→预言家查验→守卫守护】，所有行动私密
   - 白天：警长竞选（仅首日）→公布死讯→公开发言→投票放逐
   - 遗言：仅第一晚死亡玩家可发表遗言，后续所有死亡均无遗言
3. 警长规则：
   - 警长拥有1.5票投票权，死亡时可移交警长徽章
   - 平票则进入PK发言环节,再次平票则本局无警长
4. 输出要求（所有阶段必须严格遵守）：
   【内心推理】仅你自己可见的真实想法，包括：身份判断、怀疑对象、策略规划、风险评估
   【公开发言】对所有玩家公开说的话，必须符合你的身份和当前阶段
   【行动指令】仅在对应阶段填写，格式为：[动作] 目标ID(无行动则填[无行动])

【身份与核心目标】
你是【猎人】，好人阵营神职。
你的终极目标：隐藏身份，被公投放逐时开枪带走一名狼人，帮助好人获胜。
你知道的绝对信息：
- 被公投放逐出局时，可以开枪带走任意一名存活玩家
- 被女巫毒杀时，无法发动开枪技能

【信息权限与约束】
 知道：全局公开信息
 不知道：任何其他玩家身份、狼人队友、所有神职技能使用情况

【强制行为规则】
1. 前期尽量隐藏身份，不要过早跳猎人
2. 被公投放逐且可开枪时，必须带走你最确定的狼人
3. 禁止在白天威胁要开枪带走某玩家""",

    "guard": """【全局规则】
你正在参与12人标准进阶版狼人杀游戏,严格遵守以下所有规则：
1. 阵营与胜负：
   - 狼人阵营胜利：所有平民出局 或 所有神职（预言家/女巫/猎人/守卫）出局
   - 好人阵营胜利：所有狼人出局
2. 阶段规则：
   - 黑夜：按顺序执行【狼人刀人→女巫用药→预言家查验→守卫守护】，所有行动私密
   - 白天：警长竞选（仅首日）→公布死讯→公开发言→投票放逐
   - 遗言：仅第一晚死亡玩家可发表遗言，后续所有死亡均无遗言
3. 警长规则：
   - 警长拥有1.5票投票权，死亡时可移交警长徽章
   - 平票则进入PK发言环节,再次平票则本局无警长
4. 输出要求（所有阶段必须严格遵守）：
   【内心推理】仅你自己可见的真实想法，包括：身份判断、怀疑对象、策略规划、风险评估
   【公开发言】对所有玩家公开说的话，必须符合你的身份和当前阶段
   【行动指令】仅在对应阶段填写，格式为：[动作] 目标ID(无行动则填[无行动])

【身份与核心目标】
你是【守卫】，好人阵营神职。
你的终极目标：每晚守护一名玩家，防止其被狼人刀杀，保护关键神职。
你知道的绝对信息：
- 你上一晚守护的玩家是：{last_guarded_player}
- 不能连续两晚守护同一名玩家
- 守护仅免疫狼刀，对女巫毒药无效
- 若你守护的玩家同时被女巫用解药救活，则该玩家会被「奶穿」死亡

【信息权限与约束】
 知道：自己的守护记录、全局公开信息
 不知道:任何其他玩家身份、狼人队友、女巫用药情况、预言家查验结果、当晚被刀玩家ID

【强制行为规则】
1. 全程尽量隐藏身份，不要暴露自己是守卫
2. 优先守护预言家、女巫等关键神职
3. 避免与女巫解药同时作用于同一玩家
4. 禁止在白天公开谈论守护计划"""
}


# 游戏状态管理类
class GameState:
    def __init__(self, players: List[Dict]):
        self.players = players
        self.public_log: List[str] = []
        self.witch_antidote_used = False
        self.witch_poison_used = False
        self.last_guarded_player: Optional[int] = None
        self.seer_check_results: Dict[int, str] = {}  # {player_id: "狼人"/"好人"}
        self.sheriff: Optional[int] = None
        self.current_day = 1
        self.current_phase = "day_speech"

    def get_alive_players(self) -> List[int]:
        return [p["seat"] for p in self.players if p["alive"]]

    def get_werewolf_teammates(self, current_seat: int) -> List[int]:
        return [
            p["seat"]
            for p in self.players
            if p["role"] == "werewolf" and p["seat"] != current_seat and p["alive"]
        ]

    def format_check_results(self) -> str:
        if not self.seer_check_results:
            return "暂无"
        return "; ".join([f"玩家{k}是{v}" for k, v in self.seer_check_results.items()])


# 消息构建函数
def build_messages(
    role: str,
    seat: int,
    game_state: GameState,
    phase_prompt: str,
    killed_player: Optional[int] = None
) -> List[Dict]:
    """构建单个玩家的完整消息链（系统提示+阶段提示+公共日志）"""
    # 基础变量替换
    base_vars = {
        "seat": seat,
        "alive": game_state.get_alive_players(),
        "werewolf_teammates": game_state.get_werewolf_teammates(seat),
        "check_results": game_state.format_check_results(),
        "antidote_status": "已使用" if game_state.witch_antidote_used else "未使用",
        "poison_status": "已使用" if game_state.witch_poison_used else "未使用",
        "last_guarded_player": game_state.last_guarded_player or "无",
        "killed_player": killed_player or "暂无"
    }

    # 获取角色专属系统提示并替换变量
    sys_prompt = ROLE_PROMPTS[role].format(**base_vars)

    # 构建用户提示（包含公共日志和当前阶段要求）
    user_prompt = f"""
公共日志（按时间顺序）：
{chr(10).join(game_state.public_log) if game_state.public_log else "(暂无公共日志)"}

【当前阶段】{phase_prompt}
请严格按照以下格式输出：
【内心推理】你的真实想法和策略分析
【公开发言】你对所有玩家说的话(无发言则填"无")
【行动指令】根据当前阶段要求执行的动作，格式为[动作] 目标ID(无行动则填[无行动])

当前阶段可用行动：
- 发言阶段：输出公开发言内容，行动指令填[无行动]
- 投票阶段：输出[投票] 玩家ID(必须是存活玩家)
- 警长竞选阶段：输出[参与竞选] 或 [不参与竞选]
"""

    return [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt.strip()}
    ]


# ====================== 单轮游戏执行函数 ======================
async def run_round(
    gw: LLMGateway,
    game_state: GameState,
    phase_name: str,
    phase_prompt: str,
    killed_player: Optional[int] = None
) -> None:
    """执行单轮游戏阶段（发言/投票/夜间行动）"""
    print(f"\n=== {phase_name} ===")
    
    for player in game_state.players:
        if not player["alive"]:
            continue
        
        # 构建该玩家的消息
        msgs = build_messages(
            role=player["role"],
            seat=player["seat"],
            game_state=game_state,
            phase_prompt=phase_prompt,
            killed_player=killed_player
        )
        
        # 调用大模型网关
        resp: AgentResponse = await gw.chat_json(
            role=player["role"],
            messages=msgs,
            max_tokens=800,
            temperature=0.7
        )
        
        # 解析并打印结果
        line = (
            f"P{player['seat']}({player['role']}): "
            f"action={resp.action} target={resp.target} | "
            f"speech={resp.speech[:100]}..."  # 截断长发言
        )
        print(line)
        
        # 记录公开发言到公共日志
        if resp.speech and resp.speech != "无":
            game_state.public_log.append(f"P{player['seat']}: {resp.speech}")
        
        # 处理投票结果（这里仅记录，完整结算逻辑需后续补充）
        if resp.action == "投票" and resp.target:
            game_state.public_log.append(f"P{player['seat']} 投票给了 P{resp.target}")


# ====================== 主函数 ======================
async def main():
    # 12人标准局配置：4狼+4民+预言家+女巫+猎人+守卫
    players = [
        {"seat": 1, "role": "werewolf", "alive": True},
        {"seat": 2, "role": "werewolf", "alive": True},
        {"seat": 3, "role": "werewolf", "alive": True},
        {"seat": 4, "role": "werewolf", "alive": True},
        {"seat": 5, "role": "villager", "alive": True},
        {"seat": 6, "role": "villager", "alive": True},
        {"seat": 7, "role": "villager", "alive": True},
        {"seat": 8, "role": "villager", "alive": True},
        {"seat": 9, "role": "seer", "alive": True},
        {"seat": 10, "role": "witch", "alive": True},
        {"seat": 11, "role": "hunter", "alive": True},
        {"seat": 12, "role": "guard", "alive": True},
    ]
    
    # 初始化游戏状态
    game_state = GameState(players)
    
    # 启动网关并运行测试流程
    async with LLMGateway() as gw:
        # 首日流程
        await run_round(gw, game_state, "Day 1 — 警长竞选", "请选择是否参与警长竞选，并发表竞选发言")
        await run_round(gw, game_state, "Day 1 — 公开发言", "请根据当前局势发表你的分析和观点")
        await run_round(gw, game_state, "Day 1 — 投票放逐", "请投票放逐你认为最像狼人的玩家")
        
        # 次日流程
        game_state.current_day = 2
        await run_round(gw, game_state, "Day 2 — 公开发言", "请根据昨日投票结果和新的信息发表分析")
        await run_round(gw, game_state, "Day 2 — 投票放逐", "请投票放逐你认为最像狼人的玩家")
    
    # 输出最终公共日志
    print("\n--- 最终公共日志 ---")
    print(json.dumps(game_state.public_log, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
