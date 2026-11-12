#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SRS 章节引用同步脚本（把其他文档中沿用的 v1.0 章节号改成 v1.1 的新结构编号）。

背景
----
《需求规格说明书》按课程要求把正文结构调整为 8 个部分（项目概述 / 用户需求 / 领域与数据 /
规则与权限 / 行为与状态 / 质量要求 / 验收与验证 / 基线与人机协作），并新增附录 A~D。
A 报告、B 报告、附件与提交清单中原写的 v1.0 章节号（如"SRS 第 6 章业务规则"）需要同步。

执行情况（重要）
----------------
**本次修订已完成同步**：一次性扫描替换共 73 处，随后人工精修 6 处（替换串与原文重复、多章叠加
形式、整行改写等），并逐条核对残留引用 —— 最终剩余 9 处均为 v1.1 的新编号（第 4 章 规则与权限、
第 6 章 质量要求、3.3 数据字典）或提交清单中的说明性示例，无遗漏。

本脚本保留为**可复用工具**：若后续再次调整 SRS 结构，可修改 RULES 后重新运行。

设计要点
--------
1. **单次扫描**：所有规则合并为一个正则一次替换，避免"A 规则的结果被 B 规则二次替换"的链式错误
   （初版按顺序逐条 sub 曾把 "第 4 章" 改成 "第 2 章" 后又被 "第 2 章→第 1 章" 规则命中）。
2. **替换串不带冗余词**：如把 "SRS 第 8 章数据字典" 整体匹配后再替换，避免出现"数据字典数据字典"。
3. **跳过说明性文字**：含 `如"SRS` 的行是文档里的举例，不得替换。

用法（在项目根目录执行）
------------------------
    python tools/sync_srs_refs.py            # 干跑：只打印将要发生的替换，不改文件
    python tools/sync_srs_refs.py --apply    # 真正写回

不会修改《需求规格说明书.md》本身，也不会改动任何业务编号（FR/BR/PR/NFR/AC/ST/SC/PV/V/AI/E/G/M）。
"""

import glob
import io
import os
import re
import sys

# 顺序敏感：更具体的（多章叠加、带后缀的）规则必须排在更宽泛的规则之前
RULES = [
    (r"SRS\s*第\s*5\s*[、，,]\s*6\s*[、，,]\s*7\s*章", "SRS 第 2、4 章"),
    (r"SRS\s*第\s*4\s*[、，,]\s*9\s*[、，,]\s*11\s*[、，,]\s*12\s*章", "SRS 第 2、5、7 部分"),
    (r"SRS\s*第\s*4\s*[、，,]\s*9\s*章", "SRS 第 2、5 部分"),
    (r"SRS\s*第\s*2\s*[、，,]\s*3\s*[、，,]\s*6\s*章", "SRS 第 1、3、4 部分"),
    (r"第\s*4\s*[~～\-]\s*12\s*章", "第 2~7 部分"),
    (r"SRS\s*第\s*6\s*章与第\s*8\s*章", "SRS 第 4 章与 3.3 数据字典"),
    (r"SRS\s*第\s*8\s*章\s*数据字典", "SRS 3.3 数据字典"),
    (r"第\s*8\s*章\s*数据字典", "3.3 数据字典"),
    (r"SRS\s*第\s*8\s*章", "SRS 3.3 数据字典"),
    (r"SRS\s*第\s*7\s*章\s*的角色权限矩阵", "SRS 4.2 角色权限矩阵"),
    (r"第\s*7\s*章\s*权限矩阵", "4.2 角色权限矩阵"),
    (r"SRS\s*第\s*7\s*章", "SRS 4.2"),
    (r"SRS\s*1\.3\s*术语表", "SRS 3.1 术语表"),
    (r"SRS\s*1\.5\s*节", "SRS 附录 C 编号规则"),
    (r"SRS\s*2\.4\s*节\s*干系人表", "SRS 1.3 涉众与确认责任表"),
    (r"SRS\s*2\.4\s*节", "SRS 1.3"),
    (r"SRS\s*第\s*12\s*章", "SRS 7.4 需求追踪矩阵"),
    (r"SRS\s*第\s*11\s*章", "SRS 7.1 验收标准"),
    (r"SRS\s*第\s*10\s*章", "SRS 第 6 章"),
    (r"SRS\s*第\s*9\s*章", "SRS 第 5 章"),
    (r"SRS\s*第\s*6\s*章", "SRS 第 4 章"),
    (r"SRS\s*第\s*5\s*章", "SRS 第 2 章"),
    (r"SRS\s*第\s*4\s*章", "SRS 第 2 章"),
    (r"SRS\s*第\s*2\s*章", "SRS 第 1 章"),
    (r"SRS\s*4\.2\s*节", "SRS 2.3 用例清单"),
    (r"SRS\s*4\.2", "SRS 2.3"),
    (r"SRS\s*4\.1", "SRS 2.1 用户故事清单"),
    (r"SRS\s*v1\.0", "SRS v1.1"),
]

COMBINED = re.compile("|".join("(?:%s)" % p for p, _ in RULES))
SKIP = re.compile("如\x22SRS")   # 说明性示例行（如"SRS 第 6 章业务规则"）不替换

TARGETS = (
    ["A-需求分析质量验证报告.md", "B-AI智能体使用验证报告.md", "提交清单与填写说明.md", "prototype/README.md"]
    + sorted(glob.glob("attachments/*.md"))
    + sorted(glob.glob("attachments/evidence/*.md"))
)


def replace_once(text):
    """单次扫描替换：返回 (新文本, 替换处数, 变更行示例)。"""
    hits = 0
    samples = []

    def repl(m):
        s = m.group(0)
        for p, r in RULES:
            if re.fullmatch(p, s):
                return r
        return s

    out_lines = []
    for line in text.split("\n"):
        if ("SRS" in line or "第" in line) and not SKIP.search(line):
            line, n = COMBINED.subn(repl, line)
            if n:
                hits += n
                samples.append(line.strip()[:120])
        out_lines.append(line)
    return "\n".join(out_lines), hits, samples


def main(argv):
    apply_changes = "--apply" in argv
    total_files = total_hits = 0
    for path in TARGETS:
        if not os.path.isfile(path):
            continue
        text = io.open(path, encoding="utf-8").read()
        new_text, hits, samples = replace_once(text)
        if hits:
            total_files += 1
            total_hits += hits
            print("== %s -> %d 处" % (path, hits))
            if not apply_changes:
                for s in samples:
                    print("   ", s)
            if apply_changes:
                io.open(path, "w", encoding="utf-8").write(new_text)
    print("\n合计：%d 个文件、%d 处引用%s" % (total_files, total_hits,
          "（已写回）" if apply_changes else "（干跑，未修改文件）"))
    if not apply_changes and total_hits:
        print("确认无误后运行：python tools/sync_srs_refs.py --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
