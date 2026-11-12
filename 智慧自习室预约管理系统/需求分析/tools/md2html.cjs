#!/usr/bin/env node
/**
 * Markdown → HTML（Node 版，离线、零依赖）
 * 用途：与 tools/md2html.py 等价，便于在本机没有可用 Python 时重新导出 HTML/PDF。
 * 用法：node tools/md2html.cjs [file.md ...]
 *       不带参数时转换根目录下三份主报告。
 */
'use strict';

const fs = require('fs');
const path = require('path');

const CSS = `
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
`;

function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function inline(text) {
  text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
  text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, '<em>$1</em>');
  text = text.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');
  return text;
}

function isTableSep(line) {
  return /^\s*\|?[\s:\-|]+\|[\s:\-|]*$/.test(line) && line.includes('-');
}

function splitRow(line) {
  line = line.trim();
  if (line.startsWith('|')) line = line.slice(1);
  if (line.endsWith('|')) line = line.slice(0, -1);
  const cells = [];
  let buf = '';
  let inCode = false;
  for (const ch of line) {
    if (ch === '`') inCode = !inCode;
    if (ch === '|' && !inCode) {
      cells.push(buf.trim());
      buf = '';
    } else {
      buf += ch;
    }
  }
  cells.push(buf.trim());
  return cells;
}

function convert(md) {
  const lines = md.replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let i = 0;
  let inUl = false;
  let inOl = false;

  function closeLists() {
    if (inUl) { out.push('</ul>'); inUl = false; }
    if (inOl) { out.push('</ol>'); inOl = false; }
  }

  while (i < lines.length) {
    const line = lines[i];

    if (line.trim().startsWith('```')) {
      closeLists();
      i += 1;
      const buf = [];
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        buf.push(lines[i]);
        i += 1;
      }
      i += 1;
      out.push('<pre><code>' + esc(buf.join('\n')) + '</code></pre>');
      continue;
    }

    if (line.trim().startsWith('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      closeLists();
      const header = splitRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        rows.push(splitRow(lines[i]));
        i += 1;
      }
      out.push('<table><thead><tr>' +
        header.map(function (c) { return '<th>' + inline(esc(c)) + '</th>'; }).join('') +
        '</tr></thead><tbody>');
      rows.forEach(function (r) {
        const cells = r.slice(0, header.length);
        while (cells.length < header.length) cells.push('');
        out.push('<tr>' + cells.map(function (c) { return '<td>' + inline(esc(c)) + '</td>'; }).join('') + '</tr>');
      });
      out.push('</tbody></table>');
      continue;
    }

    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      closeLists();
      const level = h[1].length;
      out.push('<h' + level + '>' + inline(esc(h[2])) + '</h' + level + '>');
      i += 1;
      continue;
    }

    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      closeLists();
      out.push('<hr>');
      i += 1;
      continue;
    }

    if (line.trim().startsWith('>')) {
      closeLists();
      const buf = [];
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        buf.push(lines[i].trim().replace(/^>/, '').trim());
        i += 1;
      }
      out.push('<blockquote>' + inline(esc(buf.join(' '))) + '</blockquote>');
      continue;
    }

    const mUl = line.match(/^\s*[-*+]\s+(.*)$/);
    const mOl = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (mUl || mOl) {
      if (mUl && !inUl) { closeLists(); out.push('<ul>'); inUl = true; }
      if (mOl && !inOl) { closeLists(); out.push('<ol>'); inOl = true; }
      out.push('<li>' + inline(esc((mUl || mOl)[1])) + '</li>');
      i += 1;
      continue;
    }

    if (!line.trim()) {
      closeLists();
      i += 1;
      continue;
    }

    closeLists();
    out.push('<p>' + inline(esc(line.trim())) + '</p>');
    i += 1;
  }

  closeLists();
  return out.join('\n');
}

function convertFile(file) {
  const md = fs.readFileSync(file, 'utf8');
  const title = path.basename(file, path.extname(file));
  const target = file.replace(/\.md$/i, '.html');
  const page = '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n' +
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n' +
    '<title>' + esc(title) + '</title>\n<style>' + CSS + '</style>\n</head>\n<body>\n' +
    convert(md) + '\n</body>\n</html>\n';
  fs.writeFileSync(target, page, 'utf8');
  return target;
}

const args = process.argv.slice(2);
const files = args.length ? args : ['需求规格说明书.md', 'A-需求分析质量验证报告.md', 'B-AI智能体使用验证报告.md'];
let done = 0;
files.forEach(function (f) {
  if (fs.existsSync(f)) {
    console.log('已生成: ' + convertFile(f));
    done += 1;
  } else {
    console.log('跳过（不存在）: ' + f);
  }
});
process.exit(done ? 0 : 1);
