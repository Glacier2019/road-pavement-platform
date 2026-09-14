#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把终版报告 docx 抽成纯文本，便于检索与对照。"""
import sys
from docx import Document

SRC = "/data/cy/shujuku/output/路面性能数智化平台总体设计与模块搭建方案（7域主体-12域全景）.docx"
DST = "/data/cy/shujuku/output/_report_fulltext.txt"

doc = Document(SRC)
lines = []
for blk in doc.element.body.iter():
    tag = blk.tag.split('}')[-1]
    if tag == 'p':
        txt = "".join(n.text or "" for n in blk.iter() if n.tag.split('}')[-1] == 't')
        if txt.strip():
            lines.append(txt.rstrip())
    elif tag == 'tbl':
        lines.append("[TABLE]")
        for row in blk.iter():
            if row.tag.split('}')[-1] == 'tr':
                cells = []
                for c in row.iter():
                    if c.tag.split('}')[-1] == 'tc':
                        cells.append("".join(n.text or "" for n in c.iter() if n.tag.split('}')[-1] == 't').strip())
                lines.append(" | ".join(cells))

with open(DST, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("lines:", len(lines), "chars:", sum(len(l) for l in lines))
