"""批量造证书：由 3 份无问题的虚构底板（base_A / base_2025 / base_2026）改出 N 份各不相同的证书 + 标准答案。

用法（仓库根目录）：python tests/bulk/make_bulk.py [-n 1000] [--err 0.10] [--pairs 50] [--workers 10]
产物：tests/bulk/out/bulk_XXXX.pdf + tests/bulk/out/manifest.json（不进 git，可随时重造，内容一致）

每份都做的「换身份」改动（不改测量数据，不该触发任何规则）：
  - 三页证书编号全部换成同一个新编号（每份唯一）
  - 管理编号（base_2025 / base_2026）或出厂编号（base_A）换成新的（每份唯一，历史对比组除外）
在此之上按比例叠加 tests/tools/make_cases.py 里已有的改法：
  - 错误改法（期望报错/提示）：占 --err
  - 边界改法（刚好在边界上，期望不报）：约 20%
  - 其余只换身份
历史对比组（--pairs）：一对 base_2025 + base_2026 共用同一个新管理编号，期望在新证书上报 3 条 R6。
"""
import argparse
import json
import os
import random
import sys
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
import make_cases as mc  # noqa: E402

OUT = os.path.join(HERE, "out")
SEED = 20261106

# 每份底板的身份字段：(证书编号, 管理编号或出厂编号)
IDENT = {
    mc.C1: ("C2699000000101", "SN-DEMO-0001"),
    mc.R25: ("C2599000000103", "DEMO-001"),
    mc.R26: ("C2699000000104", "DEMO-001"),
}
CERT_SPAN = "证 书 编 号          {}"
ORIG_EXPECT = {b: (mc.ORIGINALS[b][1], mc.ORIGINALS[b][2]) for b in IDENT}
# 关键词「15」会误配到证书编号里的 15（如 C2599000000015），批量时改用更具体的「10→15」
R6_EXPECT = [dict(e, hint="10→15") if e.get("hint") == "15" else e for e in mc.PAIRS["示例历史对比"]["expect"]]


def is_error_case(exp):
    return any(e["rule_id"] != "R7" for e in exp)


ERROR_CASES = [c for c in mc.CASES if c[1] in IDENT and is_error_case(c[4])]
BOUNDARY_CASES = [c for c in mc.CASES if c[1] in IDENT and not is_error_case(c[4])]


def ident_edits(base, cert_no, id_no):
    old_cert, old_id = IDENT[base]
    return [
        (1, old_cert, cert_no, None),
        (2, CERT_SPAN.format(old_cert), CERT_SPAN.format(cert_no), None),
        (3, CERT_SPAN.format(old_cert), CERT_SPAN.format(cert_no), None),
        (1, old_id, id_no, None),
    ]


def new_ids(base, i, shared_id=None):
    old_cert, old_id = IDENT[base]
    cert_no = old_cert[:8] + f"{500000 + i:06d}"   # 500001 起，避免与底板自身编号重合
    if shared_id:
        id_no = shared_id
    elif base == mc.C1:
        id_no = f"SN-D{i:06d}"
    else:
        id_no = f"DEMO-{i:05d}"
    return cert_no, id_no


def plan(n, err, pairs, seed=SEED):
    rnd = random.Random(seed)
    items, i = [], 0
    for k in range(pairs):
        shared = f"PAIR-{k:04d}"
        for base in (mc.R25, mc.R26):
            i += 1
            cert_no, id_no = new_ids(base, i, shared)
            exp, allow = ORIG_EXPECT[base]
            exp = list(exp) + (list(R6_EXPECT) if base == mc.R26 else [])
            items.append({"file": f"bulk_{i:04d}.pdf", "base": base, "kind": "历史对比", "case": f"pair{k:04d}",
                          "cert_no": cert_no, "id_no": id_no, "case_edits": [], "expect": exp, "allow": list(allow)})
    rest = n - len(items)
    n_err = round(n * err)
    n_bnd = round(rest * 0.2)
    kinds = ["错误"] * n_err + ["边界"] * n_bnd + ["只换身份"] * (rest - n_err - n_bnd)
    rnd.shuffle(kinds)
    for kind in kinds:
        i += 1
        if kind == "只换身份":
            base = rnd.choice(list(IDENT))
            case_id, edits = "-", []
            exp, allow = ORIG_EXPECT[base]
        else:
            cid, base, _desc, edits, exp, allow = rnd.choice(ERROR_CASES if kind == "错误" else BOUNDARY_CASES)
            case_id = cid
        cert_no, id_no = new_ids(base, i)
        items.append({"file": f"bulk_{i:04d}.pdf", "base": base, "kind": kind, "case": case_id,
                      "cert_no": cert_no, "id_no": id_no, "case_edits": [list(e) for e in edits],
                      "expect": list(exp), "allow": list(allow)})
    return items


def build(item):
    base = item["base"]
    case_edits = [tuple(e) for e in item["case_edits"]]
    touched = {(p, old) for p, old, _, _ in case_edits}
    # 改法本身动到的字段（如删证书编号）不再换身份，保留改法原意
    edits = [e for e in ident_edits(base, item["cert_no"], item["id_no"]) if (e[0], e[1]) not in touched]
    edits += case_edits
    dst = os.path.join(OUT, item["file"])
    try:
        mc.mutate(os.path.join(mc.SRC, base), dst, edits)   # mutate 内部已把字体子集化
        return item["file"], None
    except Exception as e:  # noqa
        return item["file"], str(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=1000)
    ap.add_argument("--err", type=float, default=0.10)
    ap.add_argument("--pairs", type=int, default=50)
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    items = plan(a.n, a.err, a.pairs)
    fails = []
    with ProcessPoolExecutor(a.workers) as ex:
        for k, (fn, err) in enumerate(ex.map(build, items, chunksize=4), 1):
            if err:
                fails.append((fn, err))
            if k % 100 == 0:
                print(f"已造 {k}/{len(items)}", flush=True)
    bad = {fn for fn, _ in fails}
    manifest = {"seed": SEED, "n": a.n, "err": a.err, "pairs": a.pairs,
                "items": [it for it in items if it["file"] not in bad]}
    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    for fn, err in fails:
        print("FAIL", fn, err)
    size = sum(os.path.getsize(os.path.join(OUT, it["file"])) for it in manifest["items"])
    print(f"造好 {len(manifest['items'])}/{len(items)} 份，共 {size / 1e6:.0f} MB → {OUT}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
