# -*- coding: utf-8 -*-
"""审核台（首页）：python -m streamlit run app/审核台.py
页 1 审核台：上传 PDF → 左原文红框 / 右发现列表（按严重度）→ 确认/驳回+理由 → 导出 Excel
页 2 规则：查看/修改 rules.yaml 并保存
页 3 历史对比：选两份证书并排 + 漂移表
"""
import os
import sys
import io
import json
import tempfile
import datetime

import pymupdf as fitz
import yaml
import streamlit as st
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from app.cli import analyze  # noqa: E402
from app.rules import load_rules, save_rules, drift_table, SEVERITY_ORDER, DEFAULT_RULES, compare_history  # noqa: E402
from app import theme  # noqa: E402

st.set_page_config(page_title="校准证书审核台", layout="wide", page_icon="📋")
theme.apply()
SEV_COLOR = {"错误": "#d93025", "无法核验": "#e37400", "提示": "#1a73e8"}


# ------------------------------------------------------------------ 工具

def render_page(pdf_path, page_no, boxes, zoom=1.6, focus=None, clip_focus=False):
    """渲染某页为 PNG。boxes = [(bbox, color, label)]；focus = 选中的 bbox（PDF 点坐标，与 get_text 同一坐标系），
    画粗红框+半透明填充；clip_focus=True 时只输出 focus 周围的局部放大图。"""
    doc = fitz.open(pdf_path)
    page = doc[page_no - 1]
    shape = page.new_shape()
    for bbox, color, label in boxes:
        if not bbox:
            continue
        r = fitz.Rect(*bbox) + (-3, -2, 3, 2)
        shape.draw_rect(r)
        shape.finish(color=color, width=1.2)
        if label:
            shape.insert_text((r.x1 + 3, r.y1 - 1), label, fontsize=8, color=color, fontname="helv")
    if focus:
        r = fitz.Rect(*focus) + (-5, -4, 5, 4)
        shape.draw_rect(r)
        shape.finish(color=(0.85, 0.0, 0.0), fill=(1.0, 0.85, 0.0), fill_opacity=0.28, width=3)
    shape.commit()
    clip = None
    if clip_focus and focus:
        f = fitz.Rect(*focus)
        clip = fitz.Rect(max(0, f.x0 - 60), max(0, f.y0 - 45), min(page.rect.x1, f.x1 + 60), min(page.rect.y1, f.y1 + 45))
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
    png = pix.tobytes("png")
    doc.close()
    return png


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def export_excel(certs, reviewer):
    wb = Workbook()
    ws = wb.active
    ws.title = "审核记录"
    head = ["证书文件", "证书编号", "仪器", "管理编号", "发现ID", "规则", "规则名", "严重度", "页", "原文数据", "原因", "建议", "出处", "审核状态", "审核人", "审核意见", "审核时间"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E79")
        c.alignment = Alignment(wrap_text=True, vertical="top")
    fill = {"错误": "F8D7DA", "无法核验": "FFE5B4", "提示": "D6E4FF"}
    for cert in certs:
        for f in cert["findings"]:
            rv = f["review"]
            ws.append([cert["file"], cert["cert_no"], f"{cert['instrument'].get('name') or ''} {cert['instrument'].get('model') or ''}",
                       cert["instrument"].get("mgmt_no") or cert["instrument"].get("serial_no") or "",
                       f["id"], f["rule_id"], f.get("rule_name", ""), f["severity"], f.get("page"), f.get("raw", ""),
                       f["reason"], f["suggestion"], f.get("source", ""), rv["status"], rv["by"], rv["note"], rv["at"]])
            row = ws.max_row
            ws.cell(row=row, column=8).fill = PatternFill("solid", fgColor=fill.get(f["severity"], "FFFFFF"))
            for cell in ws[row]:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
    widths = [16, 18, 20, 12, 8, 6, 14, 9, 5, 34, 44, 36, 26, 9, 10, 24, 18]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    ws.freeze_panes = "A2"
    ws2 = wb.create_sheet("证书汇总")
    ws2.append(["证书文件", "证书编号", "版式", "校准日期", "仪器", "型号", "管理编号", "出厂编号", "环境温度℃", "环境湿度%RH", "错误", "无法核验", "提示", "已确认", "已驳回", "待审", "第二路抽取核对（可选）"])
    for c in ws2[1]:
        c.font = Font(bold=True)
    for cert in certs:
        fs = cert["findings"]
        cnt = lambda s: sum(1 for f in fs if f["severity"] == s)
        rc = lambda s: sum(1 for f in fs if f["review"]["status"] == s)
        ws2.append([cert["file"], cert["cert_no"], cert.get("layout"), cert.get("calib_date"), cert["instrument"].get("name"), cert["instrument"].get("model"),
                    cert["instrument"].get("mgmt_no"), cert["instrument"].get("serial_no"), cert["env"].get("temp_c"), cert["env"].get("rh"),
                    cnt("错误"), cnt("无法核验"), cnt("提示"), rc("确认"), rc("驳回"), rc("待审"), str(cert.get("extract_check", {}).get("llm_agree"))])
    ws3 = wb.create_sheet("规则包")
    rules = load_rules()
    ws3.append(["规则", "名称", "启用", "出处", "边界说明", "参数"])
    for rid, r in rules["rules"].items():
        params = {k: v for k, v in r.items() if k not in ("name", "enabled", "source", "boundary")}
        ws3.append([rid, r.get("name"), r.get("enabled", True), r.get("source"), r.get("boundary"), json.dumps(params, ensure_ascii=False)])
    ws4 = wb.create_sheet("导出信息")
    ws4.append(["导出时间", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    ws4.append(["审核人", reviewer])
    ws4.append(["说明", "判断全部由规则包产生，AI 仅做抽取与说明；每条发现均经人工确认/驳回后导出。"])
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def ensure_state():
    st.session_state.setdefault("certs", [])
    st.session_state.setdefault("history", [])
    st.session_state.setdefault("tmpdir", tempfile.mkdtemp(prefix="certaudit_"))
    st.session_state.setdefault("reviewer", "审核员")
    st.session_state.setdefault("uploaded_names", [])


def load_pdfs(files, use_llm):
    paths = []
    for uf in files:
        p = os.path.join(st.session_state["tmpdir"], uf.name)
        with open(p, "wb") as f:
            f.write(uf.getbuffer())
        paths.append(p)
    with st.spinner("解析 + 规则判定中…"):
        certs, history = analyze(paths, use_llm=use_llm)
    st.session_state["certs"] = certs
    st.session_state["history"] = history
    st.session_state["uploaded_names"] = [uf.name for uf in files]


def locate(sel_key, fid, page_key, page_no):
    """定位按钮回调：在脚本重跑前写状态，和控件渲染顺序无关。"""
    st.session_state[sel_key] = fid
    st.session_state[page_key] = page_no or 1


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


# ------------------------------------------------------------------ 页 1 审核台

def page_review():
    st.markdown('<div class="eyebrow">校准证书辅助审核</div>', unsafe_allow_html=True)
    st.title("校准证书审核台")
    st.caption("判断全部由规则包（R0–R7）产生，AI 只抽取不判 Pass/Fail；解释不了的标「无法核验」交人。")
    with st.sidebar:
        st.text_input("审核人", key="reviewer")
        use_llm = st.checkbox("启用第二路大模型抽取（需 LLM_API_KEY）", value=bool(os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")))
        files = st.file_uploader("上传校准证书 PDF（可多份）", type=["pdf"], accept_multiple_files=True)
        if files and [f.name for f in files] != st.session_state["uploaded_names"]:
            load_pdfs(files, use_llm)
        if st.button("重新分析", use_container_width=True) and files:
            load_pdfs(files, use_llm)
        if not files and not st.session_state["certs"]:
            demo_dir = os.path.join(os.path.dirname(HERE), "tests", "base")
            if os.path.isdir(demo_dir) and st.button("载入样例四份证书", use_container_width=True, type="primary"):
                paths = [os.path.join(demo_dir, n) for n in ["base_A.pdf", "base_B_errors.pdf", "base_2025.pdf", "base_2026.pdf"]]
                with st.spinner("解析中…"):
                    certs, history = analyze(paths, use_llm=use_llm)
                st.session_state["certs"], st.session_state["history"] = certs, history
                st.session_state["uploaded_names"] = ["__demo__"]
                st.rerun()
    certs = st.session_state["certs"]
    if not certs:
        st.info("左侧上传 PDF 开始审核，或点「载入样例四份证书」。")
        return

    cols = st.columns(len(certs))
    for i, c in enumerate(certs):
        fs = c["findings"]
        n_err = sum(1 for f in fs if f["severity"] == "错误")
        n_unk = sum(1 for f in fs if f["severity"] == "无法核验")
        n_tip = sum(1 for f in fs if f["severity"] == "提示")
        with cols[i]:
            st.metric(c["file"], f"{n_err} 错误 · {n_unk} 待核 · {n_tip} 提示", help=f"{c['cert_no']} · {c['instrument'].get('name')} {c['instrument'].get('model')}")
    idx = st.selectbox("当前证书", range(len(certs)), format_func=lambda i: f"{certs[i]['file']} · {certs[i]['cert_no']} · {certs[i]['instrument'].get('name') or ''} {certs[i]['instrument'].get('model') or ''}")
    cert = certs[idx]
    ec = cert.get("extract_check", {})
    agree = {True: "一致", False: "不一致", None: "未启用"}.get(ec.get("llm_agree"))
    st.markdown(
        f"<span class='muted'>版式</span> <b>{cert.get('layout')}</b>（{'有' if cert.get('layout') == 'B' else '无'}结论列）　"
        f"<span class='muted'>校准日期</span> <b>{cert.get('calib_date')}</b>　"
        f"<span class='muted'>管理编号</span> <b>{cert['instrument'].get('mgmt_no') or '-'}</b>　"
        f"<span class='muted'>出厂编号</span> <b>{cert['instrument'].get('serial_no') or '-'}</b>　"
        f"<span class='muted'>环境</span> <b>{cert['env'].get('temp_c')}℃ / {cert['env'].get('rh')}%RH</b>　"
        f"<span class='muted'>第二路抽取核对（可选）</span> <b>{agree}</b>", unsafe_allow_html=True)

    left, right = st.columns([1.1, 1])
    fs = sorted(cert["findings"], key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f.get("page") or 0))
    sel_key = f"sel_{idx}"
    page_key = f"page_{idx}"
    st.session_state.setdefault(sel_key, fs[0]["id"] if fs else None)
    _first = next((f for f in fs if f["id"] == st.session_state[sel_key]), None)
    st.session_state.setdefault(page_key, (_first or {}).get("page") or 1)
    with right:
        st.subheader(f"发现 {len(fs)} 条")
        if not fs:
            st.success("无发现：五条规则全部通过。")
        for f in fs:
            rv = f["review"]
            with st.container(border=True):
                c1, c2 = st.columns([5, 1])
                with c1:
                    st.markdown(
                        f"{theme.sev_pill(f['severity'])} {theme.rule_pill(f['rule_id'], f.get('rule_name', ''))} "
                        f"<span class='muted'>第 {f['page']} 页</span> {theme.review_pill(rv['status'])}"
                        + (" <span class='pill pill-err'>◀ 左侧已定位</span>" if f["id"] == st.session_state.get(sel_key) else ""), unsafe_allow_html=True)
                    st.markdown(f"**原因**：{f['reason']}")
                    if f.get("raw"):
                        st.caption(f"原文：{f['raw']}")
                    st.markdown(f"**建议**：{f['suggestion']}")
                    st.caption(f"出处：{f.get('source', '')}")
                with c2:
                    st.button("定位", key=f"loc_{idx}_{f['id']}", use_container_width=True, type="primary" if f["id"] == st.session_state.get(sel_key) else "secondary",
                              on_click=locate, args=(sel_key, f["id"], page_key, f.get("page")))
                a, b, cnote = st.columns([1, 1, 3])
                note = cnote.text_input("理由", value=rv["note"], key=f"note_{idx}_{f['id']}", label_visibility="collapsed", placeholder="审核理由（驳回必填）")
                if a.button("确认", key=f"ok_{idx}_{f['id']}", use_container_width=True):
                    rv.update(status="确认", by=st.session_state["reviewer"], note=note, at=now())
                    st.rerun()
                if b.button("驳回", key=f"no_{idx}_{f['id']}", use_container_width=True):
                    if not note.strip():
                        st.warning("驳回必须写理由")
                    else:
                        rv.update(status="驳回", by=st.session_state["reviewer"], note=note, at=now())
                        st.rerun()
        st.divider()
        done = sum(1 for c in certs for f in c["findings"] if f["review"]["status"] != "待审")
        total = sum(len(c["findings"]) for c in certs)
        st.markdown(f"<span class='muted'>审核进度</span> <b>{done}/{total}</b>", unsafe_allow_html=True)
        st.download_button("导出 Excel（含审核记录）", data=export_excel(certs, st.session_state["reviewer"]),
                           file_name=f"证书审核_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True, type="primary")
        st.download_button("导出 findings.json", data=json.dumps([f for c in certs for f in c["findings"]], ensure_ascii=False, indent=1),
                           file_name="findings.json", mime="application/json", use_container_width=True)
    with left:
        st.markdown('<div class="locate-anchor"></div>', unsafe_allow_html=True)
        sel_id = st.session_state.get(sel_key)
        sel = next((f for f in fs if f["id"] == sel_id), None)
        if st.session_state.get(page_key) not in range(1, cert["page_count"] + 1):
            st.session_state[page_key] = 1
        page_no = st.radio("页", list(range(1, cert["page_count"] + 1)), horizontal=True, format_func=lambda p: f"第 {p} 页", key=page_key)
        if sel:
            where = f"第 {sel.get('page') or '?'} 页"
            if not sel.get("bbox"):
                where += "（该条无精确坐标，已跳到所在页，请看整页）"
            elif sel.get("page") != page_no:
                where += f"（你正在看第 {page_no} 页，点上方「第 {sel.get('page')} 页」回到定位）"
            st.markdown(f"{theme.sev_pill(sel['severity'])} <b>当前定位 {sel['id']} · {where}</b><br><span class='muted'>{sel['reason']}</span>", unsafe_allow_html=True)
        boxes = []
        for f in fs:
            if f.get("page") == page_no and f.get("bbox") and f is not sel:
                boxes.append((f["bbox"], hex_to_rgb(SEV_COLOR[f["severity"]]), f["id"]))
        focus = sel["bbox"] if sel and sel.get("page") == page_no and sel.get("bbox") else None
        if focus:
            with st.container(border=True):
                st.caption("局部放大")
                st.image(render_page(cert["path"], page_no, [], zoom=3, focus=focus, clip_focus=True), use_container_width=True)
        with st.container(border=True):
            st.image(render_page(cert["path"], page_no, boxes, focus=focus), use_container_width=True)


# ------------------------------------------------------------------ 页 2 规则

def page_rules():
    st.markdown('<div class="eyebrow">规则包</div>', unsafe_allow_html=True)
    st.title("rules.yaml")
    st.caption(f"文件：{DEFAULT_RULES}。改阈值 → 保存 → 回审核台点「重新分析」生效。程序只照这里判，代码里没有写死的阈值。")
    rules = load_rules()
    tab1, tab2 = st.tabs(["表单编辑", "YAML 源码"])
    with tab1:
        for rid, r in rules["rules"].items():
            with st.expander(f"{rid} {r.get('name')}  ·  {'启用' if r.get('enabled', True) else '停用'}", expanded=rid in ("R1", "R2")):
                r["enabled"] = st.checkbox("启用", value=r.get("enabled", True), key=f"en_{rid}")
                st.markdown(f"**出处**：{r.get('source')}  \n**边界**：{r.get('boundary')}")
                for k, v in list(r.items()):
                    if k in ("name", "enabled", "source", "boundary"):
                        continue
                    if isinstance(v, bool):
                        r[k] = st.checkbox(k, value=v, key=f"{rid}_{k}")
                    elif isinstance(v, (int, float)):
                        r[k] = st.number_input(k, value=float(v), key=f"{rid}_{k}", format="%g")
                    elif isinstance(v, str):
                        r[k] = st.text_input(k, value=v, key=f"{rid}_{k}")
                    elif isinstance(v, list):
                        txt = st.text_input(k + "（逗号分隔）", value=", ".join(map(str, v)), key=f"{rid}_{k}")
                        r[k] = [x.strip() for x in txt.split(",") if x.strip()]
                    elif isinstance(v, dict):
                        txt = st.text_area(k + "（YAML）", value=yaml.safe_dump(v, allow_unicode=True, sort_keys=False), key=f"{rid}_{k}", height=120)
                        try:
                            r[k] = yaml.safe_load(txt)
                        except Exception as e:
                            st.error(f"{k} 解析失败：{e}")
        if st.button("保存规则包", type="primary"):
            save_rules(rules)
            st.success("已保存。回审核台点「重新分析」。")
    with tab2:
        src = open(DEFAULT_RULES, encoding="utf-8").read()
        new = st.text_area("rules.yaml", value=src, height=600)
        if st.button("保存 YAML"):
            try:
                yaml.safe_load(new)
                with open(DEFAULT_RULES, "w", encoding="utf-8") as f:
                    f.write(new)
                st.success("已保存。")
            except Exception as e:
                st.error(f"YAML 语法错误：{e}")


# ------------------------------------------------------------------ 页 3 历史对比

def page_history():
    st.markdown('<div class="eyebrow">R6 历史一致性</div>', unsafe_allow_html=True)
    st.title("历史对比")
    certs = st.session_state["certs"]
    if len(certs) < 2:
        st.info("先在审核台上传至少两份证书（同一管理编号/出厂编号会自动配对）。")
        return
    names = [f"{c['file']} · {c['cert_no']} · {c['instrument'].get('mgmt_no') or c['instrument'].get('serial_no') or '-'} · {c.get('calib_date')}" for c in certs]
    d_old, d_new = 0, 1
    if st.session_state["history"]:
        h = st.session_state["history"][0]
        d_old = next(i for i, c in enumerate(certs) if c["cert_no"] == h["old"])
        d_new = next(i for i, c in enumerate(certs) if c["cert_no"] == h["new"])
    c1, c2 = st.columns(2)
    io_ = c1.selectbox("上一份", range(len(certs)), index=d_old, format_func=lambda i: names[i])
    in_ = c2.selectbox("本次", range(len(certs)), index=d_new, format_func=lambda i: names[i])
    old, new = certs[io_], certs[in_]
    hf = compare_history(old, new)
    if old["instrument"].get("mgmt_no") != new["instrument"].get("mgmt_no") and old["instrument"].get("serial_no") != new["instrument"].get("serial_no"):
        st.warning("两份证书的管理编号与出厂编号都不同，可能不是同一台仪器。")
    for col, c in ((c1, old), (c2, new)):
        with col:
            with st.container(border=True):
                st.markdown(f"**{c['cert_no']}** <span class='muted'>· {c.get('calib_date')}</span>", unsafe_allow_html=True)
                st.json({"仪器": c["instrument"], "环境": {"温度": c["env"].get("temp_c"), "湿度": c["env"].get("rh")},
                         "标准器": [{"名称": s["name"], "编号": s["id"], "有效期": s["valid_until"]} for s in c["standards"]]}, expanded=False)
    st.subheader(f"R6 提示 {len(hf)} 条")
    for f in hf:
        st.markdown(f"{theme.sev_pill('提示')} **{f['reason']}**  \n<span class='muted'>建议：{f['suggestion']}</span>", unsafe_allow_html=True)
    st.subheader("逐点修正值变化")
    rows = drift_table(old, new)
    table = []
    for r in rows:
        u = r["unit"] or ""
        fmt = lambda v: "—" if v is None else f"{v:+g}"
        margin = r["margin"]
        hint = ""
        if r["old"] is None:
            hint = "校准点变了，不可直接比"
        elif margin is not None and r["delta"] is not None:
            if r["delta"] != 0 and margin / abs(r["delta"]) <= 1:
                hint = "按趋势下次触边，建议缩短周期"
            elif r["tolerance"] and margin / r["tolerance"] < 0.3:
                hint = "余量不足 30%"
            else:
                hint = "稳定"
        table.append({"量": r["quantity"], "校准点": f"{r['point']:g}{u}", "上次修正值": fmt(r["old"]), "本次修正值": fmt(r["new"]),
                      "允差": f"±{r['tolerance']:g}" if r["tolerance"] is not None else "—", "变化": fmt(r["delta"]),
                      "余量": f"{margin:g}" if margin is not None else "—", "档案提示": hint})
    st.dataframe(table, use_container_width=True, hide_index=True)
    st.caption("两点外推仅为演示级提示；真实产品需三年以上数据才给周期建议。")
    with st.expander("并排看原文第 3 页"):
        a, b = st.columns(2)
        a.image(render_page(old["path"], 3, []), use_container_width=True)
        b.image(render_page(new["path"], 3, []), use_container_width=True)


# ------------------------------------------------------------------ 入口

ensure_state()
page = st.sidebar.radio("审核台视图", ["审核台", "规则", "历史对比"], horizontal=True)
if page == "审核台":
    page_review()
elif page == "规则":
    page_rules()
else:
    page_history()
