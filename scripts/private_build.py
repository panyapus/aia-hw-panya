"""สร้าง dashboard ฉบับเต็ม (ITC, TRUE + PSL, TU) สำหรับดูในเครื่องเท่านั้น ไม่ push ขึ้น repo

รัน:  python3 scripts/private_build.py --setsmart "/path/to/setsmart_financials_clean_*.csv"
      (หรือตั้งตัวแปร SETSMART_CSV แทน --setsmart)

ทำไมแยกไฟล์:
- งบย้อนหลังของ PSL และ TU ไตรมาส Q3/24–Q2/26 มาจากไฟล์ SETSMART (ข้อมูลที่ซื้อมา) ห้ามอยู่ใน repo Public
- ผลลัพธ์เขียนลง data/private/ ซึ่งอยู่ใน .gitignore (data_full.csv, dashboard_full.html)
- โค้ดนี้ไม่มีตัวเลขจาก SETSMART อยู่ภายใน มีแต่วิธีอ่านและจับคู่ชื่อบรรทัด
- งวด Q3/26 และ Q3/25 ของ PSL/TU อ่านจากงบ FS ที่ยื่นต่อ SET (data/raw/) และใช้เทียบกับ SETSMART
"""
import argparse
import csv
import io
import json
import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_data as b  # noqa: E402

ROOT = b.ROOT
PRIV = ROOT / "data" / "private"

STOCKS = {
    "PSL": {"name": "พรีเชียส ชิพปิ้ง", "sector": "Transportation & Logistics", "ss": "PSL.BK"},
    "TU": {"name": "ไทยยูเนี่ยน กรุ๊ป", "sector": "Food & Beverage", "ss": "TU.BK"},
}
NOTE = {
    "PSL": "PSL: ต้นทุน = ต้นทุนการเดินเรือ + ต้นทุนการให้บริการ + ค่าเสื่อมราคา · บริหาร = ค่าใช้จ่ายบริหาร + ค่าตอบแทนผู้บริหาร · ไม่มีค่าใช้จ่ายขาย (ธุรกิจเดินเรือ จึงดู GPM แบบหุ้นทั่วไปไม่ได้)",
    "TU": "",
}
# กำไรสุทธิส่วนของบริษัทใหญ่จาก F45 (พันบาท) ไฟล์ F45 Q3/69 ของ PSL และ TU
F45 = {
    "PSL": {"Q3'26": 725909, "Q3'25": 252385, "9M'26": 1355589, "9M'25": 68837},
    "TU": {"Q3'26": 1345651, "Q3'25": 1304355, "9M'26": 3722974, "9M'25": 3596173},
}
HIST_ENDS = ["2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]
QEND = {"03-31": "Q1", "06-30": "Q2", "09-30": "Q3", "12-31": "Q4"}
FETCH = "2026-11-03"
# TU ใช้ชื่อบรรทัดเดิมได้เกือบทั้งหมด เพิ่มเฉพาะที่ต่างกัน
TU_EXTRA = {"tax": ["รายได้(ค่าใช้จ่าย)ภาษีเงินได้"], "net_profit": ["ส่วนที่เป็นของบริษัท"]}
SS_NAMES = {
    "revenue": "Revenue From Operations", "selling": "Selling Expenses", "admin": "Administrative Expenses",
    "finance_cost": "Finance Costs", "ebit": "Profit (Loss) Before Finance Costs And Income Tax Expense",
    "tax": "Income Tax Expense", "net_profit": "Net Profit (Loss) Attributable To : Owners Of The Parent",
    "other_income": "Other Income",
}


def period_of(end):
    return f"{QEND[end[5:]]}'{end[2:4]}"


def load_setsmart(path, tickers):
    out = {}
    with open(path, encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            if row[0] in tickers and row[1] == "PL" and row[2] == "Consolidated" and row[3] == "standalone_quarter":
                if row[9] != "k.Baht":
                    sys.exit(f"หน่วยไม่ใช่ k.Baht: {row[9]}")
                out.setdefault((row[0], row[4]), {})[row[7]] = float(row[8]) if row[8] else None
    return out


def map_setsmart(sym, d, end):
    def g(name, required=True):
        v = d.get(name)
        if v is None:
            if required:
                sys.exit(f"{sym} {end}: SETSMART ไม่มีบรรทัด '{name}'")
            return 0.0
        return v
    m = {k: g(v, k not in ("selling", "other_income")) for k, v in SS_NAMES.items()}
    if sym == "PSL":
        m["cogs"] = g("Costs") + g("Depreciation And Amortisation")
        m["admin"] = g("Administrative Expenses") + g("Management And Directors' Remuneration", False)
        m["selling"] = 0.0
    else:
        m["cogs"] = g("Cost Of Sales")
    m["gross_profit"] = m["revenue"] - m["cogs"]
    m["ebt"] = m["ebit"] - m["finance_cost"]
    return m


def fs_rows(sym):
    """งวด Q3/26 และ Q3/25 จากงบ FS ที่ยื่นต่อ SET (ไฟล์ใน data/raw/)"""
    z = b.raw_file(sym, f"{sym}_Q3-2026_FS.zip")
    if not z.exists():
        sys.exit(f"ไม่พบ {z.name}")
    with zipfile.ZipFile(z) as zf:
        inner = zipfile.ZipFile(io.BytesIO(zf.read("FINANCIAL_STATEMENTS.XLSX")))
        if sym == "TU":
            rows = b.sheet_rows(inner, "PL 3M")
            labels = {k: v + TU_EXTRA.get(k, []) for k, v in b.LABELS.items()}
            vals = {k: b.pick(rows, alts, k) for k, alts in labels.items()}
        else:  # PSL: งบแสดงรายการเฉพาะธุรกิจเดินเรือ ต้องรวมหลายบรรทัด
            rows = b.sheet_rows(inner, "BS&PL")
            start = next(i for i, r in enumerate(rows) if b.norm(r[0]) == "งบกำไรขาดทุน")
            rows = rows[start:]
            P = lambda lab: b.pick(rows, [lab], lab)
            two = lambda *labs: tuple(sum(P(l)[i] for l in labs) for i in (0, 1))
            vals = {
                "revenue": two("รวมรายได้จากการเดินเรือ", "รายได้จากการให้บริการ"),
                "cogs": two("รวมต้นทุนการเดินเรือ", "ค่าเสื่อมราคา", "ต้นทุนการให้บริการ"),
                "other_income": P("รายได้อื่น"),
                "selling": (0.0, 0.0),
                "admin": two("ค่าใช้จ่ายในการบริหาร", "ค่าตอบแทนผู้บริหาร"),
                "finance_cost": P("ต้นทุนทางการเงิน"),
                "ebt": P("กำไร (ขาดทุน) ก่อนผลประโยชน์ (ค่าใช้จ่าย) ภาษีเงินได้"),
                "tax": P("ผลประโยชน์ (ค่าใช้จ่าย) ภาษีเงินได้"),   # บวก = ผลประโยชน์ภาษี
                "net_profit": P("ส่วนที่เป็นของผู้ถือหุ้นของบริษัทฯ"),
            }
            vals["gross_profit"] = tuple(vals["revenue"][i] - vals["cogs"][i] for i in (0, 1))
    out = {}
    for i, (period, end) in enumerate((("Q3'26", "2026-09-30"), ("Q3'25", "2025-09-30"))):
        d = b.normalise({k: v[i] for k, v in vals.items()})
        d["ebit"] = d["ebt"] + d["finance_cost"]
        out[period] = (d, end)
    return out


# จำนวนหุ้นถัวเฉลี่ยของ TU (ล้านหุ้น) จากหมายเหตุงบ Q3/69: 9 เดือนปี 69 = 3,855.094 ตรงกับเงินปันผลระหว่างกาล ส.ค. 2026 (1,542.0 ÷ 0.40)
# และเงินปันผล ส.ค. 2025 / เม.ย. 2026 (1,349.3 ÷ 0.35 = 3,855.1) จึงถือว่าจำนวนหุ้นคงที่ตั้งแต่ ส.ค. 2025 ถึง ก.ย. 2026 (Q4/25–Q3/26)
# ก่อนหน้านั้นจำนวนหุ้นเปลี่ยน (ค่าเฉลี่ย 9 เดือนปี 68 = 4,005.8) จึงไม่แสดงตัวหารของไตรมาสเหล่านั้น
TU_SHARES = {"Q4'25": 3855.1, "Q1'26": 3855.1, "Q2'26": 3855.1, "Q3'26": 3855.1}
# EPS จากแบบ F45 ของ Q3/69 (ค่าล่าสุดที่บริษัทรายงาน)
EPS_F45 = {("PSL", "Q3'26"): 0.4889, ("TU", "Q3'26"): 0.35}


def build_per_share(rows, ss):
    """EPS รายไตรมาสของ PSL/TU: Q3/26 จาก F45, ที่เหลือจาก SETSMART (TU ทศนิยม 2 ตำแหน่ง จึงไม่ใช้อนุมานจำนวนหุ้น)"""
    out = []
    for r in rows:
        sym, per = r["symbol"], r["period"]
        if (sym, per) in EPS_F45:
            e, src, basis = EPS_F45[(sym, per)], f"{sym}_Q3-2026_F45.pdf", "แบบ F45"
        else:
            d = ss[(STOCKS[sym]["ss"], r["period_end"])]
            e, src, basis = d.get("Basic Earnings (Loss) Per Share (Baht/Share)"), "SETSMART (ไฟล์ในเครื่อง ไม่เผยแพร่)", "SETSMART (ไตรมาส)"
        if e is None:
            continue
        if sym == "PSL":   # EPS ทศนิยม 4–5 ตำแหน่ง ใช้ กำไรสุทธิ ÷ EPS เป็นจำนวนหุ้นถัวเฉลี่ยของไตรมาสนั้น (ตรงกับหมายเหตุงบ Q3/69: 1,484.7 ล้านหุ้น)
            shares = f"{r['net_profit'] / e / 1000:.1f}" if abs(e) > 1e-9 else ""
        else:
            shares = f"{TU_SHARES[per]:.1f}" if per in TU_SHARES else ""
        out.append({"symbol": sym, "period": per, "eps": f"{e:.6f}", "eps_dp": "2" if sym == "TU" else "4", "shares_mn": shares,
                    "shares_basis": ("กำไรสุทธิ ÷ EPS = จำนวนหุ้นถัวเฉลี่ยของไตรมาสนั้น (PSL ซื้อหุ้นคืน จำนวนหุ้นจึงเปลี่ยนตามไตรมาส)" if sym == "PSL"
                                     else "หมายเหตุงบ Q3/69 ร่วมกับเงินปันผล: จำนวนหุ้นคงที่ที่ 3,855.1 ล้านหุ้นตั้งแต่ ส.ค. 2025 ไตรมาสก่อนหน้าจำนวนหุ้นเปลี่ยนจึงไม่แสดง"),
                    "eps_implied_shares_mn": f"{r['net_profit'] / e / 1000:.1f}" if abs(e) > 1e-9 else "",
                    "basis": basis + (" (ปัดทศนิยม 2 ตำแหน่ง)" if sym == "TU" and basis != "แบบ F45" else ""),
                    "source_doc": src, "fetched_date": FETCH})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--setsmart", default=os.environ.get("SETSMART_CSV"))
    args = ap.parse_args()
    if not args.setsmart or not Path(args.setsmart).exists():
        sys.exit("ระบุไฟล์ SETSMART ด้วย --setsmart หรือตัวแปร SETSMART_CSV")
    fetched_ss = datetime.fromtimestamp(Path(args.setsmart).stat().st_mtime).strftime("%Y-%m-%d")
    ss = load_setsmart(args.setsmart, {v["ss"] for v in STOCKS.values()})

    rows = []
    for sym, info in STOCKS.items():
        fs = fs_rows(sym)
        obs = {}
        for period, (d, end) in fs.items():   # งบที่ยื่นต่อ SET เป็นแหล่งหลัก
            exp = F45[sym][period]
            if not b.close(d["net_profit"], exp):
                sys.exit(f"{sym} {period}: กำไรสุทธิจากงบ {d['net_profit']} ไม่ตรง F45 {exp}")
            obs[period] = (d, end, f"{sym}_Q3-2026_FS.zip (งบกำไรขาดทุน 3 เดือน)", "งบ 3 เดือน", b.NEWS_URL, FETCH)
        for end in HIST_ENDS:
            period = period_of(end)
            if (info["ss"], end) not in ss:
                sys.exit(f"{sym} {end}: ไม่มีข้อมูลใน SETSMART")
            m = map_setsmart(sym, ss[(info["ss"], end)], end)
            if period in obs:   # งวดที่ซ้ำ: เทียบทุกบรรทัดกับงบที่ยื่นต่อ SET
                fsd = obs[period][0]
                diffs = {k: (round(m[k]), round(fsd[k])) for k in b.FLOW + ["ebit"] if k != "other_income" and not b.close(m[k], fsd[k], 3)}
                if diffs:
                    sys.exit(f"{sym} {period}: SETSMART ไม่ตรงงบ SET {diffs}")
                print(f"{sym} {period}: SETSMART ตรงกับงบ SET ทุกบรรทัด (รายได้ ต้นทุน ค่าใช้จ่าย ภาษี กำไร)")
                continue
            obs[period] = (m, end, "SETSMART (ไฟล์ในเครื่อง ไม่เผยแพร่)", "SETSMART (ไตรมาส)", "SETSMART (ข้อมูลที่ซื้อมา ไม่เผยแพร่)", fetched_ss)

        # ตรวจ Q1+Q2+Q3 กับกำไรสุทธิ 9 เดือนใน F45
        for yy in ("26", "25"):
            tot = sum(obs[f"Q{n}'{yy}"][0]["net_profit"] for n in (1, 2, 3))
            if not b.close(tot, F45[sym][f"9M'{yy}"], 2.5):
                sys.exit(f"{sym} Q1+Q2+Q3 ปี {yy}: {tot} ไม่ตรง F45 9 เดือน {F45[sym][f'9M{chr(39)}{yy}']}")
        print(f"{sym}: Q1+Q2+Q3 = กำไรสุทธิ 9 เดือนใน F45 ทั้งปี 2025 และ 2026 ตรง")
        # สมการบัญชี: EBT − ภาษี − กำไรสุทธิ = ส่วนของผู้ถือหุ้นส่วนน้อย ต้องเล็ก
        for period, (d, end, *_ ) in obs.items():
            nci = d["ebt"] - d["tax"] - d["net_profit"]
            if abs(nci) > 0.5 * abs(d["net_profit"]) + 100:
                print(f"คำเตือน {sym} {period}: EBT − ภาษี − กำไรสุทธิ = {nci:,.0f} (ส่วนผู้ถือหุ้นส่วนน้อยใหญ่ผิดปกติ)")
        for period, (d, end, src, basis, url, fdate) in obs.items():
            rows.append({
                "symbol": sym, "name": info["name"], "sector": info["sector"], "period": period, "period_end": end,
                "basis": basis, **{k: int(round(d[k])) for k in ("revenue", "cogs", "gross_profit", "other_income", "selling", "admin",
                                                                "finance_cost", "ebt", "ebit", "tax", "net_profit")},
                "unit": "พันบาท", "source_doc": src, "source_url": url, "fetched_date": fdate,
                "sector_source": "ยังไม่ยืนยัน (รอผู้เรียนตรวจจาก set.or.th)", "mapping_note": NOTE[sym],
            })

    ps_priv = build_per_share(rows, ss)
    pub = list(csv.DictReader(open(b.OUT / "data.csv", encoding="utf-8-sig")))
    num = ("revenue", "cogs", "gross_profit", "other_income", "selling", "admin", "finance_cost", "ebt", "ebit", "tax", "net_profit")
    for r in pub:
        for k in num:
            r[k] = int(r[k])
    allrows = pub + rows
    allrows.sort(key=lambda r: (r["symbol"], r["period_end"]))
    PRIV.mkdir(parents=True, exist_ok=True)
    with open(PRIV / "data_full.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=b.FIELDS)
        w.writeheader()
        w.writerows(allrows)

    def read(p):
        return list(csv.DictReader(open(p, encoding="utf-8-sig"))) if Path(p).exists() else []
    notes = read(b.OUT / "mdna_notes.csv") + read(PRIV / "mdna_notes_private.csv")
    adj = read(b.OUT / "adjusted_profit.csv") + read(PRIV / "adjusted_profit_private.csv")
    rep_np = {(r["symbol"], r["period"]): r["net_profit"] / 1000 for r in allrows}
    for a in adj:
        if a["adj_net_profit_mb"] == "" or a.get("unit", "ล้านบาท") != "ล้านบาท" or not a["excluded_mb"]:
            continue
        key = (a["symbol"], a["period"])
        if key not in rep_np:
            sys.exit(f"adjusted_profit: ไม่มีงวด {key} ใน data_full.csv")
        if abs(rep_np[key] + float(a["excluded_mb"]) - float(a["adj_net_profit_mb"])) > 1.5:
            sys.exit(f"{key}: รายงาน {rep_np[key]:.1f} + ตัดออก {a['excluded_mb']} ไม่เท่ากับปรับปรุง {a['adj_net_profit_mb']}")
    print("ตรวจกำไรปรับปรุงทุกหุ้น: รายงาน + รายการที่ตัดออก = ปรับปรุง ตรงทุกรายการที่มีรายการตัดออก")
    b.report_adj_coverage(sorted({r["symbol"] for r in allrows}), adj)
    tpl = (ROOT / "scripts" / "dashboard_template.html").read_text(encoding="utf-8")
    calls = b.load_calls(b.OUT / "earning_calls") + (b.load_calls(PRIV / "earning_calls") if (PRIV / "earning_calls").exists() else [])
    ps_all = list(csv.DictReader(open(b.OUT / "per_share.csv", encoding="utf-8-sig"))) + ps_priv
    divs = list(csv.DictReader(open(b.OUT / "dividends.csv", encoding="utf-8-sig"))) + read(PRIV / "dividends_private.csv")
    payload = json.dumps({"rows": allrows, "notes": notes, "adj": adj, "calls": calls, "ps": ps_all, "div": divs}, ensure_ascii=False)
    (PRIV / "dashboard_full.html").write_text(tpl.replace("/*__DATA__*/null", payload), encoding="utf-8")
    print(f"เขียน data/private/data_full.csv {len(allrows)} แถว และ data/private/dashboard_full.html (ไม่ถูก commit)")


if __name__ == "__main__":
    main()
