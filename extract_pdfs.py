"""提取 cailiao 下所有 PDF 文本层，统计字数与页数"""
import os, sys
sys.path.insert(0, "")
os.environ["PYTHONPATH"] = ""
import fitz  # pymupdf

root = "/data/cy/shujuku/cailiao"
out = "/data/cy/shujuku/extracted"
os.makedirs(out, exist_ok=True)

results = []
for dirpath, _, files in os.walk(root):
    for fn in sorted(files):
        if not fn.lower().endswith(".pdf"):
            continue
        p = os.path.join(dirpath, fn)
        try:
            doc = fitz.open(p)
            n = doc.page_count
            chars = 0
            txt_all = []
            for page in doc:
                t = page.get_text("text")
                chars += len(t)
                txt_all.append(t)
            doc.close()
            rel = os.path.relpath(dirpath, root)
            base = fn.replace(".pdf", "").replace(" ", "_").replace("(", "").replace(")", "")
            op = os.path.join(out, f"{rel}_{base}.txt")
            with open(op, "w", encoding="utf-8") as f:
                f.write("\n\n===PAGE===\n\n".join(txt_all))
            results.append((rel, fn, n, chars, "OK"))
        except Exception as e:
            results.append((rel, fn, -1, -1, f"ERR {e}"))

for rel, fn, n, chars, st in results:
    status = f"{n}页/{chars}字" if st == "OK" else st
    print(f"[{rel}] {fn} -> {status}")