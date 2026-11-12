"""打印 PDF 每页 span（文字 + bbox + 字体），用于定位要改的字段。"""
import sys
import pymupdf as fitz
doc = fitz.open(sys.argv[1])
pages = [int(p) for p in sys.argv[2:]] or range(1, len(doc) + 1)
for pn in pages:
    page = doc[pn - 1]
    print(f"=== page {pn} size={page.rect}")
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                t = s["text"].strip()
                if t:
                    x0, y0, x1, y1 = s["bbox"]
                    print(f"{x0:6.1f} {y0:6.1f} {x1:6.1f} {y1:6.1f} {s['size']:4.1f} {s['font'][:18]:18} {t}")
