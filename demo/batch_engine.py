"""批量审核任务：给 demo/server.py 的 /api/batch/* 用，调用 app/ 下的真实审核引擎（纯规则，不调用任何模型）。

目录约定（全部按本文件位置相对解析，不写死绝对路径）：
    <根>/demo/batch_engine.py   本文件
    <根>/app/                   审核引擎
    <根>/tests/bulk/out/        示例批次（先运行 tests/bulk/make_bulk.py 生成）

一个任务 = 一批 PDF。后台线程里用多进程跑，前端轮询进度；跑完按「错误 > 无法核验 > 提示 > 无问题」排序。
"""
import glob
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SAMPLES = {"bulk": os.path.join(ROOT, "tests", "bulk", "out")}
SEV_ORDER = ["错误", "无法核验", "提示"]
_RULES = None
JOBS = {}
_LOCK = threading.Lock()


def _audit(path):
    """子进程里跑：解析 + 规则，与 app.cli.analyze 单份部分一致。"""
    global _RULES
    from app.parse import parse_pdf
    from app.rules import load_rules, run_rules
    if _RULES is None:
        _RULES = load_rules()
    c = parse_pdf(path)
    c["extract_check"] = {"llm_agree": None, "diffs": [], "note": "--no-llm"}
    c["findings"] = run_rules(c, _RULES)
    c.pop("page_texts", None)
    return c


def _counts(findings):
    d = {s: 0 for s in SEV_ORDER}
    for f in findings:
        if f.get("severity") in d:
            d[f["severity"]] += 1
    return d


def _attach_history(certs):
    from app.rules import load_rules, compare_history, match_key
    rules = load_rules()
    groups = {}
    for c in certs:
        k = match_key(c)
        if k:
            groups.setdefault(k, []).append(c)
    for lst in groups.values():
        if len(lst) < 2:
            continue
        lst.sort(key=lambda c: c.get("calib_date") or "")
        for old, new in zip(lst, lst[1:]):
            for f in compare_history(old, new, rules):
                f["id"] = f"F{len(new['findings']) + 1:03d}"
                new["findings"].append(f)


class Job:
    def __init__(self, paths, tmpdir=None, label=""):
        self.id = uuid.uuid4().hex[:8]
        self.paths = paths
        self.tmpdir = tmpdir
        self.label = label
        self.total = len(paths)
        self.done = 0
        self.flagged = 0
        self.by_sev = {s: 0 for s in SEV_ORDER}
        self.t0 = None
        self.t_end = None
        self.certs = {}
        self.error = None
        self.finished = False
        self.reviews = {}  # {file: {finding_id: {"status": 确认|驳回, "note", "at"}}}，只存在内存里，不改规则

    def review_summary(self, fname):
        """需要人看 = 有错误或无法核验；这些条目都有了确认/驳回，才算已处理。"""
        c = self.certs.get(fname) or {}
        rv = self.reviews.get(fname, {})
        must = [f["id"] for f in c.get("findings", []) if f.get("severity") in ("错误", "无法核验")]
        done = [i for i in must if i in rv]
        return {"confirmed": sum(1 for v in rv.values() if v["status"] == "确认"),
                "rejected": sum(1 for v in rv.values() if v["status"] == "驳回"),
                "must": len(must), "done": len(done), "reviewed": bool(must) and len(done) == len(must)}

    def set_review(self, fname, finding_ids, status, note=""):
        c = self.certs.get(fname)
        if not c:
            return None
        ids = {f["id"] for f in c["findings"]}
        rv = self.reviews.setdefault(fname, {})
        for fid in finding_ids:
            if fid not in ids:
                continue
            if status == "撤销":
                rv.pop(fid, None)
            else:
                rv[fid] = {"status": status, "note": note, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        return {"file": fname, "reviews": rv, "review": self.review_summary(fname),
                "reviewed_total": sum(1 for f in self.certs if self.review_summary(f)["reviewed"])}

    def elapsed(self):
        if self.t0 is None:
            return 0.0
        return (self.t_end or time.perf_counter()) - self.t0

    def run(self):
        self.t0 = time.perf_counter()
        try:
            with ProcessPoolExecutor(os.cpu_count()) as ex:
                futs = [ex.submit(_audit, p) for p in self.paths]
                for fu in as_completed(futs):
                    c = fu.result()
                    cnt = _counts(c["findings"])
                    with _LOCK:
                        self.certs[c["file"]] = c
                        self.done += 1
                        for s in SEV_ORDER:
                            self.by_sev[s] += cnt[s]
                        if cnt["错误"] or cnt["无法核验"]:
                            self.flagged += 1
            _attach_history(list(self.certs.values()))
            # 历史对比会给新证书加 R6 提示，重新统计一次
            by_sev = {s: 0 for s in SEV_ORDER}
            flagged = 0
            for c in self.certs.values():
                cnt = _counts(c["findings"])
                for s in SEV_ORDER:
                    by_sev[s] += cnt[s]
                flagged += 1 if (cnt["错误"] or cnt["无法核验"]) else 0
            with _LOCK:
                self.by_sev, self.flagged = by_sev, flagged
        except Exception as e:  # noqa
            self.error = str(e)
        finally:
            self.t_end = time.perf_counter()
            self.finished = True

    def progress(self):
        el = self.elapsed()
        with _LOCK:
            return {"job_id": self.id, "label": self.label, "total": self.total, "done": self.done,
                    "elapsed_sec": round(el, 2), "flagged": self.flagged, "by_severity": dict(self.by_sev),
                    "throughput_per_hour": round(self.done / el * 3600) if el > 0 and self.done else 0,
                    "finished": self.finished, "error": self.error}

    def results(self):
        rows = []
        for c in self.certs.values():
            cnt = _counts(c["findings"])
            top = next((f for s in SEV_ORDER for f in c["findings"] if f.get("severity") == s), None)
            ins = c.get("instrument") or {}
            rows.append({"file": c["file"], "cert_no": c.get("cert_no"), "layout": c.get("layout"),
                         "calib_date": c.get("calib_date"),
                         "instrument": {k: ins.get(k) for k in ("name", "model", "mgmt_no", "serial_no")},
                         "counts": cnt, "needs_review": bool(cnt["错误"] or cnt["无法核验"]),
                         "review": self.review_summary(c["file"]),
                         "top_finding": None if not top else {"rule_id": top["rule_id"], "severity": top["severity"],
                                                              "reason": top.get("reason")}})
        rows.sort(key=lambda r: (-r["counts"]["错误"], -r["counts"]["无法核验"], -r["counts"]["提示"], r["file"]))
        for i, r in enumerate(rows, 1):
            r["rank"] = i
        p = self.progress()
        return {"job_id": self.id, "total": self.total, "elapsed_sec": p["elapsed_sec"], "flagged": p["flagged"],
                "by_severity": p["by_severity"], "certs": rows}

    def cert(self, fname):
        c = self.certs.get(fname)
        if not c:
            return None
        import fitz
        with fitz.open(c["path"]) as doc:
            sizes = [[round(pg.rect.width, 1), round(pg.rect.height, 1)] for pg in doc]
        out = {k: v for k, v in c.items() if k not in ("findings", "path")}
        out["page_sizes_pt"] = sizes
        rv = self.reviews.get(fname, {})
        findings = [dict(f, review=rv.get(f["id"])) for f in c["findings"]]
        return {"cert": out, "findings": findings, "review": self.review_summary(fname)}

    def page_png(self, fname, page_no, width=1000):
        c = self.certs.get(fname)
        if not c:
            return None
        import fitz
        with fitz.open(c["path"]) as doc:
            if not 1 <= page_no <= len(doc):
                return None
            pg = doc[page_no - 1]
            zoom = width / pg.rect.width
            return pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).tobytes("png")


def check_engine():
    """启动任务前先确认引擎在位，缺了就给出能照做的提示，而不是在子进程里报一串堆栈。"""
    if not os.path.isdir(os.path.join(ROOT, "app")):
        raise FileNotFoundError(f"找不到审核引擎目录 {os.path.join(ROOT, 'app')}，请确认 app/ 与 demo/ 在同一级目录下。")
    try:
        import app.parse  # noqa: F401
        import app.rules  # noqa: F401
        import fitz  # noqa: F401
    except ImportError as e:
        raise FileNotFoundError(f"审核引擎依赖未装好（{e}）。请先在项目虚拟环境里执行 pip install -r requirements.txt。")


def start(paths, tmpdir=None, label=""):
    check_engine()
    job = Job(sorted(paths), tmpdir, label)
    JOBS[job.id] = job
    threading.Thread(target=job.run, daemon=True).start()
    return job


def start_sample(name):
    d = SAMPLES.get(name)
    if not d or not os.path.isdir(d) or not any(f.lower().endswith(".pdf") for f in os.listdir(d)):
        raise FileNotFoundError(
            f"示例批次 {name} 还没有生成（应在 {d} 下有 PDF）。"
            "请先在项目根目录运行：python3 tests/bulk/make_bulk.py，再点「运行 1000 份演示样本」。")
    man = os.path.join(d, "manifest.json")
    if os.path.exists(man):
        with open(man, encoding="utf-8") as f:
            paths = [os.path.join(d, it["file"]) for it in json.load(f)["items"]]
    else:
        paths = glob.glob(os.path.join(d, "*.pdf"))
    return start(paths, label=f"示例批次 {name}")


def start_uploaded(named_bytes):
    tmp = tempfile.mkdtemp(prefix="batch_upload_")
    paths = []
    for name, data in named_bytes:
        p = os.path.join(tmp, os.path.basename(name) or f"upload_{len(paths)}.pdf")
        with open(p, "wb") as f:
            f.write(data)
        paths.append(p)
    return start(paths, tmpdir=tmp, label="上传")


def cleanup():
    for j in JOBS.values():
        if j.tmpdir:
            shutil.rmtree(j.tmpdir, ignore_errors=True)
