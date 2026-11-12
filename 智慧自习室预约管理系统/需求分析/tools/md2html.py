#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Markdown → HTML 转换器（离线、只用 Python 标准库），用于把本组报告打印成 PDF。

为什么需要它
------------
课程要求"主报告推荐使用 Markdown；使用 Word 时应同时提供 PDF"。本机未安装 pandoc / wkhtmltopdf，
因此提供这个零依赖脚本：把 Markdown 转成带打印样式的 HTML，再用浏览器"打印为 PDF"即可。

用法（在本项目根目录下执行）
----------------------------
    python tools/md2html.py                          # 转换根目录下三份主报告
    python tools/md2html.py 需求规格说明书.md          # 转换指定文件
    python tools/md2html.py attachments/*.md         # 转换附件（通配符由 shell 展开）

生成 `xxx.html`（与源文件同目录）后：
    1) 用 Chrome / Edge 打开该 HTML；
    2) Ctrl+P → 目标选择"另存为 PDF"；
    3) 展开"更多设置"，勾选"背景图形"，边距选"默认"，即可得到与 Markdown 版本一致的 PDF。

支持的语法：标题(#~######)、**粗体**、*斜体*、`行内代码`、代码块(```)、有序/无序列表、
表格(| … |，含分隔行)、引用(>)、水平线(---)、链接([文本](地址))。
"""

import html
import os
import re
import sys

CSS = """
@page { size: A4; margin: 16mm 14mm; }
* { box-sizing: border-box; }
body {
  font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
  font-size: 10.5pt; line-height: 1.72; color: #16233a; margin: 0 auto; max-width: 900px; padding: 8px 6px 40px;
}
h1 { font-size: 19pt; border-bottom: 2px solid #2f5da8; padding-bottom: 6px; margin: 18px 0 12px; }
h2 { font-size: 15pt; border-left: 5px solid #2f5da8; padding-left: 8px; margin: 20px 0 10px; }
h3 { font-size: 12.5pt; color: #2f5da8; margin: 16px 0 8px; }
h4, h5, h6 { font-size: 11.5pt; margin: 14px 0 6px; }
p { margin: 7px 0; }
ul, ol { margin: 7px 0 7px 22px; padding: 0; }
li { margin: 3px 0; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 9.5pt; page-break-inside: auto; }
th, td { border: 1px solid #c8d4e8; padding: 5px 7px; text-align: left; vertical-align: top; word-break: break-word; }
th { background: #eef3fb; font-weight: 600; }
tr { page-break-inside: avoid; }
code { background: #f1f5fb; border-radius: 4px; padding: 1px 4px; font-family: Consolas, "Courier New", monospace; font-size: 9.5pt; }
pre { background: #f6f8fc; border: 1px solid #d8e2f2; border-radius: 6px; padding: 10px; overflow-x: auto; }
pre code { background: none; padding: 0; }
blockquote { margin: 10px 0; padding: 8px 12px; background: #fff8e8; border-left: 4px solid #c9820a; }
hr { border: none; border-top: 1px solid #d8e2f2; margin: 18px 0; }
a { color: #2f5da8; }
h1, h2, h3 { page-break-after: avoid; }
"""


def inline(text):
    """行内语法（在已转义文本上执行）。"""
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def is_table_sep(line):
    return bool(re.match(r"^\s*\|?[\s:\-|]+\|[\s:\-|]*$", line)) and "-" in line


def split_row(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def convert(md):
    lines = md.replace("\r\n", "\n").split("\n")
    out, i, n = [], 0, len(lines)
    in_ul = in_ol = False

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    while i < n:
        line = lines[i]

        # 代码块
        if line.strip().startswith("```"):
            close_lists()
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            out.append("<pre><code>" + html.escape("\n".join(buf)) + "</code></pre>")
            continue

        # 表格
        if line.strip().startswith("|") and i + 1 < n and is_table_sep(lines[i + 1]):
            close_lists()
            header = split_row(line)
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i]))
                i += 1
            out.append("<table><thead><tr>" +
                       "".join("<th>" + inline(html.escape(c)) + "</th>" for c in header) +
                       "</tr></thead><tbody>")
            for r in rows:
                out.append("<tr>" + "".join("<td>" + inline(html.escape(c)) + "</td>" for c in r) + "</tr>")
            out.append("</tbody></table>")
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            close_lists()
            level = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (level, inline(html.escape(m.group(2))), level))
            i += 1
            continue

        # 水平线
        if re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", line):
            close_lists()
            out.append("<hr>")
            i += 1
            continue

        # 引用
        if line.strip().startswith(">"):
            close_lists()
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append("<blockquote>" + inline(html.escape(" ".join(buf))) + "</blockquote>")
            continue

        # 列表
        m_ul = re.match(r"^\s*[-*+]\s+(.*)$", line)
        m_ol = re.match(r"^\s*\d+[.)]\s+(.*)$", line)
        if m_ul or m_ol:
            if m_ul and not in_ul:
                close_lists()
                out.append("<ul>")
                in_ul = True
            if m_ol and not in_ol:
                close_lists()
                out.append("<ol>")
                in_ol = True
            out.append("<li>" + inline(html.escape((m_ul or m_ol).group(1))) + "</li>")
            i += 1
            continue

        # 空行
        if not line.strip():
            close_lists()
            i += 1
            continue

        # 段落
        close_lists()
        out.append("<p>" + inline(html.escape(line.strip())) + "</p>")
        i += 1

    close_lists()
    return "\n".join(out)


def convert_file(path):
    with open(path, "r", encoding="utf-8") as f:
        md = f.read()
    title = os.path.splitext(os.path.basename(path))[0]
    target = os.path.splitext(path)[0] + ".html"
    page = ("<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<title>" + html.escape(title) + "</title>\n<style>" + CSS + "</style>\n</head>\n<body>\n"
            + convert(md) + "\n</body>\n</html>\n")
    with open(target, "w", encoding="utf-8") as f:
        f.write(page)
    return target


def main(argv):
    args = argv[1:]
    if not args:
        args = ["需求规格说明书.md", "A-需求分析质量验证报告.md", "B-AI智能体使用验证报告.md"]
    done, missing = [], []
    for a in args:
        if os.path.isfile(a):
            done.append(convert_file(a))
        else:
            missing.append(a)
    for d in done:
        print("已生成: " + d)
    for m in missing:
        print("跳过（不存在）: " + m)
    if done:
        print("\n下一步：用浏览器打开上面生成的 .html → Ctrl+P → 另存为 PDF（勾选“背景图形”）。")
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
