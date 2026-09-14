"""把竖图切成横带放大，便于 VL 细读"""
from PIL import Image
import os

src = "/home/zhanghe/下载/1788480134217..jpg"
outdir = "/data/cy/shujuku/bands"
os.makedirs(outdir, exist_ok=True)
img = Image.open(src).convert("RGB")
W, H = img.size  # 1080 x 2344
print("WH:", W, H)

# 内容区大约 y 250-1950，按 6 段切，每段重叠 60px
bands = [(220, 620), (560, 960), (900, 1300), (1240, 1640), (1580, 1980), (1940, 2344)]
for i, (y0, y1) in enumerate(bands, 1):
    crop = img.crop((0, max(0, y0), W, min(H, y1)))
    crop = crop.resize((W * 3, crop.height * 3), Image.LANCZOS)
    p = f"{outdir}/band{i}.png"
    crop.save(p)
    print(p, crop.size)