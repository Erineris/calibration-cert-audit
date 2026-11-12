# -*- coding: utf-8 -*-
"""命令行：python -m app.cli <pdf...> [-o out] [--rules app/rules.yaml] [--no-llm]
每份 PDF 输出 <stem>.cert.json 与 <stem>.findings.json；多份时按管理编号/出厂编号配对做历史对比（R6）。
"""
import os
import sys
import json
import argparse

from .parse import parse_pdf
from .rules import load_rules, run_rules, compare_history, match_key
from . import llm_extract


def analyze(paths, rules_path=None, use_llm=True):
    rules = load_rules(rules_path)
    certs = []
    for p in paths:
        c = parse_pdf(p)
        if use_llm:
            llm_extract.check(c)
        else:
            c["extract_check"] = {"llm_agree": None, "diffs": [], "note": "--no-llm"}
        c["findings"] = run_rules(c, rules)
        certs.append(c)
    # 历史配对
    groups = {}
    for c in certs:
        k = match_key(c)
        if k:
            groups.setdefault(k, []).append(c)
    history = []
    for k, lst in groups.items():
        if len(lst) < 2:
            continue
        lst.sort(key=lambda c: c.get("calib_date") or "")
        for old, new in zip(lst, lst[1:]):
            hf = compare_history(old, new, rules)
            # 历史提示挂到新证书上，编号接着排
            for f in hf:
                f["id"] = f"F{len(new['findings']) + 1:03d}"
                new["findings"].append(f)
            history.append({"key": k, "old": old["cert_no"], "new": new["cert_no"], "findings": hf})
    return certs, history


def write_outputs(certs, history, outdir):
    os.makedirs(outdir, exist_ok=True)
    for c in certs:
        stem = os.path.splitext(c["file"])[0]
        cert_out = {k: v for k, v in c.items() if k not in ("findings", "page_texts")}
        with open(os.path.join(outdir, f"{stem}.cert.json"), "w", encoding="utf-8") as f:
            json.dump(cert_out, f, ensure_ascii=False, indent=1)
        with open(os.path.join(outdir, f"{stem}.findings.json"), "w", encoding="utf-8") as f:
            json.dump(c["findings"], f, ensure_ascii=False, indent=1)
    if history:
        with open(os.path.join(outdir, "history.json"), "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=1)


def summary(certs, history):
    lines = []
    for c in certs:
        ec = c.get("extract_check", {})
        lines.append(f"\n{c['file']}  编号 {c['cert_no']}  版式 {c.get('layout')}  校准日期 {c.get('calib_date')}  仪器 {c['instrument'].get('name')} {c['instrument'].get('model')}  管理编号 {c['instrument'].get('mgmt_no') or '-'}  第二路抽取核对 {ec.get('llm_agree')}")
        if not c["findings"]:
            lines.append("  无发现")
        for f in c["findings"]:
            lines.append(f"  {f['id']} [{f['severity']}] {f['rule_id']} 第{f['page']}页  {f['reason']}")
    for h in history:
        lines.append(f"\n历史对比 {h['key']}: {h['old']} → {h['new']}  R6 提示 {len(h['findings'])} 条")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="校准证书审核引擎")
    ap.add_argument("pdfs", nargs="+")
    ap.add_argument("-o", "--out", default="out")
    ap.add_argument("--rules", default=None)
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args(argv)
    certs, history = analyze(a.pdfs, a.rules, use_llm=not a.no_llm)
    write_outputs(certs, history, a.out)
    print(summary(certs, history))
    print(f"\n已写入 {os.path.abspath(a.out)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
