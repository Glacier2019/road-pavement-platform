import os, sys, json
os.environ["PYTHONPATH"] = ""
import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

path = "/home/zhanghe/下载/1788480134217..jpg"
img = Image.open(path).convert("RGB")
arr = np.array(img)
print("size:", arr.shape)

ocr = RapidOCR(
    rec_model_path=os.path.expanduser("~/ocr_models/ch_PP-OCRv5_rec_server.onnx"),
    rec_keys_path=os.path.expanduser("~/ocr_models/ppocrv5_dict.txt"),
)
result, _ = ocr(arr)
lines = []
if result:
    # sort top-to-bottom, then left-to-right
    boxes = sorted(result, key=lambda b: (round(min(p[1] for p in b[0]) / 20), min(p[0] for p in b[0])))
    for box, text, score in boxes:
        y = int(min(p[1] for p in box)); x = int(min(p[0] for p in box))
        lines.append({"x": x, "y": y, "text": text, "score": round(float(score), 3)})
for ln in lines:
    print(f"y={ln['y']:4d} x={ln['x']:4d} s={ln['score']:.2f} {ln['text']}")
print("TOTAL:", len(lines))