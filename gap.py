# -*- coding: utf-8 -*-
import io
F = '工程决策记录.md'
src = io.open(F, encoding='utf-8').read()

ANCHOR = '### 处置建议（未执行）'
assert src.count(ANCHOR) == 1, 'n=%d' % src.count(ANCHOR)

ADD = (
  '### ★ 现有守卫抦不住它（实测，不是推测）\n'
  '\n'
  '`scaffold/tests/contract/test_deliverable_truth.py` 的断言 A 叫做\n'
  '\u300c生成脚本里零硬编码表数/DDL 版本」，扫的就是 `docpipe/scripts/gen_*.py`\n'
  '—— **`gen_er.py` 在扫描范围内**，而它**过了**。\n'
  '\n'
  '原因是那三条禁止模式都很窄：\n'
  '\n'
  '```python'\n'
  '_FORBIDDEN = ['\n'
  '    (r"物理\\s*\\d+\\s*表",        "硬编码物理表数"),'\n'
  '    (r"10_ddl_v\\d+\\.\\d+\\.sql", "硬编码 DDL 文件名"),'\n'
  '    (r"EXPECTED_TABLES\\s*=\\s*\\d+", "硬编码期望表数"),'\n'
  ']'\n'
  '```'\n'
  '\n'
  '`gen_er.py` 写的是 **`DDL v0.3`**（不是文件名 `10_ddl_v0.3.sql`），\n'
  '也没写「物理 35 表」（它把表名逐个列在 `t()` 里），\n'
  '所以**三条都不命中**。守卫在这里是**真空的**。\n'
  '\n'
  '> 教训：「我们有一条检查”和「那条检查能拦住**这一件**」是两回事。\n'
  '> 断言 A 存在，不代表 `gen_er.py` 这类「把旧模型整体硬编码」的风险已被覆盖。\n'
  '> 本节登记它，正是因为**没有自动化手段会发现它过期**。\n'
  '\n'
  '若将来要把它纳管，比如加一条「从 `catalog.py` 读到的域集合\n'
  '必须等于脚本里声明的域集合」，那是**另一个工单**的范围，\n'
  '不在本次登记内。\n'
  '\n')
src = src.replace(ANCHOR, ADD + ANCHOR, 1)
io.open(F, 'w', encoding='utf-8').write(src)
print('ok')