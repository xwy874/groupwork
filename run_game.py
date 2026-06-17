"""run_game.py — 引擎可运行入口（成员1）。

用法：
    python -m werewolf_gateway.run_game            # 随机角色，调用真实 LLM 端点
    python -m werewolf_gateway.run_game --seed 42  # 可复现的角色分配

需要先在 werewolf_gateway/.env 配好 LLM_BASE_URL / LLM_API_KEY（成员3提供）。
离线、不花 token 的自测见 test_engine.py。
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import random
import shutil
import time

from .client import LLMGateway
from .engine import EventType, GameEvent, WerewolfGameEngine
from .session import GameSession

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TranscriptObserver:
    """订阅引擎事件：① 打到控制台 ② 收集成一份人类可读的对话记录。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def _record(self, text: str) -> None:
        self.lines.append(text)

    def emit(self, event: GameEvent) -> None:
        p = event.payload
        if event.type == EventType.PHASE_CHANGE:
            self._record(f"\n=== [{event.day}天] 阶段：{p.get('phase')} ===")
        elif event.type == EventType.CHAIN_OF_THOUGHT:
            line = f"    [思维链] {p['seat']}号({p['role']}): {p['thought']}"
            print(line[:140]); self._record(line)
        elif event.type == EventType.SPEECH:
            line = f"  [发言] {p['seat']}号({p['role']}): {p['text']}"
            print(line[:140]); self._record(line)
        elif event.type == EventType.WOLF_CHAT:
            self._record(f"  [狼频道] {p.get('message')}")
        elif event.type == EventType.PUBLIC_LOG:
            self._record(f"[公开] {p.get('message')}")
        elif event.type == EventType.VOTE_RESULT:
            self._record(f"  [投票结果] 出局={p.get('eliminated')} 票数={p.get('tally')}")
        elif event.type == EventType.SHERIFF:
            self._record(f"  [警长] {p.get('stage')} -> {p.get('sheriff')}")
        elif event.type == EventType.GAME_OVER:
            line = f"  [结束] 胜方={p.get('winner')}  真实身份={p.get('roles')}"
            print(line); self._record(line)


def collect_results(game_id: str, transcript: TranscriptObserver,
                    subdir: str = "1", base_dir: str = "result") -> str:
    """把本局所有产物归集到 <base_dir>/<subdir>/ ：对话记录 + 复盘 + 指标 + 调用日志。

    指标只统计本局：按 game_id 精确过滤出本局记录，写成干净的 game_log.jsonl，
    再让分析只读该目录，绝不混入历史对局或测试残留。
    """
    out_dir = os.path.join(PROJECT_ROOT, base_dir, subdir)
    os.makedirs(out_dir, exist_ok=True)

    # 1) 人类可读对话记录
    with open(os.path.join(out_dir, "transcript.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(transcript.lines))

    # 2) 复盘 JSON（含 ground truth）
    replay_src = os.path.join(PROJECT_ROOT, "replays", f"{game_id}.json")
    if os.path.exists(replay_src):
        shutil.copy(replay_src, os.path.join(out_dir, "replay.json"))

    # 3) 抽取「仅本局」的调用日志 —— 扫描所有 logs/ 但只保留 game_id 匹配的行，
    #    避免“取最近文件”把上一局/测���残留也带进来。
    log_dir = os.path.join(PROJECT_ROOT, "logs")
    game_log = os.path.join(out_dir, "game_log.jsonl")
    n_lines = 0
    with open(game_log, "w", encoding="utf-8") as out_f:
        for src in glob.glob(os.path.join(log_dir, "*.jsonl")):
            for line in open(src, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("game_id") == game_id:
                    out_f.write(line + "\n")
                    n_lines += 1
    print(f"[日志] 本局 {n_lines} 条记录 -> {game_log}")

    # 4) 成员4 指标 —— 只读 result/<subdir>/（本局日志 + 本局复盘），指标即本局指标。
    try:
        from .analysis_member4 import analyze, print_report, plot_comparison
        result = analyze(log_folder=out_dir, replay_folder=out_dir)
        with open(os.path.join(out_dir, "metrics.json"), "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            print_report(result, plot=False)
        with open(os.path.join(out_dir, "metrics_report.txt"), "w", encoding="utf-8") as f:
            f.write(buf.getvalue())
        print(buf.getvalue())
        if len(result.get("metrics", {})) > 1:
            plot_comparison(result["metrics"], out_path=os.path.join(out_dir, "strategy_gap.png"))
    except Exception as e:
        print(f"[指标跳过] {type(e).__name__}: {e}")

    return out_dir


async def main(seed: int | None = None, analyze: bool = True,
               out_subdir: str = "1", base_dir: str = "result") -> None:
    rng = random.Random(seed) if seed is not None else None
    game_id = f"cli-{seed if seed is not None else int(time.time())}"
    transcript = TranscriptObserver()
    async with LLMGateway() as gw:
        game = GameSession(gw, game_id=game_id)
        engine = WerewolfGameEngine(game, observer=transcript, rng=rng)
        print(f"角色分配：{engine.roster}")
        transcript._record(f"角色分配：{engine.roster}")
        winner = await engine.run()
        print(f"\n最终胜方：{winner}")
        engine.save_replay(os.path.join(PROJECT_ROOT, "replays", f"{game_id}.json"))

    # 归集所有产物到 <base_dir>/<out_subdir>/
    if analyze:
        print("\n" + "=" * 60)
        print(f"正在归集对话记录与量化指标到 {base_dir}/{out_subdir}/ ...")
        out_dir = collect_results(game_id, transcript, subdir=out_subdir, base_dir=base_dir)
        print(f"\n✅ 本局所有产物已保存到：{out_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="运行一局12人狼人杀（调用真实 LLM）。")
    parser.add_argument("--seed", type=int, default=None, help="随机种子，用于可复现的角色分配。")
    parser.add_argument("--out", default="1", help="结果归集到 <result-dir>/<out>/ ，默认子目录 1。")
    parser.add_argument("--result-dir", default="result",
                        help="结果根目录，默认 result；反转实验用 reversed-result。")
    parser.add_argument("--no-analyze", action="store_true", help="跑完后不归集结果/运行分析。")
    args = parser.parse_args()
    asyncio.run(main(args.seed, analyze=not args.no_analyze,
                     out_subdir=args.out, base_dir=args.result_dir))
