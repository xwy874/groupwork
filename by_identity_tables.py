"""by_identity_tables.py — 按身份（狼人/平民/神职）分表，各 1 张，共 3 张。

现有 metrics.json 是“按模型”聚合的：同组里狼+神共用一个模型会被合并，无法分身份。
本脚本回到每局原始 game_log.jsonl + replay.json，按【身份】重新计算成员4 的指标，
再跨 30 局取均值。三组实验里每个身份所用模型：

           狼人        平民        神职(seer/witch/hunter/guard)
  正常     pro         flash       pro
  反转     flash       pro         flash
  全弱     flash       flash       flash

输出 3 张表（狼人 / 平民 / 神职），每张表的行=三组配置，列=该身份所用模型 + 指标：
  欺骗频次、被信任入度、平均置信度、怀疑目标改变率（+ 该身份阵营胜率）。
控制台 + by_identity_report.txt。
"""
from __future__ import annotations

import glob
import json
import os
from collections import defaultdict

import numpy as np

from werewolf_gateway.analysis_member4 import (
    agent_responses, build_trust_matrix, extract_seats, sentiment_score,
    DISTRUST_KW,
)

ROOT = os.path.dirname(os.path.abspath(__file__))
PRO, FLASH = "deepseek-v4-pro", "deepseek-v4-flash"

WOLF = {"werewolf"}
VILLAGER = {"villager"}
GOD = {"seer", "witch", "hunter", "guard"}

GROUPS = [
    ("正常", "result"),
    ("反转", "reversed-result"),
    ("全弱", "result-allflash"),
    ("全强", "result-allpro"),
]


def model_of(group, role):
    if group == "全弱":
        return FLASH
    if group == "全强":
        return PRO
    dec = PRO if group == "正常" else FLASH   # 狼+神
    vil = FLASH if group == "正常" else PRO   # 村民
    return vil if role in VILLAGER else dec


def identity_of(role):
    if role in WOLF:
        return "狼人"
    if role in VILLAGER:
        return "平民"
    if role in GOD:
        return "神职"
    return None


def is_deception(r, roles):
    """复用成员4 的欺骗判定（A/B/C 三选一）。"""
    role = r.get("role")
    resp = r["response"]
    thought = resp.get("thought", "") or ""
    speech = resp.get("speech", "") or ""
    action = resp.get("action")
    target = resp.get("target")
    seat = r.get("seat")
    if role == "werewolf" and roles and sentiment_score(speech) > 0:
        for s in extract_seats(speech):
            if s != seat and roles.get(s) == "werewolf":
                return True
    if thought and speech:
        th, sp = set(extract_seats(thought)), set(extract_seats(speech))
        if th & sp and sentiment_score(thought) * sentiment_score(speech) < 0:
            return True
    import re
    m = re.search(r"(?:投票?给?|杀|刀|击杀)\s*(\d+)\s*号", thought)
    if m and action in ("vote", "kill") and target is not None:
        if int(m.group(1)) != target:
            return True
    return False


def per_game_by_identity(game_dir, group):
    """返回该局 {identity: {metric: value}}，缺数据返回 None。"""
    glog = os.path.join(game_dir, "game_log.jsonl")
    rep = os.path.join(game_dir, "replay.json")
    if not os.path.exists(glog):
        return None, None
    roles = {}
    winner = None
    if os.path.exists(rep):
        rj = json.load(open(rep, encoding="utf-8"))
        roles = {int(s): p["role"] for s, p in rj.get("players", {}).items()}
        winner = rj.get("winner")
    records = [json.loads(l) for l in open(glog, encoding="utf-8") if l.strip()]
    ar = agent_responses(records)
    if not ar:
        return None, winner
    gid = ar[0].get("game_id")

    # 信任入度：每个目标座位收到的正向入边数
    mat = build_trust_matrix(ar, gid)
    in_pos = defaultdict(int)
    for frm, tos in mat.items():
        for to, w in tos.items():
            if w > 0:
                in_pos[to] += 1

    # 怀疑目标序列（改变率）：按 seat
    focus_seq = defaultdict(list)
    for r in ar:
        seat, role = r.get("seat"), r.get("role")
        resp = r["response"]
        action, target = resp.get("action"), resp.get("target")
        speech = resp.get("speech", "") or ""
        focus = None
        if action == "vote" and target is not None and target != seat:
            focus = target
        else:
            mentioned = [s for s in extract_seats(speech) if s != seat]
            if mentioned and any(kw in speech for kw in DISTRUST_KW):
                focus = mentioned[0]
        if focus is not None and seat:
            focus_seq[seat].append((r.get("ts", 0), focus))

    # 逐 seat 聚合到 identity
    by_seat_decep = defaultdict(lambda: [0, 0])  # seat -> [欺骗数, 总行动]
    by_seat_conf = defaultdict(list)
    seat_role = {}
    for r in ar:
        seat, role = r.get("seat"), r.get("role")
        if not (seat and role):
            continue
        seat_role[seat] = role
        by_seat_decep[seat][1] += 1
        if is_deception(r, roles):
            by_seat_decep[seat][0] += 1
        c = r["response"].get("confidence")
        if c is not None:
            by_seat_conf[seat].append(c)

    # 汇到 identity（对该局内同身份的 seat 取均值）
    acc = defaultdict(lambda: defaultdict(list))
    for seat, role in seat_role.items():
        ident = identity_of(role)
        if not ident:
            continue
        dec, tot = by_seat_decep[seat]
        acc[ident]["deception_freq"].append(dec / tot if tot else 0.0)
        acc[ident]["trust_density"].append(in_pos.get(seat, 0))
        if by_seat_conf[seat]:
            acc[ident]["avg_confidence"].append(float(np.mean(by_seat_conf[seat])))
        seq = sorted(focus_seq.get(seat, []))
        if len(seq) >= 2:
            ch = sum(1 for i in range(1, len(seq)) if seq[i][1] != seq[i - 1][1])
            acc[ident]["evolution_speed"].append(ch / (len(seq) - 1))

    out = {ident: {k: float(np.mean(v)) for k, v in km.items() if v}
           for ident, km in acc.items()}
    return out, winner


def aggregate_group(group, d):
    """跨 30 局聚合 -> {identity: {metric: 均值}} + 阵营胜率。"""
    per = defaultdict(lambda: defaultdict(list))
    wolf_win = good_win = ngames = 0
    for sd in sorted(glob.glob(os.path.join(ROOT, d, "*"))):
        if not os.path.isdir(sd):
            continue
        res, winner = per_game_by_identity(sd, group)
        if res is None:
            continue
        ngames += 1
        if winner == "werewolf":
            wolf_win += 1
        elif winner in ("god", "good", "villager"):
            good_win += 1
        for ident, md in res.items():
            for k, v in md.items():
                per[ident][k].append(v)
    avg = {ident: {k: float(np.mean(v)) for k, v in km.items()}
           for ident, km in per.items()}
    return avg, wolf_win, good_win, ngames


METRICS = [("deception_freq", "欺骗频次"), ("trust_density", "被信任入度"),
           ("avg_confidence", "平均置信度"), ("evolution_speed", "怀疑改变率")]


def main():
    data = {}
    win = {}
    for group, d in GROUPS:
        avg, ww, gw, n = aggregate_group(group, d)
        data[group] = avg
        win[group] = (ww, gw, n)

    L = []
    def p(s=""): print(s); L.append(s)

    p("=" * 74)
    p("按身份分表：狼人 / 平民 / 神职（每组30局，跨局均值）")
    p("=" * 74)
    p("三组配置： 正常(狼神=pro,村民=flash)  反转(狼神=flash,村民=pro)  全弱(全flash)")

    identities = [
        ("狼人", "werewolf", "狼人阵营"),
        ("平民", "villager", "好人阵营"),
        ("神职", "god", "好人阵营"),
    ]
    for ident, _role, faction in identities:
        p("\n" + "─" * 74)
        p(f"【表 · {ident}】（该身份所用模型 + 4 指标 + 所属阵营胜率）")
        p("─" * 74)
        hdr = f"  {'配置':6}{'模型':14}" + "".join(f"{lab:>12}" for _, lab in METRICS) + f"{'阵营胜率':>12}"
        p(hdr)
        for group, _d in GROUPS:
            role_for_model = "werewolf" if ident == "狼人" else ("villager" if ident == "平民" else "seer")
            mdl = model_of(group, role_for_model).replace("deepseek-v4-", "")
            md = data[group].get(ident, {})
            ww, gw, n = win[group]
            wr = (ww / n) if ident == "狼人" else (gw / n)
            cells = "".join(f"{md.get(k, float('nan')):>12.4f}" for k, _ in METRICS)
            p(f"  {group:6}{mdl:14}{cells}{wr:>11.1%}")

        # 该身份的“模型代差”：pro 配置值 − flash 配置值（仅狼/神/平民各自能比时）
        if ident in ("狼人", "神职"):
            pro_v = data["正常"].get(ident, {})    # 正常组该身份=pro
            fla_v = data["反转"].get(ident, {})    # 反转组该身份=flash
            tag = "正常(pro)−反转(flash)"
        else:  # 平民
            pro_v = data["反转"].get(ident, {})    # 反转组平民=pro
            fla_v = data["正常"].get(ident, {})    # 正常组平民=flash
            tag = "反转(pro)−正常(flash)"
        deltas = "  ".join(
            f"{lab}{pro_v[k]-fla_v[k]:+.4f}"
            for k, lab in METRICS if k in pro_v and k in fla_v
        )
        p(f"  ▶ 模型代差 Δ(pro−flash, {tag}): {deltas}")

    with open(os.path.join(ROOT, "by_identity_report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    p(f"\n✅ 已写入 {ROOT}\\by_identity_report.txt")


if __name__ == "__main__":
    main()
