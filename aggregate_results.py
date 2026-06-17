"""aggregate_results.py — 跨对局聚合多次实验的平均指标（成员1/4）。

扫描某个结果根目录（如 reversed-result）下的所有子文件夹（每个=一局），
读取每局的 metrics.json + replay.json，按模型对各指标取平均，并统计胜率，
输出：
  - 控制台 + <root>/aggregate_report.txt 的平均指标报告
  - <root>/aggregate_metrics.json 结构化结果（每局明细 + 平均）
  - <root>/aggregate_chart.png 三指标（欺骗/置信/信任）平均值对比图

用法：
  python aggregate_results.py reversed-result
  python aggregate_results.py reversed-result --subdirs 1-30
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict

import numpy as np

# 聚合这些“每模型”指标（数值型，跨局取均值）
MODEL_METRIC_KEYS = [
    "deception_freq", "evolution_speed", "trust_density",
    "avg_confidence", "calibration",
]


def _parse_subdirs(spec, root):
    """'1-30' / '1,3,5' / None(全部数字子目录) -> 排序后的子目录名列表。"""
    if spec:
        names = []
        for part in spec.split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-")
                names += [str(i) for i in range(int(a), int(b) + 1)]
            elif part:
                names.append(part)
        return names
    # 默认：root 下所有“纯数字”子目录
    names = [d for d in os.listdir(root)
             if os.path.isdir(os.path.join(root, d)) and d.isdigit()]
    return sorted(names, key=int)


def load_one(game_dir):
    """读取一局的 metrics + winner；缺文件返回 None。"""
    mpath = os.path.join(game_dir, "metrics.json")
    rpath = os.path.join(game_dir, "replay.json")
    if not os.path.exists(mpath):
        return None
    try:
        metrics = json.load(open(mpath, encoding="utf-8")).get("metrics", {})
    except (json.JSONDecodeError, OSError):
        return None
    winner = None
    if os.path.exists(rpath):
        try:
            winner = json.load(open(rpath, encoding="utf-8")).get("winner")
        except (json.JSONDecodeError, OSError):
            pass
    return {"metrics": metrics, "winner": winner}


def aggregate(root, subdirs):
    per_game = []          # [{"name","winner","metrics"}]
    # model -> metric_key -> [values across games]
    acc = defaultdict(lambda: defaultdict(list))
    win_counter = defaultdict(int)

    for name in subdirs:
        game_dir = os.path.join(root, name)
        one = load_one(game_dir)
        if one is None:
            continue
        per_game.append({"name": name, "winner": one["winner"], "metrics": one["metrics"]})
        if one["winner"]:
            win_counter[one["winner"]] += 1
        for model, md in one["metrics"].items():
            for k in MODEL_METRIC_KEYS:
                v = md.get(k)
                if isinstance(v, (int, float)):
                    acc[model][k].append(v)

    avg = {
        model: {k: (float(np.mean(vs)) if vs else None) for k, vs in km.items()}
        for model, km in acc.items()
    }
    # 同时记录每个均值的样本数（有些局某模型可能缺某指标）
    nobs = {
        model: {k: len(vs) for k, vs in km.items()}
        for model, km in acc.items()
    }
    return {
        "root": root,
        "n_games_found": len(per_game),
        "n_subdirs_requested": len(subdirs),
        "win_counts": dict(win_counter),
        "avg_metrics": avg,
        "n_obs": nobs,
        "per_game": per_game,
    }


def print_report(agg):
    lines = []

    def p(s=""):
        print(s); lines.append(s)

    p("=" * 64)
    p(f"跨对局平均指标聚合：{agg['root']}")
    p("=" * 64)
    p(f"成功聚合 {agg['n_games_found']} / {agg['n_subdirs_requested']} 局\n")

    wc = agg["win_counts"]
    total_win = sum(wc.values())
    p("--- 胜率 ---")
    if total_win:
        for fac, c in sorted(wc.items(), key=lambda x: -x[1]):
            name = {"god": "好人阵营", "werewolf": "狼人阵营"}.get(fac, fac)
            p(f"  {name}: {c}/{total_win} = {c/total_win:.1%}")
    else:
        p("  无胜负数据")

    labels = {
        "deception_freq": "欺骗频次",
        "evolution_speed": "怀疑目标改变率",
        "trust_density": "被信任入度",
        "avg_confidence": "平均置信度",
        "calibration": "置信度校准",
    }
    p("\n--- 各模型平均指标（跨局均值）---")
    for model in sorted(agg["avg_metrics"]):
        p(f"  [{model}]")
        for k in MODEL_METRIC_KEYS:
            v = agg["avg_metrics"][model].get(k)
            n = agg["n_obs"][model].get(k, 0)
            if v is None:
                p(f"     {labels[k]:14}: 无数据")
            else:
                p(f"     {labels[k]:14}: {v:.4f}  (n={n}局)")

    # 强弱模型小结
    strong = next((m for m in agg["avg_metrics"] if "pro" in m), None)
    weak = next((m for m in agg["avg_metrics"] if "flash" in m or "chat" in m), None)
    if strong and weak:
        p("\n--- 强(pro) vs 弱(flash) 平均对比 ---")
        s, w = agg["avg_metrics"][strong], agg["avg_metrics"][weak]
        for k in ("deception_freq", "avg_confidence", "trust_density"):
            sv, wv = s.get(k), w.get(k)
            if sv is not None and wv is not None:
                p(f"  {labels[k]:10} 强 {sv:.3f}  vs  弱 {wv:.3f}")
    return "\n".join(lines)


def plot_avg(agg, out_path):
    try:
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
    except ImportError:
        print("[提示] 无 matplotlib，跳过绘图")
        return
    models = sorted(agg["avg_metrics"])
    if len(models) < 1:
        return
    specs = [
        ("deception_freq", "平均欺骗频次", "#e76f51"),
        ("avg_confidence", "平均置信度", "#e9c46a"),
        ("trust_density", "平均被信任入度", "#264653"),
    ]
    x = np.arange(len(models))
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.5))
    for ax, (key, title, color) in zip(axes, specs):
        vals = [agg["avg_metrics"][m].get(key) or 0 for m in models]
        bars = ax.bar(x, vals, 0.55, color=color)
        ax.set_xticks(x)
        ax.set_xticklabels([m.replace("deepseek-", "") for m in models], fontsize=9)
        ax.set_title(title, fontsize=12)
        ax.set_ylim(0, max(vals) * 1.25 if max(vals) > 0 else 1)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.3f}",
                    ha="center", va="bottom", fontsize=10)
    fig.suptitle(f"{agg['n_games_found']} 局平均：欺骗频次 / 平均置信度 / 被信任入度", fontsize=13)
    import matplotlib.pyplot as plt
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    print(f"[图表] 已保存：{out_path}")


def main():
    ap = argparse.ArgumentParser(description="跨对局聚合平均指标")
    ap.add_argument("root", help="结果根目录，如 reversed-result")
    ap.add_argument("--subdirs", default=None, help="子目录范围，如 1-30 或 1,2,5；默认全部数字子目录")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    subdirs = _parse_subdirs(args.subdirs, root)
    agg = aggregate(root, subdirs)

    report = print_report(agg)
    with open(os.path.join(root, "aggregate_report.txt"), "w", encoding="utf-8") as f:
        f.write(report)
    with open(os.path.join(root, "aggregate_metrics.json"), "w", encoding="utf-8") as f:
        json.dump(agg, f, ensure_ascii=False, indent=2)
    plot_avg(agg, os.path.join(root, "aggregate_chart.png"))
    print(f"\n✅ 聚合结果已写入 {root}/（aggregate_report.txt / aggregate_metrics.json / aggregate_chart.png）")


if __name__ == "__main__":
    main()

