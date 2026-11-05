"""ตรวจสรุป earning call (output/earning_calls/*.json) กับ transcript และเอกสารบริษัท

รัน:  python3 scripts/check_call.py
ตรวจ (ผิด = หยุด exit 1):
  1. โครงสร้างข้อมูลครบ และทุก guidance มีที่มา
  2. เลขข้อ Q&A เรียง 1..N และ N เท่ากับจำนวนคำถามที่ระบุใน call.transcript_questions
  3. ทุกเวลาที่อ้าง (▶ mm:ss) มีอยู่จริงใน transcript (เมื่อมีไฟล์ transcript ในเครื่อง)
  4. thesis / catalysts (ไม่บังคับ แสดงเฉพาะเมื่อข้อมูลพอ) ถ้ามีต้องผ่านเกณฑ์: thesis ≥3 ข้อสนับสนุน และ ≥2 ความเสี่ยง
     ทุกข้อมีหลักฐานและที่มา, catalysts ≥1 ข้อมีที่มา และอย่างน้อย 1 ข้อมีช่วงเวลาที่ผู้บริหารระบุ (ไม่ขึ้นต้นด้วย "ไม่ระบุ")
     ไม่ผ่านเกณฑ์ = ให้ลบฟิลด์ออก หน้าเว็บจะไม่แสดงส่วนนี้
เตือน (ไม่หยุด): ตัวเลขที่ปรากฏในสรุปแต่หาไม่เจอใน transcript, MD&A หรือตารางงบ ให้ผู้ตรวจดูทีละตัว
ไฟล์ transcript อยู่ใน data/raw/ (ไม่ขึ้น repo) ถ้าไม่มีไฟล์ จะข้ามข้อ 3 และการเทียบตัวเลข
"""
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_text import text_of  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
errors, warns = [], []


def walk_text(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            if k not in ("t", "url", "source_doc", "kind", "published", "speakers"):
                yield from walk_text(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk_text(v)


def norm(s):
    return s.replace(",", "")


def check_thesis(d, tag):
    """thesis/catalysts ไม่บังคับ แต่ถ้ามีต้องผ่านเกณฑ์ข้อมูลพอ (ไม่พอ = ลบฟิลด์ ไม่แสดง)"""
    th, cat = d.get("thesis"), d.get("catalysts")
    if th is not None:
        pil, risks = th.get("pillars", []), th.get("risks", [])
        if not th.get("headline") or not th.get("basis"):
            errors.append(f"{tag}: thesis ขาด headline หรือ basis")
        if len(pil) < 3 or len(risks) < 2:
            errors.append(f"{tag}: thesis ข้อมูลไม่พอ (ข้อสนับสนุน {len(pil)}/3, ความเสี่ยง {len(risks)}/2) ให้ลบ thesis ออก")
        for kind, items in (("ข้อสนับสนุน", pil), ("ความเสี่ยง", risks)):
            for i, x in enumerate(items, 1):
                for f in ("point", "evidence", "source"):
                    if not x.get(f):
                        errors.append(f"{tag}: thesis {kind}ข้อ {i} ขาด {f}")
    if cat is not None:
        if len(cat) < 1:
            errors.append(f"{tag}: catalysts ว่าง ให้ลบฟิลด์ออก")
        for i, x in enumerate(cat, 1):
            for f in ("event", "timing", "impact", "direction", "source"):
                if not x.get(f):
                    errors.append(f"{tag}: catalyst ข้อ {i} ขาด {f}")
            if x.get("direction") not in ("บวก", "ลบ", "ไม่แน่"):
                errors.append(f"{tag}: catalyst ข้อ {i} direction ต้องเป็น บวก/ลบ/ไม่แน่")
        if cat and not any(not str(x.get("timing", "")).startswith("ไม่ระบุ") for x in cat):
            errors.append(f"{tag}: catalysts ไม่มีข้อไหนที่ผู้บริหารระบุช่วงเวลา (ข้อมูลไม่พอ) ให้ลบ catalysts ออก")


def check(path, sources):
    d = json.loads(path.read_text(encoding="utf-8"))
    sym = d["symbol"]
    tag = f"{path.name}"
    for k in ("symbol", "period", "call", "verdict", "guidance", "highlights", "financial_view", "qa", "caveats"):
        if k not in d:
            errors.append(f"{tag}: ขาดฟิลด์ {k}")
    if errors:
        return
    for i, g in enumerate(d["guidance"], 1):
        if not g.get("source"):
            errors.append(f"{tag}: guidance ข้อ {i} ไม่มีที่มา")
    nums = [q["n"] for q in d["qa"]]
    if nums != list(range(1, len(nums) + 1)):
        errors.append(f"{tag}: เลขข้อ Q&A ไม่เรียง 1..N: {nums}")
    if len(nums) != d["call"].get("transcript_questions"):
        errors.append(f"{tag}: Q&A {len(nums)} ข้อ แต่ระบุว่า transcript มี {d['call'].get('transcript_questions')} คำถาม")
    for q in d["qa"]:
        for f in ("t", "q", "a", "topic", "answerer"):
            if not q.get(f):
                errors.append(f"{tag}: Q{q['n']} ขาด {f}")

    check_thesis(d, tag)

    row = next((r for r in sources if r["symbol"] == sym and r["period"] == d["period"]), None)
    local = ROOT / row["local_file"] if row and row["local_file"] else None
    if local and not local.exists():   # ย้ายไฟล์ไปโฟลเดอร์อื่น: หาตามชื่อไฟล์ใต้ data/raw/
        found = list((ROOT / "data" / "raw").rglob(local.name))
        local = found[0] if found else local
    refs = ""
    if local and local.exists():
        tr = text_of(local)
        if len(re.findall(r"^\*\*\d+:\d+", tr, re.M)) < 5:
            errors.append(f"{tag}: ไฟล์ transcript {local.name} ไม่มีข้อความที่มีเวลา (ว่างหรือไม่ครบ)")
        refs += tr
        stamps = [q["t"] for q in d["qa"]] + [h["t"] for h in d["highlights"] if h.get("t")] + [p["t"] for p in d["verdict"]["parts"] if p.get("t")]
        stamps += [x["t"] for x in (d.get("thesis", {}).get("pillars", []) + d.get("thesis", {}).get("risks", []) + d.get("catalysts", [])) if x.get("t")]
        for t in sorted(set(stamps)):
            if f"**{t}**" not in tr:
                errors.append(f"{tag}: เวลา {t} ไม่มีใน transcript")
        print(f"{sym}: ตรวจเวลา {len(set(stamps))} จุดกับ transcript แล้ว")
    else:
        warns.append(f"{tag}: ไม่พบไฟล์ transcript ในเครื่อง ข้ามการตรวจเวลา")

    ec = ROOT / "data" / "raw" / sym / "Earnings Call"
    slides = sorted(ec.glob("*.pdf")) if ec.exists() else sorted((ROOT / "data" / "raw").glob(f"{sym}_Q*_SLIDES.pdf"))
    for pdf in sorted(set((ROOT / "data" / "raw").rglob(f"{sym}_Q*-2026_MDA.pdf"))) + slides:
        try:
            refs += text_of(pdf)
        except Exception as e:  # noqa: BLE001
            warns.append(f"อ่าน {pdf.name} ไม่ได้: {e}")
    for r in csv.DictReader(open(OUT / "data.csv", encoding="utf-8-sig")):
        if r["symbol"] == sym:
            refs += " " + " ".join(str(round(int(r[k]) / 1000)) for k in ("revenue", "gross_profit", "net_profit", "selling", "admin"))
    for r in csv.DictReader(open(OUT / "adjusted_profit.csv", encoding="utf-8-sig")):
        if r["symbol"] == sym:
            refs += " " + r["adj_net_profit_mb"]
    refs_n = norm(refs)
    if refs.strip():
        seen = set()
        for s in walk_text({k: v for k, v in d.items() if k != "call"}):
            for m in re.findall(r"\d[\d,]*\.?\d*", s):
                n = norm(m).rstrip(".")
                if (len(n.replace(".", "")) >= 3 or "." in n) and n not in seen:
                    seen.add(n)
                    if n not in refs_n:
                        warns.append(f"{tag}: ตัวเลข {m} ไม่พบใน transcript/MD&A/ตารางงบ (ตรวจว่ามาจากการคำนวณหรือพิมพ์ผิด)")
    print(f"{sym}: Q&A {len(nums)} ข้อ guidance {len(d['guidance'])} รายการ"
          + (f" · thesis {len(d['thesis']['pillars'])}+{len(d['thesis']['risks'])}" if d.get("thesis") else " · ไม่มี thesis")
          + (f" · catalysts {len(d['catalysts'])}" if d.get("catalysts") else " · ไม่มี catalysts"))


def main():
    sources = list(csv.DictReader(open(OUT / "earning_call_sources.csv", encoding="utf-8-sig")))
    files = sorted((OUT / "earning_calls").glob("*.json"))
    priv = ROOT / "data" / "private" / "earning_calls"
    if priv.exists():
        files += sorted(priv.glob("*.json"))
    if not files:
        sys.exit("ไม่พบไฟล์ earning_calls/*.json")
    for f in files:
        check(f, sources)
    for w in warns:
        print("เตือน:", w)
    if errors:
        for e in errors:
            print("ผิดพลาด:", e)
        sys.exit(1)
    print("ผ่านการตรวจ earning call")


if __name__ == "__main__":
    main()
