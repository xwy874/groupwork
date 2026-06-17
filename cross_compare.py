"""cross_compare.py — 跨【四组】实验的总对比（成员1/4 收尾图表）。

四组各 30 局，唯一变量是「哪个模型扮演哪个身份」：
  - 正常(normal)    : 狼+神=pro,   村民=flash
  - 反转(reversed)  : 狼+神=flash, 村民=pro
  - 全弱(allflash)  : 全部=flash（无代差·弱基线）
  - 全强(allpro)    : 全部=pro  （无代差·强基线）

两条独立的论证线：

A. 胜率的 2×2 析因（狼方模型 × 好人方模型）——全强组是关键对照：
        好人=flash      好人=pro
   狼=flash   全弱76.7%     反转73.3%
   狼=pro     正常90.0%     全强73.3%
   → 全弱≈全强 证明“绝对能力不决定胜率”；只有正常(狼强好人弱)的代差能冲到90%。

B. 按身份×模型（控制身份，正常↔反转做差）量化模型代差对 欺骗/被信任 的净增量。

输出： cross_compare_report.txt + cross_compare.png（① 2×2胜率 ② 欺骗 ③ 被信任）
用法： python cross_compare.py
"""
from __future__ import annotations

import json
import os

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
PRO, FLASH = "deepseek-v4-pro", "deepseek-v4-flash"

GROUPS = {
    "normal":   {"dir": "result",          "wolf": PRO,   "good": FLASH},
    "reversed": {"dir": "reversed-result", "wolf": FLASH, "good": PRO},
    "allflash": {"dir": "result-allflash", "wolf": FLASH, "good": FLASH},
    "allpro":   {"dir": "result-allpro",   "wolf": PRO,   "good": PRO},
}


def load_group(d):
    j = json.load(open(os.path.join(ROOT, d, "aggregate_metrics.json"), encoding="utf-8"))
    wc = j.get("win_counts", {})
    total = sum(wc.values()) or 1
    return {
        "n": j.get("n_games_found", 0),
        "wolf_winrate": wc.get("werewolf", 0) / total,
        "god_winrate": wc.get("god", 0) / total,
        "avg": j.get("avg_metrics", {}),
    }


def cell(g, group, model, key):
    return (g[group]["avg"].get(model) or {}).get(key)


def build():
    g = {name: load_group(cfg["dir"]) for name, cfg in GROUPS.items()}

    # ---- A. 2×2 析因胜率（狼方 × 好人方）----
    matrix = {
        ("flash", "flash"): ("全弱", g["allflash"]["wolf_winrate"]),
        ("flash", "pro"):   ("反转", g["reversed"]["wolf_winrate"]),
        ("pro", "flash"):   ("正常", g["normal"]["wolf_winrate"]),
        ("pro", "pro"):     ("全强", g["allpro"]["wolf_winrate"]),
    }

    # ---- B. 身份×模型（正常↔反转，控制身份比模型）----
    keys3 = ("deception_freq", "trust_density", "avg_confidence")
    table = {
        "狼+神 身份": {
            "pro (正常)":   {k: cell(g, "normal", PRO, k) for k in keys3},
            "flash (反转)": {k: cell(g, "reversed", FLASH, k) for k in keys3},
        },
        "村民 身份": {
            "pro (反转)":   {k: cell(g, "reversed", PRO, k) for k in keys3},
            "flash (正常)": {k: cell(g, "normal", FLASH, k) for k in keys3},
        },
    }
    delta = {}
    for ident, cells in table.items():
        prok = next(k for k in cells if k.startswith("pro"))
        flak = next(k for k in cells if k.startswith("flash"))
        delta[ident] = {k: cells[prok][k] - cells[flak][k]
                        for k in cells[prok] if cells[prok][k] is not None and cells[flak][k] is not None}

    # 胜率的“代差效应” vs “绝对能力效应”
    gap_effect = g["normal"]["wolf_winrate"] - np.mean(
        [g["allflash"]["wolf_winrate"], g["allpro"]["wolf_winrate"]])
    ability_effect = g["allpro"]["wolf_winrate"] - g["allflash"]["wolf_winrate"]

    return g, matrix, table, delta, gap_effect, ability_effect


def report(g, matrix, table, delta, gap_effect, ability_effect):
    L = []
    def p(s=""): print(s); L.append(s)

    p("=" * 72)
    p("跨四组总对比：代差效应 vs 绝对能力效应（每组30局）")
    p("=" * 72)
    p("正常(狼pro/民flash) 反转(狼flash/民pro) 全弱(全flash) 全强(全pro)\n")

    p("--- A. 狼队胜率 2×2 析因（狼方模型 × 好人方模型）---")
    p(f"  {'':12}{'好人=flash':>14}{'好人=pro':>14}")
    for wolf in ("flash", "pro"):
        row = f"  狼={wolf:<8}"
        for good in ("flash", "pro"):
            name, wr = matrix[(wolf, good)]
            row += f"{name+f'{wr:.0%}':>14}"
        p(row)
    p(f"\n  ▶ 绝对能力效应 = 全强({g['allpro']['wolf_winrate']:.1%}) − 全弱({g['allflash']['wolf_winrate']:.1%}) "
      f"= {ability_effect:+.1%}  （≈0 → 同时变强/变弱，狼队优势不变）")
    p(f"  ▶ 代差效应     = 正常({g['normal']['wolf_winrate']:.1%}) − 无代差均值"
      f"({np.mean([g['allflash']['wolf_winrate'], g['allpro']['wolf_winrate']]):.1%}) "
      f"= {gap_effect:+.1%}  （狼比好人强才显著拉高胜率）")

    labels = {"deception_freq": "欺骗频次", "trust_density": "被信任入度", "avg_confidence": "平均置信度"}
    p("\n--- B. 按身份×模型（控制身份，对比模型，正常↔反转）---")
    for ident, cells in table.items():
        p(f"  【{ident}】")
        cols = list(cells.keys())
        p(f"     {'指标':12}{cols[0]:>14}{cols[1]:>14}{'Δ(pro−flash)':>16}")
        for mk, lab in labels.items():
            v0, v1 = cells[cols[0]].get(mk), cells[cols[1]].get(mk)
            dv = delta[ident].get(mk)
            s0 = f"{v0:.4f}" if v0 is not None else "—"
            s1 = f"{v1:.4f}" if v1 is not None else "—"
            sd = f"{dv:+.4f}" if dv is not None else "—"
            p(f"     {lab:12}{s0:>14}{s1:>14}{sd:>16}")

    p("\n--- C. 全弱 vs 全强：无代差下两个能力水平的指标 ---")
    p(f"  {'指标':12}{'全弱(flash)':>14}{'全强(pro)':>14}")
    for mk, lab in labels.items():
        wv = cell(g, "allflash", FLASH, mk)
        pv = cell(g, "allpro", PRO, mk)
        sw = f"{wv:.4f}" if wv is not None else "—"
        sp = f"{pv:.4f}" if pv is not None else "—"
        p(f"  {lab:12}{sw:>14}{sp:>14}")
    return "\n".join(L)


def plot(g, matrix, table, out_path):
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

    fig, axes = plt.subplots(1, 3, figsize=(17, 5.2))
    PRO_C, FLASH_C = "#2a6f97", "#e76f51"

    # ① 2×2 胜率热力（狼方×好人方）
    ax = axes[0]
    grid = np.array([[matrix[("flash", "flash")][1], matrix[("flash", "pro")][1]],
                     [matrix[("pro", "flash")][1], matrix[("pro", "pro")][1]]]) * 100
    im = ax.imshow(grid, cmap="RdYlGn_r", vmin=60, vmax=95, aspect="auto")
    ax.set_xticks([0, 1]); ax.set_xticklabels(["好人=flash", "好人=pro"], fontsize=11)
    ax.set_yticks([0, 1]); ax.set_yticklabels(["狼=flash", "狼=pro"], fontsize=11)
    names = [["全弱", "反转"], ["正常", "全强"]]
    for i in range(2):
        for j in range(2):
            ax.text(j, i, f"{names[i][j]}\n{grid[i,j]:.0f}%", ha="center", va="center",
                    fontsize=14, fontweight="bold",
                    color="white" if grid[i, j] > 82 else "black")
    ax.set_title("① 狼队胜率 2×2 析因\n对角线(全弱≈全强)证明绝对能力无关", fontsize=11)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label="狼队胜率 %")

    # ②③ 欺骗/被信任：按身份 pro vs flash
    def grouped(ax, key, title):
        idents = list(table.keys())
        x = np.arange(len(idents)); w = 0.36
        pv, fv = [], []
        for ident in idents:
            cells = table[ident]
            prok = next(k for k in cells if k.startswith("pro"))
            flak = next(k for k in cells if k.startswith("flash"))
            pv.append(cells[prok].get(key) or 0)
            fv.append(cells[flak].get(key) or 0)
        b1 = ax.bar(x - w / 2, pv, w, label="pro", color=PRO_C)
        b2 = ax.bar(x + w / 2, fv, w, label="flash", color=FLASH_C)
        ax.set_xticks(x); ax.set_xticklabels(idents, fontsize=10)
        ax.set_title(title, fontsize=11); ax.legend(fontsize=9)
        for bars in (b1, b2):
            for b in bars:
                h = b.get_height()
                ax.text(b.get_x() + b.get_width() / 2, h, f"{h:.3f}", ha="center", va="bottom", fontsize=9)
        ax.set_ylim(0, max(pv + fv) * 1.25)

    grouped(axes[1], "deception_freq", "② 欺骗频次（控制身份：pro vs flash）")
    grouped(axes[2], "trust_density", "③ 被信任入度（控制身份：pro vs flash）")

    fig.suptitle("跨四组总对比：代差效应(胜率/欺骗) vs 绝对能力效应（各30局）", fontsize=14, y=1.02)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"[图表] 已保存：{out_path}")


def main():
    g, matrix, table, delta, ge, ae = build()
    txt = report(g, matrix, table, delta, ge, ae)
    with open(os.path.join(ROOT, "cross_compare_report.txt"), "w", encoding="utf-8") as f:
        f.write(txt)
    plot(g, matrix, table, os.path.join(ROOT, "cross_compare.png"))
    print(f"\n✅ 已写入 {ROOT}\\cross_compare_report.txt 与 cross_compare.png")


if __name__ == "__main__":
    main()
