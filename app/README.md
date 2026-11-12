# app/ — 校准证书审核引擎 + 审核台

引擎按规则包（`rules.yaml`）逐条判断校准证书 PDF，每条发现都带页码和原文位置；**判断全部由程序规则产生，AI 不判 Pass/Fail**。解释不了的标「无法核验」交给人。

## 依赖
在仓库根目录：
```bash
python -m venv .venv
source .venv/bin/activate          # Windows：.venv\Scripts\activate
pip install -r requirements.txt
```

## 命令行（JSON 是引擎与界面之间的唯一接口）
```bash
# 在仓库根目录执行；tests/base/ 里是 4 份完全虚构的示例证书
python -m app.cli tests/base/base_A.pdf tests/base/base_B_errors.pdf \
  tests/base/base_2025.pdf tests/base/base_2026.pdf -o app/out --no-llm
```
输出 `app/out/<文件名>.cert.json`（解析结果）、`<文件名>.findings.json`（发现）、`history.json`（多份且管理编号/出厂编号相同时自动做 R6 历史对比，提示挂在较新的证书上）。`app/out/` 已在 `.gitignore` 里。

选项：`--rules 路径` 换规则包；`--no-llm` 跳过第二路大模型抽取。

### 可选：第二路大模型抽取（默认关闭）
`llm_extract.py` 是**可选、默认关闭**的第二路抽取：只负责把关键字段再抽一遍，与程序解析结果对比，不一致就标「无法核验」交人，不做任何判断。设了 `LLM_API_KEY`（或 `OPENAI_API_KEY`）、`LLM_BASE_URL`、`LLM_MODEL` 环境变量才启用（OpenAI 兼容接口）；不设则 `extract_check.llm_agree = null`，不阻塞主流程。
**本仓库所有测试数字（验收、批量实测）都是不调模型跑出来的**（`--no-llm`）。

## 界面
```bash
python -m streamlit run app/审核台.py
```
三页：审核台（上传 PDF → 左原文红框 / 右发现列表 → 确认/驳回 → 导出 Excel；左侧「载入样例四份证书」一键载入 `tests/base/`）、规则（改 `rules.yaml` 阈值并保存）、历史对比（两份并排 + 漂移表）。
`app/ui.py` 是兼容薄壳，`python -m streamlit run app/ui.py` 同样可用。

## 文件
- `parse.py`：抽取（PyMuPDF 词坐标 → 按 y 聚行、按 x 间距合并单元格 → 表头列归属），不做判断
- `rules.yaml`：规则包 R0–R7（阈值、严重度、出处、边界），界面可改
- `rules.py`：规则引擎 + 历史对比
- `llm_extract.py`：可选的第二路抽取（见上），只抽不判
- `cli.py`：命令行入口；`审核台.py`：Streamlit 审核台；`theme.py`：界面样式

## 在虚构示例上的预期结果
| 文件 | 预期发现 |
|---|---|
| `base_A.pdf`（版式 A，无结论列） | 仅 R7（模板占位符残留） |
| `base_B_errors.pdf` | R2 环境温度 35℃ 错误；R1 湿度 40%RH 修正值 +6 超过允差 ±5 却标 Pass 错误；60 / 80%RH 超差且已标 Fail 两条提示；R7 |
| `base_2025.pdf` → `base_2026.pdf` | R6 历史对比三条：温度校准点 10→15、制造商 空→示例制造厂、标准器 DP-A01→DP-B01；两份各有 R7 |

完整验收（39 个用例、67 条期望发现）见 `tests/README.md` 与 `tests/report.md`。

## 已知局限
- 只验证了「文字层 PDF」的两种版式（有 / 无结论列）；扫描件、其它版式没有测。
- 驳回某条发现后，系统**不会**自动生成修订建议或更新规则：这条反馈闭环在代码里没有接通。
