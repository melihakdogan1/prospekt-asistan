import json
import re
from pathlib import Path

METADATA_FILE = Path(__file__).resolve().parent / "titck_raw_metadata.json"

with open(METADATA_FILE, "r", encoding="utf-8") as f:
    records = json.load(f)

# Büyük/küçük harf duyarsız ve daha esnek regex
pdf_pattern = re.compile(r'/kubKtAttachments/([^"\'<>\s]+)', re.IGNORECASE)

hatali_ornekler = []
gercek_boslar = 0
regex_kacirdigi = 0

for r in records:
    kt_html = r.get("documentPathKt", "")
    kub_html = r.get("documentPathKub", "")
    
    # İçinde herhangi bir pdf kelimesi geçiyor mu?
    kt_has_pdf = bool(pdf_pattern.search(kt_html))
    kub_has_pdf = bool(pdf_pattern.search(kub_html))
    
    if not kt_has_pdf and not kub_has_pdf:
        # Gerçekten link etiketi boş mu yoksa uzantı mı kaçtı?
        if "href" in kt_html or "href" in kub_html:
            regex_kacirdigi += 1
            if len(hatali_ornekler) < 5:
                hatali_ornekler.append({
                    "name": r.get("name"),
                    "kt_html": kt_html,
                    "kub_html": kub_html
                })
        else:
            gercek_boslar += 1

print(f"Gerçekten Hiç Linki Olmayan: {gercek_boslar}")
print(f"Link Var Ama Regex'in Kaçırdığı: {regex_kacirdigi}")
if hatali_ornekler:
    print("\nRegex'in Kaçırdığı Örnekler:")
    print(json.dumps(hatali_ornekler, indent=2, ensure_ascii=False))