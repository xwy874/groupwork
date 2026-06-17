import json
import os
from collections import Counter, defaultdict


LOG_FOLDER = "D:\学习资料\大三下\文本分析与大模型\小组\group work (2)\group work\logs"

def load_jsonl_files(folder):
    records = []
    if not os.path.exists(folder):
        print(f"错误：文件夹不存在 -> {folder}")
        return records
    for filename in os.listdir(folder):
        if filename.endswith(".jsonl"):
            filepath = os.path.join(folder, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        records.append(obj)
                    except json.JSONDecodeError as e:
                        print(f"警告：{filename} 第{line_num}行 JSON 解析失败: {e}")
    return records


def main():
    records = load_jsonl_files(LOG_FOLDER)
    if not records:
        print("没有找到任何 JSONL 记录，请检查 LOG_FOLDER 路径。")
        return

    # 统计容器
    role_action_cnt = Counter()  # (role, action) -> count
    role_confidence = defaultdict(list)  # role -> list of confidence
    kills = []  # (seat, target)
    votes = []  # (seat, target)
    checks = []  # (seat, target)
    protects = []  # (seat, target)
    skips = []  # (seat, action)
    game_ids = set()
    agent_responses = []  # 存储完整响应供后续分析

    for rec in records:
        # 只处理 agent_response 类型（有 kind 字段且值为 "agent_response"）
        if rec.get("kind") != "agent_response":
            continue

        game_id = rec.get("game_id", "")
        if game_id:
            game_ids.add(game_id)

        seat = rec.get("seat")
        role = rec.get("role")
        resp = rec.get("response", {})
        if not resp or not role:
            continue

        action = resp.get("action")
        target = resp.get("target")
        confidence = resp.get("confidence")
        thought = resp.get("thought", "")
        speech = resp.get("speech", "")

        # 记录
        if action:
            role_action_cnt[(role, action)] += 1

        if confidence is not None:
            role_confidence[role].append(confidence)

        # 按行动类型收集目标
        if action == "kill" and target is not None:
            kills.append((seat, target))
        elif action == "vote" and target is not None:
            votes.append((seat, target))
        elif action == "check" and target is not None:
            checks.append((seat, target))
        elif action == "protect" and target is not None:
            protects.append((seat, target))
        elif action == "skip":
            skips.append((seat, action))

        # 保存完整响应（可选，用于后续文本分析）
        agent_responses.append({
            "game_id": game_id,
            "seat": seat,
            "role": role,
            "action": action,
            "target": target,
            "confidence": confidence,
            "thought": thought[:100] + "..." if len(thought) > 100 else thought,
            "speech": speech[:100] + "..." if len(speech) > 100 else speech
        })

    # 输出报告
    print("=" * 70)
    print("狼人杀多智能体信任度量化分析报告（基于 JSONL 日志）")
    print("=" * 70)
    print(f"分析文件：{LOG_FOLDER} 下的 .jsonl 文件")
    print(f"游戏局数：{len(game_ids)}")
    print(f"有效 agent_response 记录数：{len(agent_responses)}\n")

    print("--- 1. 各角色行动统计 ---")
    for (role, action), cnt in role_action_cnt.most_common():
        print(f"  {role:12} -> {action:8} : {cnt} 次")

    print("\n--- 2. 模型置信度（自评）---")
    for role, confs in role_confidence.items():
        avg_conf = sum(confs) / len(confs)
        print(f"  {role:12} : 平均置信度 = {avg_conf:.2f}  (样本数 {len(confs)})")

    if votes:
        vote_targets = Counter([t for _, t in votes])
        print("\n--- 3. 白天投票分布 ---")
        print(f"  总投票次数：{len(votes)}")
        for target, cnt in vote_targets.most_common():
            print(f"    座位 {target} 得票：{cnt} 次")

    if kills:
        kill_targets = Counter([t for _, t in kills])
        print("\n--- 4. 夜间击杀提议（狼人） ---")
        print(f"  总提议击杀次数：{len(kills)}")
        for target, cnt in kill_targets.most_common():
            print(f"    目标座位 {target} 被提议 {cnt} 次")
        # 检查狼人一致性
        unique_targets = len(kill_targets)
        if len(kills) > 1 and unique_targets > 1:
            print(f"  ⚠️ 狼人之间存在分歧：{len(kills)} 次提议指向 {unique_targets} 个不同目标")

    if checks:
        print("\n--- 5. 预言家查验 ---")
        for seat, target in checks:
            print(f"    座位 {seat} (预言家) 查验了座位 {target}")

    if protects:
        print("\n--- 6. 守卫守护 ---")
        for seat, target in protects:
            print(f"    座位 {seat} (守卫) 守护了座位 {target}")

    if skips:
        print(f"\n--- 7. 跳过行动（女巫/其他）---")
        print(f"    共 {len(skips)} 次 skip")

    # 简单信任分（基于活跃度）
    print("\n--- 8. 信任度基础分（活跃度）---")
    role_total_actions = defaultdict(int)
    for (role, _), cnt in role_action_cnt.items():
        role_total_actions[role] += cnt
    for role, total in role_total_actions.items():
        print(f"  {role:12} : 参与行动 {total} 次")

    # 可选：输出部分样例响应
    print("\n--- 9. 部分行动样例（前5条）---")
    for i, resp in enumerate(agent_responses[:5]):
        print(
            f"  [{i + 1}] {resp['role']} 座位{resp['seat']} -> {resp['action']} 目标{resp['target']} (置信度{resp['confidence']})")
        if resp['speech']:
            print(f"      发言: {resp['speech']}")

    print("\n" + "=" * 70)
    print("分析完成。如需更精确的信任分（正确/错误），需要游戏结果标注（谁赢、查验是否正确等）。")



if __name__ == "__main__":
    main()

# 重新组织数据结构：按 game_id 归类
games_data = defaultdict(lambda: {
    "kills": [], "votes": [], "checks": [], "protects": [],
    "saves": [], "poisons": [], "shoots": [], "no_votes": [],
    "roles": {}  # 记录每个座位这一局是什么角色
})

for rec in records:
    if rec.get("kind") != "agent_response":
        continue

    game_id = rec.get("game_id", "unknown_game")
    seat = rec.get("seat")
    role = rec.get("role")
    resp = rec.get("response", {})
    action = resp.get("action")
    target = resp.get("target")

    if not role or not action:
        continue

    games_data[game_id]["roles"][seat] = role

    # 精确归类到各自的对局中
    if action == "kill":
        games_data[game_id]["kills"].append((seat, target))
    elif action == "vote":
        games_data[game_id]["votes"].append((seat, target))
    elif action == "check":
        games_data[game_id]["checks"].append((seat, target))
    elif action == "protect":
        games_data[game_id]["protects"].append((seat, target))
    elif action == "save":
        games_data[game_id]["saves"].append((seat, target))
    elif action == "poison":
        games_data[game_id]["poisons"].append((seat, target))
    elif action == "shoot":
        games_data[game_id]["shoots"].append((seat, target))
    elif action == "no_vote":
        games_data[game_id]["no_votes"].append((seat, target))

# 输出报告时，遍历 games_data
for g_id, g_info in games_data.items():
    print(f"\n对局 {g_id} 详细分析：")
    # 在这里面算狼人分歧：因为 g_info["kills"] 只属于当前这一局！
    # 甚至可以进一步根据时间戳 ts 划分出 Night 1, Night 2...