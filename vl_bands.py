"""逐条横带细读：qwen3-vl-plus 放大图"""
import os, sys, json, base64, time
from urllib import request

env_path = os.path.expanduser("~/.reasonix/.env")
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line.startswith("DASHSCOPE_API_KEY="):
            os.environ["DASHSCOPE_API_KEY"] = line.split("=", 1)[1]

api_key = os.environ.get("DASHSCOPE_API_KEY", "")
assert api_key

PROMPT = ("这是道路基础设施数据库设计手写笔记的一张裁剪放大片段。请逐字忠实转写图中所有文字，"
          "按从上到下从左到右顺序，保留编号、箭头(→)、括号、星号、下划线、圈注等标记。"
          "不要润色或补充，看不清的标【?】。若图中有左右两栏/两列内容，请分别转写并说明是左栏还是右栏。")

def qwen(img_path, prompt, temp=0.2):
    with open(img_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    payload = json.dumps({
        "model": "qwen3-vl-plus",
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            {"type": "text", "text": prompt}]}],
        "temperature": temp,
        "max_tokens": 1500,
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

for i in range(1, 7):
    p = f"/data/cy/shujuku/bands/band{i}.png"
    out = qwen(p, PROMPT)
    print(f"===== BAND {i} =====")
    print(out)
    print()
    time.sleep(0.5)