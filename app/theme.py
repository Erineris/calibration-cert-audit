# -*- coding: utf-8 -*-
"""统一视觉：Notion 风（纯白底、米灰侧栏、系统字体、细灰线、方角色块标签、Notion 蓝主按钮）。
用法：import theme; theme.apply()  （在 st.set_page_config 之后、任何内容之前）
"""
import streamlit as st

ACCENT = "#2383E2"
CSS = """
<style>
/* Notion 风：纯白底 / 米灰侧栏 / 系统字体 / 细灰线 / 小圆角 / 方角色块标签 */
html, body, [class*="css"], .stApp, button, input, textarea {
  font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", Helvetica, Arial, sans-serif; }
.stApp { background: #FFFFFF; color: #37352F; }
header[data-testid="stHeader"] { background: transparent; }
#MainMenu, footer { visibility: hidden; }
.block-container { padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1320px; }
h1 { font-size: 2.1rem !important; font-weight: 700 !important; letter-spacing: -0.015em; color: #37352F; line-height: 1.2 !important; margin-bottom: .3rem !important; }
h2 { font-size: 1.35rem !important; font-weight: 600 !important; color: #37352F; margin-top: .8rem !important; }
h3 { font-size: 1.05rem !important; font-weight: 600 !important; color: #37352F; }
p, li, .stMarkdown { color: #37352F; line-height: 1.6; }
.stCaption, [data-testid="stCaptionContainer"] { color: #787774 !important; }
/* 侧栏 */
section[data-testid="stSidebar"] { background: #F7F7F5; border-right: 1px solid #EDEDEC; }
section[data-testid="stSidebar"] [data-testid="stSidebarNav"] a { border-radius: 4px; margin: 1px 6px; color: #5F5E5B; }
section[data-testid="stSidebar"] [data-testid="stSidebarNav"] a:hover { background: rgba(55,53,47,.06); }
section[data-testid="stSidebar"] [data-testid="stSidebarNav"] a[aria-current="page"] { background: rgba(55,53,47,.08); color: #37352F; font-weight: 600; }
/* 卡片 = 细灰线块，无阴影 */
[data-testid="stVerticalBlockBorderWrapper"] { background: #FFFFFF; border: 1px solid #E9E9E7 !important; border-radius: 6px !important; padding: .3rem .45rem; box-shadow: none; }
[data-testid="stMetric"] { background: #F7F7F5; border: none; border-radius: 6px; padding: .8rem 1rem; }
[data-testid="stMetricLabel"] { color: #787774; font-size: .8rem; }
[data-testid="stMetricValue"] { font-size: 1.1rem; font-weight: 600; color: #37352F; }
[data-testid="stExpander"] { background: #FFFFFF; border: 1px solid #E9E9E7; border-radius: 6px; }
[data-testid="stExpander"] details summary { font-weight: 500; }
[data-testid="stExpander"] details summary:hover { background: rgba(55,53,47,.04); }
/* 控件 */
.stButton > button, .stDownloadButton > button { border-radius: 4px; border: 1px solid #E9E9E7; background: #FFFFFF; color: #37352F; font-weight: 500; box-shadow: none; padding: .25rem .75rem; }
.stButton > button:hover, .stDownloadButton > button:hover { background: rgba(55,53,47,.06); border-color: #E9E9E7; color: #37352F; }
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] { background: %(accent)s; border-color: %(accent)s; color: #FFFFFF; }
.stButton > button[kind="primary"]:hover, .stDownloadButton > button[kind="primary"]:hover { background: #0F6FCF; border-color: #0F6FCF; color: #FFFFFF; }
.stTextInput input, .stNumberInput input, .stSelectbox > div > div, .stTextArea textarea { border-radius: 4px !important; border-color: #E9E9E7 !important; background: #FFFFFF; color: #37352F; }
div[role="radiogroup"] label { background: transparent; border: 1px solid #E9E9E7; border-radius: 4px; padding: .1rem .6rem; margin-right: .3rem; }
.stTabs [data-baseweb="tab-list"] { gap: .2rem; border-bottom: 1px solid #EDEDEC; }
.stTabs [data-baseweb="tab"] { border-radius: 4px 4px 0 0; padding: .35rem .8rem; color: #787774; }
.stTabs [aria-selected="true"] { color: #37352F; font-weight: 600; }
/* 表格 */
[data-testid="stDataFrame"], [data-testid="stTable"] { border: 1px solid #E9E9E7; border-radius: 4px; overflow: hidden; background: #FFFFFF; }
[data-testid="stTable"] th { background: #F7F7F5; color: #787774; font-weight: 500; font-size: .8rem; }
.stAlert { border-radius: 4px; }
hr { border-color: #EDEDEC; }
/* 方角色块标签（Notion 选项色） */
.pill { display: inline-block; padding: 1px 7px; border-radius: 3px; font-size: .78rem; font-weight: 500; line-height: 1.55; vertical-align: middle; }
.pill-err { background: #FFE2DD; color: #5D1715; }
.pill-unk { background: #FADEC9; color: #49290E; }
.pill-tip { background: #D3E5EF; color: #183347; }
.pill-ok  { background: #DBEDDB; color: #1C3829; }
.pill-no  { background: #E3E2E0; color: #32302C; text-decoration: line-through; }
.pill-wait{ background: #F1F1EF; color: #787774; }
.pill-rule{ background: rgba(135,131,120,.15); color: #EB5757; font-weight: 400; font-family: "SFMono-Regular", Menlo, Consolas, monospace; font-size: .76rem; }
/* 审核台左栏（原文）粘在视野内：翻右侧发现列表时，定位结果始终可见 */
div[data-testid="stHorizontalBlock"]:has(.locate-anchor) { align-items: flex-start; }
div[data-testid="stColumn"]:has(.locate-anchor) { position: sticky; top: 3.2rem; max-height: calc(100vh - 3.6rem); overflow-y: auto; z-index: 1; }
.muted { color: #787774; font-size: .86rem; }
.eyebrow { color: #9B9A97; font-size: .8rem; margin-bottom: -.1rem; }
</style>
""" % {"accent": ACCENT}

SEV_PILL = {"错误": "pill-err", "无法核验": "pill-unk", "提示": "pill-tip"}
REVIEW_PILL = {"待审": "pill-wait", "确认": "pill-ok", "驳回": "pill-no"}


def apply():
    st.markdown(CSS, unsafe_allow_html=True)


def pill(text, cls):
    return f'<span class="pill {cls}">{text}</span>'


def sev_pill(sev):
    return pill(sev, SEV_PILL.get(sev, "pill-rule"))


def review_pill(status):
    return pill(status, REVIEW_PILL.get(status, "pill-wait"))


def rule_pill(rid, name=""):
    return pill(f"{rid} {name}".strip(), "pill-rule")
