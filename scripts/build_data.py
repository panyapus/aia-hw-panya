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


def raw_file(sym, name):
    """ไฟล์ดิบของหุ้น: ค้นใต้ data/raw/<SYM>/ ทุกระดับ (เช่น <SYM>/Financial Statement/, <SYM>/Earnings Call/) แล้วค่อยค้น data/raw/ (แบบเดิมไม่แยกโฟลเดอร์)"""
    base = RAW / sym
    found = sorted(base.rglob(name)) if base.exists() else []
    if found:
        return found[0]
    if (RAW / name).exists():
        return RAW / name
    return base / name


def raw_glob(sym, pattern):
    """ไฟล์ดิบของหุ้นที่ตรงรูปแบบ ทั้งใต้โฟลเดอร์บริษัท (ทุกระดับ) และโฟลเดอร์รวม"""
    base = RAW / sym
    return sorted({*(base.rglob(pattern) if base.exists() else []), *RAW.glob(pattern)})


FETCHED = "2026-11-02"
NEWS_URL = "https://www.set.or.th/th/market/news-and-alert/news"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}

# sector ยังไม่ได้ยืนยันจาก set.or.th ผู้เรียนต้องตรวจก่อนใช้จริง
STOCKS = {
    "ITC": {"name": "ไอ-เทล คอร์ปอเรชั่น", "sector": "Food & Beverage"},
    "TRUE": {"name": "ทรู คอร์ปอเรชั่น", "sector": "Technology / ICT"},
    "ADVANC": {"name": "แอดวานซ์ อินโฟร์ เซอร์วิส", "sector": "Technology / ICT"},
}

# (ไฟล์, ชนิดงบ, ชีต, [(งวดคอลัมน์ที่ 1 = ปีปัจจุบัน, วันสิ้นงวด), (งวดคอลัมน์ที่ 2 = ปีก่อน, วันสิ้นงวด)])
# ไฟล์ตั้งชื่อด้วยปี ค.ศ. (Q3-2025 = งบ Q3 ปี พ.ศ. 2568)
SPEC = {
    "ITC": [
        ("Q3-2026", "3M", "PL_One Statement- 1 steps", [("Q3'26", "2026-09-30"), ("Q3'25", "2025-09-30")]),
        ("Q3-2026", "9M", "PL_One Statement- 1 steps 9M", [("9M'26", ""), ("9M'25", "")]),
        ("Q2-2026", "3M", "PL_One Statement- 1 steps", [("Q2'26", "2026-06-30"), ("Q2'25", "2025-06-30")]),
        ("Q1-2026", "3M", "PL_One Statement- 1 steps", [("Q1'26", "2026-03-31"), ("Q1'25", "2025-03-31")]),
        ("Q3-2025", "3M", "PL_One Statement- 1 steps 3M", [("Q3'25", "2025-09-30"), ("Q3'24", "2024-09-30")]),
        ("Q3-2025", "9M", "PL_One Statement- 1 steps 9M", [("9M'25", ""), ("9M'24", "")]),
        ("Q4-2025", "FY", "PL 10-11", [("FY25", ""), ("FY24", "")]),
    ],
    "TRUE": [
        ("Q3-2026", "3M", "thai 5-6 (3M)", [("Q3'26", "2026-09-30"), ("Q3'25", "2025-09-30")]),
        ("Q3-2026", "9M", "thai 7-8 (9M)", [("9M'26", ""), ("9M'25", "")]),
        ("Q2-2026", "3M", "thai 5-6 (3M)", [("Q2'26", "2026-06-30"), ("Q2'25", "2025-06-30")]),
        ("Q1-2026", "3M", "thai 5-6 (3M)", [("Q1'26", "2026-03-31"), ("Q1'25", "2025-03-31")]),
        ("Q3-2025", "3M", "thai 5-6(3M)", [("Q3'25", "2025-09-30"), ("Q3'24", "2024-09-30")]),
        ("Q3-2025", "9M", "thai 7-8 (9M)", [("9M'25", ""), ("9M'24", "")]),
        ("Q4-2025", "FY", "thai 12-13", [("FY25", ""), ("FY24", "")]),
    ],
    "ADVANC": [
        ("Q3-2026", "3M", "SI (3ด) (P.6)", [("Q3'26", "2026-09-30"), ("Q3'25", "2025-09-30")]),
        ("Q3-2026", "9M", "SI (9ด) (P.8)", [("9M'26", ""), ("9M'25", "")]),
        ("Q2-2026", "3M", "SI (3ด) (P.6)", [("Q2'26", "2026-06-30"), ("Q2'25", "2025-06-30")]),
        ("Q1-2026", "3M", "SI (3ด) (P.6)", [("Q1'26", "2026-03-31"), ("Q1'25", "2025-03-31")]),
        ("Q3-2025", "3M", "SI (3ด) P.6", [("Q3'25", "2025-09-30"), ("Q3'24", "2024-09-30")]),
        ("Q3-2025", "9M", "SI (9ด) P.8", [("9M'25", ""), ("9M'24", "")]),
        ("Q4-2025", "FY", "SI 11", [("FY25", ""), ("FY24", "")]),
    ],
}

# งบที่ตัวเลขไม่ใช่หน่วยพันบาท: ตัวคูณแปลงเป็นพันบาท (ADVANC งบทั้งปี Q4/2568 ระบุ "หน่วย : บาท")
UNIT_SCALE = {("ADVANC", "Q4-2025"): 0.001}

# ชื่อบรรทัดในงบกำไรขาดทุน (ตัดช่องว่างก่อนเทียบ) เจอบรรทัดแรกที่ตรงกับตัวเลือกใดก็ได้
LABELS = {
    "revenue": ["รายได้จากการขายและการให้บริการ", "รายได้จากการขายและบริการ", "รวมรายได้"],
    "cogs": ["ต้นทุนขายและการให้บริการ", "ต้นทุนขายและบริการ", "รวมต้นทุนขายและการให้บริการ", "รวมต้นทุน"],
    "gross_profit": ["กำไรขั้นต้น"],
    "other_income": ["รายได้อื่น"],
    "selling": ["ค่าใช้จ่ายในการขาย", "ค่าใช้จ่ายในการขายและต้นทุนในการจัดจำหน่าย", "ต้นทุนในการจัดจำหน่าย"],
    "admin": ["ค่าใช้จ่ายในการบริหาร"],
    "finance_cost": ["ต้นทุนทางการเงิน"],
    "ebt": ["กำไรก่อนภาษีเงินได้", "กำไร(ขาดทุน)ก่อนภาษีเงินได้"],
    "tax": ["ค่าใช้จ่ายภาษีเงินได้", "ภาษีเงินได้", "รายได้(ค่าใช้จ่าย)ภาษีเงินได้"],
    "net_profit": ["ส่วนที่เป็นของผู้เป็นเจ้าของของบริษัทใหญ่", "ส่วนของผู้เป็นเจ้าของของบริษัท", "ส่วนที่เป็นของผู้ถือหุ้นของบริษัท"],
}
FLOW = list(LABELS)  # ทุกฟิลด์เป็นยอดสะสมในงวด ลบกันได้

# กำไรสุทธิส่วนของบริษัทใหญ่ จากแบบ F45 (พันบาท) ใช้ตรวจตัวเลขจากงบ FS
F45 = {
    "ITC": {"Q3'26": 755824, "Q3'25": 812103, "Q2'26": 844094, "Q2'25": 695956, "Q1'26": 871273, "Q1'25": 676870,
            "Q3'24": 976224, "9M'26": 2471191, "9M'25": 2184929, "9M'24": 2806814, "FY25": 2977918, "FY24": 3597261},
    "TRUE": {"Q3'26": 6670089, "Q3'25": 1572898, "Q2'26": 6554129, "Q2'25": 2031150, "Q1'26": 6588720, "Q1'25": 1633880,
             "Q3'24": -810173, "9M'26": 19812938, "9M'25": 5237926, "9M'24": -3458075, "FY25": 9240461, "FY24": -10965859},
    "ADVANC": {"Q3'26": 13504271, "Q3'25": 12038861, "Q2'26": 13716006, "Q2'25": 10981885, "Q1'26": 13495505, "Q1'25": 10583526,
               "Q3'24": 8788129, "9M'26": 40715782, "9M'25": 33604272, "9M'24": 25816446, "FY25": 47885902, "FY24": 35075357},
}
# F45 งวดสะสม 6 เดือนของ Q2 ใช้ตรวจว่า Q1 + Q2 = ครึ่งปี
F45_H1 = {"ITC": {"26": 1715367, "25": 1372826}, "TRUE": {"26": 13142849, "25": 3665029},
           "ADVANC": {"26": 27211511, "25": 21565411}}

FIELDS = ["symbol", "name", "sector", "period", "period_end", "basis", "revenue", "cogs", "gross_profit",
          "other_income", "selling", "admin", "finance_cost", "ebt", "ebit", "tax", "net_profit",
          "unit", "source_doc", "source_url", "fetched_date", "sector_source", "mapping_note"]


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
            if len(vals) == 5 and re.fullmatch(r"\d{1,2}(,\s*\d{1,2})*", vals[0]):  # มีคอลัมน์หมายเหตุนำหน้า (เลขหมายเหตุ เช่น "23" หรือ "22, 29")
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


EPS_LABELS = ["กำไรต่อหุ้นขั้นพื้นฐาน(บาทต่อหุ้น)", "ขั้นพื้นฐาน(บาทต่อหุ้น)", "กำไรต่อหุ้นขั้นพื้นฐาน"]
EPS_DP = {"ADVANC": "2"}  # ADVANC รายงาน EPS ทศนิยม 2 ตำแหน่ง
PS_FIELDS = ["symbol", "period", "eps", "eps_dp", "shares_mn", "shares_basis", "eps_implied_shares_mn", "basis", "source_doc", "fetched_date"]


def collect_eps(sym):
    """กำไรต่อหุ้นขั้นพื้นฐาน (EPS) รายไตรมาสจากงบ FS ที่ยื่นต่อ SET
    ไตรมาส 1–3 อ่านจากงบ 3 เดือน, ไตรมาส 4 = EPS ทั้งปี − EPS 9 เดือน (ทั้งคู่เป็นตัวเลขที่บริษัทรายงาน)
    ตรวจ: Q1+Q2+Q3 = EPS 9 เดือน"""
    obs = {}
    for tag, kind, sheet, periods in SPEC[sym]:
        zpath = raw_file(sym, f"{sym}_{tag}_FS.zip")
        with zipfile.ZipFile(zpath) as zf:
            inner = zipfile.ZipFile(io.BytesIO(zf.read("FINANCIAL_STATEMENTS.XLSX")))
            rows = sheet_rows(inner, sheet)
        vals = pick(rows, EPS_LABELS, "EPS")
        for i, (period, _) in enumerate(periods):
            if period not in obs or i == 0:
                obs[period] = (vals[i], f"{sym}_{tag}_FS.zip")
    for yy in ("25", "24"):
        obs[f"Q4'{yy}"] = (obs[f"FY{yy}"][0] - obs[f"9M'{yy}"][0], f"{sym}_Q4-2025_FS.zip (EPS ทั้งปี) − {sym}_Q3-2025_FS.zip (EPS 9 เดือน)")
    for yy in ("26", "25"):
        tot = sum(obs[f"Q{n}'{yy}"][0] for n in (1, 2, 3))
        if abs(tot - obs[f"9M'{yy}"][0]) > 0.004:
            sys.exit(f"{sym}: EPS Q1+Q2+Q3 ปี {yy} = {tot:.5f} ไม่เท่ากับ EPS 9 เดือน {obs[f'9M{chr(39)}{yy}'][0]:.5f}")
    return {k: v for k, v in obs.items() if k.startswith("Q")}


def shares_from_dividends(divs):
    """จำนวนหุ้นที่มีสิทธิรับปันผล (ล้านหุ้น) = เงินปันผลรวมที่บริษัทประกาศ ÷ ปันผลต่อหุ้น ใช้เป็นตัวหารของ Revenue per Share"""
    out = {}
    for sym in {d["symbol"] for d in divs}:
        v = sorted(float(d["total_mb"]) / float(d["dps"]) for d in divs if d["symbol"] == sym and d["total_mb"] and float(d["dps"]) > 0)
        if not v:
            continue
        if (v[-1] - v[0]) / v[0] > 0.001:
            sys.exit(f"{sym}: จำนวนหุ้นจากเงินปันผลแต่ละครั้งต่างกันเกิน 0.1% {v}")
        out[sym] = v[len(v) // 2]
    return out


def write_per_share(out_rows, divs):
    shares = shares_from_dividends(divs)
    ps = []
    for sym in sorted({r["symbol"] for r in out_rows}):
        eps = collect_eps(sym)
        if sym not in shares:
            sys.exit(f"{sym}: ไม่มีเงินปันผลที่ใช้หาจำนวนหุ้น เพิ่มแถวใน dividends.csv พร้อม total_mb")
        for r in out_rows:
            if r["symbol"] != sym:
                continue
            e, src = eps[r["period"]]
            implied = r["net_profit"] / e / 1000 if abs(e) > 1e-9 else None
            ps.append({"symbol": sym, "period": r["period"], "eps": f"{e:.6f}", "eps_dp": EPS_DP.get(sym, "5"), "shares_mn": f"{shares[sym]:.1f}",
                       "shares_basis": "เงินปันผลรวมที่บริษัทประกาศ ÷ ปันผลต่อหุ้น (ค่าเดียวกันทุกไตรมาส เพราะจำนวนหุ้นไม่เปลี่ยน)",
                       "eps_implied_shares_mn": f"{implied:.1f}" if implied else "",
                       "basis": "คำนวณ (ปี − 9 เดือน)" if r["period"].startswith("Q4") else "งบ 3 เดือน",
                       "source_doc": src if r["period"].startswith("Q4") else src + " (งบกำไรขาดทุน 3 เดือน)", "fetched_date": FETCHED})
    with open(OUT / "per_share.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=PS_FIELDS)
        w.writeheader()
        w.writerows(ps)
    for sym in sorted(shares):
        imp = [float(r["eps_implied_shares_mn"]) for r in ps if r["symbol"] == sym and r["eps_implied_shares_mn"]]
        dev = max(abs(x - shares[sym]) / shares[sym] for x in imp)
        print(f"{sym}: จำนวนหุ้นจากเงินปันผล {shares[sym]:,.1f} ล้านหุ้น · กำไรสุทธิ ÷ EPS ต่างสูงสุด {dev * 100:.2f}%")
    return ps


def load_calls(folder):
    """สรุป earning call ต่อหุ้น (ไฟล์ JSON ที่ AI สรุปจาก transcript และผ่าน scripts/check_call.py)"""
    out = []
    for f in sorted(Path(folder).glob("*.json")):
        out.append(json.loads(f.read_text(encoding="utf-8")))
    print(f"earning call: {len(out)} หุ้น ({', '.join(c['symbol'] for c in out) or 'ไม่มี'})")
    return out


def report_adj_coverage(symbols, adj):
    """ทุกหุ้นใน dashboard ต้องมีบรรทัดกำไรปรับปรุง: มีตัวเลข / แยกสกุลเงิน / หรือระบุว่าไม่เปิดเผย ถ้าขาดให้เตือน"""
    for sym in symbols:
        mine = [a for a in adj if a["symbol"] == sym]
        have = [a for a in mine if a["adj_net_profit_mb"] != ""]
        none = [a for a in mine if a["adj_net_profit_mb"] == ""]
        if have:
            units = sorted({a.get("unit") or "ล้านบาท" for a in have})
            print(f"กำไรปรับปรุง {sym}: {len(have)} ไตรมาส ({', '.join(units)})")
        elif none:
            print(f"กำไรปรับปรุง {sym}: บริษัทไม่เปิดเผย (แสดง – พร้อมเหตุผล)")
        else:
            print(f"คำเตือน {sym}: ยังไม่ได้ตรวจกำไรปรับปรุงใน MD&A ให้เพิ่มลง adjusted_profit.csv (ตัวเลข หรือแถวว่างเมื่อไม่เปิดเผย)")


def main():
    out_rows = []
    dup_checked = {}
    checked_lines = {}
    for sym, info in STOCKS.items():
        obs = {}  # period -> (values, source_doc, own)
        for tag, kind, sheet, periods in SPEC[sym]:
            zpath = raw_file(sym, f"{sym}_{tag}_FS.zip")
            if not zpath.exists():
                sys.exit(f"ไม่พบไฟล์ {zpath.name} ใน data/raw/{sym}/ หรือ data/raw/")
            with zipfile.ZipFile(zpath) as zf:
                inner = zipfile.ZipFile(io.BytesIO(zf.read("FINANCIAL_STATEMENTS.XLSX")))
                rows = sheet_rows(inner, sheet)
            sc = UNIT_SCALE.get((sym, tag), 1)
            vals = {k: tuple(x * sc for x in pick(rows, alts, k)) for k, alts in LABELS.items()}
            label = {"3M": "งบกำไรขาดทุน 3 เดือน", "9M": "งบกำไรขาดทุน 9 เดือน", "FY": "งบกำไรขาดทุนทั้งปี"}[kind]
            for i, (period, _) in enumerate(periods):
                d = normalise({k: v[i] for k, v in vals.items()})
                d["ebit"] = d["ebt"] + d["finance_cost"]  # คำนวณเอง: กำไรก่อนภาษี + ต้นทุนการเงิน
                src = f"{sym}_{tag}_FS.zip ({label})"
                own = (i == 0)  # คอลัมน์ปีปัจจุบันของไฟล์นั้น = งวดของไฟล์เอง
                if period in obs:
                    old, _, old_own = obs[period]
                    diffs = [k for k in FLOW if not close(old[k], d[k], 3)]
                    dup_checked[sym] = dup_checked.get(sym, 0) + 1
                    if diffs:
                        print(f"คำเตือน {sym} {period}: ตัวเลขในสองไฟล์ต่างกัน {diffs} (อาจมีการจัดประเภทใหม่) ใช้ของไฟล์งวดนั้นเอง")
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
        for yy in ("26", "25"):
            bad = []
            for k in FLOW:
                total = sum(obs[f"Q{n}'{yy}"][0][k] for n in (1, 2, 3))
                if not close(total, obs[f"9M'{yy}"][0][k], 3):
                    bad.append(f"{k} {total:.0f} vs {obs[f'9M{chr(39)}{yy}'][0][k]:.0f}")
            checked_lines[sym] = checked_lines.get(sym, 0) + len(FLOW) - len(bad)
            if bad:
                print(f"คำเตือน {sym} ปี {yy}: Q1+Q2+Q3 ไม่เท่ากับ 9 เดือน → {bad}")
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
                "sector_source": "ยังไม่ยืนยัน (รอผู้เรียนตรวจจาก set.or.th)", "mapping_note": "",
            })
    out_rows.sort(key=lambda r: (r["symbol"], r["period_end"]))
    OUT.mkdir(exist_ok=True)
    with open(OUT / "data.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(out_rows)
    print(f"เขียน output/data.csv {len(out_rows)} แถว (กำไรสุทธิตรง F45 ทุกแถว) · ตรวจ Q1+Q2+Q3 = 9 เดือน ผ่าน {checked_lines} บรรทัด · เทียบงวดซ้ำ {dup_checked}")

    notes = list(csv.DictReader(open(OUT / "mdna_notes.csv", encoding="utf-8-sig")))
    # กำไรสุทธิปรับปรุง ตามนิยามของแต่ละบริษัท (จาก MD&A) ตรวจว่า รายงาน + รายการที่ตัดออก = ปรับปรุง
    adj = list(csv.DictReader(open(OUT / "adjusted_profit.csv", encoding="utf-8-sig")))
    reported = {(r["symbol"], r["period"]): r["net_profit"] / 1000 for r in out_rows}
    for a in adj:
        if a["adj_net_profit_mb"] == "" or a.get("unit", "ล้านบาท") != "ล้านบาท":
            continue   # แถวที่บริษัทไม่เปิดเผย หรือเป็นสกุลเงินอื่น ไม่ตรวจกับกำไรเป็นบาท
        key = (a["symbol"], a["period"])
        if key not in reported:
            sys.exit(f"adjusted_profit.csv: ไม่มีงวด {key} ใน data.csv")
        if a["excluded_mb"] and abs(reported[key] + float(a["excluded_mb"]) - float(a["adj_net_profit_mb"])) > 1.5:
            sys.exit(f"{key}: รายงาน {reported[key]:.0f} + ตัดออก {a['excluded_mb']} ไม่เท่ากับปรับปรุง {a['adj_net_profit_mb']}")
        if float(a["adj_net_profit_mb"]) < reported[key] - 5:
            # เป็นไปได้เมื่อรายการพิเศษที่ตัดออกเป็นกำไร (เช่น ผลบวกครั้งเดียว) จึงไม่ถือว่าผิด แต่ให้ตรวจกับ MD&A
            print(f"หมายเหตุ {key}: กำไรปรับปรุงต่ำกว่ากำไรที่รายงาน ให้ตรวจว่าตรงกับ MD&A (รายการพิเศษที่ตัดออกเป็นกำไร)")
    report_adj_coverage(sorted({r["symbol"] for r in out_rows}), adj)
    print(f"ตรวจกำไรสุทธิปรับปรุง {len(adj)} รายการ (ที่มีรายการที่ตัดออก: รายงาน + ตัดออก = ปรับปรุงตรงทุกรายการ)")
    tpl = (ROOT / "scripts" / "dashboard_template.html").read_text(encoding="utf-8")
    calls = load_calls(OUT / "earning_calls")
    divs = list(csv.DictReader(open(OUT / "dividends.csv", encoding="utf-8-sig")))
    ps = write_per_share(out_rows, divs)
    payload = json.dumps({"rows": out_rows, "notes": notes, "adj": adj, "calls": calls, "ps": ps, "div": divs}, ensure_ascii=False)
    (OUT / "dashboard.html").write_text(tpl.replace("/*__DATA__*/null", payload), encoding="utf-8")
    print("เขียน output/dashboard.html")


if __name__ == "__main__":
    main()
