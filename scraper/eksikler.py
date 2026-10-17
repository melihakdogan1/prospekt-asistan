import json
from collections import Counter
from pathlib import Path

JSONL_PATH = Path(__file__).resolve().parent / "kt_profil_raporu.jsonl"

if not JSONL_PATH.exists():
    raise SystemExit(f"HATA: {JSONL_PATH} bulunamadı!")

error_files = []
kub_files = []
mixed_files = []

# Başarısızlık kategorileri
ocr_count = 0
doc_type_mismatch_count = 0  # 5 bölümü var ama document_type KT denmemiş olanlar
toc_body_confusion = 0
section_distribution = Counter()
missing_section_counter = Counter()
sample_missing = []

with open(JSONL_PATH, "r", encoding="utf-8") as f:
    for line in f:
        data = json.loads(line)
        filename = data.get("filename")
        status = data.get("status")
        doc_type = data.get("document_type")
        is_five = data.get("kt_five_body_headings_candidate", False)
        ocr_pages = data.get("ocr_candidate_pages", [])
        body_found = data.get("body_sections_found", [])
        toc_found = data.get("toc_sections_found", [])

        # 1. Hata verenler
        if status == "error":
            error_files.append((filename, data.get("error")))
            continue

        # 2. KT yerine KÜB yüklenenler
        if doc_type == "KUB":
            kub_files.append(filename)

        if doc_type == "mixed":
            mixed_files.append(filename)

        # 3. 5'li zincirde olmayanlar
        if not is_five:
            # Kaç bölüm yakalanmış?
            section_distribution[len(body_found)] += 1

            if ocr_pages:
                ocr_count += 1
            elif len(body_found) == 5 and doc_type != "KT":
                # Aslında 5 bölümü de var ama başlık etiketinden dolayı elenmiş!
                doc_type_mismatch_count += 1
            elif len(body_found) < 5:
                missing = set(range(1, 6)) - set(body_found)
                for m in missing:
                    missing_section_counter[m] += 1
                
                if len(sample_missing) < 5:
                    sample_missing.append({
                        "filename": filename,
                        "doc_type": doc_type,
                        "body_sections": body_found,
                        "toc_sections": toc_found,
                        "missing": list(missing)
                    })

print("=" * 65)
print("❌ 1. HATA VEREN 4 DOSYA:")
print("=" * 65)
for fname, err in error_files:
    print(f"- {fname} -> {err}")

print("\n" + "=" * 65)
print(f"📄 2. KÜB OLARAK ALGILANAN {len(kub_files)} DOSYA:")
print("=" * 65)
for fname in kub_files:
    print(f"- {fname}")

print("\n" + "=" * 65)
print("🔍 3. 5 BÖLÜMÜ TAMAMLAYAMAYANLARIN TEKNİK DAĞILIMI:")
print("=" * 65)
print(f"- Doğrudan Taranmış Görsel / OCR Olan Dosyalar : {ocr_count}")
print(f"- 5 Bölümü de Var Ama Type='KT' Sayılmayanlar  : {doc_type_mismatch_count}")
print("\nGövdede (Body) Yakalanan Bölüm Sayısı Dağılımı:")
for count, freq in sorted(section_distribution.items()):
    print(f"  {count} Bölüm Yakalanan : {freq} dosya")

print("\nYakalanmayan Bölümlerin Frekansı:")
for sec, freq in sorted(missing_section_counter.items()):
    print(f"  Bölüm {sec} : {freq} dosyada bulunamadı")

if sample_missing:
    print("\nİnceleme İçin Örnek Dosyalar:")
    print(json.dumps(sample_missing, indent=2, ensure_ascii=False))