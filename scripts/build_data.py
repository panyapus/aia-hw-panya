"""สร้าง output/data.csv จากไฟล์งบ (FS.zip) ใน data/raw/ แล้วฝังข้อมูลลง output/dashboard.html

รัน:  python3 scripts/build_data.py
ใช้เฉพาะ standard library ไม่ต้องติดตั้งอะไรเพิ่ม
ไฟล์ดิบอยู่ใน data/raw/ (ไม่ถูก push) ใน repo เก็บเฉพาะตัวเลขที่สรุป

ไตรมาส 1–3 อ่านจากงบ 3 เดือนโดยตรง
ไตรมาส 4 ไม่มีงบ 3 เดือน จึงคำนวณ = งบทั้งปี − งบ 9 เดือน (ระบุใน data.csv คอลัมน์ basis)
กำไรสุทธิทุกไตรมาสถูกตรวจกับแบบ F45 ถ้าไม่ตรงสคริปต์จะหยุดทันที
"""
import csv
import io
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "output"
FETCHED = "2026-11-02"
NEWS_URL = "https://www.set.or.th/th/market/news-and-alert/news"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}

# sector ยังไม่ได้ยืนยันจาก set.or.th ผู้เรียนต้องตรวจก่อนใช้จริง
STOCKS = {
    "ITC": {"name": "ไอ-เทล คอร์ปอเรชั่น", "sector": "Food & Beverage"},
    "TRUE": {"name": "ทรู คอร์ปอเรชั่น", "sector": "Technology / ICT"},
}

# (ไฟล์, ชนิดงบ, ชีต, [(งวดคอลัมน์ที่ 1 = ปีปัจจุบัน, วันสิ้นงวด), (งวดคอลัมน์ที่ 2 = ปีก่อน, วันสิ้นงวด)])
# ไฟล์ตั้งชื่อด้วยปี ค.ศ. (Q3-2025 = งบ Q3 ปี พ.ศ. 2568)
SPEC = {
    "ITC": [
        ("Q3-2026", "3M", "PL_One Statement- 1 steps", [("Q3'26", "2026-09-30"), ("Q3'25", "2025-09-30")]),
        ("Q2-2026", "3M", "PL_One Statement- 1 steps", [("Q2'26", "2026-06-30"), ("Q2'25", "2025-06-30")]),
        ("Q1-2026", "3M", "PL_One Statement- 1 steps", [("Q1'26", "2026-03-31"), ("Q1'25", "2025-03-31")]),
        ("Q3-2025", "3M", "PL_One Statement- 1 steps 3M", [("Q3'25", "2025-09-30"), ("Q3'24", "2024-09-30")]),
        ("Q3-2025", "9M", "PL_One Statement- 1 steps 9M", [("9M'25", ""), ("9M'24", "")]),
        ("Q4-2025", "FY", "PL 10-11", [("FY25", ""), ("FY24", "")]),
    ],
    "TRUE": [
        ("Q3-2026", "3M", "thai 5-6 (3M)", [("Q3'26", "2026-09-30"), ("Q3'25", "2025-09-30")]),
        ("Q2-2026", "3M", "thai 5-6 (3M)", [("Q2'26", "2026-06-30"), ("Q2'25", "2025-06-30")]),
        ("Q1-2026", "3M", "thai 5-6 (3M)", [("Q1'26", "2026-03-31"), ("Q1'25", "2025-03-31")]),
        ("Q3-2025", "3M", "thai 5-6(3M)", [("Q3'25", "2025-09-30"), ("Q3'24", "2024-09-30")]),
        ("Q3-2025", "9M", "thai 7-8 (9M)", [("9M'25", ""), ("9M'24", "")]),
        ("Q4-2025", "FY", "thai 12-13", [("FY25", ""), ("FY24", "")]),
    ],
}

# ชื่อบรรทัดในงบกำไรขาดทุน (ตัดช่องว่างก่อนเทียบ) เจอบรรทัดแรกที่ตรงกับตัวเลือกใดก็ได้
LABELS = {
    "revenue": ["รายได้จากการขายและการให้บริการ", "รายได้จากการขายและบริการ", "รวมรายได้"],
    "cogs": ["ต้นทุนขายและการให้บริการ", "ต้นทุนขายและบริการ", "รวมต้นทุนขายและการให้บริการ"],
    "gross_profit": ["กำไรขั้นต้น"],
    "other_income": ["รายได้อื่น"],
    "selling": ["ค่าใช้จ่ายในการขาย", "ค่าใช้จ่ายในการขายและต้นทุนในการจัดจำหน่าย"],
    "admin": ["ค่าใช้จ่ายในการบริหาร"],
    "finance_cost": ["ต้นทุนทางการเงิน"],
    "ebt": ["กำไรก่อนภาษีเงินได้", "กำไร(ขาดทุน)ก่อนภาษีเงินได้"],
    "tax": ["ค่าใช้จ่ายภาษีเงินได้", "ภาษีเงินได้"],
    "net_profit": ["ส่วนที่เป็นของผู้เป็นเจ้าของของบริษัทใหญ่", "ส่วนของผู้เป็นเจ้าของของบริษัท"],
}
FLOW = list(LABELS)  # ทุกฟิลด์เป็นยอดสะสมในงวด ลบกันได้

# กำไรสุทธิส่วนของบริษัทใหญ่ จากแบบ F45 (พันบาท) ใช้ตรวจตัวเลขจากงบ FS
F45 = {
    "ITC": {"Q3'26": 755824, "Q3'25": 812103, "Q2'26": 844094, "Q2'25": 695956, "Q1'26": 871273, "Q1'25": 676870,
            "Q3'24": 976224, "9M'25": 2184929, "9M'24": 2806814, "FY25": 2977918, "FY24": 3597261},
    "TRUE": {"Q3'26": 6670089, "Q3'25": 1572898, "Q2'26": 6554129, "Q2'25": 2031150, "Q1'26": 6588720, "Q1'25": 1633880,
             "Q3'24": -810173, "9M'25": 5237926, "9M'24": -3458075, "FY25": 9240461, "FY24": -10965859},
}
# F45 งวดสะสม 6 เดือนของ Q2 ใช้ตรวจว่า Q1 + Q2 = ครึ่งปี
F45_H1 = {"ITC": {"26": 1715367, "25": 1372826}, "TRUE": {"26": 13142849, "25": 3665029}}

FIELDS = ["symbol", "name", "sector", "period", "period_end", "basis", "revenue", "cogs", "gross_profit",
          "other_income", "selling", "admin", "finance_cost", "ebt", "ebit", "tax", "net_profit",
          "unit", "source_doc", "source_url", "fetched_date", "sector_source"]


def sheet_rows(zf, sheet_name):
    ss = []
    if "xl/sharedStrings.xml" in zf.namelist():
        for si in ET.fromstring(zf.read("xl/sharedStrings.xml")).findall("m:si", NS):
            ss.append("".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])))
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))}
    for s in wb.find("m:sheets", NS):
        if s.get("name") != sheet_name:
            continue
        path = "xl/" + rels[s.get("{%s}id" % NS["r"])].lstrip("/").replace("xl/", "")
        rows = []
        for row in ET.fromstring(zf.read(path)).findall(".//m:row", NS):
            cells = []
            for c in row.findall("m:c", NS):
                v = c.find("m:v", NS)
                if v is not None:
                    cells.append(ss[int(v.text)] if c.get("t") == "s" else v.text)
            if cells:
                rows.append([x.strip() for x in cells])
        return rows
    sys.exit(f"ไม่พบชีต '{sheet_name}'")


def num(x):
    return 0.0 if x == "-" else float(x)


def norm(s):
    return re.sub(r"\s+", "", s)


def pick(rows, alts, field):
    """คืน (ปีปัจจุบัน, ปีก่อน) ของงบการเงินรวม จากแถวแรกที่ชื่อตรงกับตัวเลือกใดตัวเลือกหนึ่ง"""
    want = {norm(a) for a in alts}
    for r in rows:
        if norm(r[0]) in want:
            vals = r[1:]
            if len(vals) == 5:  # มีคอลัมน์หมายเหตุนำหน้า
                vals = vals[1:]
            if len(vals) < 2:
                continue
            return num(vals[0]), num(vals[1])
    sys.exit(f"ไม่พบบรรทัดของ '{field}' ({alts[0]})")


def normalise(d):
    """ต้นทุน/ค่าใช้จ่ายเก็บเป็นค่าบวก ภาษีบวก = ค่าใช้จ่าย"""
    d = dict(d)
    for k in ("cogs", "selling", "admin", "finance_cost"):
        d[k] = abs(d[k])
    d["tax"] = -d["tax"]
    return d


def close(a, b, tol=1.5):
    return abs(a - b) <= tol


def main():
    out_rows = []
    for sym, info in STOCKS.items():
        obs = {}  # period -> (values, source_doc, own)
        for tag, kind, sheet, periods in SPEC[sym]:
            zpath = RAW / f"{sym}_{tag}_FS.zip"
            if not zpath.exists():
                sys.exit(f"ไม่พบไฟล์ {zpath.name} ใน data/raw/")
            with zipfile.ZipFile(zpath) as zf:
                inner = zipfile.ZipFile(io.BytesIO(zf.read("FINANCIAL_STATEMENTS.XLSX")))
                rows = sheet_rows(inner, sheet)
            vals = {k: pick(rows, alts, k) for k, alts in LABELS.items()}
            label = {"3M": "งบกำไรขาดทุน 3 เดือน", "9M": "งบกำไรขาดทุน 9 เดือน", "FY": "งบกำไรขาดทุนทั้งปี"}[kind]
            for i, (period, _) in enumerate(periods):
                d = normalise({k: v[i] for k, v in vals.items()})
                d["ebit"] = d["ebt"] + d["finance_cost"]  # คำนวณเอง: กำไรก่อนภาษี + ต้นทุนการเงิน
                src = f"{sym}_{tag}_FS.zip ({label})"
                own = (i == 0)  # คอลัมน์ปีปัจจุบันของไฟล์นั้น = งวดของไฟล์เอง
                if period in obs:
                    old, _, old_own = obs[period]
                    if not close(old["net_profit"], d["net_profit"]):
                        print(f"คำเตือน {sym} {period}: กำไรสุทธิในไฟล์ต่างกัน ({old['net_profit']} vs {d['net_profit']}) ใช้ของไฟล์งวดนั้นเอง")
                    if own and not old_own:
                        obs[period] = (d, src, own)
                else:
                    obs[period] = (d, src, own)

        # ตรวจกำไรสุทธิจากงบ FS กับ F45
        for period, (d, _, _) in obs.items():
            exp = F45[sym][period]
            if not close(d["net_profit"], exp):
                sys.exit(f"{sym} {period}: กำไรสุทธิจากงบ {d['net_profit']} ไม่ตรง F45 {exp}")

        # ไตรมาส 4 = ทั้งปี − 9 เดือน
        for yy in ("25", "24"):
            fy, fy_src, _ = obs[f"FY{yy}"]
            nm, nm_src, _ = obs[f"9M'{yy}"]
            q4 = {k: fy[k] - nm[k] for k in FLOW}
            q4["ebit"] = q4["ebt"] + q4["finance_cost"]
            exp = F45[sym][f"FY{yy}"] - F45[sym][f"9M'{yy}"]
            if not close(q4["net_profit"], exp):
                sys.exit(f"{sym} Q4'{yy}: คำนวณได้ {q4['net_profit']} ไม่ตรง F45 (ปี−9M) {exp}")
            obs[f"Q4'{yy}"] = (q4, f"{fy_src.split(' (')[0]} (ทั้งปี) − {nm_src.split(' (')[0]} (9 เดือน)", False)

        # ตรวจความสอดคล้อง: Q1 + Q2 = 6 เดือนของ F45, Q1+Q2+Q3 = 9 เดือน
        for yy in ("26", "25"):
            h1 = obs[f"Q1'{yy}"][0]["net_profit"] + obs[f"Q2'{yy}"][0]["net_profit"]
            if not close(h1, F45_H1[sym][yy], 2.5):
                sys.exit(f"{sym} Q1+Q2 ปี {yy}: {h1} ไม่ตรง F45 6 เดือน {F45_H1[sym][yy]}")
        q9 = sum(obs[f"Q{n}'25"][0]["net_profit"] for n in (1, 2, 3))
        nine = F45[sym]["9M'25"]
        if not close(q9, nine, 2.5):
            sys.exit(f"{sym} Q1+Q2+Q3 ปี 25: {q9} ไม่ตรง F45 9 เดือน {nine}")

        ends = {"Q1": "03-31", "Q2": "06-30", "Q3": "09-30", "Q4": "12-31"}
        for period, (d, src, _) in obs.items():
            if not period.startswith("Q"):
                continue
            q, yy = period[:2], period[3:]
            out_rows.append({
                "symbol": sym, "name": info["name"], "sector": info["sector"], "period": period,
                "period_end": f"20{yy}-{ends[q]}", "basis": "คำนวณ (ปี − 9 เดือน)" if q == "Q4" else "งบ 3 เดือน",
                **{k: int(round(v)) for k, v in d.items()},
                "unit": "พันบาท", "source_doc": src, "source_url": NEWS_URL, "fetched_date": FETCHED,
                "sector_source": "ยังไม่ยืนยัน (รอผู้เรียนตรวจจาก set.or.th)",
            })
    out_rows.sort(key=lambda r: (r["symbol"], r["period_end"]))
    OUT.mkdir(exist_ok=True)
    with open(OUT / "data.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(out_rows)
    print(f"เขียน output/data.csv {len(out_rows)} แถว (กำไรสุทธิตรง F45 ทุกแถว และ Q1+Q2+Q3 = 9 เดือน)")

    notes = list(csv.DictReader(open(OUT / "mdna_notes.csv", encoding="utf-8-sig")))
    tpl = (ROOT / "scripts" / "dashboard_template.html").read_text(encoding="utf-8")
    payload = json.dumps({"rows": out_rows, "notes": notes}, ensure_ascii=False)
    (OUT / "dashboard.html").write_text(tpl.replace("/*__DATA__*/null", payload), encoding="utf-8")
    print("เขียน output/dashboard.html")


if __name__ == "__main__":
    main()
