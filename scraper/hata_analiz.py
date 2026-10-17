import json
import re
from pathlib import Path
from urllib.parse import unquote

RAW_FILE = Path(__file__).resolve().parent / "titck_raw_metadata.json"
CLEAN_FILE = Path(__file__).resolve().parent / "titck_clean_metadata.json"

# href içindeki tam linki yakalayan regex
HREF_REGEX = re.compile(r'href=["\']([^"\']+\.pdf)["\']', re.IGNORECASE)

def clean_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()

def extract_pdf_info(html_str: str) -> tuple[str | None, str | None]:
    if not html_str:
        return None, None
    match = HREF_REGEX.search(html_str)
    if not match:
        return None, None
    
    full_url = match.group(1).strip()
    if not full_url.startswith("http"):
        full_url = "https://titck.gov.tr" + ("" if full_url.startswith("/") else "/") + full_url
    
    # URL'in sonundaki gerçek dosya adını al
    filename = unquote(full_url.split("/")[-1])
    return full_url, filename

with open(RAW_FILE, "r", encoding="utf-8") as f:
    raw_records = json.load(f)

clean_records = []
unique_tasks = set()

for idx, r in enumerate(raw_records, start=1):
    name = clean_text(r.get("name", ""))
    element = clean_text(r.get("element", ""))
    firm = clean_text(r.get("firmName", ""))
    
    kt_url, kt_filename = extract_pdf_info(r.get("documentPathKt", ""))
    kub_url, kub_filename = extract_pdf_info(r.get("documentPathKub", ""))
    
    if kt_url and kt_filename:
        unique_tasks.add(("KT", kt_url, kt_filename))
    if kub_url and kub_filename:
        unique_tasks.add(("KÜB", kub_url, kub_filename))
        
    clean_records.append({
        "id": idx,
        "name": name,
        "element": element,
        "firm_name": firm,
        "kt_onay_tarihi": clean_text(r.get("confirmationDateKt", "")),
        "kub_onay_tarihi": clean_text(r.get("confirmationDateKub", "")),
        "has_kt": kt_url is not None,
        "kt_url": kt_url,
        "kt_pdf": kt_filename,
        "has_kub": kub_url is not None,
        "kub_url": kub_url,
        "kub_pdf": kub_filename
    })

with open(CLEAN_FILE, "w", encoding="utf-8") as f:
    json.dump(clean_records, f, ensure_ascii=False, indent=2)

print("--- YENİ METADATA GÜNCELLEMESİ TAMAMLANDI ---")
print(f"Toplam Kayıt       : {len(clean_records)}")
print(f"İndirilecek Tekil KT : {len([t for t in unique_tasks if t[0] == 'KT'])}")
print(f"İndirilecek Tekil KÜB: {len([t for t in unique_tasks if t[0] == 'KÜB'])}")
print(f"Örnek Yakalanan URL : {clean_records[0]['kt_url']}")