"""批量实测：对 tests/bulk/out/ 里的证书测速 + 对标准答案。

用法（仓库根目录）：
  python tests/bulk/run_bulk.py --check 100     # 第②步：抽 100 份先对答案，必须零漏报零误报
  python tests/bulk/run_bulk.py                 # 第③④步：A 单进程 / B 多核 / C 逐份启动，写 tests/bulk/report.md
全程 --no-llm（判定只靠程序，不调模型、不花钱）。
"""
import argparse
import datetime as dt
import json
import os
import platform
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(HERE))
from app.parse import parse_pdf  # noqa: E402
from app.rules import load_rules, run_rules, compare_history, match_key  # noqa: E402
from app.cli import write_outputs  # noqa: E402
from run_acceptance import match_case  # noqa: E402

OUT = os.path.join(HERE, "out")
REPORT = os.path.join(HERE, "report.md")


def load_manifest():
    with open(os.path.join(OUT, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


def audit_one(path, rules):
    """与 app.cli.analyze 单份部分相同：解析 + 规则（--no-llm）。"""
    t0 = time.perf_counter()
    c = parse_pdf(path)
    c["extract_check"] = {"llm_agree": None, "diffs": [], "note": "--no-llm"}
    c["findings"] = run_rules(c, rules)
    return c, time.perf_counter() - t0


def attach_history(certs, rules):
    """与 app.cli.analyze 的历史配对部分相同。"""
    groups = defaultdict(list)
    for c in certs:
        k = match_key(c)
        if k:
            groups[k].append(c)
    n_pairs = 0
    for lst in groups.values():
        if len(lst) < 2:
            continue
        lst.sort(key=lambda c: c.get("calib_date") or "")
        for old, new in zip(lst, lst[1:]):
            for f in compare_history(old, new, rules):
                f["id"] = f"F{len(new['findings']) + 1:03d}"
                new["findings"].append(f)
            n_pairs += 1
    return n_pairs


_RULES = None


def _worker(path):
    global _RULES
    if _RULES is None:
        _RULES = load_rules()
    return audit_one(path, _RULES)


def score(certs, items):
    by_file = {c["file"]: c for c in certs}
    tot = Counter()
    per_rule = defaultdict(Counter)
    bad = []
    for it in items:
        c = by_file[it["file"]]
        hits, misses, fps, _ign, notes = match_case(it["expect"], it["allow"], c["findings"])
        tot["期望"] += len(it["expect"])
        tot["命中"] += len(hits)
        tot["漏报"] += len(misses)
        tot["误报"] += len(fps)
        for e, _ in hits:
            per_rule[e["rule_id"]]["命中"] += 1
        for e in misses:
            per_rule[e["rule_id"]]["漏报"] += 1
        for f in fps:
            per_rule[f.get("rule_id")]["误报"] += 1
        for e in it["expect"]:
            per_rule[e["rule_id"]]["期望"] += 1
        if misses or fps or notes:
            bad.append((it, misses, fps, notes))
    return tot, per_rule, bad


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def rss_mb():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / 1e6 if sys.platform == "darwin" else r / 1e3  # macOS 单位是字节


def run_A(paths, rules):
    certs, secs = [], []
    t0 = time.perf_counter()
    for p in paths:
        c, s = audit_one(p, rules)
        certs.append(c)
        secs.append(s)
    t_audit = time.perf_counter() - t0
    t1 = time.perf_counter()
    n_pairs = attach_history(certs, rules)
    t_hist = time.perf_counter() - t1
    tmp = tempfile.mkdtemp(prefix="bulkA_")
    t2 = time.perf_counter()
    write_outputs(certs, [], tmp)
    t_write = time.perf_counter() - t2
    shutil.rmtree(tmp, ignore_errors=True)
    return certs, {"secs": secs, "t_audit": t_audit, "t_hist": t_hist, "t_write": t_write, "n_pairs": n_pairs,
                   "total": t_audit + t_hist + t_write, "rss": rss_mb()}


def run_B(paths, rules, workers):
    t0 = time.perf_counter()
    with ProcessPoolExecutor(workers) as ex:
        res = list(ex.map(_worker, paths, chunksize=8))
    t_audit = time.perf_counter() - t0
    certs = [c for c, _ in res]
    t1 = time.perf_counter()
    attach_history(certs, rules)
    t_hist = time.perf_counter() - t1
    return certs, {"total": t_audit + t_hist, "t_audit": t_audit, "workers": workers}


def run_C(paths):
    py = sys.executable
    env = dict(os.environ, PYTHONPATH=ROOT)
    secs = []
    tmp = tempfile.mkdtemp(prefix="bulkC_")
    try:
        for p in paths:
            t0 = time.perf_counter()
            subprocess.run([py, "-m", "app.cli", p, "-o", os.path.join(tmp, "o"), "--no-llm"], cwd=tmp, env=env,
                           capture_output=True, check=True)
            secs.append(time.perf_counter() - t0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return secs


def check(items, rules, k):
    # 抽样：历史对比组整对带上，其余按顺序取，保证每类改法都有
    sample, seen_cases = [], set()
    pairs = [it for it in items if it["kind"] == "历史对比"][:4]
    sample += pairs
    for it in items:
        if len(sample) >= k:
            break
        if it in sample:
            continue
        if it["case"] not in seen_cases or len(seen_cases) > 40:
            sample.append(it)
            seen_cases.add(it["case"])
    for it in items:
        if len(sample) >= k:
            break
        if it not in sample:
            sample.append(it)
    certs = [audit_one(os.path.join(OUT, it["file"]), rules)[0] for it in sample]
    attach_history(certs, rules)
    tot, per_rule, bad = score(certs, sample)
    print(f"抽查 {len(sample)} 份（改法 {len({it['case'] for it in sample})} 种）：期望 {tot['期望']}，"
          f"命中 {tot['命中']}，漏报 {tot['漏报']}，误报 {tot['误报']}")
    for it, misses, fps, notes in bad[:20]:
        print(" ✗", it["file"], it["base"], it["kind"], it["case"], "漏", [m["rule_id"] for m in misses],
              "误", [(f.get("rule_id"), f.get("severity"), str(f.get("reason"))[:60]) for f in fps], notes)
    return 0 if not bad else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", type=int, default=0, help="只抽查 K 份对答案")
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    ap.add_argument("--c-sample", type=int, default=100, help="C 跑法抽多少份（每份单独起一次 Python）")
    a = ap.parse_args()
    man = load_manifest()
    items = man["items"]
    rules = load_rules()
    if a.check:
        return check(items, rules, a.check)

    paths = [os.path.join(OUT, it["file"]) for it in items]
    size_mb = sum(os.path.getsize(p) for p in paths) / 1e6
    started = dt.datetime.now()
    print(f"A 单进程顺序跑 {len(paths)} 份…", flush=True)
    certs_a, A = run_A(paths, rules)
    print(f"  {A['total']:.1f}s", flush=True)
    print(f"B {a.workers} 核同时跑…", flush=True)
    certs_b, B = run_B(paths, rules, a.workers)
    print(f"  {B['total']:.1f}s", flush=True)
    c_paths = paths[:a.c_sample]
    print(f"C 逐份单独启动（抽 {len(c_paths)} 份）…", flush=True)
    C = run_C(c_paths)
    print(f"  中位 {statistics.median(C):.2f}s", flush=True)

    tot, per_rule, bad = score(certs_a, items)
    tot_b, _, bad_b = score(certs_b, items)
    same = all(json.dumps(x["findings"], sort_keys=True, default=str) == json.dumps(y["findings"], sort_keys=True, default=str)
               for x, y in zip(certs_a, certs_b))
    kinds = Counter(it["kind"] for it in items)
    flagged = sum(1 for c in certs_a if any(f.get("severity") in ("错误", "无法核验") for f in c["findings"]))
    s = A["secs"]

    L = ["# 批量实测报告", "",
         f"- 运行时间：{started:%Y-%m-%d %H:%M}；机器：{platform.machine()} · {os.cpu_count()} 核 · Python {platform.python_version()}",
         f"- 证书：{len(items)} 份，共 {size_mb:.0f} MB（{', '.join(f'{k} {v}' for k, v in kinds.items())}）；"
         f"带错比例 {man['err']:.0%} 为**假设值**（真实比例未知）",
         "- 全程 `--no-llm`：判定只靠程序，不调用任何模型",
         "- 复现：`python tests/bulk/make_bulk.py && python tests/bulk/run_bulk.py`（造数据内容一致，可重复）", "",
         "## 结论", "",
         f"- **单机 {a.workers} 核：{len(items)} 份 {B['total']:.1f} 秒审完**，折合每小时约 {len(items) / B['total'] * 3600:,.0f} 份（实测吞吐换算，不是单份推算）",
         f"- 单进程：{len(items)} 份 {A['total']:.1f} 秒；单份审核耗时中位 {statistics.median(s) * 1000:.0f} ms，"
         f"最慢 5% 在 {pct(s, 95) * 1000:.0f} ms 以上，最慢一份 {max(s) * 1000:.0f} ms",
         f"- 准确：期望发现 {tot['期望']} 条，命中 {tot['命中']}，**漏报 {tot['漏报']}，误报 {tot['误报']}**；多核跑结果与单进程逐字一致：{'是' if same else '否'}",
         f"- 需要人看的证书（有「错误」或「无法核验」）：{flagged} 份 / {len(items)} 份，有问题的才需要人点开", "",
         "## 三种跑法", "",
         "| 跑法 | 回答什么 | 结果 |", "|---|---|---|",
         f"| A 单进程顺序跑 | 引擎本身多快 | 审核 {A['t_audit']:.1f}s + 历史配对 {A['t_hist']:.2f}s（配上 {A['n_pairs']} 对）+ 写结果文件 {A['t_write']:.1f}s = {A['total']:.1f}s；峰值内存 {A['rss']:.0f} MB |",
         f"| B {a.workers} 核同时跑 | 一台电脑一小时能审多少 | {B['total']:.1f}s，约 {len(items) / B['total'] * 3600:,.0f} 份/小时 |",
         f"| C 每份单独启动一次程序 | 命令行逐份调用时，单份耗时多出的部分是什么 | 抽 {len(C)} 份：中位 {statistics.median(C):.2f}s，"
         f"比 A 的单份多出约 {statistics.median(C) - statistics.median(s):.2f}s，这部分是每次启动 Python 和加载程序的开销 |", "",
         "## 每条规则", "", "| 规则 | 期望 | 命中 | 漏报 | 误报 |", "|---|---|---|---|---|"]
    for r in sorted(per_rule, key=str):
        d = per_rule[r]
        L.append(f"| {r} | {d['期望']} | {d['命中']} | {d['漏报']} | {d['误报']} |")
    L += ["", "## 没对上的", ""]
    if not bad and not bad_b:
        L.append("无。")
    for it, misses, fps, notes in bad[:50]:
        L.append(f"- {it['file']}（{it['base']} · {it['kind']} · {it['case']}）漏报 {[m['rule_id'] for m in misses]} "
                 f"误报 {[(f.get('rule_id'), f.get('severity')) for f in fps]} {notes}")
    L += ["", "## 这个数字能说明什么、不能说明什么", "",
          "- 能说明：在这 3 种底板、2 种版式上，量放大到上千份，速度稳定，准确率不掉，历史配对不乱。",
          "- 不能说明：真实环境的准确率。底板只有 3 份，每份最多一处人为改动，答案是我们自己写的；没有扫描件和新版式。这一步要靠真实证书做盲测。",
          "- 每份证书只换了编号，测量数据和底板相同（叠加改法的除外）。对速度没有影响：引擎每份都从头解析，不做缓存。"]
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L[8:13]))
    print("报告 →", REPORT)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
