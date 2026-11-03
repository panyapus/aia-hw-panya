"""อ่านข้อความจากเอกสารที่ดาวน์โหลดมา (.md .txt .docx .pdf) ใช้เฉพาะ standard library และ Swift PDFKit ของ macOS

ใช้เป็นโมดูล:  from extract_text import text_of
หรือรันตรง ๆ:  python3 scripts/extract_text.py "data/raw/ITC_Q3-2026_CALL.docx" > out.txt
ข้อความ PDF ภาษาไทยบางไฟล์สระ/วรรณยุกต์ตกหล่น ให้เทียบกับภาพหน้า PDF ก่อนใช้ตัวเลข
"""
import re
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent


def text_of(path, pages=(1, 400)):
    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".md", ".txt"):
        return p.read_text(encoding="utf-8")
    if ext == ".docx":
        w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        root = ET.fromstring(zipfile.ZipFile(p).read("word/document.xml"))
        paras = ["".join(t.text or "" for t in para.iter(w + "t")) for para in root.iter(w + "p")]
        return "\n".join(paras)
    if ext == ".pdf":
        r = subprocess.run(["swift", str(HERE / "pdf_text.swift"), str(p), str(pages[0]), str(pages[1])],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"อ่าน PDF ไม่ได้: {r.stderr[:200]}")
        return r.stdout
    raise ValueError(f"ไม่รองรับไฟล์ชนิด {ext}")


if __name__ == "__main__":
    sys.stdout.write(text_of(sys.argv[1]))
