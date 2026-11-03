// ดึงข้อความจาก PDF ด้วย PDFKit ที่มากับ macOS (ไม่ต้องติดตั้งอะไรเพิ่ม)
// ใช้:  swift scripts/pdf_text.swift <ไฟล์.pdf> <หน้าเริ่ม> <หน้าสุดท้าย>
import PDFKit
import Foundation
let a = CommandLine.arguments
guard a.count >= 4, let d = PDFDocument(url: URL(fileURLWithPath: a[1])) else { print("open failed"); exit(1) }
let s = Int(a[2])!, e = min(Int(a[3])!, d.pageCount)
for i in (s-1)..<e { print("--- page \(i+1)"); print(d.page(at: i)?.string ?? "") }
