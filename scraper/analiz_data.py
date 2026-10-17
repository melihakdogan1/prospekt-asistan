import json
import re
from pathlib import Path

METADATA_FILE = Path(__file__).resolve().parent / "titck_raw_metadata.json"

with open(METADATA_FILE, "r", encoding="utf-8") as f:
    records = json.load(f)

total = len(records)
has_kt = 0
has_kub = 0
has_both = 0
only_kub = 0
no_docs = 0

pdf_pattern = re.compile(r'/kubKtAttachments/([a-zA-Z0-9_\-]+\.pdf)')

clean_data = []

for r in records:
    name = r.get("name", "").strip()
    element = r.get("element", "").strip()
    firm = r.get("firmName", "").strip()
    
    kt_html = r.get("documentPathKt", "")
    kub_html = r.get("documentPathKub", "")
    
    kt_match = pdf_pattern.search(kt_html)
    kub_match = pdf_pattern.search(kub_html)
    
    kt_pdf = kt_match.group(1) if kt_match else None
    kub_pdf = kub_match.group(1) if kub_match else None
    
    kt_exists = kt_pdf is not None
    kub_exists = kub_pdf is not None
    
    if kt_exists and kub_exists:
        has_both += 1
    elif kub_exists and not kt_exists:
        only_kub += 1
    elif not kub_exists and not kt_exists:
        no_docs += 1
        
    if kt_exists: has_kt += 1
    if kub_exists: has_kub += 1

print(f"--- TİTCK VERİ SETİ ÖZETİ ---")
print(f"Toplam Kayıt: {total}")
print(f"KT'si Olan İlaç Sayısı: {has_kt} (%{has_kt/total*100:.1f})")
print(f"KÜB'ü Olan İlaç Sayısı: {has_kub} (%{has_kub/total*100:.1f})")
print(f"Hem KT Hem KÜB Olan: {has_both}")
print(f"YALNIZCA KÜB Olan (Hastanelik/Profesyonel): {only_kub}")
print(f"Hiçbir Belgesi Olmayan: {no_docs}")