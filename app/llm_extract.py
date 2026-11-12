# -*- coding: utf-8 -*-
"""双路抽取第二路：OpenAI 兼容接口抽取关键字段，与程序解析对比。
环境变量：LLM_BASE_URL（默认 OPENAI_BASE_URL 或 https://api.openai.com/v1）、LLM_API_KEY（或 OPENAI_API_KEY）、LLM_MODEL。
没有 key / 请求失败 / 超时 → 返回 (None, [])，调用方把 extract_check.llm_agree 置 null，绝不阻塞主流程。
AI 只抽取、不判断 Pass/Fail。
"""
import os
import json
import urllib.request

PROMPT = """你是计量证书的数据抽取员。只抽取、不判断。从下面校准证书文本中抽取 JSON，字段：
{"cert_no": 证书编号, "calib_date": "YYYY-MM-DD", "mgmt_no": 管理编号或null, "serial_no": 出厂编号或null,
 "env": {"temp_c": 环境温度数值, "rh": 环境相对湿度数值},
 "standards": [{"id": 标准器编号, "valid_until": "YYYY-MM-DD"}],
 "points": [{"quantity": "温度"或"湿度", "point": 校准点数值, "correction": 修正值数值, "tolerance": 允差数值(去掉±), "conclusion": 结论原文或null}]}
注意：环境温度在「校准地点、环境条件」栏，不是校准点。只输出 JSON，不要解释。
证书文本：
"""


def _cfg():
    key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base = (os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("LLM_MODEL") or "gpt-4o-mini"
    return key, base, model


def available():
    return bool(_cfg()[0])


def llm_extract(page_texts, timeout=25):
    """返回 dict 或 None。"""
    key, base, model = _cfg()
    if not key:
        return None
    body = {"model": model, "temperature": 0,
            "messages": [{"role": "user", "content": PROMPT + "\n\n".join(page_texts)[:12000]}]}
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        txt = data["choices"][0]["message"]["content"].strip()
        if txt.startswith("```"):
            txt = txt.strip("`")
            txt = txt[txt.find("{"):txt.rfind("}") + 1]
        return json.loads(txt)
    except Exception as e:  # 任何失败都不阻塞
        return {"_error": str(e)}


def _num(v):
    try:
        return round(float(v), 4)
    except Exception:
        return None


def compare(cert, llm):
    """对比程序解析与 LLM 结果，返回 (agree, diffs)。llm 为 None/出错 → (None, [])。"""
    if not llm or "_error" in (llm or {}):
        return None, [{"field": "llm", "program": "", "llm": llm.get("_error"), "page": None, "bbox": None}] if llm else []
    diffs = []
    fb = cert.get("fields_bbox", {})

    def d(field, prog, got, page=None, bbox=None):
        diffs.append({"field": field, "program": prog, "llm": got, "page": page, "bbox": bbox})

    if (llm.get("cert_no") or None) != cert.get("cert_no"):
        d("cert_no", cert.get("cert_no"), llm.get("cert_no"), 1, (fb.get("cert_no") or {}).get("bbox"))
    if (llm.get("calib_date") or None) != cert.get("calib_date"):
        d("calib_date", cert.get("calib_date"), llm.get("calib_date"), 1, (fb.get("calib_date") or {}).get("bbox"))
    env = cert.get("env") or {}
    le = llm.get("env") or {}
    if _num(le.get("temp_c")) != _num(env.get("temp_c")):
        d("env.temp_c", env.get("temp_c"), le.get("temp_c"), env.get("page"), env.get("bbox"))
    if _num(le.get("rh")) != _num(env.get("rh")):
        d("env.rh", env.get("rh"), le.get("rh"), env.get("page"), env.get("bbox"))
    ps = sorted({s.get("id") for s in cert.get("standards", []) if s.get("id")})
    ls = sorted({s.get("id") for s in (llm.get("standards") or []) if s.get("id")})
    if ps != ls:
        d("standards", "/".join(ps), "/".join(ls), 2, None)
    for p in cert.get("points", []):
        m = next((x for x in (llm.get("points") or []) if x.get("quantity") == p.get("quantity") and _num(x.get("point")) == _num(p.get("point"))), None)
        label = f"{p.get('quantity')}@{p.get('point'):g}"
        if m is None:
            d(f"points.{label}", f"{p.get('correction')}/{p.get('tolerance')}", "未抽到", p["page"], p["bbox"])
            continue
        if _num(m.get("correction")) != _num(p.get("correction")):
            d(f"points.{label}.correction", p.get("correction"), m.get("correction"), p["page"], p["bbox"])
        if _num(m.get("tolerance")) != _num(p.get("tolerance")):
            d(f"points.{label}.tolerance", p.get("tolerance"), m.get("tolerance"), p["page"], p["bbox"])
        if (m.get("conclusion") or None) != (p.get("conclusion") or None):
            d(f"points.{label}.conclusion", p.get("conclusion"), m.get("conclusion"), p["page"], p["bbox"])
    return (len(diffs) == 0), diffs


def check(cert):
    """就地写入 cert['extract_check']。"""
    if not available():
        cert["extract_check"] = {"llm_agree": None, "diffs": [], "note": "未配置 LLM_API_KEY，跳过第二路抽取"}
        return cert
    llm = llm_extract(cert.get("page_texts", []))
    agree, diffs = compare(cert, llm)
    if llm and "_error" in llm:
        cert["extract_check"] = {"llm_agree": None, "diffs": [], "note": f"大模型调用失败：{llm['_error'][:200]}"}
    else:
        cert["extract_check"] = {"llm_agree": agree, "diffs": diffs, "model": _cfg()[2]}
    return cert
