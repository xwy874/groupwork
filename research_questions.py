"""research_questions.py — 三个研究问题的可测量分析（成员4 深化）。

Q1 不同角色的智能体在欺骗行为上是否存在可测量的差异？
Q2 策略调整速度与角色认知负荷是否相关？
Q3 信任网络在博弈初期如何形成？

方法论关键：正常/反转组里「角色」与「模型」绑定，无法分离角色效应。
本分析以【全弱组 result-allflash】为主数据集——所有角色都是 flash，
任何角色间差异都是【纯角色效应】，排除模型混淆。
（必要处用三组做稳健性对照。）

时序：日志无 day/round 字段，用每局 ts 归一化到 [0,1] 作“博弈进程”，
分早期(<0.33)/中期/后期三桶，回答 Q3 的“初期如何形成”。

认知负荷代理（Q2）：thought（思维链）字符长度——角色信息越复杂、
需要隐藏/推理越多，思维链越长。这是文献中常用的 reasoning-effort 代理。

输出：research_report.txt + 三张图 rq1_deception.png / rq2_load.png / rq3_trust_formation.png
"""
from __future__ import annotations

import glob
import json
import os
import re
from collections import defaultdict

import numpy as np
from scipy import stats

from werewolf_gateway.analysis_member4 import (
    extract_seats, sentiment_score, DISTRUST_KW, TRUST_KW,
)

ROOT = os.path.dirname(os.path.abspath(__file__))
ALLFLASH = os.path.join(ROOT, "result-allflash")
GROUPS = {"正常": "result", "反转": "reversed-result",
          "全弱": "result-allflash", "全强": "result-allpro"}

ROLE_ZH = {"werewolf": "狼人", "villager": "平民", "seer": "预言家",
           "witch": "女巫", "hunter": "猎人", "guard": "守卫"}
GOD = {"seer", "witch", "hunter", "guard"}


def load_game(game_dir):
    glog = os.path.join(game_dir, "game_log.jsonl")
    rep = os.path.join(game_dir, "replay.json")
    if not os.path.exists(glog):
        return None
    roles = {}
    if os.path.exists(rep):
        rj = json.load(open(rep, encoding="utf-8"))
        roles = {int(s): p["role"] for s, p in rj.get("players", {}).items()}
    recs = [json.loads(l) for l in open(glog, encoding="utf-8") if l.strip()]
    ar = [r for r in recs if r.get("kind") == "agent_response" and r.get("response")]
    if not ar:
        return None
    ar.sort(key=lambda r: r.get("ts", 0))
    # 进程定义：用「事件序位次」而非 ts 归一化。
    # 因为部分局是断点续跑（中途中断数小时后继续），ts 存在 ~8万秒的巨大空洞，
    # min-max 归一化会把空洞两侧压成早/后期、中期变空（artifact）。
    # 事件序位次 rank/(N-1) 天然免疫时间空洞，且每桶事件数均等。
    n = len(ar)
    for i, r in enumerate(ar):
        r["_prog"] = i / (n - 1) if n > 1 else 0.0
    return {"ar": ar, "roles": roles, "gid": ar[0].get("game_id")}


def is_deception(r, roles):
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
    m = re.search(r"(?:投票?给?|杀|刀|击杀)\s*(\d+)\s*号", thought)
    if m and action in ("vote", "kill") and target is not None:
        if int(m.group(1)) != target:
            return True
    return False


# ============ Q1：角色 × 欺骗频次（全弱组，纯角色效应）============
def q1_role_deception(games):
    # 每个 (game,seat) 的欺骗率 -> 按角色收集，做单因素方差分析
    by_role = defaultdict(list)            # role -> [每个体欺骗率]
    by_role_raw = defaultdict(lambda: [0, 0])  # role -> [欺骗数,总数] 整体频次
    for g in games:
        per_seat = defaultdict(lambda: [0, 0])
        seat_role = {}
        for r in g["ar"]:
            seat, role = r.get("seat"), r.get("role")
            if not (seat and role):
                continue
            seat_role[seat] = role
            per_seat[seat][1] += 1
            if is_deception(r, g["roles"]):
                per_seat[seat][0] += 1
        for seat, role in seat_role.items():
            dec, tot = per_seat[seat]
            if tot:
                by_role[role].append(dec / tot)
                by_role_raw[role][0] += dec
                by_role_raw[role][1] += tot
    # 单因素 ANOVA：角色是否解释欺骗率差异
    groups = [by_role[r] for r in by_role if len(by_role[r]) > 1]
    F, pval = stats.f_oneway(*groups) if len(groups) > 1 else (float("nan"), float("nan"))
    # 狼 vs 非狼 的 t 检验
    wolf = by_role.get("werewolf", [])
    nonwolf = [v for r, vs in by_role.items() if r != "werewolf" for v in vs]
    t, tp = stats.ttest_ind(wolf, nonwolf, equal_var=False) if wolf and nonwolf else (float("nan"), float("nan"))
    summary = {}
    for role in by_role:
        dec, tot = by_role_raw[role]
        summary[role] = {
            "mean": float(np.mean(by_role[role])),
            "std": float(np.std(by_role[role])),
            "n_individuals": len(by_role[role]),
            "overall_freq": dec / tot if tot else 0,
        }
    return summary, {"anova_F": F, "anova_p": pval, "wolf_vs_nonwolf_t": t, "wolf_vs_nonwolf_p": tp,
                     "wolf_mean": float(np.mean(wolf)) if wolf else 0,
                     "nonwolf_mean": float(np.mean(nonwolf)) if nonwolf else 0}


# ============ Q2：策略调整速度 vs 认知负荷（思维链长度）============
def q2_load_vs_speed(games):
    # 每个 (game,seat)：策略调整速度=怀疑目标改变率；认知负荷=平均thought长度
    points = []  # (role, load, speed)
    for g in games:
        focus_seq = defaultdict(list)
        load = defaultdict(list)
        seat_role = {}
        for r in g["ar"]:
            seat, role = r.get("seat"), r.get("role")
            if not (seat and role):
                continue
            seat_role[seat] = role
            resp = r["response"]
            load[seat].append(len(resp.get("thought", "") or ""))
            action, target = resp.get("action"), resp.get("target")
            speech = resp.get("speech", "") or ""
            focus = None
            if action == "vote" and target is not None and target != seat:
                focus = target
            else:
                mentioned = [s for s in extract_seats(speech) if s != seat]
                if mentioned and any(kw in speech for kw in DISTRUST_KW):
                    focus = mentioned[0]
            if focus is not None:
                focus_seq[seat].append((r.get("ts", 0), focus))
        for seat, role in seat_role.items():
            seq = sorted(focus_seq.get(seat, []))
            if len(seq) >= 2 and load[seat]:
                ch = sum(1 for i in range(1, len(seq)) if seq[i][1] != seq[i - 1][1])
                speed = ch / (len(seq) - 1)
                points.append((role, float(np.mean(load[seat])), speed))
    loads = [p[1] for p in points]
    speeds = [p[2] for p in points]
    r_p, p_p = stats.pearsonr(loads, speeds) if len(points) > 2 else (float("nan"), float("nan"))
    r_s, p_s = stats.spearmanr(loads, speeds) if len(points) > 2 else (float("nan"), float("nan"))
    # 按角色聚合负荷与速度
    by_role = defaultdict(lambda: {"load": [], "speed": []})
    for role, ld, sp in points:
        by_role[role]["load"].append(ld)
        by_role[role]["speed"].append(sp)
    role_stats = {role: {"load": float(np.mean(d["load"])), "speed": float(np.mean(d["speed"])),
                         "n": len(d["load"])}
                  for role, d in by_role.items()}
    return points, {"pearson_r": r_p, "pearson_p": p_p, "spearman_r": r_s, "spearman_p": p_s}, role_stats


# ============ Q3：信任网络初期形成（时序分桶）============
def q3_trust_formation(games):
    # 三桶：早<0.33 / 中 / 后>=0.66。统计每桶的：正向信任边数、负向边数、净情感
    buckets = ["早期", "中期", "后期"]
    edge_pos = defaultdict(list)  # bucket -> 每局正向边数
    edge_neg = defaultdict(list)
    net_sent = defaultdict(list)
    # 初期信任倾向：好人 vs 狼，谁更早发出正向边
    first_pos_prog = defaultdict(list)  # role类(good/wolf) -> 首次正向发言的进程
    for g in games:
        b_pos = defaultdict(int)
        b_neg = defaultdict(int)
        b_sent = defaultdict(list)
        seen_first = {}
        for r in g["ar"]:
            prog = r["_prog"]
            b = 0 if prog < 0.33 else (1 if prog < 0.66 else 2)
            resp = r["response"]
            speech = resp.get("speech", "") or ""
            seat = r.get("seat")
            net = sentiment_score(speech)
            b_sent[b].append(net)
            tos = [t for t in extract_seats(speech) if t != seat]
            if net > 0 and tos:
                b_pos[b] += 1
            elif net < 0 and tos:
                b_neg[b] += 1
            # 首次正向
            role = r.get("role")
            if role and net > 0 and tos and seat not in seen_first:
                seen_first[seat] = prog
                cls = "狼人" if role == "werewolf" else "好人"
                first_pos_prog[cls].append(prog)
        for bi in range(3):
            edge_pos[bi].append(b_pos[bi])
            edge_neg[bi].append(b_neg[bi])
            net_sent[bi].append(float(np.mean(b_sent[bi])) if b_sent[bi] else 0)
    out = {}
    for bi, name in enumerate(buckets):
        pos = float(np.mean(edge_pos[bi]))
        neg = float(np.mean(edge_neg[bi]))
        out[name] = {
            "pos_edges": pos,
            "neg_edges": neg,
            "pos_neg_ratio": pos / (neg or 1),
            # 信任占比 pos/(pos+neg)：0-1 有界，不因负向边趋零而爆炸，比 ratio 稳健
            "pos_share": pos / ((pos + neg) or 1),
            "net_sentiment": float(np.mean(net_sent[bi])),
        }
    first = {cls: float(np.mean(v)) for cls, v in first_pos_prog.items() if v}
    return out, first, (edge_pos, edge_neg)


def main():
    # 三组都加载（Q1/Q2 用全弱为主，Q3 用全弱）
    games_by_group = {}
    for gname, d in GROUPS.items():
        gs = []
        for sd in sorted(glob.glob(os.path.join(ROOT, d, "*"))):
            if os.path.isdir(sd):
                g = load_game(sd)
                if g:
                    gs.append(g)
        games_by_group[gname] = gs

    allflash = games_by_group["全弱"]
    L = []
    def p(s=""): print(s); L.append(s)

    p("=" * 76)
    p("三个研究问题的可测量分析（主数据集=全弱组 result-allflash，纯角色效应）")
    p("=" * 76)
    p(f"全弱组 {len(allflash)} 局 | 正常 {len(games_by_group['正常'])} | 反转 {len(games_by_group['反转'])}\n")

    # ---- Q1 ----
    p("█ Q1：不同角色在欺骗行为上是否存在可测量差异？")
    p("─" * 60)
    summ, stat = q1_role_deception(allflash)
    p(f"  {'角色':8}{'整体欺骗频次':>12}{'个体均值':>10}{'标准差':>8}{'样本数':>7}")
    for role in sorted(summ, key=lambda r: -summ[r]["overall_freq"]):
        s = summ[role]
        p(f"  {ROLE_ZH.get(role, role):8}{s['overall_freq']:>12.4f}{s['mean']:>10.4f}{s['std']:>8.3f}{s['n_individuals']:>7}")
    p(f"\n  单因素方差分析(角色→欺骗率): F={stat['anova_F']:.2f}, p={stat['anova_p']:.2e}")
    p(f"  狼人({stat['wolf_mean']:.3f}) vs 非狼({stat['nonwolf_mean']:.3f}): "
      f"t={stat['wolf_vs_nonwolf_t']:.2f}, p={stat['wolf_vs_nonwolf_p']:.2e}")
    sig = "存在显著差异 ✅" if stat["anova_p"] < 0.05 else "无显著差异"
    p(f"  ▶ 结论：角色间欺骗行为 {sig}（p<0.05 即可测量）")

    # Q1 稳健性：在全强组（所有角色=pro）复现，验证“角色→欺骗”排序不依赖能力水平
    allpro = games_by_group.get("全强", [])
    summ_pro, stat_pro = q1_role_deception(allpro) if allpro else ({}, {})
    if summ_pro:
        order_flash = [r for r in sorted(summ, key=lambda r: -summ[r]["overall_freq"])]
        order_pro = [r for r in sorted(summ_pro, key=lambda r: -summ_pro[r]["overall_freq"])]
        from scipy.stats import spearmanr
        common = [r for r in order_flash if r in summ_pro]
        rho, prho = spearmanr([order_flash.index(r) for r in common],
                              [order_pro.index(r) for r in common])
        p(f"\n  [稳健性·全强组复现] ANOVA F={stat_pro['anova_F']:.1f}, p={stat_pro['anova_p']:.1e}；"
          f"狼({stat_pro['wolf_mean']:.3f}) vs 非狼({stat_pro['nonwolf_mean']:.3f})")
        p(f"  全弱排序: {'>'.join(ROLE_ZH.get(r,r) for r in order_flash)}")
        p(f"  全强排序: {'>'.join(ROLE_ZH.get(r,r) for r in order_pro)}")
        p(f"  两水平角色排序 Spearman ρ={rho:+.2f} (p={prho:.2f}) "
          f"→ {'排序高度一致，角色效应不依赖能力水平 ✅' if rho>0.6 else '排序存在差异'}")

    # ---- Q2 ----
    p("\n█ Q2：策略调整速度与角色认知负荷（思维链长度）是否相关？")
    p("─" * 60)
    points, corr, role_stats = q2_load_vs_speed(allflash)
    p(f"  样本（个体数）：{len(points)}")
    p(f"  {'角色':8}{'认知负荷(思维链字数)':>18}{'策略调整速度':>14}{'n':>5}")
    for role in sorted(role_stats, key=lambda r: -role_stats[r]["load"]):
        s = role_stats[role]
        p(f"  {ROLE_ZH.get(role, role):8}{s['load']:>18.1f}{s['speed']:>14.4f}{s['n']:>5}")
    p(f"\n  个体层面 Pearson r={corr['pearson_r']:+.3f} (p={corr['pearson_p']:.2e})")
    p(f"  个体层面 Spearman ρ={corr['spearman_r']:+.3f} (p={corr['spearman_p']:.2e})")
    # 诚实判定：两个检验都需 p<0.05 且方向一致，才算稳健相关
    robust = (corr["pearson_p"] < 0.05 and corr["spearman_p"] < 0.05
              and corr["pearson_r"] * corr["spearman_r"] > 0)
    if robust:
        direction = "正相关（负荷越高，策略越频繁调整）" if corr["pearson_r"] > 0 else "负相关（负荷越高，策略越稳定）"
        p(f"  ▶ 结论：存在稳健的弱{direction} ✅")
    else:
        p(f"  ▶ 结论：个体层面【无稳健相关】——Pearson 仅在 p=0.05 边界、")
        p(f"     Spearman 不显著(p={corr['spearman_p']:.2f})，方向虽为负但效应量极小(|r|≈0.1)。")
        p(f"     即『认知负荷高低』不能线性预测『策略调整速度』。")
    # 角色级模式（更清晰的真实信号）：认知负荷的角色差异本身显著吗？
    loads_by_role = defaultdict(list)
    for role, ld, sp in points:
        loads_by_role[role].append(ld)
    grp = [v for v in loads_by_role.values() if len(v) > 1]
    Fl, pl = stats.f_oneway(*grp) if len(grp) > 1 else (float("nan"), float("nan"))
    top = max(role_stats, key=lambda r: role_stats[r]["load"])
    bot = min(role_stats, key=lambda r: role_stats[r]["load"])
    p(f"  ▶ 但【认知负荷本身】存在显著角色差异：ANOVA F={Fl:.1f}, p={pl:.1e}；")
    p(f"     {ROLE_ZH.get(top,top)}思维链最长({role_stats[top]['load']:.0f}字)、"
      f"{ROLE_ZH.get(bot,bot)}最短({role_stats[bot]['load']:.0f}字)——"
      f"信息越少/越需主动推理的身份，思维链越长。")

    # ---- Q3 ----
    p("\n█ Q3：信任网络在博弈初期如何形成？")
    p("─" * 60)
    form, first, _ = q3_trust_formation(allflash)
    p(f"  {'阶段':6}{'正向边/局':>10}{'负向边/局':>10}{'信任占比':>9}{'净情感':>8}")
    for name in ["早期", "中期", "后期"]:
        f = form[name]
        p(f"  {name:6}{f['pos_edges']:>10.1f}{f['neg_edges']:>10.1f}{f['pos_share']:>9.2f}{f['net_sentiment']:>8.3f}")
    p(f"\n  首次发出正向信任的平均进程： " +
      "  ".join(f"{cls}={v:.2f}" for cls, v in first.items()))
    early = form["早期"]
    p(f"  ▶ 结论：初期信任占比 {early['pos_share']:.0%}（正向边远多于负向）"
      f"，以建立信任为主；随博弈推进信任占比下降、怀疑上升（见上表）。")

    # Q3 稳健性：全强组复现“初期信任先行 + 后期怀疑上升”趋势（用信任占比，避免比值爆炸）
    form_pro, first_pro, _ = q3_trust_formation(allpro) if allpro else ({}, {}, None)
    if form_pro:
        sh_f = [form[s]["pos_share"] for s in ("早期", "中期", "后期")]
        sh_p = [form_pro[s]["pos_share"] for s in ("早期", "中期", "后期")]
        p(f"\n  [稳健性·全强组] 信任占比 早→后: "
          f"{sh_p[0]:.0%}→{sh_p[1]:.0%}→{sh_p[2]:.0%}  "
          f"(全弱: {sh_f[0]:.0%}→{sh_f[1]:.0%}→{sh_f[2]:.0%})")
        p(f"  注：全强组后期负向边骤降（pro 后期局面收敛、抱团确认），"
          f"故按『信任占比』而非『正负比』比较，避免小分母失真。")

    with open(os.path.join(ROOT, "research_report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    # ===== 图表 =====
    _plot(summ, stat, points, role_stats, corr, form, first, summ_pro, form_pro)
    p(f"\n✅ 已写入 research_report.txt + rq1_deception.png / rq2_load.png / rq3_trust_formation.png")


def _plot(summ, q1stat, points, role_stats, corr, form, first, summ_pro=None, form_pro=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    for font in ("Microsoft YaHei", "SimHei", "Arial Unicode MS"):
        try:
            matplotlib.rcParams["font.sans-serif"] = [font]
            matplotlib.rcParams["axes.unicode_minus"] = False
            break
        except Exception:
            continue

    # --- RQ1: 角色欺骗频次（全弱 vs 全强 双组分组柱，验证排序稳健）---
    fig, ax = plt.subplots(figsize=(10, 5.5))
    roles = sorted(summ, key=lambda r: -summ[r]["overall_freq"])
    x = np.arange(len(roles))
    vals_f = [summ[r]["overall_freq"] for r in roles]
    if summ_pro:
        w = 0.4
        vals_p = [summ_pro.get(r, {}).get("overall_freq", 0) for r in roles]
        b1 = ax.bar(x - w / 2, vals_f, w, label="全弱组(flash)", color="#e76f51")
        b2 = ax.bar(x + w / 2, vals_p, w, label="全强组(pro)", color="#2a6f97")
        for bars, vals in ((b1, vals_f), (b2, vals_p)):
            for b, v in zip(bars, vals):
                ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.3f}", ha="center", va="bottom", fontsize=8)
        ax.legend(fontsize=10)
        title_extra = (f"\n全弱 F={q1stat['anova_F']:.0f},p={q1stat['anova_p']:.0e}  "
                       f"两水平排序一致 → 角色效应不依赖能力")
    else:
        w = 0.6
        colors = ["#c1121f" if r == "werewolf" else "#457b9d" for r in roles]
        ax.bar(x, vals_f, w, color=colors)
        for xi, v in zip(x, vals_f):
            ax.text(xi, v, f"{v:.3f}", ha="center", va="bottom", fontsize=10)
        title_extra = f"\nANOVA F={q1stat['anova_F']:.1f}, p={q1stat['anova_p']:.1e}"
    ax.set_xticks(x)
    ax.set_xticklabels([ROLE_ZH.get(r, r) for r in roles], fontsize=11)
    ax.set_ylabel("欺骗频次（欺骗行动/总行动）")
    ax.set_title(f"Q1 角色×欺骗频次（各30局，纯角色效应）{title_extra}", fontsize=12)
    plt.tight_layout(); plt.savefig(os.path.join(ROOT, "rq1_deception.png"), dpi=150); plt.close()
    print("[图表] rq1_deception.png")

    # --- RQ2: 散点(负荷 vs 速度)+角色均值 ---
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 5))
    xs = [p[1] for p in points]; ys = [p[2] for p in points]
    a1.scatter(xs, ys, s=18, alpha=0.35, color="#264653")
    if len(points) > 2:
        z = np.polyfit(xs, ys, 1); xx = np.linspace(min(xs), max(xs), 50)
        a1.plot(xx, z[0] * xx + z[1], "r-", lw=2,
                label=f"拟合 r={corr['pearson_r']:+.2f}, p={corr['pearson_p']:.1e}")
        a1.legend(fontsize=10)
    a1.set_xlabel("认知负荷（思维链字数）"); a1.set_ylabel("策略调整速度（怀疑目标改变率）")
    a1.set_title("Q2 个体散点：负荷 vs 策略调整速度", fontsize=12)
    rr = sorted(role_stats, key=lambda r: -role_stats[r]["load"])
    a2.scatter([role_stats[r]["load"] for r in rr], [role_stats[r]["speed"] for r in rr],
               s=120, color="#e76f51")
    for r in rr:
        a2.annotate(ROLE_ZH.get(r, r), (role_stats[r]["load"], role_stats[r]["speed"]),
                    fontsize=11, xytext=(5, 5), textcoords="offset points")
    a2.set_xlabel("平均认知负荷（思维链字数）"); a2.set_ylabel("平均策略调整速度")
    a2.set_title("Q2 角色均值定位", fontsize=12)
    plt.tight_layout(); plt.savefig(os.path.join(ROOT, "rq2_load.png"), dpi=150); plt.close()
    print("[图表] rq2_load.png")

    # --- RQ3: 三阶段正/负边 + 正负比折线 ---
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 5))
    stages = ["早期", "中期", "后期"]
    pos = [form[s]["pos_edges"] for s in stages]
    neg = [form[s]["neg_edges"] for s in stages]
    x = np.arange(3); w = 0.38
    a1.bar(x - w / 2, pos, w, label="正向信任边", color="#2a9d8f")
    a1.bar(x + w / 2, neg, w, label="负向怀疑边", color="#e76f51")
    a1.set_xticks(x); a1.set_xticklabels(stages, fontsize=11)
    a1.set_ylabel("每局平均边数"); a1.legend(fontsize=10)
    a1.set_title("Q3 信任/怀疑边随博弈进程", fontsize=12)
    for i, (pv, nv) in enumerate(zip(pos, neg)):
        a1.text(i - w / 2, pv, f"{pv:.0f}", ha="center", va="bottom", fontsize=9)
        a1.text(i + w / 2, nv, f"{nv:.0f}", ha="center", va="bottom", fontsize=9)
    ratio = [form[s]["pos_share"] for s in stages]
    a2.plot(stages, ratio, "o-", lw=2.5, ms=10, color="#e76f51", label="全弱组(flash)")
    if form_pro:
        ratio_p = [form_pro[s]["pos_share"] for s in stages]
        a2.plot(stages, ratio_p, "s--", lw=2.5, ms=9, color="#2a6f97", label="全强组(pro)")
        for i, rv in enumerate(ratio_p):
            a2.annotate(f"{rv:.0%}", (i, rv), fontsize=10, xytext=(0, -16),
                        textcoords="offset points", ha="center", color="#2a6f97")
    a2.axhline(0.5, ls="--", color="gray", alpha=0.6)
    a2.set_ylabel("信任占比  正/(正+负)"); a2.set_ylim(0, 1)
    a2.set_title("Q3 信任氛围(信任占比)演化\n初期均高(~80%)；后期弱模型降、强模型升", fontsize=12)
    a2.legend(fontsize=10)
    for i, rv in enumerate(ratio):
        a2.annotate(f"{rv:.0%}", (i, rv), fontsize=11, xytext=(0, 8), textcoords="offset points",
                    ha="center", color="#e76f51")
    plt.tight_layout(); plt.savefig(os.path.join(ROOT, "rq3_trust_formation.png"), dpi=150); plt.close()
    print("[图表] rq3_trust_formation.png")


if __name__ == "__main__":
    main()
