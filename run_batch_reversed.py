"""run_batch_reversed.py — 3 并发批量跑反转局（狼+神=flash, 村民=pro）。

种子 2..30 各跑一局，产物分别进 reversed-result/<seed>/。
- 3 并发：同时最多 3 局在跑，完成一局立即补下一局（滑动窗口）。
- 降并发防限流：每个子进程把 LLM_MAX_CONCURRENCY 降到 4（3×4=12，温和）。
- 断点续跑：已存在 reversed-result/<seed>/metrics.json 的局直接跳过。
- 失败隔离：某局非零退出只记录并继续，不中断整批。
- 末尾自动聚合：全部跑完调用 aggregate_results.py 出平均。

每局约 45-55 分钟，3 并发 29 局约 8-9 小时。建议后台运行。

用法：
  WW_REVERSE_ROLES=1 python run_batch_reversed.py
  WW_REVERSE_ROLES=1 python run_batch_reversed.py --seeds 2-30 --concurrency 3
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def parse_seeds(spec):
    out = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


async def run_one_seed(seed, result_dir, per_proc_concurrency, log_dir):
    """子进程跑一局；返回 (seed, 退出码)。日志写到 <log_dir>/batch-seed-<seed>.log。"""
    sub = str(seed)
    env = dict(os.environ)
    # 模式开关（WW_REVERSE_ROLES / WW_ALL_FLASH）从父进程环境继承，dict(os.environ)
    # 已复制，无需在此覆盖 —— 这样同一脚本可跑反转局 / 全弱基线 / 正常局。
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    # 降低每进程网关并发，避免 3 进程叠加打爆 API 限流
    env["LLM_MAX_CONCURRENCY"] = str(per_proc_concurrency)
    env["LLM_MAX_CONCURRENCY_PER_MODEL"] = str(per_proc_concurrency)

    log_path = os.path.join(log_dir, f"batch-seed-{seed}.log")
    cmd = [sys.executable, "-u", "-m", "werewolf_gateway.run_game",
           "--seed", str(seed), "--result-dir", result_dir, "--out", sub]
    with open(log_path, "w", encoding="utf-8") as logf:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=PROJECT_ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT,
        )
        rc = await proc.wait()
    return seed, rc


async def run_batch(seeds, result_dir, concurrency, per_proc_concurrency):
    os.makedirs(os.path.join(PROJECT_ROOT, result_dir), exist_ok=True)
    log_dir = os.path.join(PROJECT_ROOT, result_dir, "_batch_logs")
    os.makedirs(log_dir, exist_ok=True)

    # 跳过已完成
    todo, skipped = [], []
    for s in seeds:
        if os.path.exists(os.path.join(PROJECT_ROOT, result_dir, str(s), "metrics.json")):
            skipped.append(s)
        else:
            todo.append(s)
    print(f"待跑 {len(todo)} 局，跳过(已完成) {len(skipped)} 局：{skipped}")
    print(f"并发 {concurrency}，每进程网关并发 {per_proc_concurrency}\n")

    t0 = time.time()
    done, failed = [], []
    sem = asyncio.Semaphore(concurrency)
    completed = 0
    total = len(todo)

    async def worker(seed):
        nonlocal completed
        async with sem:
            print(f"  ▶ 开始 seed={seed}（已用时 {(time.time()-t0)/60:.0f} 分钟）")
            s, rc = await run_one_seed(seed, result_dir, per_proc_concurrency, log_dir)
            completed += 1
            ok = rc == 0 and os.path.exists(
                os.path.join(PROJECT_ROOT, result_dir, str(s), "metrics.json"))
            (done if ok else failed).append(s)
            status = "✅" if ok else f"❌(rc={rc})"
            print(f"  {status} 完成 seed={seed}  [{completed}/{total}]  用时 {(time.time()-t0)/60:.0f} 分钟")

    await asyncio.gather(*(worker(s) for s in todo))

    print(f"\n{'#'*64}")
    print(f"批量完成：成功 {len(done)} | 跳过 {len(skipped)} | 失败 {len(failed)}")
    if failed:
        print(f"失败 seeds: {sorted(failed)}（见 {result_dir}/_batch_logs/）")
    print(f"总用时 {(time.time()-t0)/3600:.1f} 小时")
    print(f"{'#'*64}")

    print("\n聚合所有局平均指标 ...")
    subprocess.run([sys.executable, os.path.join(PROJECT_ROOT, "aggregate_results.py"),
                    result_dir], cwd=PROJECT_ROOT)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="3 并发批量跑对局（正常/反转/全弱基线）")
    ap.add_argument("--seeds", default="2-30", help="种子范围，默认 2-30")
    ap.add_argument("--result-dir", default="reversed-result")
    ap.add_argument("--concurrency", type=int, default=3, help="同时在跑的局数，默认 3")
    ap.add_argument("--per-proc-concurrency", type=int, default=4,
                    help="每个子进程的网关并发上限，默认 4（3×4=12 温和）")
    ap.add_argument("--normal", action="store_true",
                    help="正常配置（狼+神=pro, 村民=flash），不设任何模型开关。")
    args = ap.parse_args()

    reverse = os.getenv("WW_REVERSE_ROLES", "").strip() in ("1", "true", "True")
    allflash = os.getenv("WW_ALL_FLASH", "").strip() in ("1", "true", "True")
    allpro = os.getenv("WW_ALL_PRO", "").strip() in ("1", "true", "True")
    if not (reverse or allflash or allpro or args.normal):
        print("⚠️  未指定模式！请四选一：")
        print("    正常局：   python run_batch_reversed.py --normal --result-dir result")
        print("    反转局：   WW_REVERSE_ROLES=1 python run_batch_reversed.py --result-dir reversed-result")
        print("    全弱基线： WW_ALL_FLASH=1   python run_batch_reversed.py --result-dir result-allflash")
        print("    全强基线： WW_ALL_PRO=1     python run_batch_reversed.py --result-dir result-allpro")
        sys.exit(1)
    if args.normal and (reverse or allflash or allpro):
        print("⚠️  --normal 不能与 WW_REVERSE_ROLES/WW_ALL_FLASH/WW_ALL_PRO 同时使用。")
        sys.exit(1)

    mode = ("全强基线(all-pro)" if allpro else
            "全弱基线(all-flash)" if allflash else
            "反转(reversed)" if reverse else "正常(normal: 狼神=pro, 村民=flash)")
    seeds = parse_seeds(args.seeds)
    print(f"模式={mode}，将以 {args.concurrency} 并发跑 {len(seeds)} 局，seeds={seeds}")
    asyncio.run(run_batch(seeds, args.result_dir, args.concurrency, args.per_proc_concurrency))
