# -*- coding: utf-8 -*-
"""规则引擎：读 rules.yaml，对 cert.json 结构产出 findings。所有阈值来自 yaml，代码里不写死。"""
import os
import re
import datetime
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RULES = os.path.join(HERE, "rules.yaml")
SEVERITY_ORDER = {"错误": 0, "无法核验": 1, "提示": 2}


def load_rules(path=None):
    with open(path or DEFAULT_RULES, encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_rules(rules, path=None):
    with open(path or DEFAULT_RULES, "w", encoding="utf-8") as f:
        yaml.safe_dump(rules, f, allow_unicode=True, sort_keys=False)


class Collector:
    def __init__(self, cert, rules):
        self.cert = cert
        self.rules = rules["rules"]
        self.items = []

    def add(self, rule_id, severity, reason, suggestion, page=None, bbox=None, raw=None, field=None):
        r = self.rules.get(rule_id, {})
        self.items.append({
            "id": None,
            "rule_id": rule_id,
            "rule_name": r.get("name", rule_id),
            "severity": severity,
            "cert_no": self.cert.get("cert_no"),
            "file": self.cert.get("file"),
            "page": page,
            "bbox": bbox,
            "raw": raw if raw is not None else "",
            "field": field,
            "reason": reason,
            "suggestion": suggestion,
            "source": r.get("source", ""),
            "review": {"status": "待审", "by": "", "note": "", "at": ""},
        })

    def enabled(self, rid):
        return self.rules.get(rid, {}).get("enabled", True)


def _fmt(v, unit=""):
    if v is None:
        return "空"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return f"{v}{unit}"


def _signed(v):
    if v is None:
        return "空"
    s = f"{v:+g}"
    return s


def _date(s):
    try:
        return datetime.date.fromisoformat(s)
    except Exception:
        return None


# ------------------------------------------------------------------ 单份证书规则

def rule_R1(c, cfg, col):
    for p in c["points"]:
        unit = p.get("unit") or ""
        raw = f"{p['quantity'] or ''}校准点 {_fmt(p['point'])}{unit}：修正值 {p['raw'].get('correction')}，允差 {p['raw'].get('tolerance')}，结论 {p['raw'].get('conclusion') or '（无）'}"
        if p["correction"] is None or p["tolerance"] is None:
            continue  # 缺失由 R5 报
        over = abs(p["correction"]) > p["tolerance"] if cfg.get("equal_is_pass", True) else abs(p["correction"]) >= p["tolerance"]
        concl = p.get("conclusion")
        is_pass = concl in cfg.get("pass_words", []) if concl else None
        is_fail = concl in cfg.get("fail_words", []) if concl else None
        if over and is_pass:
            col.add("R1", cfg.get("severity_conflict", "错误"),
                    f"|{_signed(p['correction'])}| = {abs(p['correction']):g} > 允差 {p['tolerance']:g}，已超差，但结论写「{concl}」，结论与数据冲突",
                    "复核原始记录；若数据无误，结论应改为 Fail 并重新出证",
                    page=p["page"], bbox=p["bbox"], raw=raw, field="points")
        elif over and is_fail:
            col.add("R1", cfg.get("severity_over_marked_fail", "提示"),
                    f"|{_signed(p['correction'])}| = {abs(p['correction']):g} > 允差 {p['tolerance']:g}，该点超差，证书已正确标 Fail",
                    "无需改证书；提醒客户该仪器此点不合格",
                    page=p["page"], bbox=p["bbox"], raw=raw, field="points")
        elif over and concl is None:
            if p.get("has_conclusion_col"):
                col.add("R1", "提示",
                        f"|{_signed(p['correction'])}| = {abs(p['correction']):g} > 允差 {p['tolerance']:g}，该点超差；结论栏未填（未填不算错）",
                        "提醒客户该点超差；是否补结论由审核员定",
                        page=p["page"], bbox=p["bbox"], raw=raw, field="points")
            else:
                col.add("R1", "提示",
                        f"|{_signed(p['correction'])}| = {abs(p['correction']):g} > 允差 {p['tolerance']:g}，该点超差，证书无结论列，不构成结论冲突",
                        "提醒客户该点超差",
                        page=p["page"], bbox=p["bbox"], raw=raw, field="points")
        elif (not over) and is_fail:
            col.add("R1", cfg.get("severity_conflict", "错误"),
                    f"|{_signed(p['correction'])}| = {abs(p['correction']):g} ≤ 允差 {p['tolerance']:g}，未超差，但结论写「{concl}」，结论与数据冲突",
                    "复核原始记录；若数据无误，结论应改为 Pass",
                    page=p["page"], bbox=p["bbox"], raw=raw, field="points")
        elif concl and is_pass is False and is_fail is False:
            col.add("R1", "无法核验",
                    f"结论「{concl}」不是已知的合格/不合格词，无法判断是否冲突",
                    "人工确认结论含义，或把该词加入规则 pass_words / fail_words",
                    page=p["page"], bbox=p["bbox"], raw=raw, field="points")


def rule_R2(c, cfg, col):
    env = c.get("env") or {}
    page, bbox, raw = env.get("page"), env.get("bbox"), env.get("raw")
    if env.get("temp_c") is None and env.get("rh") is None:
        col.add("R2", "无法核验", "未识别到「校准地点、环境条件」栏的温湿度", "人工查看第 2 页环境条件栏", page=page or 2, bbox=bbox, raw=raw or "", field="env")
        return
    tmin, tmax = cfg.get("temp_min", 20), cfg.get("temp_max", 30)
    inc = cfg.get("temp_inclusive", True)
    t = env.get("temp_c")
    if t is None:
        col.add("R2", "无法核验", "环境温度未识别", "人工查看环境条件栏", page=page, bbox=bbox, raw=raw, field="env")
    else:
        bad = (t < tmin or t > tmax) if inc else (t <= tmin or t >= tmax)
        if bad:
            col.add("R2", cfg.get("severity", "错误"),
                    f"环境温度 {t:g}℃ 超出规定范围 {tmin:g}–{tmax:g}℃（含两端）",
                    "核实校准现场环境记录；环境不满足时校准结果无效，应重新校准",
                    page=page, bbox=bbox, raw=raw, field="env")
    rh = env.get("rh")
    rmax = cfg.get("rh_max", 85)
    if rh is None:
        col.add("R2", "无法核验", "环境湿度未识别", "人工查看环境条件栏", page=page, bbox=bbox, raw=raw, field="env")
    else:
        bad = rh > rmax if cfg.get("rh_max_inclusive", False) else rh >= rmax
        if bad:
            col.add("R2", cfg.get("severity", "错误"),
                    f"环境湿度 {rh:g}%RH 不满足 < {rmax:g}%RH",
                    "核实校准现场环境记录；环境不满足时应重新校准",
                    page=page, bbox=bbox, raw=raw, field="env")


def rule_R3(c, cfg, col):
    ranges = cfg.get("ranges", {})
    for p in c["points"]:
        q = p.get("quantity")
        r = ranges.get(q)
        if not r or p.get("point") is None:
            continue
        v = p["point"]
        if v < r["min"] or v > r["max"]:
            col.add("R3", cfg.get("severity", "提示"),
                    f"{q}校准点 {v:g}{r.get('unit','')} 超出常规范围 {r['min']:g}–{r['max']:g}{r.get('unit','')}",
                    "人工核验该校准点是否为客户特殊要求及标准器能力是否覆盖",
                    page=p["page"], bbox=p["bbox"], raw=f"校准点 {p['raw'].get('point')}{p.get('unit') or ''}", field="points")


def rule_R4(c, cfg, col):
    cd = _date(c.get("calib_date") or "")
    if cd is None:
        col.add("R4", "无法核验", "校准日期缺失或无法解析，无法判断标准器是否过期", "人工核对第 1 页校准日期",
                page=1, bbox=(c.get("fields_bbox", {}).get("calib_date") or {}).get("bbox"), raw=c.get("calib_date_raw") or "", field="calib_date")
        return
    if not c.get("standards"):
        col.add("R4", "无法核验", "未识别到标准器表", "人工查看第 2 页标准器表", page=2, field="standards")
        return
    for s in c["standards"]:
        due = _date(s.get("valid_until") or "")
        raw = f"{s.get('name') or ''} {s.get('id') or ''} 有效期 {s.get('valid_until_raw') or '空'}"
        if due is None:
            col.add("R4", "无法核验", f"标准器 {s.get('id')} 的有效期缺失或无法解析", "人工核对标准器有效期",
                    page=s.get("page"), bbox=s.get("bbox"), raw=raw, field="standards")
            continue
        expired = cd > due if cfg.get("due_date_inclusive", True) else cd >= due
        if expired:
            col.add("R4", cfg.get("severity", "错误"),
                    f"标准器 {s.get('id')} 有效期 {due.isoformat()} 早于校准日期 {cd.isoformat()}，校准时标准器已过期",
                    "确认标准器溯源证书是否已续；过期标准器出具的结果不应发证",
                    page=s.get("page"), bbox=s.get("bbox"), raw=raw, field="standards")


def rule_R5(c, cfg, col):
    fb = c.get("fields_bbox", {})
    # 编号
    if not c.get("cert_no"):
        col.add("R5", cfg.get("severity_missing", "无法核验"), "证书编号未识别", "人工核对证书编号", page=1, field="cert_no")
    nos = [(p["page"], p.get("cert_no")) for p in c.get("pages", [])]
    missing = [pg for pg, n in nos if not n]
    if missing:
        col.add("R5", cfg.get("severity_missing", "无法核验"), f"第 {', '.join(map(str, missing))} 页未识别到证书编号，无法确认各页一致", "人工核对该页编号",
                page=missing[0], field="cert_no")
    distinct = {n for _, n in nos if n}
    if len(distinct) > 1:
        bad = [f"第{pg}页 {n}" for pg, n in nos if n and n != c.get("cert_no")]
        col.add("R5", cfg.get("severity_cert_no_mismatch", "错误"), f"各页证书编号不一致：{'；'.join(bad)}（首页 {c.get('cert_no')}）",
                "检查是否装订错页或出证系统拼页错误", page=int(bad[0][1:bad[0].index('页')]), raw="；".join(bad), field="cert_no")
    # 日期
    if not c.get("calib_date"):
        col.add("R5", cfg.get("severity_missing", "无法核验"), f"校准日期缺失或无法解析（原文「{c.get('calib_date_raw') or ''}」）", "人工核对第 1 页校准日期",
                page=1, bbox=(fb.get("calib_date") or {}).get("bbox"), raw=c.get("calib_date_raw") or "", field="calib_date")
    # 校准点
    if not c.get("points"):
        col.add("R5", cfg.get("severity_missing", "无法核验"), "未识别到任何校准点数据", "人工查看校准结果页", page=3, field="points")
    units = cfg.get("units", {})
    for p in c["points"]:
        raw = f"{p.get('quantity') or ''}校准点 {p['raw'].get('point')}：修正值 {p['raw'].get('correction')}，允差 {p['raw'].get('tolerance')}"
        if p.get("correction") is None:
            col.add("R5", cfg.get("severity_missing", "无法核验"), f"校准点 {_fmt(p['point'])} 的修正值缺失或无法识别（原文「{p['raw'].get('correction') or ''}」）",
                    "人工核对该行修正值", page=p["page"], bbox=p["bbox"], raw=raw, field="points")
        if p.get("tolerance") is None:
            col.add("R5", cfg.get("severity_missing", "无法核验"), f"校准点 {_fmt(p['point'])} 的允差缺失或无法识别（原文「{p['raw'].get('tolerance') or ''}」）",
                    "人工核对该行允差；允差通常由客户提供", page=p["page"], bbox=p["bbox"], raw=raw, field="points")
        q = p.get("quantity")
        want = units.get(q)
        if want:
            got = p.get("unit")
            if not got:
                col.add("R5", cfg.get("severity_missing", "无法核验"), f"{q}部分表头未识别到单位", "人工核对表头单位", page=p["page"], bbox=p["bbox"], raw=raw, field="points")
            elif got.replace("°C", "℃") != want:
                col.add("R5", cfg.get("severity_unit", "错误"), f"{q}部分单位为「{got}」，应为「{want}」", "更正单位后重新出证",
                        page=p["page"], bbox=p["bbox"], raw=raw, field="points")


def rule_R7(c, cfg, col):
    pat = re.compile(cfg.get("pattern", r"\{del_[^}]*\}"))
    for ph in c.get("placeholders", []):
        if pat.search(ph["raw"]):
            col.add("R7", cfg.get("severity", "提示"), f"正文出现模板占位符「{ph['raw']}」，应为出证系统模板未清理",
                    "反馈给出证系统维护方修模板；与校准人员无关", page=ph["page"], bbox=ph["bbox"], raw=ph["raw"], field="template")


def rule_R0(c, cfg, col):
    ec = c.get("extract_check") or {}
    if ec.get("llm_agree") is False:
        for d in ec.get("diffs", []):
            col.add("R0", cfg.get("severity", "无法核验"),
                    f"字段「{d.get('field')}」程序解析为「{d.get('program')}」，大模型抽取为「{d.get('llm')}」，两路不一致",
                    "人工核对原文后确认该字段；相关规则结论以人工为准",
                    page=d.get("page"), bbox=d.get("bbox"), raw=str(d.get("program")), field=d.get("field"))


def is_certificate(cert):
    """抬头有「校准证书」或抓到了证书编号/校准点，才当作证书审核。"""
    first = (cert.get("page_texts") or [""])[0].replace(" ", "")
    return bool(cert.get("cert_no")) or bool(cert.get("points")) or ("校准证书" in first) or ("CalibrationCertificate" in first)


def run_rules(cert, rules=None):
    rules = rules or load_rules()
    col = Collector(cert, rules)
    if not is_certificate(cert):
        col.add("R5", "无法核验", "该文件没有「校准证书」抬头、证书编号和校准点数据，不是校准证书，未审核",
                "确认是否误传文件；本系统只审核校准证书", page=1, raw=(cert.get("page_texts") or [""])[0][:80].replace("\n", " "), field="document")
        cert["not_certificate"] = True
        return finalize(col.items)
    for rid, fn in [("R5", rule_R5), ("R1", rule_R1), ("R2", rule_R2), ("R3", rule_R3), ("R4", rule_R4), ("R7", rule_R7), ("R0", rule_R0)]:
        if col.enabled(rid):
            fn(cert, rules["rules"].get(rid, {}), col)
    return finalize(col.items)


def finalize(items, prefix="F"):
    items.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f.get("page") or 0, f["rule_id"]))
    for i, f in enumerate(items, 1):
        f["id"] = f"{prefix}{i:03d}"
    return items


# ------------------------------------------------------------------ 历史对比 R6

def match_key(cert):
    ins = cert.get("instrument", {})
    return ins.get("mgmt_no") or ins.get("serial_no")


def compare_history(old, new, rules=None):
    """old/new 两份同一仪器的 cert；返回挂在 new 上的 R6 提示。"""
    rules = rules or load_rules()
    cfg = rules["rules"].get("R6", {})
    if not cfg.get("enabled", True):
        return []
    col = Collector(new, rules)
    sev = cfg.get("severity", "提示")
    fields = set(cfg.get("compare_fields", []))
    on, nn = old.get("cert_no"), new.get("cert_no")
    base = f"对比上一份证书 {on}（{old.get('calib_date')}）"
    fb = new.get("fields_bbox", {})
    for key, label in [("maker", "制造商"), ("model", "型号规格"), ("name", "器具名称"), ("serial_no", "出厂编号")]:
        if key not in fields:
            continue
        a, b = old["instrument"].get(key), new["instrument"].get(key)
        if a != b:
            col.add("R6", sev, f"{base}：{label}由「{a or '空'}」变为「{b or '空'}」",
                    "确认是否同一台仪器、是否补录信息；无误则在档案中更新",
                    page=(fb.get(key) or {}).get("page", 1), bbox=(fb.get(key) or {}).get("bbox"), raw=f"{a or '空'} → {b or '空'}", field=key)
    if "standards" in fields:
        a = sorted({s.get("id") for s in old.get("standards", []) if s.get("id")})
        b = sorted({s.get("id") for s in new.get("standards", []) if s.get("id")})
        if a != b:
            pg = new["standards"][0]["page"] if new.get("standards") else 2
            bb = None
            if new.get("standards"):
                xs = [s["bbox"] for s in new["standards"] if s.get("bbox")]
                if xs:
                    bb = [min(x[0] for x in xs), min(x[1] for x in xs), max(x[2] for x in xs), max(x[3] for x in xs)]
            col.add("R6", sev, f"{base}：标准器由 {'/'.join(a) or '空'} 变为 {'/'.join(b) or '空'}",
                    "正常更换标准器属常见情况；确认新标准器在有效期内即可",
                    page=pg, bbox=bb, raw=f"{'/'.join(a)} → {'/'.join(b)}", field="standards")
    if "points" in fields or "tolerance" in fields:
        for q in ("温度", "湿度"):
            po = [p for p in old.get("points", []) if p.get("quantity") == q]
            pn = [p for p in new.get("points", []) if p.get("quantity") == q]
            if not po or not pn:
                continue
            unit = pn[0].get("unit") or ""
            so, sn = [p["point"] for p in po], [p["point"] for p in pn]
            if "points" in fields and so != sn:
                if len(so) == len(sn):
                    changed = [(x, y) for x, y in zip(so, sn) if x != y]
                    desc = "、".join(f"{_fmt(x)}→{_fmt(y)}" for x, y in changed)
                    bb = next((p["bbox"] for p in pn if p["point"] in [y for _, y in changed]), None)
                else:
                    desc = f"{'/'.join(_fmt(x) for x in so)} → {'/'.join(_fmt(x) for x in sn)}"
                    bb = pn[0]["bbox"]
                col.add("R6", sev, f"{base}：{q}校准点由 {'/'.join(_fmt(x) for x in so)} 改为 {'/'.join(_fmt(x) for x in sn)}{unit}（{desc}），不可直接逐点比较",
                        "确认是否客户要求变更校准点；变更点的历史漂移曲线需重新起算",
                        page=pn[0]["page"], bbox=bb, raw=desc, field="points")
            if "tolerance" in fields:
                for p in pn:
                    m = next((x for x in po if x["point"] == p["point"]), None)
                    if m and m.get("tolerance") != p.get("tolerance"):
                        col.add("R6", sev, f"{base}：{q}校准点 {_fmt(p['point'])}{unit} 允差由 ±{_fmt(m.get('tolerance'))} 变为 ±{_fmt(p.get('tolerance'))}",
                                "允差由客户提供；确认客户是否变更要求", page=p["page"], bbox=p["bbox"],
                                raw=f"±{_fmt(m.get('tolerance'))} → ±{_fmt(p.get('tolerance'))}", field="tolerance")
    return finalize(col.items, prefix="H")


def drift_table(old, new):
    """供档案/界面用：逐点修正值变化（不产生 finding）。"""
    rows = []
    for p in new.get("points", []):
        m = next((x for x in old.get("points", []) if x.get("quantity") == p.get("quantity") and x.get("point") == p.get("point")), None)
        rows.append({
            "quantity": p.get("quantity"), "point": p.get("point"), "unit": p.get("unit"),
            "old": m.get("correction") if m else None, "new": p.get("correction"), "tolerance": p.get("tolerance"),
            "delta": (p["correction"] - m["correction"]) if (m and m.get("correction") is not None and p.get("correction") is not None) else None,
            "margin": (p["tolerance"] - abs(p["correction"])) if (p.get("tolerance") is not None and p.get("correction") is not None) else None,
        })
    return rows
