"""plot_by_identity.py — 按身份（狼人/平民/神职）各出 1 张图，共 3 张。

指标固定为三项（去掉平均置信度）：欺骗频次、被信任入度、狼队/好人阵营胜率。
数据口径完全复用 by_identity_tables.aggregate_group（回到每局原始日志按身份聚合），
保证与文本表 by_identity_report.txt 完全一致。

每张图 = 1 个身份，1×3 子图（欺骗频次 / 被信任入度 / 阵营胜率），
每个子图三根柱 = 三组配置（正常 / 反转 / 全弱），柱色按该身份所用模型：
  pro=蓝、flash=橙。柱顶标注真实值。

输出： by_identity_狼人.png / by_identity_平民.png / by_identity_神职.png
"""
from __future__ import annotations

import os

from by_identity_tables import GROUPS, aggregate_group, model_of, PRO, FLASH

ROOT = os.path.dirname(os.path.abspath(__file__))
PRO_C, FLASH_C = "#2a6f97", "#e76f51"


def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    for font in ("Microsoft YaHei", "SimHei", "Arial Unicode MS"):
        try:
            matplotlib.rcParams["font.sans-serif"] = [font]
            matplotlib.rcParams["axes.unicode_minus"] = False
            break
        except Exception:
            continue

    # 聚合三组
    data, win = {}, {}
    for group, d in GROUPS:
        avg, ww, gw, n = aggregate_group(group, d)
        data[group] = avg
        win[group] = (ww, gw, n)

    identities = [
        ("狼人", "werewolf", True),   # True=用狼队胜率
        ("平民", "villager", False),
        ("神职", "seer", False),
    ]
    group_names = [g for g, _ in GROUPS]   # 正常/反转/全弱

    for ident, role_for_model, use_wolf_wr in identities:
        # 每组该身份的值
        decep = [data[g].get(ident, {}).get("deception_freq", 0) for g in group_names]
        trust = [data[g].get(ident, {}).get("trust_density", 0) for g in group_names]
        wr = []
        for g in group_names:
            ww, gw, n = win[g]
            wr.append((ww / n if use_wolf_wr else gw / n) * 100)
        # 每组该身份所用模型 -> 颜色 + 标签
        models = [model_of(g, role_for_model) for g in group_names]
        colors = [PRO_C if m == PRO else FLASH_C for m in models]
        xticklabels = [f"{g}\n{m.replace('deepseek-v4-','')}" for g, m in zip(group_names, models)]

        fig, axes = plt.subplots(1, 3, figsize=(13, 4.6))
        specs = [
            (decep, "欺骗频次", False),
            (trust, "被信任入度", False),
            (wr, f"{'狼队' if use_wolf_wr else '好人阵营'}胜率 (%)", True),
        ]
        x = np.arange(len(group_names))
        for ax, (vals, title, is_pct) in zip(axes, specs):
            bars = ax.bar(x, vals, 0.6, color=colors)
            ax.set_xticks(x)
            ax.set_xticklabels(xticklabels, fontsize=9)
            ax.set_title(title, fontsize=12)
            top = max(vals) * 1.25 if max(vals) > 0 else 1
            ax.set_ylim(0, 100 if is_pct else top)
            for b, v in zip(bars, vals):
                txt = f"{v:.0f}%" if is_pct else f"{v:.3f}"
                ax.text(b.get_x() + b.get_width() / 2, v, txt,
                        ha="center", va="bottom", fontsize=10)
        # 图例：pro vs flash
        from matplotlib.patches import Patch
        fig.legend(handles=[Patch(color=PRO_C, label="pro"), Patch(color=FLASH_C, label="flash")],
                   loc="upper right", fontsize=10, ncol=2)
        fig.suptitle(f"身份「{ident}」跨三组对比：欺骗频次 / 被信任入度 / 胜率（各30局）",
                     fontsize=13)
        plt.tight_layout(rect=[0, 0, 1, 0.95])
        out = os.path.join(ROOT, f"by_identity_{ident}.png")
        plt.savefig(out, dpi=150)
        plt.close(fig)
        print(f"[图表] 已保存：{out}")


if __name__ == "__main__":
    main()
