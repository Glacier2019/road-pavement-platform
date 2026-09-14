"""多轮 Qwen3-VL-Plus 读取手写页面（不打印 key）"""
import os, sys, json, base64, mimetypes, time
from urllib import request

# 从 ~/.reasonix/.env 载入 key
env_path = os.path.expanduser("~/.reasonix/.env")
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line.startswith("DASHSCOPE_API_KEY="):
            os.environ["DASHSCOPE_API_KEY"] = line.split("=", 1)[1]

api_key = os.environ.get("DASHSCOPE_API_KEY", "")
assert api_key, "no key"

img_path = "/home/zhanghe/下载/1788480134217..jpg"
with open(img_path, "rb") as f:
    b64 = base64.b64encode(f.read()).decode()
mime = "image/jpeg"

def qwen(prompt, temperature):
    payload = json.dumps({
        "model": "qwen3-vl-plus",
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            {"type": "text", "text": prompt}]}],
        "temperature": temperature,
        "max_tokens": 3000,
    }).encode()
    req = request.Request(
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
    try:
        with request.urlopen(req, timeout=120) as r:
            d = json.loads(r.read())
            return d["choices"][0]["message"]["content"]
    except Exception as e:
        body = e.read().decode()[:500] if hasattr(e, "read") else str(e)
        return f"ERROR: {body}"

prompts = [
    "这是一张手写的项目/数据库设计笔记照片。请逐字忠实转写这张纸上所有内容，包括标题、编号条目（如 1、2、3…）、中文文字、英文（如 postgres）、日期、箭头连接关系。按纸上原有结构和顺序输出，不要润色、不要补充、不要编造。看不清的字用【?】标注。",
    "请再次仔细阅读这张手写笔记，按从上到下、从左到右的顺序，把所有可见文字和条目完整转写出来，包括每个条目的子要点和缩进关系、以及各条目之间的连线/箭头含义。逐字忠实转写，不确定处标【?】。",
    "请第三次转写这张手写笔记的全部内容，特别关注：1) 标题或页首文字；2) 所有数字编号列表及其子项；3) 提到 postgres/数据库/表/字段相关的技术词；4) 页面上的批注、箭头、框线。逐字转写，不要猜测补充。",
]

for i, p in enumerate(prompts, 1):
    temp = [0.1, 0.3, 0.6][i - 1]
    out = qwen(p, temp)
    print(f"===== ROUND {i} (temp={temp}) =====")
    print(out)
    print()
    time.sleep(1)