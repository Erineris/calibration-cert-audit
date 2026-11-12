# -*- coding: utf-8 -*-
"""校准证书 PDF 解析：PyMuPDF 取词+坐标，按坐标重排单元格，输出 cert.json 结构。
不做任何判断，只抽取。判断全部在 rules.py。
"""
import re
import os
import pymupdf as fitz  # PyMuPDF >= 1.24.3

CJK = re.compile(r"[一-鿿]")
CERT_NO = re.compile(r"(?<![A-Za-z0-9])C\d{12,16}(?![0-9])")
DATE = re.compile(r"(\d{4})[/\-年.](\d{1,2})[/\-月.](\d{1,2})日?")
NUM = re.compile(r"^[+\-−]?\d+(?:\.\d+)?$")
TOL = re.compile(r"^[±\+/\-]*\s*(\d+(?:\.\d+)?)$")
PLACEHOLDER = re.compile(r"\{del_[^}]*\}")
EMPTY_VALUES = {"--------", "-------", "---", "--", "—", "/", ""}

LINE_TOL = 3.5      # 同一行的 y 容差（pt）
CELL_GAP = 5.0      # 词间距小于此值合并为一个单元格（修复 "+0." "6" 被拆开）


def _norm_num(s):
    return s.replace("−", "-").replace("＋", "+").strip()


def _clean(v):
    if v is None:
        return None
    v = v.strip()
    return None if v in EMPTY_VALUES else v


def parse_date(s):
    """返回 ISO 日期字符串，解析不了返回 None。"""
    if not s:
        return None
    m = DATE.search(s)
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    try:
        import datetime
        return datetime.date(y, mo, d).isoformat()
    except ValueError:
        return None


# ---------------------------------------------------------------- 几何重排

def page_lines(page):
    """把一页的词按 y 聚成行，行内按 x 排序并把相邻词合并成单元格。
    返回 [ {y, cells:[{text,x0,y0,x1,y1}]} ]，按 y 排序。
    """
    words = page.get_text("words")  # x0,y0,x1,y1,word,block,line,wordno
    words.sort(key=lambda w: (round(w[1] / LINE_TOL), w[0]))
    lines = []
    for x0, y0, x1, y1, w, *_ in words:
        yc = (y0 + y1) / 2
        placed = False
        for ln in lines:
            if abs(ln["y"] - yc) <= LINE_TOL:
                ln["words"].append((x0, y0, x1, y1, w))
                ln["y"] = (ln["y"] * (len(ln["words"]) - 1) + yc) / len(ln["words"])
                placed = True
                break
        if not placed:
            lines.append({"y": yc, "words": [(x0, y0, x1, y1, w)]})
    lines.sort(key=lambda l: l["y"])
    for ln in lines:
        ws = sorted(ln["words"], key=lambda t: t[0])
        cells = []
        for x0, y0, x1, y1, w in ws:
            if cells and x0 - cells[-1]["x1"] < CELL_GAP and not (CJK.search(w) and CJK.search(cells[-1]["text"]) and x0 - cells[-1]["x1"] > 2):
                c = cells[-1]
                c["text"] += w
                c["x1"] = max(c["x1"], x1)
                c["y0"] = min(c["y0"], y0)
                c["y1"] = max(c["y1"], y1)
            else:
                cells.append({"text": w, "x0": x0, "y0": y0, "x1": x1, "y1": y1})
        ln["cells"] = cells
        ln["text"] = " ".join(c["text"] for c in cells)
        ln["compact"] = "".join(c["text"] for c in cells)
        del ln["words"]
    return lines


def bbox_of(cells):
    if not cells:
        return None
    return [round(min(c["x0"] for c in cells), 1), round(min(c["y0"] for c in cells), 1),
            round(max(c["x1"] for c in cells), 1), round(max(c["y1"] for c in cells), 1)]


def is_english_line(ln):
    return not CJK.search(ln["compact"]) and not re.search(r"\d", ln["compact"])


# ---------------------------------------------------------------- 字段抽取

KNOWN_LABELS = {"证书编号", "器具名称", "型号规格", "制造商", "出厂编号", "管理编号", "接收日期", "校准日期", "发布日期", "委托单位", "委托单位地址"}


def extract_label_values(lines):
    """页1「标签 值 标签 值」行：连续的单字中文单元格拼成标签；标签命中已知标签后，下一个单元格即值（值可以是中文，如制造商「示例制造厂」）。"""
    out = {}
    for ln in lines:
        label = ""
        expect_value = False
        for c in ln["cells"]:
            t = c["text"]
            if expect_value:
                out.setdefault(label, {"value": t, "bbox": bbox_of([c])})
                label, expect_value = "", False
                continue
            if CJK.fullmatch(t) and len(t) <= 3:
                label += t
                if label in KNOWN_LABELS:
                    expect_value = True
            else:
                label = ""
        if expect_value and label:
            out.setdefault(label, {"value": "", "bbox": bbox_of(ln["cells"])})
        for c in ln["cells"]:
            m = re.match(r"^([\u4e00-\u9fff]{2,12})([0-9/\-]{6,})$", c["text"])
            if m:
                out.setdefault(m.group(1), {"value": m.group(2), "bbox": bbox_of([c])})
    return out


def _col_index(header_xs, x):
    """单元格 x0 落在哪一列：取最后一个 header_x <= x+6 的列。"""
    idx = 0
    for i, hx in enumerate(header_xs):
        if x + 6 >= hx:
            idx = i
    return idx


def extract_points(lines, page_no):
    """校准结果表：温度部分 / 湿度部分，各自一个表头行 + 数据行。"""
    points = []
    quantity = None
    i = 0
    while i < len(lines):
        ln = lines[i]
        if "温度部分" in ln["compact"]:
            quantity = "温度"
        elif "湿度部分" in ln["compact"]:
            quantity = "湿度"
        if "校准点" in ln["compact"] and "修正值" in ln["compact"]:
            headers = []
            for c in ln["cells"]:
                t = c["text"]
                key = None
                if t.startswith("校准点"):
                    key = "point"
                elif t.startswith("修正值"):
                    key = "correction"
                elif t.startswith("允差") or "MPE" in t:
                    key = "tolerance"
                elif t.startswith("结论"):
                    key = "conclusion"
                elif "不确定度" in t:
                    key = "uncertainty"
                headers.append((key, c["x0"], t))
            header_xs = [h[1] for h in headers]
            unit = None
            m = re.search(r"校准点\s*[（(]\s*([^）)]+?)\s*[）)]", ln["compact"])
            if m:
                unit = m.group(1).replace("°C", "℃").replace("℃", "℃")
            has_conclusion = any(h[0] == "conclusion" for h in headers)
            q = quantity or ("温度" if unit == "℃" else "湿度" if unit and "RH" in unit else None)
            j = i + 1
            while j < len(lines):
                row = lines[j]
                if CJK.search(row["compact"]) and not re.match(r"^[+\-−]?\d", row["cells"][0]["text"]):
                    break  # 下一段落（湿度部分 / 备注）
                if is_english_line(row):
                    j += 1
                    continue
                cols = {}
                for c in row["cells"]:
                    key = headers[_col_index(header_xs, c["x0"])][0]
                    if key:
                        cols.setdefault(key, []).append(c)
                pt_cells = cols.get("point", [])
                if not pt_cells or not NUM.match(_norm_num("".join(c["text"] for c in pt_cells))):
                    j += 1
                    continue
                def val(key):
                    cs = cols.get(key, [])
                    return "".join(c["text"] for c in cs) if cs else None
                raw_point, raw_corr, raw_tol, raw_con = val("point"), val("correction"), val("tolerance"), val("conclusion")
                corr = None
                if raw_corr and NUM.match(_norm_num(raw_corr)):
                    corr = float(_norm_num(raw_corr))
                tol = None
                if raw_tol:
                    mt = TOL.match(_norm_num(raw_tol).replace(" ", ""))
                    if mt:
                        tol = float(mt.group(1))
                concl = None
                if has_conclusion:
                    concl = _clean(raw_con)
                points.append({
                    "quantity": q, "point": float(_norm_num(raw_point)), "unit": unit,
                    "correction": corr, "tolerance": tol, "conclusion": concl,
                    "has_conclusion_col": has_conclusion,
                    "raw": {"point": raw_point, "correction": raw_corr, "tolerance": raw_tol, "conclusion": raw_con},
                    "page": page_no,
                    "bbox": bbox_of(cols.get("point", []) + cols.get("correction", []) + cols.get("tolerance", []) + cols.get("conclusion", [])),
                })
                j += 1
            i = j
            continue
        i += 1
    return points


def extract_env(lines, page_no):
    env = {"temp_c": None, "rh": None, "page": None, "bbox": None, "raw": None}
    for ln in lines:
        if re.search(r"温度\s*[:：]", ln["compact"]) and ("湿度" in ln["compact"] or "环境" in ln["compact"] or "℃" in ln["compact"]):
            mt = re.search(r"温度\s*[:：]\s*([+\-]?\d+(?:\.\d+)?)\s*(℃|°C|C)?", ln["compact"])
            mh = re.search(r"湿度\s*[:：]\s*(\d+(?:\.\d+)?)\s*(%RH|%)?", ln["compact"])
            if mt:
                env["temp_c"] = float(mt.group(1))
                env["temp_unit"] = mt.group(2)
            if mh:
                env["rh"] = float(mh.group(1))
                env["rh_unit"] = mh.group(2)
            env["page"] = page_no
            env["bbox"] = bbox_of(ln["cells"])
            env["raw"] = ln["text"]
            break
    return env


def extract_standards(lines, page_no):
    """标准器表：表头含「编号」「有效期」。按列 x 归属，遇到编号列有值就产出一台。"""
    stds = []
    for i, ln in enumerate(lines):
        if "有效期" in ln["compact"] and ("编号" in ln["compact"] or "名称" in ln["compact"]):
            cols = {}
            for c in ln["cells"]:
                t = c["text"]
                if t.startswith("名称"):
                    cols["name"] = c["x0"]
                elif t.startswith("编号"):
                    cols["id"] = c["x0"]
                elif t.startswith("测量范围"):
                    cols["range"] = c["x0"]
                elif "证书号" in t:
                    cols["cert"] = c["x0"]
                elif t.startswith("有效期"):
                    cols["due"] = c["x0"]
            if "id" not in cols or "due" not in cols:
                continue
            name_buf, pending = [], None
            j = i + 1
            while j < len(lines):
                row = lines[j]
                txt = row["compact"]
                if re.search(r"校准结果|备注|以下空白|总部地址|实验室地址", txt) or (CJK.search(txt) and row["cells"][0]["x0"] < cols.get("name", 0) - 10 and re.match(r"^\d+\.", txt)):
                    break
                for c in row["cells"]:
                    x = c["x0"]
                    t = c["text"]
                    if "name" in cols and x < cols["id"] - 5:
                        if CJK.search(t):
                            name_buf.append(t)
                    elif x >= cols["id"] - 5 and x < cols.get("range", cols["id"] + 40) - 5:
                        if re.match(r"^[0-9A-Za-z\-]{3,}$", t):
                            pending = {"name": "".join(name_buf), "id": t, "valid_until": None,
                                       "valid_until_raw": None, "page": page_no, "bbox": bbox_of([c]), "cells": [c]}
                            name_buf = []
                            stds.append(pending)
                    elif x >= cols["due"] - 8:
                        d = parse_date(t)
                        if d and pending is not None and pending["valid_until"] is None:
                            pending["valid_until"] = d
                            pending["valid_until_raw"] = t
                            pending["cells"].append(c)
                            pending["bbox"] = bbox_of(pending["cells"])
                j += 1
            for s in stds:
                s.pop("cells", None)
            break
    return stds


def find_placeholders(lines, page_no):
    out = []
    for ln in lines:
        for c in ln["cells"]:
            if PLACEHOLDER.search(c["text"]):
                out.append({"page": page_no, "bbox": bbox_of([c]), "raw": c["text"]})
    return out


# ---------------------------------------------------------------- 主入口

def parse_pdf(path):
    doc = fitz.open(path)
    cert = {
        "file": os.path.basename(path), "path": os.path.abspath(path),
        "cert_no": None, "pages": [], "instrument": {"name": None, "model": None, "serial_no": None, "mgmt_no": None, "maker": None},
        "calib_date": None, "calib_date_raw": None, "issue_date": None, "next_calib_date": None,
        "env": {"temp_c": None, "rh": None, "page": None, "bbox": None},
        "standards": [], "points": [], "placeholders": [],
        "fields_bbox": {}, "page_count": len(doc),
        "extract_check": {"llm_agree": None, "diffs": []},
        "page_texts": [],
    }
    for pi, page in enumerate(doc):
        pno = pi + 1
        lines = page_lines(page)
        cert["page_texts"].append("\n".join(l["text"] for l in lines))
        # 证书编号：每页都找
        pcert = None
        for ln in lines:
            if "证书编号" in ln["compact"] or "证 书 编 号" in ln["text"]:
                m = CERT_NO.search(ln["compact"])
                if m:
                    pcert = m.group(0)
                    for c in ln["cells"]:
                        if m.group(0) in c["text"]:
                            cert["fields_bbox"].setdefault("cert_no", {"page": pno, "bbox": bbox_of([c])})
                    break
        # 不做全页兜底搜索：标准器表的「证书号/溯源机构」列也是 C 开头编号，会误抓
        cert["pages"].append({"page": pno, "cert_no": pcert})
        if pno == 1 and pcert:
            cert["cert_no"] = pcert

        lv = extract_label_values(lines)
        def take(*labels):
            for lb in labels:
                if lb in lv:
                    return lv[lb]
            return None
        if pno == 1 or cert["instrument"]["name"] is None:
            for key, labels in [("name", ("器具名称",)), ("model", ("型号规格",)), ("maker", ("制造商",)),
                                ("serial_no", ("出厂编号",)), ("mgmt_no", ("管理编号",))]:
                v = take(*labels)
                if v and cert["instrument"][key] is None:
                    cert["instrument"][key] = _clean(v["value"])
                    cert["fields_bbox"][key] = {"page": pno, "bbox": v["bbox"], "raw": v["value"]}
            v = take("校准日期")
            if v and cert["calib_date"] is None:
                cert["calib_date"] = parse_date(v["value"])
                cert["calib_date_raw"] = v["value"]
                cert["fields_bbox"]["calib_date"] = {"page": pno, "bbox": v["bbox"], "raw": v["value"]}
            v = take("发布日期")
            if v:
                cert["issue_date"] = parse_date(v["value"])
            v = take("建议下次校准日期")
            if v:
                cert["next_calib_date"] = parse_date(v["value"])

        env = extract_env(lines, pno)
        if env["temp_c"] is not None or env["rh"] is not None:
            if cert["env"].get("page") is None:
                cert["env"] = env
        cert["standards"].extend(extract_standards(lines, pno))
        cert["points"].extend(extract_points(lines, pno))
        cert["placeholders"].extend(find_placeholders(lines, pno))
    doc.close()
    cert["layout"] = "B" if any(p.get("has_conclusion_col") for p in cert["points"]) else "A"
    return cert


if __name__ == "__main__":
    import sys, json
    for p in sys.argv[1:]:
        c = parse_pdf(p)
        c.pop("page_texts", None)
        print(json.dumps(c, ensure_ascii=False, indent=1))
