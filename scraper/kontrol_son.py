import json
import re
from pathlib import Path
import pymupdf

ROOT = Path(__file__).resolve().parent
KT_DIR = ROOT.parent / "data" / "kt"
OUT_DIR = ROOT.parent / "data" / "pilot_parsed_60"

print("=" * 75)
print("🔎 1. DUOBAK VE BOSELİX'TE KAÇAN 3. BÖLÜM BAŞLIĞI NASIL YAZILMIŞ?")
print("=" * 75)

for fname, pages_to_check in [("0029f2cb27715.pdf", [3, 4, 5]), ("0194a6f077249.pdf", [4, 5, 6, 7])]:
    doc = pymupdf.open(KT_DIR / fname)
    print(f"\n📄 {fname}:")
    for pno in pages_to_check:
        if pno <= len(doc):
            for line in doc[pno - 1].get_text("text").splitlines():
                s = line.strip()
                # Başında 3 olan veya içinde ilaç adı / 'kullan' / 'uygun' geçen kısa satırları göster
                if re.match(r"^[38B]\s*[.)\-]?", s) or any(
                    k in s.lower() for k in ("duobak", "bosel", "nasil", "nasıl", "uygun kullan")
                ):
                    if len(s) < 110:
                        print(f"  [Sayfa {pno}] {s}")

print("\n" + "=" * 75)
print("📦 2. ÜRETİLEN JSON KALİTE KONTROLÜ (Örnek: SANDACE - 03895dc974383.json)")
print("=" * 75)

sandace_json = json.loads((OUT_DIR / "03895dc974383.json").read_text(encoding="utf-8"))
print(f"İlaç Adı          : {sandace_json['primary_drug_name']}")
print(f"PDF Başlığı       : {sandace_json['pdf_extracted_drug_title']}")
print(f"Etkin Madde       : {sandace_json['active_substance']}")
print(f"Yardımcı Maddeler : {sandace_json['excipients']}")
print(f"Uygulama Yolu     : {sandace_json['administration_route']}")
print(f"Yakalanan Bölümler: {sandace_json['sections_captured']}")
print(f"Toplam Chunk      : {len(sandace_json['chunks'])}")

# Bölüm 4 ve Bölüm 5'ten birer örnek chunk göster (TÜFAM silinmiş mi, Saklama doğru mu?)
for c in sandace_json["chunks"]:
    if c["section_id"] in (4, 5):
        print(f"\n--- Örnek Chunk ({c['chunk_id']} | Bölüm {c['section_id']} | {c['token_count']} token) ---")
        print(c["retrieval_text"][:600])