"""用 PyMuPDF 从零排版 4 份完全虚构的校准证书底板 → tests/base/*.pdf

用法（仓库根目录）：python tests/tools/make_base.py
    - 不读取任何外部 PDF，不放任何图片（无标志、印章、二维码、手写签名）。
    - 字体用 PyMuPDF 自带的 CJK 字体（fitz.Font("cjk")），换系统也能重新生成，内容可重复生成（PDF 字节可能因文档 ID 不同而不同）。
    - 实验室、委托方、地址、证书编号、仪器型号/编号、标准器及其溯源证书号全部虚构；签字人只用代号。
    - 校准点、修正值、允差、环境温湿度、日期等通用数字沿用常见量级，方便用例按坐标改字。
    - 每页页脚一行小字「虚构示例证书，仅供演示」。

四份底板（对应测试里的四种角色）：
    base_A.pdf       版式 A：校准结果表没有「结论」列；无规则问题（只剩模板占位符 R7）
    base_B_errors.pdf 版式 B：有结论列；环境温度 35℃ 超限、湿度点 +6>±5 却标 Pass 等真错
    base_2025.pdf    版式 B：2025 年报告，无规则问题（只剩 R7）
    base_2026.pdf    版式 B：2026 年报告（同一台仪器的下一年），第 3 页有被拆开的单元格 +0.|6，
                     与 2025 年相比校准点 10→15、制造商 空→示例制造厂、标准器 DP-A01→DP-B01（R6）
"""
import os
import sys

import pymupdf as fitz

fitz.TOOLS.mupdf_display_errors(False)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # tests/
OUT = os.path.join(ROOT, "base")

FONT = fitz.Font("cjk")
ASC = FONT.ascender          # span 的 bbox 顶 = 基线 - ASC*字号；下面所有 y 都是 bbox 顶
GREY = (0.45, 0.45, 0.45)
FOOTER = "虚构示例证书，仅供演示"
PLACEHOLDER = "{del_下次校准日期=Y,del_结论=N}"   # 模板占位符残留（R7 的触发物）

LAB_CN = "示  例  计  量  实  验  室"
LAB_EN = "SAMPLE   METROLOGY   LABORATORY"
LAB_ADDR_CN = "实验室地址：示例省示例市示例区示例路 1 号（虚构地址）"
LAB_ADDR_EN = "Laboratory address: No. 1 Example Road, Example District, Example City (fictitious)"

# 标准器表的两种排版：S1 = 温度在前（三行名称），S2 = 露点在前（四行名称）
S1 = {"rows": [568.6, 605.6], "row_dy": (0.0, 11.0, 21.9), "value_y": [579.6, 616.5], "name_lines": 3}
S2 = {"rows": [569.5, 618.8], "row_dy": (0.0, 10.9, 21.8, 32.7), "value_y": [585.8, 635.2], "name_lines": 4}


def std(kind, model_en, sid, rng, unc, trace, due):
    """kind: 'T' 温度 / 'D' 露点。名称拆两行是为了贴近真实证书里窄列折行的样子。"""
    name1, name2 = ("示例露点仪（温", "度）") if kind == "T" else ("示例露点仪（露", "点）")
    return {"name1": name1, "name2": name2, "model_en": model_en, "id": sid, "range": rng,
            "unc": unc, "trace": trace, "due": due}


CERTS = {
    "base_A.pdf": dict(
        cert_no="C2699000000101", name="温湿度检测仪", model="DEMO-TH10", maker=None,
        serial="SN-DEMO-0001", mgmt=None, recv="2026/10/26", calib="2026/10/28", issue="2026/10/28",
        next="2027/10/27", signers=("批准人A", "审核员A", "校准员B"), layout="A", env=(23.2, 56),
        std_layout=S1,
        stds=[std("T", "Demo Dew DP-1", "DP-A01", "(-40～100)℃", "U =0.04℃,k =2", "C2588000000201", "2027/10/01"),
              std("D", "Demo Dew DP-1", "DP-A01", "(-40～50）℃DP", "U =0.14℃,k =2", "C2588000000202", "2027/10/01")],
        temp=[(-15, "+0.14", "±0.50", None, "0.20℃"), (5, "+0.30", "±0.50", None, "0.20℃"),
              (15, "+0.28", "±0.50", None, "0.20℃"), (30, "+0.24", "±0.50", None, "0.20℃")],
        hum=[(20, "-4.4", "±5.0", None, "1.5%RH"), (50, "-2.1", "±5.0", None, "1.5%RH"), (80, "+3.3", "±5.0", None, "1.5%RH")],
        notes=[("依据JJF1059.1-2012测量不确定度评定与表示。", "According to JJF1059.1-2012 Evaluation and Expression of Uncertainty in Measurement."),
               ("按校准结果使用。", "Using according to the results of calibration."),
               ("允差由客户提供。", "Permissible error is provided by the customer."),
               ("按客户要求校准。", "Calibrated according to the customer request.")],
    ),
    "base_B_errors.pdf": dict(
        cert_no="C2699000000102", name="温湿度表", model="DEMO-TH30", maker=None,
        serial=None, mgmt="DEMO-B02", recv="2026/10/22", calib="2026/10/24", issue="2026/10/28",
        next="2027/10/23", signers=("批准人A", "审核员A", "校准员A"), layout="B", env=(35.0, 55),
        std_layout=S2,
        stds=[std("D", "Demo Dew DP-2", "DP-B01", "(-20~+40)℃DP", "U =0.14℃,k =2", "C2688000000205", "2027/10/01"),
              std("T", "Demo Dew DP-2", "DP-B01", "(-40~100)℃", "U =0.04℃,k =2", "C2688000000206", "2027/10/01")],
        temp=[(10, "-1.0", "±2.0", "Pass", "0.6℃"), (20, "-0.9", "±2.0", "Pass", "0.6℃"), (30, "-0.5", "±2.0", "Pass", "0.6℃")],
        hum=[(40, "+6", "±5", "Pass", "2%RH"), (60, "+24", "±5", "Fail", "2%RH"), (80, "+28", "±7", "Fail", "2%RH")],
        notes=[("依据JJF1059.1-2012测量不确定度评定与表示。", "According to JJF1059.1-2012 Evaluation and Expression of Uncertainty in Measurement."),
               ("按校准结果使用。", "Using according to the results of calibration."),
               ("按客户要求校准。", "Calibrated according to the customer request.")],
    ),
    "base_2025.pdf": dict(
        cert_no="C2599000000103", name="温湿度计", model="DEMO-TH20", maker=None,
        serial=None, mgmt="DEMO-001", recv="2025/11/13", calib="2025/11/17", issue="2025/11/18",
        next="2026/11/16", signers=("批准人A", "审核员A", "校准员A"), layout="B", env=(23.5, 56),
        std_layout=S1,
        stds=[std("T", "Demo Dew DP-1", "DP-A01", "(-40～100)℃", "U =0.04℃,k =2", "C2588000000203", "2026/10/13"),
              std("D", "Demo Dew DP-1", "DP-A01", "(-40～50）℃DP", "U =0.14℃,k =2", "C2588000000204", "2026/10/13")],
        temp=[(10, "+0.1", "±2.0", "Pass", "0.6℃"), (20, "+0.3", "±2.0", "Pass", "0.6℃"), (30, "+0.4", "±2.0", "Pass", "0.6℃")],
        hum=[(40, "+2", "±5", "Pass", "2%RH"), (60, "+3", "±5", "Pass", "2%RH"), (80, "+2", "±7", "Pass", "2%RH")],
        notes=[("依据JJF1059.1-2012测量不确定度评定与表示。", "According to JJF1059.1-2012 Evaluation and Expression of Uncertainty in Measurement."),
               ("校准项目符合技术要求。", "The calibrated measurand are accord with the technical specifications."),
               ("按客户要求校准。", "Calibrated according to the customer request.")],
    ),
    "base_2026.pdf": dict(
        cert_no="C2699000000104", name="温湿度计", model="DEMO-TH20", maker="示例制造厂",
        serial=None, mgmt="DEMO-001", recv="2026/10/28", calib="2026/10/29", issue="2026/10/29",
        next="2027/10/28", signers=("批准人A", "审核员A", "校准员B"), layout="B", env=(23.2, 54),
        std_layout=S2, shuffle_p3=True, split_cell=True,
        stds=[std("D", "Demo Dew DP-2", "DP-B01", "(-20~+40)℃DP", "U =0.14℃,k =2", "C2688000000205", "2027/10/01"),
              std("T", "Demo Dew DP-2", "DP-B01", "(-40~100)℃", "U =0.04℃,k =2", "C2688000000206", "2027/10/01")],
        temp=[(15, "-0.1", "±2.0", "Pass", "0.6℃"), (20, "+0.1", "±2.0", "Pass", "0.6℃"), (30, "+0.6", "±2.0", "Pass", "0.6℃")],
        hum=[(40, "+2", "±5", "Pass", "2%RH"), (60, "+4", "±5", "Pass", "2%RH"), (80, "+3", "±7", "Pass", "2%RH")],
        notes=[("依据JJF1059.1-2012测量不确定度评定与表示。", "According to JJF1059.1-2012 Evaluation and Expression of Uncertainty in Measurement."),
               ("校准项目符合技术要求。", "The calibrated measurand are accord with the technical specifications."),
               ("按客户要求校准。", "Calibrated according to the customer request.")],
    ),
}


class Pg:
    """一页：所有文字都用同一个内嵌 CJK 字体；y 一律是文字 bbox 的顶。"""

    def __init__(self, doc):
        self.p = doc.new_page(width=595.25, height=841.85)
        self.p.insert_font(fontname="cjk", fontbuffer=FONT.buffer)
        self.ops = []

    def t(self, x, y, s, size=9.0, color=(0, 0, 0)):
        self.ops.append((x, y, s, size, color))
        return self

    def flush(self, order=None):
        ops = self.ops if order is None else [self.ops[i] for i in order]
        for x, y, s, size, color in ops:
            self.p.insert_text((x, y + ASC * size), s, fontname="cjk", fontsize=size, color=color)
        self.ops = []

    def rule(self, x0, y, x1, w=0.5):
        self.p.draw_line((x0, y), (x1, y), color=(0.6, 0.6, 0.6), width=w)

    def box(self, x0, y0, x1, y1):
        self.p.draw_rect(fitz.Rect(x0, y0, x1, y1), color=(0.6, 0.6, 0.6), width=0.5)

    def footer(self):
        w = FONT.text_length(FOOTER, 7)
        self.p.insert_text(((595.25 - w) / 2, 818 + ASC * 7), FOOTER, fontname="cjk", fontsize=7, color=GREY)


def cw(s, size):
    return FONT.text_length(s, size)


def centered(cx, s, size=9.0):
    return cx - cw(s, size) / 2


def page_head(pg, c, no):
    """第 2、3 页抬头：证书编号 + 页码。"""
    ny = 159.2 if no == 2 else 161.4
    pg.t(55.5, ny, "证 书 编 号          " + c["cert_no"], 12)
    pg.t(55.5, ny + 13.8, "Certificate No.", 9.9)
    pg.t(463.5, 159.2, f"第 {no} 页 共 3 页", 12)
    pg.t(478.5, 173.7, f"Page {no} of 3", 9.9)


def page1(doc, c):
    pg = Pg(doc)
    pg.t(99.2, 73.6, LAB_CN, 28)
    pg.t(145.2, 109.4, LAB_EN, 14)
    pg.t(225.2, 129.8, "校  准  证  书", 28)
    pg.t(234.5, 165.6, "Calibration    Certificate", 14)
    pg.t(140.9, 198.0, PLACEHOLDER, 6)
    pg.t(464.2, 220.7, "第 1 页 共 3 页", 12)
    pg.t(479.2, 235.2, "Page 1 of 3", 9.9)
    rows = [  # (y, 中文标签, 英文标签, 值)
        (223.0, "证  书  编  号", "Certificate No.", c["cert_no"]),
        (258.8, "委  托  单  位", "Customer", "示例委托方有限公司"),
        (295.7, "委托单位地址", "Address", "示例省示例市示例区示例路 2 号（虚构地址）"),
        (332.5, "器  具  名  称", "Name of instrument", c["name"]),
        (369.4, "型  号  规  格", "Model", c["model"]),
        (406.2, "制    造    商", "Manufacturer", c["maker"] or "--------"),
    ]
    for y, cn, en, val in rows:
        pg.t(58.5, y, cn, 12)
        pg.t(58.5, y + 16.4, en, 9.9)
        pg.t(160.6, y, val, 12)
    pg.t(58.5, 443.0, "出  厂  编  号", 12)
    pg.t(160.6, 443.3, c["serial"] or "--------", 12)
    pg.t(58.5, 459.6, "Serial   No.", 9.9)
    pg.t(309.9, 443.0, "管  理  编  号", 12)
    pg.t(401.1, 443.3, c["mgmt"] or "--------", 12)
    pg.t(310.7, 459.6, "Management No.", 9.9)
    pg.t(58.5, 479.9, "接  收  日  期", 12)
    pg.t(160.6, 479.9, c["recv"], 12)
    pg.t(309.9, 479.9, "校  准  日  期      " + c["calib"], 12)
    pg.t(58.5, 496.4, "Received date", 9.9)
    pg.t(312.2, 496.4, "Calibration date", 9.9)
    pg.t(58.5, 516.7, "发  布  日  期", 12)
    pg.t(160.6, 516.7, c["issue"], 12)
    pg.t(295.1, 516.7, "建议下次校准日期" + c["next"], 12)
    pg.t(58.5, 533.3, "Issue date", 9.9)
    pg.t(302.8, 533.3, "Next calibration date", 9.9)
    ap, rv, cb = c["signers"]
    for y, cn, en, who in [(596.0, "批        准", "Approved by", ap), (639.1, "审        核", "Inspected by", rv),
                           (681.2, "校        准", "Calibrated by", cb)]:
        pg.t(318.9, y, cn, 12)
        pg.t(318.6, y + 19.7, en, 9.9)
        pg.t(503.1, y + 10.5, who, 9.9)
    pg.t(117.7, 640.3, "证书/报告章（虚构示例，无印章）", 9.9)
    pg.t(134.8, 659.7, "Stamp", 8)
    pg.t(58.5, 759.7, LAB_ADDR_CN, 9)
    pg.t(57.8, 772.0, LAB_ADDR_EN, 7)
    pg.t(58.5, 782.1, "邮编 Post code：000000", 9)
    pg.t(215.4, 782.1, "电话 Tel.：000-00000000", 9)
    pg.t(346.5, 782.1, "电子邮箱 E-mail：demo@example.invalid", 9)
    pg.flush()
    pg.footer()


def page2(doc, c):
    pg = Pg(doc)
    page_head(pg, c, 2)
    pg.t(267.8, 72.1, "说 明", 26)
    pg.t(268.5, 117.7, "Directions", 14)
    items = [
        (215.6, "本证书校准结果均可溯源至国际单位制（SI）单位。", "The results are traceable to International System of Units(SI)."),
        (257.0, "本证书为虚构示例文件，不具备任何效力。未经本实验室书面批准，不得部分复制此证书。",
         "This certificate is a fictitious sample and has no validity. It shall not be copied partly without written approval."),
        (298.4, "本证书校准结果只与受校准仪器有关。如证书中的英文内容与中文内容有差异，以中文为准。",
         "The results relate only to the items calibrated. In case of any discrepancy, the Chinese version shall prevail."),
        (349.0, "本次校准的技术依据：", "Reference documents for the calibration"),
        (406.4, "校准地点、环境条件：", "Place and environment condition during calibration"),
        (498.1, "本次校准所使用的主要计量标准器具：", "Main measurement standards used in the calibration"),
    ]
    for n, (y, cn, en) in enumerate(items, 1):
        pg.t(55.8, y, f"{n}.", 9)
        pg.t(73.8, y, cn, 9)
        pg.t(72.9, y + 13.8, en, 7)
    pg.t(73.8, 374.4, "JJF 1076-2020  数字式温湿度计校准规范", 9)
    pg.t(73.8, 431.8, "地点：本实验室示例间(3)", 9)
    pg.t(72.9, 445.6, "Place", 7)
    t, rh = c["env"]
    pg.t(73.8, 457.2, f"温度：{t:.1f}℃", 9)
    pg.t(281.4, 457.2, f"相对湿度：{rh}%", 9)
    pg.t(72.9, 471.0, "Temperature", 7)
    pg.t(280.7, 471.0, "R.H.", 7)
    pg.t(512.4, 500.2, "计量溯源性声明", 5.5)
    hy = 527.5
    for x, y, s, sz in [(73.8, hy, "名称/型号规格", 9), (141.9, hy, "编号", 9), (181.8, hy, "测量范围", 9),
                        (281.4, hy - 5.5, "准确度等级/最大允许误差/不", 9), (281.4, hy + 5.4, "确定度", 9),
                        (405.2, hy, "证书号/溯源机构", 9), (513.2, hy, "有效期", 9),
                        (72.9, hy + 24, "Name/Model", 7), (141.2, hy + 24, "Serial No.", 7),
                        (180.9, hy + 24, "Measurement range", 7), (280.7, hy + 19.7, "Accuracy class/Maximum permissible", 7),
                        (280.7, hy + 28.3, "error/Uncertainty", 7), (404.4, hy + 24, "Certificate No./Traceability to", 7),
                        (512.4, hy + 24, "Due date", 7)]:
        pg.t(x, y, s, sz)
    lay = c["std_layout"]
    for k, s in enumerate(c["stds"]):
        y0, vy = lay["rows"][k], lay["value_y"][k]
        dy = lay["row_dy"]
        pg.t(73.8, y0, s["name1"], 9)
        pg.t(73.8, y0 + dy[1], s["name2"], 9)
        pg.t(73.8, y0 + dy[2], s["model_en"], 9)
        pg.t(141.9, vy, s["id"], 9)
        pg.t(181.8, vy, s["range"], 9)
        pg.t(280.7, vy, s["unc"], 9)
        pg.t(405.2, vy - 5.4, s["trace"], 9)
        pg.t(405.2, vy + 5.5, "示例溯源机构（虚构）", 9)
        pg.t(513.2, vy, s["due"], 9)
    pg.flush()
    pg.box(58, 517, 560, 655 if lay is S2 else 640)
    pg.rule(58, 569, 560)
    pg.footer()


def result_tables(c):
    """第 3 页「校准结果」两张表 → 文字操作列表 [(x, y, s, size)]（单位 pt，y 为 bbox 顶）。"""
    A = c["layout"] == "A"
    ops = []
    ops += [(63.6, 214.8, "1.", 9), (73.3, 214.8, "外观及工作正常性检查", 9),
            (73.3, 229.2, "Appearance and function check", 9), (73.3, 243.6, "正常 Normal", 9),
            (63.6, 272.4, "2.", 9), (73.3, 272.4, "修正值：", 9), (73.3, 286.8, "Corrected Value", 9)]
    tx = (73.3, 179.5, 292.0 if A else 265.0, 352.9, 412.9)       # 温度表头 x
    hx = (73.3, 174.0, 286.4 if A else 259.4, 352.9, 412.9)       # 湿度表头 x
    en_x = (73.3, 171.4, 299.3 if A else 272.3, 345.7, 417.2)
    pt_c, corr_c, tol_c, con_c = 102.1, 200.0, (308.0 if A else 281.0), 362.0
    y = 315.6
    ops += [(73.3, y, "温度部分", 9), (73.3, y + 14.4, "Temperature part", 9)]
    y += 28.8

    def header(y, unit, xs):
        o = [(xs[0], y, f"校准点({unit})", 9), (xs[1], y, f"修正值({unit})", 9), (xs[2], y, f"允差({unit})", 9)]
        if not A:
            o.append((xs[3], y, "结论", 9))
        o.append((xs[4], y, "扩展不确定度（k=2）", 9))
        e = [(en_x[0], y + 14.4, "Calibration point", 9), (en_x[1], y + 14.4, "Corrected Value", 9),
             (en_x[2], y + 14.4, "MPE", 9)]
        if not A:
            e.append((en_x[3], y + 14.4, "Pass/Fail", 9))
        e.append((en_x[4], y + 14.4, "Expanded Uncertainty", 9))
        return o + e

    def rows(y, pts, split_last=False):
        o = []
        for k, (pt, corr, tol, con, unc) in enumerate(pts):
            yy = y + 14.4 * k
            ps = f"{pt}"
            o.append((centered(pt_c, ps), yy, ps, 9))
            if split_last and k == len(pts) - 1 and corr.endswith("6"):
                head, tailc = corr[:-1], corr[-1]
                x0 = centered(corr_c, corr)
                o.append((x0, yy, head, 9))
                o.append((x0 + cw(head, 9) + 0.5, yy, tailc, 9))     # 被拆开的单元格 +0.|6
            else:
                o.append((centered(corr_c, corr), yy, corr, 9))
            o.append((centered(tol_c, tol), yy, tol, 9))
            if con and not A:
                o.append((centered(con_c, con), yy, con, 9))
            o.append((441.9, yy, "U=" + unc, 9))
        return o

    ops += header(y, "℃", tx)
    ops += rows(y + 28.8, c["temp"], split_last=c.get("split_cell", False))
    y = y + 28.8 + 14.4 * len(c["temp"]) + 14.4
    ops += [(73.3, y, "湿度部分   （温度点", 9), (168.5, y, "20", 9), (187.9, y, "℃)", 9),
            (73.3, y + 14.4, "Humidity section               (At", 9), (195.5, y + 14.4, "20", 9), (214.9, y + 14.4, "℃ grade)", 9)]
    y += 28.8
    ops += header(y, "%RH", hx)
    ops += rows(y + 28.8, c["hum"])
    y = y + 28.8 + 14.4 * len(c["hum"]) + 14.4
    ops += [(58.3, y, "备注：", 9), (58.3, y + 14.4, "Notes", 9)]
    y += 28.0
    for n, (cn, en) in enumerate(c["notes"], 1):
        ops += [(63.6, y, f"{n}.", 9), (73.3, y, cn, 9), (73.3, y + 11.0, en, 9)]
        y += 26.2
    y += 4
    ops += [(288.1, y, "以下空白", 9), (284.4, y + 14.4, "Blank below", 9)]
    return ops


def page3(doc, c):
    pg = Pg(doc)
    page_head(pg, c, 3)
    pg.t(235.5, 74.4, "校 准 结 果", 26)
    pg.t(237.8, 120.0, "Results of calibration", 14)
    for x, y, s, sz in result_tables(c):
        pg.t(x, y, s, sz)
    order = None
    if c.get("shuffle_p3"):          # 第 3 页文字流乱序：按偶数位在前、奇数位在后画，坐标不变
        n = len(pg.ops)
        order = list(range(0, n, 2)) + list(range(1, n, 2))
    pg.flush(order)
    pg.footer()


def build(name, c):
    doc = fitz.open()
    page1(doc, c)
    page2(doc, c)
    page3(doc, c)
    doc.set_metadata({"title": "虚构示例校准证书（仅供演示）", "author": "", "subject": "", "keywords": "", "creator": "make_base.py", "producer": ""})
    doc.subset_fonts()
    dst = os.path.join(OUT, name)
    doc.save(dst, garbage=4, deflate=True)
    doc.close()
    return dst


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, c in CERTS.items():
        p = build(name, c)
        print(f"{name}  {os.path.getsize(p) / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
