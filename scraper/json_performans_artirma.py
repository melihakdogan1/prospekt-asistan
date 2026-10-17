import json
import re
from pathlib import Path

RAW_FILE = Path(__file__).resolve().parent / "titck_raw_metadata.json"
CLEAN_FILE = Path(__file__).resolve().parent / "titck_clean_metadata.json"

PDF_REGEX = re.compile(r'/kubKtAttachments/([a-zA-Z0-9_\-]+\.pdf)', re.IGNORECASE)

def clean_text(text: str) -> str:
    if not text:
        return ""
    # Fazla boşlukları tek boşluğa indir ve kenar boşluklarını sil
    text = re.sub(r'\s+', ' ', text).strip()
    return text

with open(RAW_FILE, "r", encoding="utf-8") as f:
    raw_records = json.load(f)

clean_records = []
unique_kt_pdfs = set()
unique_kub_pdfs = set()

for idx, r in enumerate(raw_records, start=1):
    name = clean_text(r.get("name", ""))
    element = clean_text(r.get("element", ""))
    firm = clean_text(r.get("firmName", ""))
    
    kt_html = r.get("documentPathKt", "")
    kub_html = r.get("documentPathKub", "")
    
    kt_match = PDF_REGEX.search(kt_html)
    kub_match = PDF_REGEX.search(kub_html)
    
    kt_pdf = kt_match.group(1) if kt_match else None
    kub_pdf = kub_match.group(1) if kub_match else None
    
    if kt_pdf:
        unique_kt_pdfs.add(kt_pdf)
    if kub_pdf:
        unique_kub_pdfs.add(kub_pdf)
        
    clean_records.append({
        "id": idx,
        "name": name,
        "element": element,
        "firm_name": firm,
        "kt_onay_tarihi": clean_text(r.get("confirmationDateKt", "")),
        "kub_onay_tarihi": clean_text(r.get("confirmationDateKub", "")),
        "has_kt": kt_pdf is not None,
        "kt_pdf": kt_pdf,
        "has_kub": kub_pdf is not None,
        "kub_pdf": kub_pdf
    })

with open(CLEAN_FILE, "w", encoding="utf-8") as f:
    json.dump(clean_records, f, ensure_ascii=False, indent=2)

print("--- METADATA TEMİZLEME TAMAMLANDI ---")
print(f"Toplam Kayıt: {len(clean_records)}")
print(f"KT'si Olan Kayıt: {sum(1 for x in clean_records if x['has_kt'])}")
print(f"İndirilecek TEKİL KT PDF Sayısı: {len(unique_kt_pdfs)}")
print(f"KÜB'ü Olan Kayıt: {sum(1 for x in clean_records if x['has_kub'])}")
print(f"İndirilecek TEKİL KÜB PDF Sayısı: {len(unique_kub_pdfs)}")
print(f"Çıktı: {CLEAN_FILE.resolve()}")