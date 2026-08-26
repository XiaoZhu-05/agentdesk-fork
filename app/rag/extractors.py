"""多格式文档解析：把各类文件统一提取为纯文本，交给现有切分/向量管线。

支持：txt / md / pdf / docx / xlsx / csv / pptx。
解析失败会抛出异常，由调用方（indexer）决定跳过该文件。
"""
from __future__ import annotations

import csv
import os
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

SUPPORTED_EXTS = {".txt", ".md", ".pdf", ".docx", ".xlsx", ".csv", ".pptx",
                  ".html", ".json", ".rtf", ".xml", ".xls", ".odt", ".epub"}


def _read_txt(path: str) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _read_pdf(path: str) -> str:
    from pypdf import PdfReader

    reader = PdfReader(path)
    parts = []
    for i, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        parts.append(f"[第{i}页]\n{text}")
    return "\n\n".join(parts)


def _read_docx(path: str) -> str:
    from docx import Document

    doc = Document(path)
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(parts)


def _read_xlsx(path: str) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    lines = []
    for ws in wb.worksheets:
        lines.append(f"# Sheet: {ws.title}")
        for row in ws.iter_rows(values_only=True):
            vals = ["" if v is None else str(v) for v in row]
            if any(vals):
                lines.append(" | ".join(vals))
    return "\n".join(lines)


def _read_csv(path: str) -> str:
    lines = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f):
            lines.append(" | ".join(cell.strip() for cell in row))
    return "\n".join(lines)


def _read_pptx(path: str) -> str:
    from pptx import Presentation

    prs = Presentation(path)
    parts = []
    for i, slide in enumerate(prs.slides, 1):
        slide_lines = [f"[第{i}页]"]
        for shape in slide.shapes:
            if shape.has_text_frame:
                text = "\n".join(
                    p.text for p in shape.text_frame.paragraphs if p.text.strip()
                )
                if text:
                    slide_lines.append(text)
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    slide_lines.append(" | ".join(cell.text.strip() for cell in row.cells))
        parts.append("\n".join(slide_lines))
    return "\n\n".join(parts)


class _HTMLTextExtractor(HTMLParser):
    """剥掉 HTML 标签/脚本，保留正文与表格分隔。"""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs) -> None:
        if tag in ("script", "style"):
            self.skip += 1
        if tag in ("p", "div", "tr", "h1", "h2", "h3", "h4", "li", "br",
                   "table", "section", "article"):
            self.parts.append("\n")
        if tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_endtag(self, tag) -> None:
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data) -> None:
        if not self.skip:
            self.parts.append(data)


def _html_to_text(raw: str) -> str:
    p = _HTMLTextExtractor()
    try:
        p.feed(raw)
    except Exception:
        pass
    text = "".join(p.parts)
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _read_html(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return _html_to_text(f.read())


def _flatten_json(obj, prefix: str = "", out: list | None = None) -> list:
    out = out if out is not None else []
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten_json(v, f"{prefix}{k}.", out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _flatten_json(v, f"{prefix}{i}.", out)
    else:
        out.append(f"{prefix.rstrip('.')}: {obj}")
    return out


def _read_json(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return "\n".join(_flatten_json(data))


def _read_rtf(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        raw = f.read()
    raw = re.sub(r"\{\\fonttbl[^}]*\}", "", raw, flags=re.I)  # 去掉字体表
    # \uN? -> 字符
    raw = re.sub(r"\\u(-?\d+)\?", lambda m: chr(max(0, int(m.group(1)))), raw)
    raw = re.sub(r"\\par\b|\\line\b|\\\n", "\n", raw, flags=re.I)
    raw = re.sub(r"\\'[0-9a-fA-F]{2}", "", raw)
    raw = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", raw)
    raw = raw.replace("{", "").replace("}", "")
    lines = [ln.strip() for ln in raw.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _read_xml(path: str) -> str:
    tree = ET.parse(path)
    lines = []
    for elem in tree.iter():
        if elem.attrib:
            lines.append(f"<{elem.tag}> " + " ".join(
                f"{k}={v}" for k, v in elem.attrib.items()))
        if elem.text and elem.text.strip():
            lines.append(f"{elem.tag}: {elem.text.strip()}")
    return "\n".join(lines)


def _read_xls(path: str) -> str:
    import xlrd

    book = xlrd.open_workbook(path)
    lines = []
    for sh in book.sheets():
        lines.append(f"# Sheet: {sh.name}")
        for r in range(sh.nrows):
            vals = ["" if sh.cell_value(r, c) is None else str(sh.cell_value(r, c))
                    for c in range(sh.ncols)]
            if any(vals):
                lines.append(" | ".join(vals))
    return "\n".join(lines)


def _read_odt(path: str) -> str:
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("content.xml"))
    parts = [e.text.strip() for e in root.iter() if e.text and e.text.strip()]
    return "\n".join(parts)


def _read_epub(path: str) -> str:
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist()
                 if n.lower().endswith((".xhtml", ".html", ".htm"))]
        parts = []
        for n in sorted(names):
            raw = z.read(n).decode("utf-8", errors="ignore")
            text = _html_to_text(raw)
            if text.strip():
                parts.append(f"[{n}]\n{text}")
    return "\n\n".join(parts)


_EXTRACTORS = {
    ".txt": _read_txt,
    ".md": _read_txt,
    ".pdf": _read_pdf,
    ".docx": _read_docx,
    ".xlsx": _read_xlsx,
    ".csv": _read_csv,
    ".pptx": _read_pptx,
    ".html": _read_html,
    ".json": _read_json,
    ".rtf": _read_rtf,
    ".xml": _read_xml,
    ".xls": _read_xls,
    ".odt": _read_odt,
    ".epub": _read_epub,
}


def extract_text(path: str) -> str:
    """按扩展名提取文本；不支持的格式抛 ValueError。"""
    ext = os.path.splitext(path)[1].lower()
    fn = _EXTRACTORS.get(ext)
    if fn is None:
        raise ValueError(f"unsupported file type: {ext}")
    return fn(path)
