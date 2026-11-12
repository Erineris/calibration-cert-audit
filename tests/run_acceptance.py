"""验收：对四份虚构底板 + tests/cases 全部 case 跑审核引擎命令行，比对 expected.yaml，写 tests/report.md

用法（仓库根目录）：
    python tests/run_acceptance.py --tag full         # 全量验收
    python tests/run_acceptance.py --only r1_over_pass.pdf   # 单跑一个
可选：--cmd "{python} -m app.cli {pdfs}"   覆盖 CLI 调用方式（{pdfs} 会替换成文件列表）

做法：每次调用前把 PDF 复制进一个临时空目录，cwd=临时目录、PYTHONPATH=仓库根，
     这样 CLI 无论往 stdout、cwd 还是 PDF 旁边写 JSON，都落在临时目录，不污染仓库。
     从 stdout JSON 和临时目录下所有 *.json 里递归收集带 rule_id 的对象，按 id/内容去重。
原始输出存 tests/runs/<tag>/，便于复现。
"""
import argparse
import datetime as dt
import glob
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "tests", "base")
CASES = os.path.join(ROOT, "tests", "cases")
RULES = ["R0", "R1", "R2", "R3", "R4", "R5", "R6", "R7"]


# ---------------- 调 CLI ----------------
def collect_findings(obj, out):
    if isinstance(obj, dict):
        if "rule_id" in obj:
            out.append(obj)
        else:
            for v in obj.values():
                collect_findings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect_findings(v, out)


def parse_json_blobs(text):
    """stdout 可能是单个 JSON、JSON Lines、或夹杂日志；尽量都解析。"""
    blobs = []
    text = text.strip()
    if not text:
        return blobs
    try:
        return [json.loads(text)]
    except Exception:
        pass
    dec = json.JSONDecoder()
    i = 0
    while i < len(text):
        j = min([k for k in (text.find("{", i), text.find("[", i)) if k >= 0], default=-1)
        if j < 0:
            break
        try:
            obj, end = dec.raw_decode(text, j)
            blobs.append(obj)
            i = end
        except Exception:
            i = j + 1
    return blobs


def run_cli(pdf_paths, cmd_tpl, raw_dir, label):
    tmp = tempfile.mkdtemp(prefix="b_accept_")
    try:
        local = []
        for p in pdf_paths:
            dst = os.path.join(tmp, os.path.basename(p))
            shutil.copy(p, dst)
            local.append(dst)
        cmd = cmd_tpl.replace("{python}", shlex.quote(sys.executable)).replace("{pdfs}", " ".join(shlex.quote(x) for x in local))
        env = dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""))
        t0 = dt.datetime.now()
        try:
            cp = subprocess.run(cmd, shell=True, cwd=tmp, env=env, capture_output=True, text=True, timeout=180)
            rc, out, err = cp.returncode, cp.stdout, cp.stderr
        except subprocess.TimeoutExpired as e:
            rc, out, err = "timeout", e.stdout or "", (e.stderr or "") + "\n[超时 180s]"
            out = out.decode() if isinstance(out, bytes) else out
            err = err.decode() if isinstance(err, bytes) else err
        secs = (dt.datetime.now() - t0).total_seconds()
        found = []
        for b in parse_json_blobs(out):
            collect_findings(b, found)
        files = sorted(set(glob.glob(os.path.join(tmp, "**", "*.json"), recursive=True)))
        for fpath in files:
            try:
                with open(fpath, encoding="utf-8") as f:
                    collect_findings(json.load(f), found)
            except Exception:
                pass
        # 去重（stdout 和文件可能是同一份）
        seen, uniq = set(), []
        for f in found:
            key = f.get("id") or json.dumps({k: f.get(k) for k in ("rule_id", "severity", "page", "raw", "reason")},
                                            ensure_ascii=False, sort_keys=True, default=str)
            key = (key, json.dumps(f.get("bbox"), default=str))
            if key not in seen:
                seen.add(key)
                uniq.append(f)
        os.makedirs(raw_dir, exist_ok=True)
        with open(os.path.join(raw_dir, label + ".log"), "w", encoding="utf-8") as f:
            shown = cmd.replace(shlex.quote(sys.executable), "python").replace(tmp, "<tmp>")  # 日志里不留本机路径
            f.write(f"$ {shown}\n# rc={rc} {secs:.1f}s\n# json files={[os.path.relpath(x, tmp) for x in files]}\n"
                    f"--- stdout ---\n{out.replace(tmp, '<tmp>')}\n--- stderr ---\n{err.replace(tmp, '<tmp>')}\n")
        with open(os.path.join(raw_dir, label + ".findings.json"), "w", encoding="utf-8") as f:
            json.dump(uniq, f, ensure_ascii=False, indent=1, default=str)
        return {"rc": rc, "secs": secs, "findings": uniq, "stderr": err, "stdout": out, "json_files": files}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------- 比对 ----------------
def sev_ok(exp_sev, act_sev):
    return act_sev in exp_sev.split("|")


def allowed(f, allow):
    for a in allow:
        r, s = a.split(":")
        if f.get("rule_id") == r and (s == "*" or f.get("severity") == s):
            return True
    return False


def text_of(f):
    return " ".join(str(f.get(k, "")) for k in ("raw", "reason", "suggestion"))


def match_case(expect, allow, findings):
    pool = list(findings)
    hits, misses, notes = [], [], []
    # 有 hint 的先配（让 R6 三条、证书2 四条各配各的）
    order = sorted(expect, key=lambda e: 0 if e.get("hint") else 1)
    for e in order:
        cands = [f for f in pool if f.get("rule_id") == e["rule_id"] and sev_ok(e["severity"], f.get("severity"))]
        if e.get("hint"):
            pref = [f for f in cands if e["hint"] in text_of(f)]
            cands = pref or cands
            if cands and not pref:
                notes.append(f"{e['rule_id']} 配上了但原文/理由里没有「{e['hint']}」")
        if not cands:
            misses.append(e)
            continue
        f = cands[0]
        pool.remove(f)
        hits.append((e, f))
        if e.get("forbid") and e["forbid"] in text_of(f):
            notes.append(f"{e['rule_id']} 引用了错误数据：发现里出现「{e['forbid']}」→ {fmt_f(f)}")
        if e.get("page") and str(f.get("page")) != str(e["page"]):
            notes.append(f"{e['rule_id']} 位置不符：期望第 {e['page']} 页，实际 {f.get('page')}")
    fps = [f for f in pool if not allowed(f, allow)]
    ignored = [f for f in pool if allowed(f, allow)]
    return hits, misses, fps, ignored, notes


def fmt_f(f):
    raw = str(f.get("raw", "")).replace("\n", " ").replace("|", "\\|")[:60]
    reason = str(f.get("reason", "")).replace("\n", " ").replace("|", "\\|")[:80]
    return f"{f.get('rule_id')}/{f.get('severity')} p{f.get('page')} raw=`{raw}` · {reason}"


def fmt_e(e):
    s = f"{e['rule_id']}/{e['severity']}"
    if e.get("page"):
        s += f" p{e['page']}"
    if e.get("hint"):
        s += f"（含「{e['hint']}」）"
    return s


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default=dt.datetime.now().strftime("%H%M"))
    ap.add_argument("--cmd", default="{python} -m app.cli {pdfs} -o out --no-llm")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--report", default=os.path.join(ROOT, "tests", "report.md"))
    args = ap.parse_args()

    with open(os.path.join(CASES, "expected.yaml"), encoding="utf-8") as f:
        exp_all = yaml.safe_load(f)
    pairs = exp_all.pop("_pairs", {})
    exp_all.pop("_说明", None)
    if args.only:
        exp_all = {k: v for k, v in exp_all.items() if k in args.only}
        pairs = {k: v for k, v in pairs.items() if k in args.only}
    raw_dir = os.path.join(ROOT, "tests", "runs", args.tag)

    stat = {r: {"exp": 0, "hit": 0, "fp": 0} for r in RULES}
    rows, miss_rows, fp_rows, note_rows, disputed_rows, err_rows = [], [], [], [], [], []
    t_start = dt.datetime.now()

    def account(name, desc, spec, res, only_rules=None):
        finds = res["findings"]
        if only_rules:
            finds = [f for f in finds if f.get("rule_id") in only_rules]
        expect = [e for e in spec["expect"] if not e.get("disputed")]
        disputed = [e for e in spec["expect"] if e.get("disputed")]
        hits, misses, fps, ignored, notes = match_case(expect, spec.get("allow", []), finds)
        # 口径待定的条目：从剩余 fps 里单独配，不进命中率
        for d in disputed:
            m = [f for f in fps if f.get("rule_id") == d["rule_id"]]
            disputed_rows.append((name, desc, fmt_e(d), fmt_f(m[0]) if m else "未报"))
            if m:
                fps.remove(m[0])
        for e in expect:
            stat.setdefault(e["rule_id"], {"exp": 0, "hit": 0, "fp": 0})["exp"] += 1
        for e, _ in hits:
            stat[e["rule_id"]]["hit"] += 1
        for f in fps:
            stat.setdefault(str(f.get("rule_id")), {"exp": 0, "hit": 0, "fp": 0})["fp"] += 1
        ok = not misses and not fps and res["rc"] == 0 and not notes
        if res["rc"] != 0:
            err_rows.append((name, res["rc"], (res["stderr"] or "").strip().splitlines()[-3:]))
        rows.append((name, "✅" if ok else ("⚠️" if not misses and not fps and res["rc"] == 0 else "❌"), len(hits), len(misses), len(fps), len(ignored), f"{res['secs']:.1f}s", desc))
        for e in misses:
            miss_rows.append((name, desc, fmt_e(e), "; ".join(fmt_f(f) for f in finds) or "（无任何发现）"))
        for f in fps:
            fp_rows.append((name, desc, fmt_f(f)))
        for n in notes:
            note_rows.append((name, n))

    for name, spec in exp_all.items():
        path = os.path.join(BASE, name) if spec["base"] == "底板" else os.path.join(CASES, name)
        res = run_cli([path], args.cmd, raw_dir, os.path.splitext(name)[0])
        # 单份文件不判 R6（历史对比在 pair 里判）；单份若报 R6 视为误报
        account(name, spec["desc"], spec, res)
        print(rows[-1][1], name, f"hit={rows[-1][2]} miss={rows[-1][3]} fp={rows[-1][4]}", flush=True)
    for name, spec in pairs.items():
        paths = [os.path.join(BASE, x) for x in spec["files"]]
        res = run_cli(paths, args.cmd, raw_dir, name)
        account(name, spec["desc"], spec, res, only_rules=spec.get("only_rules"))
        print(rows[-1][1], name, f"hit={rows[-1][2]} miss={rows[-1][3]} fp={rows[-1][4]}", flush=True)

    # ---------------- 写报告 ----------------
    n_ok = sum(1 for r in rows if r[1] == "✅")
    tot_exp = sum(v["exp"] for v in stat.values())
    tot_hit = sum(v["hit"] for v in stat.values())
    tot_fp = sum(v["fp"] for v in stat.values())
    L = []
    L.append(f"# 验收报告（{args.tag}）\n")
    L.append(f"- 运行时间：{t_start:%Y-%m-%d %H:%M}，耗时 {(dt.datetime.now() - t_start).total_seconds():.0f}s")
    L.append(f"- 被测命令：`{args.cmd.replace("{python}", "python")}`（cwd=临时目录，PYTHONPATH=仓库根；不调用任何大模型）")
    L.append(f"- 用例：{len(rows)} 条（底板 4 + 改造 PDF {len(rows) - 4 - len(pairs)} + 历史对比 {len(pairs)}）；期望来源 `tests/cases/expected.yaml`")
    L.append(f"- 原始输出：`tests/runs/{args.tag}/<用例>.log` 与 `.findings.json`\n")
    L.append("## 结论\n")
    n_warn = sum(1 for r in rows if r[1] == "⚠️")
    L.append(f"- 用例全对：**{n_ok}/{len(rows)}**；规则命中但页码/引用数据不对（⚠️）：**{n_warn}**")
    L.append(f"- 期望发现命中：**{tot_hit}/{tot_exp}**，漏报 **{tot_exp - tot_hit}**，误报 **{tot_fp}**")
    if err_rows:
        L.append(f"- CLI 非零退出 / 超时：**{len(err_rows)}** 条")
    L.append("")
    L.append("## 每条规则命中率\n")
    L.append("| 规则 | 期望条数 | 命中 | 命中率 | 误报 |")
    L.append("|---|---|---|---|---|")
    for r in sorted(stat):
        v = stat[r]
        if v["exp"] or v["fp"]:
            rate = f"{v['hit'] / v['exp']:.0%}" if v["exp"] else "—"
            L.append(f"| {r} | {v['exp']} | {v['hit']} | {rate} | {v['fp']} |")
    L.append("")
    L.append("## 漏报\n")
    if miss_rows:
        L.append("| 用例 | 改了什么 | 期望 | CLI 实际全部输出 |")
        L.append("|---|---|---|---|")
        for r in miss_rows:
            L.append("| " + " | ".join(str(x).replace("\n", " ") for x in r) + " |")
    else:
        L.append("无。")
    L.append("\n## 误报\n")
    if fp_rows:
        L.append("| 用例 | 改了什么 | 多报的发现 |")
        L.append("|---|---|---|")
        for r in fp_rows:
            L.append("| " + " | ".join(str(x).replace("\n", " ") for x in r) + " |")
    else:
        L.append("无。")
    if note_rows:
        L.append("\n## 位置 / 原文不符（规则命中，但页码或引用数据不对；用例记 ⚠️）\n")
        for n, t in note_rows:
            L.append(f"- `{n}`：{t}")
    if disputed_rows:
        L.append("\n## 口径待定（不计入命中率，等裁决）\n")
        L.append("| 用例 | 场景 | 我们暂定的期望 | CLI 实际 |")
        L.append("|---|---|---|---|")
        for r in disputed_rows:
            L.append("| " + " | ".join(r) + " |")
    if err_rows:
        L.append("\n## CLI 报错\n")
        for n, rc, tail in err_rows:
            L.append(f"- `{n}` rc={rc}：" + " / ".join(x.strip() for x in tail))
    L.append("\n## 逐用例\n")
    L.append("| 用例 | 结果 | 命中 | 漏报 | 误报 | 允许的额外 | 耗时 | 说明 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        L.append("| " + " | ".join(str(x) for x in r) + " |")
    L.append("\n## 复现\n")
    L.append("```bash\n# 在仓库根目录执行")
    L.append("python tests/tools/make_base.py            # 重新生成 4 份虚构底板 tests/base/*.pdf（内容可重复生成）")
    L.append("python tests/tools/make_cases.py           # 重新造 tests/cases/*.pdf + expected.yaml（内容可重复生成）")
    L.append(f"python tests/run_acceptance.py --tag {args.tag}   # 全量验收，写本报告")
    L.append("python tests/run_acceptance.py --only r1_over_pass.pdf --tag dbg   # 单跑一个用例")
    L.append("python -m app.cli tests/cases/r1_over_pass.pdf   # 直接看 CLI 原始输出")
    L.append("```")
    with open(args.report, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"\n用例全对 {n_ok}/{len(rows)}；命中 {tot_hit}/{tot_exp}；误报 {tot_fp} → {os.path.relpath(args.report, ROOT)}")


if __name__ == "__main__":
    main()
