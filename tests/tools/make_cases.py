"""造测试集：改四份虚构底板（tests/base/，由 make_base.py 生成）的文本层 → tests/cases/*.pdf + tests/cases/expected.yaml

用法（仓库根目录）：python tests/tools/make_cases.py
做法：对每个目标 span 精确匹配文字（必要时按 y 坐标消歧）→ redact（不动图形/线条）
      → 在原 span 的 origin 基线、原字号 insert_text（纯西文用 Times，含中文用 PyMuPDF 自带 CJK 字体，保存时子集化）。
校验：改完重新抽取，要求「消失的 span = 旧文字，新增的 span = 新文字」，其余 span 一字不差。
"""
import os
import sys
from collections import Counter

import pymupdf as fitz
import yaml

fitz.TOOLS.mupdf_display_errors(False)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "tests", "base")
OUT = os.path.join(ROOT, "tests", "cases")

CJK_BUF = fitz.Font("cjk").buffer   # PyMuPDF 自带 CJK 字体，不依赖系统字体
# 四份底板：A=版式A（无结论列）、B=含多处真错、R25/R26=同一台仪器 2025/2026 年报告
C1, C2, R25, R26 = "base_A.pdf", "base_B_errors.pdf", "base_2025.pdf", "base_2026.pdf"
TRACE_25_T = "C2588000000203"   # 底板 R25 第一台标准器的溯源证书号（「C+数字」形态，用来检验不会被误当成本页证书编号）
R7 = {"rule_id": "R7", "severity": "提示"}


def E(rule, sev, page=None, hint=None, forbid=None):
    d = {"rule_id": rule, "severity": sev}
    if forbid:
        d["forbid"] = forbid
    if page:
        d["page"] = page
    if hint:
        d["hint"] = hint
    return d


# 编辑：(页, 旧文字, 新文字, y坐标或None)；新文字 "" = 删除
CASES = [
    # ---------- R1 超差与结论冲突 ----------
    ("r1_eq_tol_pass", R25, "温度10℃点 修正值 +0.1→+2.0（=允差±2.0）标 Pass：等于允差不算超差",
     [(3, "+0.1", "+2.0", 373.2)], [R7], []),
    ("r1_over_pass", R25, "温度20℃点 修正值 +0.3→+2.1（>±2.0）仍标 Pass：超差却标合格",
     [(3, "+0.3", "+2.1", 387.6)], [E("R1", "错误", 3), R7], []),
    ("r1_under_fail", R25, "温度30℃点 +0.4 未超差，结论 Pass→Fail：未超差却标不合格",
     [(3, "Pass", "Fail", 402.0)], [E("R1", "错误", 3), R7], []),
    ("r1_over_fail", R25, "湿度60%RH点 +3→+6（>±5），结论 Pass→Fail：超差且已标 Fail 只提示",
     [(3, "+3", "+6", 502.8), (3, "Pass", "Fail", 502.8)], [E("R1", "提示", 3), R7], []),
    ("r1_neg_over_pass", R25, "温度20℃点 修正值 +0.3→-2.1（|−2.1|>±2.0）仍标 Pass：负向超差",
     [(3, "+0.3", "-2.1", 387.6)], [E("R1", "错误", 3), R7], []),
    ("r1_blank_conc", R25, "温度30℃点 结论 Pass 删空：未填结论不算错",
     [(3, "Pass", "", 402.0)], [R7], []),
    ("r1_A_eq_tol", C1, "版式A（无结论列）温度-15℃点 +0.14→+0.50（=允差±0.50）：边界不算超差",
     [(3, "+0.14", "+0.50", 373.2)], [R7], []),
    ("r1_A_over_noconc", C1, "版式A（无结论列）温度5℃点 +0.30→-0.51（>±0.50）：不构成结论冲突，只提示超差（按规则裁定：无结论列不构成冲突）",
     [(3, "+0.30", "-0.51", 387.6)], [E("R1", "提示", 3), R7], []),
    ("r1_split_cell", R26, "2026 底板第3页被拆成「+0.」「6」的单元格改成「+2.」「6」=+2.6（>±2.0）标 Pass：考按坐标重排",
     [(3, "+0.", "+2.", 402.0)], [E("R1", "错误", 3), R7], []),
    # ---------- R2 环境条件 ----------
    ("r2_t20", R25, "环境温度 23.5→20.0℃（下边界，合格）", [(2, "温度：23.5℃", "温度：20.0℃", None)], [R7], []),
    ("r2_t30", R25, "环境温度 23.5→30.0℃（上边界，合格）", [(2, "温度：23.5℃", "温度：30.0℃", None)], [R7], []),
    ("r2_t19_9", R25, "环境温度 23.5→19.9℃（超下限）", [(2, "温度：23.5℃", "温度：19.9℃", None)],
     [E("R2", "错误", 2), R7], []),
    ("r2_rh85", R25, "环境湿度 56→85%（≥85 不合格）", [(2, "相对湿度：56%", "相对湿度：85%", None)],
     [E("R2", "错误", 2), R7], []),
    ("r2_rh84", R25, "环境湿度 56→84%（<85 合格）", [(2, "相对湿度：56%", "相对湿度：84%", None)], [R7], []),
    ("r2_A_t30_1", C1, "版式A 环境温度 23.2→30.1℃（超上限）", [(2, "温度：23.2℃", "温度：30.1℃", None)],
     [E("R2", "错误", 2), R7], []),
    # ---------- R3 校准点范围 ----------
    ("r3_t100", R25, "温度校准点 10→100℃（上边界，在范围）", [(3, "10", "100", 373.2)], [R7], []),
    ("r3_t101", R25, "温度校准点 10→101℃（超范围）", [(3, "10", "101", 373.2)], [E("R3", "提示", 3), R7], []),
    ("r3_tm40", C1, "版式A 温度校准点 -15→-40℃（下边界，在范围）", [(3, "-15", "-40", 373.2)], [R7], []),
    ("r3_tm41", C1, "版式A 温度校准点 -15→-41℃（超范围）", [(3, "-15", "-41", 373.2)], [E("R3", "提示", 3), R7], []),
    ("r3_rh4", R25, "湿度校准点 40→4%RH（超范围）", [(3, "40", "4", 488.4)], [E("R3", "提示", 3), R7], []),
    ("r3_rh98", R25, "湿度校准点 80→98%RH（上边界，在范围）", [(3, "80", "98", 517.2)], [R7], []),
    # ---------- R4 标准器有效期 ----------
    ("r4_eq", R25, "两台标准器有效期都改成 = 校准日期 2025/11/17（当天有效）",
     [(2, "2026/10/13", "2025/11/17", 579.6), (2, "2026/10/13", "2025/11/17", 616.5)], [R7], []),
    ("r4_first_daybefore", R25, "第一台标准器有效期 → 2025/11/16（早校准日期一天，过期）",
     [(2, "2026/10/13", "2025/11/16", 579.6)], [E("R4", "错误", 2), R7], []),
    ("r4_second_only", R25, "只有第二台（露点）标准器有效期 → 2025/11/16（过期）",
     [(2, "2026/10/13", "2025/11/16", 616.5)], [E("R4", "错误", 2), R7], []),
    ("r4_A_second_only", C1, "版式A 只有第二台（露点）标准器有效期 → 2026/10/27，早于校准日期 2026/10/28",
     [(2, "2027/10/01", "2026/10/27", 616.6)], [E("R4", "错误", 2), R7], []),
    ("r4_second_only_26", R26, "2026 底板版式（露点在前）只有第二台（温度）有效期 → 2026/10/28，早于校准日期 2026/10/29",
     [(2, "2027/10/01", "2026/10/28", 635.2)], [E("R4", "错误", 2), R7], []),
    # ---------- R5 缺失或矛盾 ----------
    ("r5_no_certno_p1", R25, "删第1页证书编号（第2、3页保留）",
     [(1, "C2599000000103", "", None)], [E("R5", "错误|无法核验", 1), R7], ["R5:*"]),
    ("r5_no_certno_all", R25, "三页证书编号全删",
     [(1, "C2599000000103", "", None),
      (2, "证 书 编 号          C2599000000103", "证 书 编 号", None),
      (3, "证 书 编 号          C2599000000103", "证 书 编 号", None)],
     [E("R5", "错误|无法核验", forbid=TRACE_25_T), R7], ["R5:*"]),
    ("r5_no_certno_p2", R25, "只删第2页证书编号（第1、3页保留）：不得把标准器溯源证书号 C2588000000203 当成本页编号",
     [(2, "证 书 编 号          C2599000000103", "证 书 编 号", None)],
     [E("R5", "错误|无法核验", 2, forbid=TRACE_25_T), R7], ["R5:*"]),
    ("r5_page_mismatch", R25, "第3页证书编号末位 3→4（与第1、2页不一致）",
     [(3, "证 书 编 号          C2599000000103", "证 书 编 号          C2599000000104", None)],
     [E("R5", "错误", 3), R7], ["R5:*"]),
    ("r5_unit_F", R25, "第3页温度部分表头单位 ℃→℉（校准点/修正值/允差三列）",
     [(3, "校准点(℃)", "校准点(℉)", None), (3, "修正值(℃)", "修正值(℉)", None), (3, "允差(℃)", "允差(℉)", None)],
     [E("R5", "错误", 3), R7], ["R5:*", "R1:无法核验", "R3:无法核验"]),
    ("r5_missing_corr", R25, "删温度30℃点修正值 +0.4",
     [(3, "+0.4", "", 402.0)], [E("R5", "错误|无法核验", 3), R7], ["R5:*", "R1:无法核验"]),
    ("r5_missing_date", R25, "删第1页校准日期 2025/11/17",
     [(1, "校  准  日  期      2025/11/17", "校  准  日  期", None)],
     [E("R5", "错误|无法核验", 1), R7], ["R5:*", "R4:无法核验"]),
    # ---------- R7 模板残留 ----------
    ("r7_removed", R25, "删第1页占位符 {del_…}：不应再报 R7",
     [(1, "{del_下次校准日期=Y,del_结论=N}", "", None)], [], []),
]

ORIGINALS = {
    C1: ("底板：版式A 无结论列，无问题（考不误报）", [R7], []),
    C2: ("底板：含多处真错的示例", [E("R2", "错误", 2, "35"), E("R1", "错误", 3, "40"),
                     E("R1", "提示", 3, "60"), E("R1", "提示", 3, "80"), R7], []),
    R25: ("底板：单份无规则问题", [R7], []),
    R26: ("底板：单份无规则问题（第3页文字流乱序）", [R7], []),
}

PAIRS = {
    "示例历史对比": {
        "files": [R25, R26],
        "only_rules": ["R6"],
        "desc": "2025→2026 示例报告历史对比：校准点 10→15、制造商 空→示例制造厂、标准器 DP-A01→DP-B01",
        "expect": [E("R6", "提示", hint="15"), E("R6", "提示", hint="示例制造厂"), E("R6", "提示", hint="DP-B01")],
        "allow": [],
    }
}


class NoAlias(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


def spans(page):
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                if s["text"].strip():
                    out.append(s)
    return out


def find_span(page, text, y):
    cands = [s for s in spans(page) if s["text"].strip() == text]
    if y is not None:
        cands = [s for s in cands if abs(s["bbox"][1] - y) < 1.5]
    if len(cands) != 1:
        raise RuntimeError(f"p{page.number + 1} 找到 {len(cands)} 个 '{text}' (y={y})")
    return cands[0]


def mutate(src, dst, edits):
    doc = fitz.open(src)
    by_page = {}
    for pn, old, new, y in edits:
        by_page.setdefault(pn, []).append((old, new, y))
    for pn, items in by_page.items():
        page = doc[pn - 1]
        before = Counter(s["text"].strip() for s in spans(page))
        plan = []
        for old, new, y in items:
            s = find_span(page, old, y)
            r = fitz.Rect(s["bbox"])
            page.add_redact_annot(fitz.Rect(r.x0 - 0.3, r.y0 + 1.0, r.x1 + 0.3, r.y1 - 1.0))
            plan.append((s, old, new))
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
        for s, old, new in plan:
            if new:
                c = s["color"]
                rgb = ((c >> 16) & 255) / 255, ((c >> 8) & 255) / 255, (c & 255) / 255
                if new.isascii():  # 纯西文：Times，与底板西文字宽接近，不跨列
                    page.insert_text(s["origin"], new, fontname="tiro", fontsize=s["size"], color=rgb)
                else:  # 含中文/℃℉：嵌入 PyMuPDF 自带 CJK 字体
                    page.insert_font(fontname="cjkfull", fontbuffer=CJK_BUF)
                    page.insert_text(s["origin"], new, fontname="cjkfull", fontsize=s["size"], color=rgb)
        after = Counter(s["text"].strip() for s in spans(page))
        gone, added = before - after, after - before
        want_gone = Counter(o for _, o, _ in plan)
        want_added = Counter(n for _, _, n in plan if n)
        if gone != want_gone or added != want_added:
            raise RuntimeError(f"{dst} p{pn} 校验失败 gone={dict(gone)} added={dict(added)}")
    doc.subset_fonts()  # 只留用到的字，否则每份带整套 CJK 字体（约 3.5MB）
    doc.save(dst, garbage=4, deflate=True)


def main():
    os.makedirs(OUT, exist_ok=True)
    expected = {"_说明": {
        "格式": "文件名 → {base, desc, edits, expect:[{rule_id, severity, page?, hint?, forbid?, disputed?}], allow:[规则:等级]}",
        "forbid": "命中的发现原文/理由里不该出现的字串（引用了错的数据），违反记「原文不符」",
        "severity": "错误|无法核验 表示两者皆可",
        "allow": "可接受的额外发现，不计误报；R5:* 表示任意等级的 R5",
        "page": "期望页码，不符记「位置不符」不记漏报",
        "disputed": "口径未定，单列不计入命中率",
        "底板": "base=底板 的条目路径在 tests/base/（虚构底板，由 tests/tools/make_base.py 生成）",
    }}
    for name, (desc, exp, allow) in ORIGINALS.items():
        expected[name] = {"base": "底板", "desc": desc, "expect": exp, "allow": allow}
    ok = 0
    for cid, base, desc, edits, exp, allow in CASES:
        fn = f"{cid}.pdf"
        try:
            mutate(os.path.join(SRC, base), os.path.join(OUT, fn), edits)
            ok += 1
        except Exception as e:  # noqa
            print("FAIL", cid, e)
            continue
        expected[fn] = {"base": base, "desc": desc,
                        "edits": [f"p{p} '{o}' → '{n}'" for p, o, n, _ in edits],
                        "expect": exp, "allow": allow}
    expected["_pairs"] = PAIRS
    with open(os.path.join(OUT, "expected.yaml"), "w", encoding="utf-8") as f:
        yaml.dump(expected, f, Dumper=NoAlias, allow_unicode=True, sort_keys=False, width=200)
    print(f"造好 {ok}/{len(CASES)} 份 → {OUT}")
    return 0 if ok == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
