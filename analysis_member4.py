"""analysis_member4.py — 信任度量化与策略代差分析（成员4 指标，适配真实日志格式）。

成员4 的原始草稿对数据格式有 4 处错误假设，直接跑会得到全 0 / 空结果。
本文件在保留其三大指标命题的前提下，修正了与引擎真实产出的契约不匹配：

  1. 座位提取：AI 实际说“3号”而非“座位3” —— 正则改为 r"(\\d+)\\s*号"。
  2. 轮次：日志无 round 字段，只有 ts —— 用 ts 排序近似回合时序。
  3. game_id：实际是 "cli-42" 等，无 expA/expB/expC 前缀 —— 直接按 game_id 聚合。
  4. 角色→模型：跟随 config.py 当前路由（狼+神职=deepseek-v4-pro，平民=deepseek-chat）。

额外增强：用 replays/*.json 的 ground truth（真实身份）把“欺骗”定义得更准——
狼人嘴上信任/洗白同伴、或思考与发言对某座位态度相反，才算欺骗。

运行：
    python analysis_member4.py
"""

from __future__ import annotations

import glob
import json
import os
import re
from collections import defaultdict

import numpy as np

# ==================== 配置 ====================
PKG_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(PKG_DIR)
# 日志默认写在项目根的 ./logs（见 .env 的 LLM_LOG_DIR）；复盘写在 ./replays。
# 两处都允许被环境变量覆盖，便于成员4 指向自己的数据目录。
LOG_FOLDER = os.environ.get("WW_LOG_FOLDER", os.path.join(PROJECT_ROOT, "logs"))
REPLAY_FOLDER = os.environ.get("WW_REPLAY_FOLDER", os.path.join(PROJECT_ROOT, "replays"))
PLOT_RESULTS = True

# 角色→模型：直接复用 config.py 的 DEFAULT_ROLE_MODEL，保证与实际路由（含反转开关
# WW_REVERSE_ROLES）永远一致，不会因手抄而错配。失败则回退到默认映射。
try:
    from .config import DEFAULT_ROLE_MODEL as ROLE_TO_MODEL
except Exception:
    ROLE_TO_MODEL = {
        "werewolf": "deepseek-v4-pro",
        "seer": "deepseek-v4-pro",
        "witch": "deepseek-v4-pro",
        "guard": "deepseek-v4-pro",
        "hunter": "deepseek-v4-pro",
        "villager": "deepseek-v4-flash",
        "moderator": "deepseek-v4-pro",
    }

TRUST_KW = ["相信", "好人", "信任", "同意", "支持", "没错", "说得对", "金水", "站边", "认"]
DISTRUST_KW = ["怀疑", "狼人", "可疑", "不信", "觉得是狼", "有问题", "骗子", "查杀", "出他", "踩"]

# 修正点 1：AI 用“X号”称呼，不是“座位X”
_SEAT_RE = re.compile(r"(\d+)\s*号")


def load_jsonl(folder):
    records = []
    if not os.path.isdir(folder):
        print(f"[警告] 日志目录不存在：{folder}")
        return records
    for path in glob.glob(os.path.join(folder, "*.jsonl")):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def load_ground_truth(folder):
    """读取所有复盘文件，返回 {game_id: {seat: role}}（真实身份）。"""
    truth = {}
    if not os.path.isdir(folder):
        return truth
    for path in glob.glob(os.path.join(folder, "*.json")):
        try:
            d = json.load(open(path, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        gid = d.get("game_id")
        players = d.get("players", {})
        if gid and players:
            truth[gid] = {int(s): p["role"] for s, p in players.items()}
    return truth


def extract_seats(text):
    if not isinstance(text, str):
        return []
    return [int(x) for x in _SEAT_RE.findall(text)]


def sentiment_score(text):
    if not isinstance(text, str):
        return 0
    pos = sum(text.count(kw) for kw in TRUST_KW)
    neg = sum(text.count(kw) for kw in DISTRUST_KW)
    return pos - neg


def agent_responses(records):
    """只保留 agent_response，并按 (game_id, ts) 排序近似回合时序（修正点 2）。"""
    ar = [r for r in records if r.get("kind") == "agent_response" and r.get("response")]
    ar.sort(key=lambda r: (r.get("game_id", ""), r.get("ts", 0)))
    return ar


# ---------- 指标 1：欺骗频次 ----------
def compute_deception_frequency(ar, truth):
    """欺骗事件数 / 总行动数，按模型分组。

    欺骗判定（三选一即记一次）：
      A. 狼人公开发言信任/洗白另一名狼队友（嘴上帮真狼说话）。
      B. thought 与 speech 对同一座位态度相反（想踩却公开夸，或反之）。
      C. thought 里明说要投/杀某号，实际 action 的 target 不同（声东击西）。
    """
    deception = defaultdict(int)
    total = defaultdict(int)

    for r in ar:
        role = r.get("role")
        if not role:
            continue
        model = ROLE_TO_MODEL.get(role, "Other")
        total[model] += 1

        resp = r["response"]
        thought = resp.get("thought", "") or ""
        speech = resp.get("speech", "") or ""
        action = resp.get("action")
        target = resp.get("target")
        gid = r.get("game_id")
        seat = r.get("seat")
        roles = truth.get(gid, {})

        flagged = False

        # A. 狼人公开为另一只狼背书
        if role == "werewolf" and roles and sentiment_score(speech) > 0:
            for s in extract_seats(speech):
                if s != seat and roles.get(s) == "werewolf":
                    flagged = True
                    break

        # B. 思考与发言对同一座位态度相反
        if not flagged and thought and speech:
            th_seats = set(extract_seats(thought))
            sp_seats = set(extract_seats(speech))
            if th_seats & sp_seats and (sentiment_score(thought) * sentiment_score(speech) < 0):
                flagged = True

        # C. 思考里的投/杀目标与实际 target 不一致
        if not flagged:
            m = re.search(r"(?:投票?给?|杀|刀|击杀)\s*(\d+)\s*号", thought)
            if m and action in ("vote", "kill") and target is not None:
                if int(m.group(1)) != target:
                    flagged = True

        if flagged:
            deception[model] += 1

    freq = {m: (deception[m] / total[m] if total[m] else 0.0) for m in total}
    return freq, dict(total), dict(deception)


# ---------- 指标 2：策略演变速度（怀疑/投票目标改变率）----------
def compute_strategy_evolution_speed(ar):
    """每个 agent 怀疑对象（投票/指认 target）随时间改变的比例，按模型分组取均值。

    旧版统计相邻 action 或 target 变化 —— 但白天 speak/vote 天然交替，导致人人
    接近 1.0，度量的是“行动类型切换”而非策略。改为只看“怀疑/投票目标”的序列：
    从 vote 的 target，以及发言里点的第一个非自己座位（指认目标）中提取，
    相邻两次目标不同才记一次改变。这才是真正的策略摇摆。
    """
    seqs = defaultdict(list)  # (game, seat) -> [(ts, target, role)]
    for r in ar:
        gid, seat, role = r.get("game_id"), r.get("seat"), r.get("role")
        if not (gid and seat and role):
            continue
        resp = r["response"]
        action, target = resp.get("action"), resp.get("target")
        speech = resp.get("speech", "") or ""

        # 提取本回合的“怀疑目标”
        focus = None
        if action == "vote" and target is not None and target != seat:
            focus = target
        else:
            mentioned = [s for s in extract_seats(speech) if s != seat]
            # 发言里带强怀疑词时，取首个被点的座位作为指认对象
            if mentioned and any(kw in speech for kw in DISTRUST_KW):
                focus = mentioned[0]
        if focus is not None:
            seqs[(gid, seat)].append((r.get("ts", 0), focus, role))

    model_rates = defaultdict(list)
    for (_gid, _seat), seq in seqs.items():
        if len(seq) < 2:
            continue
        seq.sort(key=lambda x: x[0])
        changes = sum(1 for i in range(1, len(seq)) if seq[i][1] != seq[i - 1][1])
        rate = changes / (len(seq) - 1)
        model = ROLE_TO_MODEL.get(seq[0][2], "Other")
        model_rates[model].append(rate)

    avg = {m: float(np.mean(v)) for m, v in model_rates.items() if v}
    return avg, dict(model_rates)


# ---------- 指标 3：信任网络密度（按模型入度）----------
def build_trust_matrix(ar, game_id):
    """构建有向信任矩阵 mat[from][to] = 累计信任分（发言情感 + 投票）。"""
    mat = defaultdict(lambda: defaultdict(float))
    for r in ar:
        if r.get("game_id") != game_id:
            continue
        seat = r.get("seat")
        resp = r["response"]
        speech = resp.get("speech", "") or ""
        action, target = resp.get("action"), resp.get("target")

        net = sentiment_score(speech)
        for t in extract_seats(speech):
            if t != seat:
                mat[seat][t] += net
        if action == "vote" and target is not None and target != seat:
            mat[seat][target] -= 1  # 投票=不信任，扣分
    return mat


def _seat_role_map(ar, game_id):
    """该局 seat -> role（取该 seat 任一条记录的角色）。"""
    m = {}
    for r in ar:
        if r.get("game_id") == game_id and r.get("seat") and r.get("role"):
            m.setdefault(r["seat"], r["role"])
    return m


def compute_trust_density(ar):
    """按模型的「被信任度」：每个模型的角色平均收到多少条正向信任入边。

    旧版按整局算一个 density 再赋给局内所有模型 —— 同一局两模型必然相等，
    无法区分。改为统计每个【目标座位】收到的正向入边数（谁被信任），
    按目标座位的模型归并取均值。这样 pro 角色和 flash 角色能拉开差异。

    返回 (avg_in_degree_by_model, per_game_overall_density)。
    """
    games = {r.get("game_id") for r in ar if r.get("game_id")}
    model_indeg = defaultdict(list)   # model -> 每个该模型角色的正向入度
    per_game = {}                     # 仍保留整局密度，供跨局生态比较
    for gid in games:
        mat = build_trust_matrix(ar, gid)
        seat_role = _seat_role_map(ar, gid)

        # 正向入度：每个目标座位收到多少条 w>0 的信任边
        in_pos = defaultdict(int)
        nodes, edges = set(), 0
        for frm, tos in mat.items():
            nodes.add(frm)
            for to, w in tos.items():
                nodes.add(to)
                if w > 0:
                    edges += 1
                    in_pos[to] += 1

        # 整局密度（保留，用于 per_game 生态比较）
        n = len(nodes)
        per_game[gid] = edges / (n * (n - 1)) if n > 1 else 0.0

        # 按【目标座位的模型】归并入度 —— 谁这个模型的角色更受信任
        for seat, role in seat_role.items():
            model = ROLE_TO_MODEL.get(role, "Other")
            model_indeg[model].append(in_pos.get(seat, 0))

    avg = {m: float(np.mean(v)) for m, v in model_indeg.items() if v}
    return avg, per_game


# ---------- 指标 4：决策置信度（自评把握 + 校准）----------
GOOD_ROLES = {"villager", "seer", "witch", "hunter", "guard"}


def compute_confidence(ar, truth):
    """按模型统计 confidence 字段：平均自评置信度，以及“校准度”。

    - avg_confidence：模型对自己每次行动的平均自评把握（0-1）。
    - calibration：好人方投票里，高置信(≥0.85)桶 vs 低置信桶 投中真狼的正确率。
      高置信正确率 > 低置信 = 校准好（自信且准）；反过来 = 盲目自信。
      返回 high_acc - low_acc 作为校准分（正=校准好，负=盲目自信），需复盘真相。
    """
    by_model = defaultdict(list)
    for r in ar:
        c = r["response"].get("confidence")
        if c is not None and r.get("role"):
            by_model[ROLE_TO_MODEL.get(r["role"], "Other")].append(c)
    avg_conf = {m: float(np.mean(v)) for m, v in by_model.items() if v}

    # 校准：好人投票投中真狼算正确，按置信桶统计
    buckets = defaultdict(lambda: {"high": [0, 0], "low": [0, 0]})  # model -> bucket -> [对,总]
    for r in ar:
        if r.get("role") not in GOOD_ROLES:
            continue
        resp = r["response"]
        if resp.get("action") != "vote":
            continue
        t, c = resp.get("target"), resp.get("confidence")
        gid = r.get("game_id")
        wolves = {s for s, role in truth.get(gid, {}).items() if role == "werewolf"}
        if t is None or c is None or not wolves:
            continue
        m = ROLE_TO_MODEL.get(r["role"], "Other")
        b = "high" if c >= 0.85 else "low"
        buckets[m][b][1] += 1
        buckets[m][b][0] += 1 if t in wolves else 0

    calib = {}
    for m, bk in buckets.items():
        h_cor, h_tot = bk["high"]
        l_cor, l_tot = bk["low"]
        if h_tot and l_tot:
            calib[m] = (h_cor / h_tot) - (l_cor / l_tot)
    return avg_conf, calib


def plot_comparison(metrics, out_path=None):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        # 尽量启用中文字体，避免图里中文变方块（无则自动回退）
        for font in ("Microsoft YaHei", "SimHei", "Arial Unicode MS"):
            try:
                matplotlib.rcParams["font.sans-serif"] = [font]
                matplotlib.rcParams["axes.unicode_minus"] = False
                break
            except Exception:
                continue
    except ImportError:
        print("[提示] 未安装 matplotlib，跳过绘图。")
        return
    models = list(metrics.keys())
    # 三个指标量纲不同（频率 0-1、改变率 0-1、入度 0-N），分三个子图各自 y 轴，
    # 否则入度(~7)会把另两个压成 0。每根柱标注真实值，模型差异一目了然。
    specs = [
        ("deception_freq", "欺骗频次", "#e76f51"),
        ("evolution_speed", "怀疑目标改变率", "#2a9d8f"),
        ("trust_density", "被信任入度", "#264653"),
    ]
    x = np.arange(len(models))
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.5))
    for ax, (key, title, color) in zip(axes, specs):
        vals = [metrics[m][key] for m in models]
        bars = ax.bar(x, vals, 0.55, color=color)
        ax.set_xticks(x)
        ax.set_xticklabels([m.replace("deepseek-", "") for m in models], fontsize=9)
        ax.set_title(title, fontsize=12)
        ax.set_ylim(0, max(vals) * 1.25 if max(vals) > 0 else 1)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.3f}",
                    ha="center", va="bottom", fontsize=10)
    fig.suptitle("不同模型策略代差对比（强 v4-pro vs 弱 v4-flash）", fontsize=13)
    plt.tight_layout()
    out = out_path or os.path.join(PROJECT_ROOT, "strategy_gap_comparison.png")
    plt.savefig(out, dpi=150)
    print(f"[图表] 已保存：{out}")


def plot_metrics(metrics, keys, out_path, suptitle="模型指标对比"):
    """通用三指标对比图：每个指标一个子图、各自 y 轴、柱顶标真实值。

    keys: [(metric_key, 中文标题, 颜色), ...]，量纲不同也能同框不互相压制。
    """
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
        print("[提示] 未安装 matplotlib，跳过绘图。")
        return
    models = list(metrics.keys())
    x = np.arange(len(models))
    fig, axes = plt.subplots(1, len(keys), figsize=(4.3 * len(keys), 4.5))
    if len(keys) == 1:
        axes = [axes]
    for ax, (key, title, color) in zip(axes, keys):
        vals = [metrics[m].get(key) or 0 for m in models]
        bars = ax.bar(x, vals, 0.55, color=color)
        ax.set_xticks(x)
        ax.set_xticklabels([m.replace("deepseek-", "") for m in models], fontsize=9)
        ax.set_title(title, fontsize=12)
        ax.set_ylim(0, max(vals) * 1.25 if max(vals) > 0 else 1)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.3f}",
                    ha="center", va="bottom", fontsize=10)
    fig.suptitle(suptitle, fontsize=13)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    print(f"[图表] 已保存：{out_path}")


def analyze(log_folder=LOG_FOLDER, replay_folder=REPLAY_FOLDER):
    """跑完整分析，返回结构化结果 dict（供编程调用 / 主流程整合）。

    返回：
        {
          "n_games": int, "n_records": int, "n_truth": int,
          "metrics": {model: {"deception_freq","evolution_speed","trust_density",
                              "deception_count","total_actions"}},
          "per_game_density": {game_id: float},
        }
    """
    records = load_jsonl(log_folder)
    truth = load_ground_truth(replay_folder)
    ar = agent_responses(records)
    if not ar:
        return {"n_games": 0, "n_records": 0, "n_truth": len(truth),
                "metrics": {}, "per_game_density": {}}

    dfreq, total, dcount = compute_deception_frequency(ar, truth)
    espeed, _ = compute_strategy_evolution_speed(ar)
    tdens, per_game = compute_trust_density(ar)
    aconf, calib = compute_confidence(ar, truth)

    models = set(dfreq) | set(espeed) | set(tdens) | set(aconf)
    metrics = {
        m: {
            "deception_freq": dfreq.get(m, 0.0),
            "evolution_speed": espeed.get(m, 0.0),
            "trust_density": tdens.get(m, 0.0),
            "avg_confidence": aconf.get(m, 0.0),
            "calibration": calib.get(m),  # None 表示样本不足
            "deception_count": dcount.get(m, 0),
            "total_actions": total.get(m, 0),
        }
        for m in models
    }
    games = {r.get("game_id") for r in ar if r.get("game_id")}
    return {
        "n_games": len(games),
        "n_records": len(ar),
        "n_truth": len(truth),
        "metrics": metrics,
        "per_game_density": per_game,
    }


def print_report(result, *, plot=True):
    """把 analyze() 的结果打印成报告（并可选绘图）。"""
    print("=" * 60)
    print("狼人杀策略代差分析（成员4 指标）")
    print("=" * 60)
    print(f"对局数：{result['n_games']} | agent_response 记录：{result['n_records']} | "
          f"复盘真相：{result['n_truth']} 局\n")

    metrics = result["metrics"]
    if not metrics:
        print("无 agent_response 数据，请先跑一局游戏生成 logs/。")
        return

    print("--- 1. 欺骗频次（欺骗事件/总行动）---")
    for m in sorted(metrics):
        d = metrics[m]
        print(f"  {m:18} {d['deception_freq']:.4f}  ({d['deception_count']}/{d['total_actions']})")

    print("\n--- 2. 策略演变速度（怀疑目标改变率）---")
    for m in sorted(metrics):
        print(f"  {m:18} {metrics[m]['evolution_speed']:.4f}")

    print("\n--- 3. 信任网络密度（按模型平均被信任入度）---")
    for m in sorted(metrics):
        print(f"  {m:18} {metrics[m]['trust_density']:.4f}")

    print("\n--- 4. 决策置信度（自评把握 0-1）---")
    for m in sorted(metrics):
        d = metrics[m]
        cal = d.get("calibration")
        cal_str = (f"  校准={cal:+.2f}（{'自信且准' if cal > 0 else '盲目自信'}）"
                   if cal is not None else "  校准=样本不足")
        print(f"  {m:18} 均值={d['avg_confidence']:.3f}{cal_str}")

    print("\n--- 策略代差小结 ---")
    strong = next((m for m in metrics if "pro" in m), None)
    weak = next((m for m in metrics if "flash" in m or "chat" in m), None)
    if strong and weak and strong != weak:
        s, w = metrics[strong], metrics[weak]
        print(f"  欺骗频次   强模型({strong}) {s['deception_freq']:.3f}  vs  弱模型({weak}) {w['deception_freq']:.3f}")
        print(f"  目标改变率 强模型 {s['evolution_speed']:.3f}  vs  弱模型 {w['evolution_speed']:.3f}")
        print(f"  被信任入度 强模型 {s['trust_density']:.3f}  vs  弱模型 {w['trust_density']:.3f}")
        print(f"  决策置信度 强模型 {s['avg_confidence']:.3f}  vs  弱模型 {w['avg_confidence']:.3f}")
    else:
        print("  （需两个不同模型的对局数据才能对比代差）")

    if plot and len(metrics) > 1:
        plot_comparison(metrics)


def main():
    result = analyze()
    print_report(result, plot=PLOT_RESULTS)
    print("\n分析完成。")


if __name__ == "__main__":
    main()
