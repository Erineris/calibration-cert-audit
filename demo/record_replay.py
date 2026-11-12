#!/usr/bin/env python3
"""
录制脚本：用真实审核引擎跑一遍一个 PDF 目录，把「在线回放版」需要的数据写到 docs/data/。

用法（在项目根目录，用装好依赖的虚拟环境）：
    python3 demo/record_replay.py tests/bulk/out
    python3 demo/record_replay.py tests/bulk/out --out docs/data --width 1000 --format jpg --quality 80

写出的结构（页面 docs/index.html 读取的就是这些，全部相对路径）：
    docs/data/summary.json            汇总：总份数、真实总耗时、三档计数、录制时间线（回放计数器用）、页图格式
    docs/data/results.json            全部证书的列表（与实时版 /results 接口同结构，1000 份都在）
    docs/data/cert/<文件名>.json      只给「需要人看」的证书：详情 + 发现列表（与实时版 /cert 接口同结构）
    docs/data/page/<文件名>/<页>.jpg  只给「需要人看」的证书：每页一张图（默认宽 1000px）

数字全部来自引擎真实运行，脚本不编造任何计数；总耗时是引擎从开始到结束的真实用时。
"""
import argparse
import glob
import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))


def collect_paths(pdf_dir: Path):
    man = pdf_dir / "manifest.json"
    if man.exists():
        with open(man, encoding="utf-8") as f:
            return [str(pdf_dir / it["file"]) for it in json.load(f)["items"]]
    return glob.glob(str(pdf_dir / "*.pdf"))


def render_pages(pdf_path: str, out_dir: Path, width: int, fmt: str, quality: int):
    import fitz
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    with fitz.open(pdf_path) as doc:
        for i, pg in enumerate(doc, 1):
            zoom = width / pg.rect.width
            pix = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            data = pix.tobytes("jpeg", jpg_quality=quality) if fmt == "jpg" else pix.tobytes("png")
            (out_dir / f"{i}.{fmt}").write_bytes(data)
            n += 1
    return n


def dir_size(p: Path):
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def main():
    ap = argparse.ArgumentParser(description="用真实引擎录制批量审核，生成在线回放版数据")
    ap.add_argument("pdf_dir", help="PDF 目录（例如 tests/bulk/out）")
    ap.add_argument("--out", default=str(ROOT / "docs" / "data"), help="输出目录（默认 docs/data）")
    ap.add_argument("--width", type=int, default=1000, help="页图宽度像素（默认 1000）")
    ap.add_argument("--format", choices=["jpg", "png"], default="jpg", help="页图格式（默认 jpg，体积小得多）")
    ap.add_argument("--quality", type=int, default=80, help="jpg 质量 1-100（默认 80）")
    ap.add_argument("--max-mb", type=float, default=30.0, help="data/ 总体积上限 MB，超了报错（默认 30）")
    ap.add_argument("--sample-interval", type=float, default=0.2, help="记录进度时间线的间隔秒数（默认 0.2）")
    ap.add_argument("--forbid-list", default=None,
                    help="可选：一行一个词的文件；录完扫描输出，命中任一词就报错（用来拦截不该公开的字样）")
    args = ap.parse_args()

    pdf_dir = Path(args.pdf_dir).resolve()
    if not pdf_dir.is_dir():
        sys.exit(f"错误：目录不存在 {pdf_dir}。示例批次请先运行 python3 tests/bulk/make_bulk.py")
    paths = collect_paths(pdf_dir)
    if not paths:
        sys.exit(f"错误：{pdf_dir} 下没有 PDF。")

    import batch_engine
    try:
        batch_engine.check_engine()
    except FileNotFoundError as e:
        sys.exit(f"错误：{e}")

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    # 只清理本脚本自己生成的内容，不碰 out 下别的东西
    for sub in ("cert", "page"):
        shutil.rmtree(out / sub, ignore_errors=True)
    for f in ("summary.json", "results.json"):
        (out / f).unlink(missing_ok=True)

    # ---------- 1. 真跑，同时记录进度时间线 ----------
    print(f"录制开始：{len(paths)} 份 PDF（{pdf_dir}）")
    job = batch_engine.start(paths, label="录制")
    timeline = []
    while not job.finished:
        p = job.progress()
        timeline.append({"t": p["elapsed_sec"], "done": p["done"], "flagged": p["flagged"]})
        print(f"\r  {p['done']}/{p['total']}  {p['elapsed_sec']:.1f}s", end="", flush=True)
        time.sleep(args.sample_interval)
    final = job.progress()
    print(f"\r  {final['done']}/{final['total']}  {final['elapsed_sec']:.1f}s  完成")
    if final["error"]:
        sys.exit(f"错误：引擎运行失败：{final['error']}")
    # 历史对比在全部跑完之后才做，最后一个点用最终值，保证回放终点与结果一致
    timeline.append({"t": final["elapsed_sec"], "done": final["done"], "flagged": final["flagged"]})

    # ---------- 2. 汇总与列表 ----------
    results = job.results()
    flagged_files = [c["file"] for c in results["certs"] if c["needs_review"]]
    summary = {
        "schema": 1,
        "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": final["total"],
        "elapsed_sec": final["elapsed_sec"],
        "flagged": final["flagged"],
        "by_severity": final["by_severity"],
        "throughput_per_hour": final["throughput_per_hour"],
        "page_ext": args.format,
        "page_width_px": args.width,
        "detail_count": len(flagged_files),
        "timeline": timeline,
    }
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------- 3. 需要人看的证书：详情 JSON + 页图 ----------
    (out / "cert").mkdir(exist_ok=True)
    pages = 0
    for i, fname in enumerate(flagged_files, 1):
        detail = job.cert(fname)
        (out / "cert" / f"{fname}.json").write_text(json.dumps(detail, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        pages += render_pages(job.certs[fname]["path"], out / "page" / fname, args.width, args.format, args.quality)
        print(f"\r  详情与页图：{i}/{len(flagged_files)}", end="", flush=True)
    print()

    # ---------- 4. 体积与字样检查 ----------
    size_mb = dir_size(out) / 1024 / 1024
    print(f"写出：results.json（{results['total']} 份）、详情 {len(flagged_files)} 份、页图 {pages} 张，data/ 共 {size_mb:.1f} MB")
    if args.forbid_list:
        words = [w.strip() for w in Path(args.forbid_list).read_text(encoding="utf-8").splitlines() if w.strip()]
        hits = []
        for f in out.rglob("*.json"):
            text = f.read_text(encoding="utf-8")
            hits += [(str(f.relative_to(out)), w) for w in words if w in text]
        if hits:
            sys.exit("错误：输出里命中禁用词（不要公开）：\n" + "\n".join(f"  {a}: {w}" for a, w in hits[:20]))
        print(f"禁用词扫描：{len(words)} 个词，JSON 里 0 命中（页图是位图，需人工抽查）")
    if size_mb > args.max_mb:
        sys.exit(f"错误：体积 {size_mb:.1f} MB 超过上限 {args.max_mb} MB。可改小 --width 或 --quality，或用 --format jpg。")
    print("录制完成。下一步：用本地 HTTP 服务打开 docs/index.html 验收，例如 python3 -m http.server -d docs 8080")


if __name__ == "__main__":
    main()
