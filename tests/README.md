# tests/ — 验收与批量实测

底板是 4 份**完全虚构**的校准证书（`tests/base/`），由 `tests/tools/make_base.py` 用代码从零排版生成：实验室、委托方、地址、证书编号、仪器与标准器信息全部虚构，不含任何图片（无标志、印章、二维码、手写签名），每页页脚标注「虚构示例证书，仅供演示」。所有命令在仓库根目录执行，依赖见 `requirements.txt`（`pip install -r requirements.txt`）。

```bash
python tests/tools/make_base.py                  # 生成 4 份虚构底板 → tests/base/*.pdf（内容可重复生成）
python tests/tools/make_cases.py                 # 由底板改出 34 份用例 + 标准答案 → tests/cases/（内容可重复生成）
python tests/run_acceptance.py --tag full        # 底板 4 + 改造 34 + 历史对比 1 = 39 个用例，写 tests/report.md
python tests/run_acceptance.py --only r1_over_pass.pdf --tag dbg   # 单跑一个用例
```

当前结果：**39/39 用例全对，67/67 期望发现命中，漏报 0、误报 0**（全程 `--no-llm`，不调用任何模型；详见 `tests/report.md`）。

## 四份底板
| 文件 | 角色 |
|---|---|
| `base_A.pdf` | 版式 A（校准结果表没有「结论」列），无规则问题，只剩模板占位符 R7；考「不误报」 |
| `base_B_errors.pdf` | 版式 B（有结论列），含真错：环境温度 35℃、湿度点 +6>±5 标 Pass、两个超差已标 Fail |
| `base_2025.pdf` | 版式 B，2025 年报告，无规则问题（只剩 R7）；两台标准器有效期到 2026/10/13 |
| `base_2026.pdf` | 同一台仪器的 2026 年报告：校准点 10→15、制造商 空→示例制造厂、标准器 DP-A01→DP-B01（R6）；第 3 页文字流乱序，且有一个被拆成「+0.」「6」的单元格 |

## 文件
- `tools/make_base.py`：生成底板。用 PyMuPDF 自带的 CJK 字体（`fitz.Font("cjk")`），不依赖系统字体。
- `tools/make_cases.py`：用例定义（改哪页哪个字 → 期望哪些发现）的唯一来源。改法：PyMuPDF redact 原 span（不动线条）→ 在原基线、原字号重写；纯西文用 Times，含中文用自带 CJK 字体并子集化。改完自动校验「只有目标 span 变了」，校验不过就报 FAIL 不出文件。
- `cases/expected.yaml`：文件名 → 期望发现（rule_id + severity，可带 page / hint / forbid / disputed）+ allow（可接受的额外发现）。字段含义见文件头 `_说明`。
- `run_acceptance.py`：每个 PDF 复制到临时空目录再调 `python -m app.cli <pdf> -o out --no-llm`，不往仓库别处写东西；原始输出存 `runs/<tag>/`（已 gitignore）。
- `tools/dump.py`：打印 PDF 每页 span（文字 + 坐标 + 字体），定位要改的字段用。
- `bulk/`：批量实测（见下）。

用例覆盖（每条规则至少一个命中 + 一个边界不命中）：

| 规则 | 命中 | 边界不命中 |
|---|---|---|
| R1 | 超差标 Pass（正/负向）、未超差标 Fail、超差已标 Fail→提示、拆开的单元格 +0.\|6→+2.6 | 修正值 = 允差、结论留空、无结论列（base_A） |
| R2 | 环境温度 19.9 / 30.1（版式 A）、湿度 85 | 20.0、30.0、湿度 84 |
| R3 | 温度点 101、−41、湿度点 4 | 100、−40、98 |
| R4 | 第一台早一天、只有第二台过期（三种版式各一） | 有效期 = 校准日期 |
| R5 | 第 1 页 / 第 2 页 / 全部编号删除、第 3 页编号改一位、单位 ℉、修正值缺、校准日期缺 | 底板（各页一致、单位正确） |
| R6 | 2025→2026：校准点、制造商、标准器 | 单份文件不应报 R6 |
| R7 | 所有保留占位符的文件 | 删掉占位符的 r7_removed |

## 批量实测（`bulk/`）
```bash
python tests/bulk/make_bulk.py --workers 10      # 由 3 份无问题底板造 1000 份（带错 10%、边界约 20%、历史对比 50 对、固定随机种子）+ 标准答案 → tests/bulk/out/（已 gitignore）
python tests/bulk/run_bulk.py --check 100        # 先抽 100 份对答案
python tests/bulk/run_bulk.py --workers 10       # 单进程 / 多进程 / 逐份启动三种跑法，写 tests/bulk/report.md
```
每份证书只换身份（证书编号、管理编号或出厂编号），再按比例叠加上面用例里的改法；测量数据与底板相同。结论里的数字只说明「在这 3 份底板、2 种版式上量放大后速度稳定、结果不变」，不代表真实证书上的准确率：真实环境需要用真实证书盲测。

## 可选的大模型抽取
`app/llm_extract.py` 是可选、默认关闭的第二路抽取。**本目录所有数字都是不调模型跑出来的。**
