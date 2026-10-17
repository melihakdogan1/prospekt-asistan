import hashlib
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
META_FILE = ROOT / "titck_clean_metadata.json"
PROFIL_FILE = ROOT / "kt_profil_raporu.jsonl"
KT_DIR = ROOT.parent / "data" / "kt"
QUARANTINE_DIR = ROOT.parent / "data" / "kt_karantina"


def main():
    with open(META_FILE, "r", encoding="utf-8") as f:
        records = json.load(f)

    # 1. Profil raporundan bozuk (error: 4) ve yanlış yüklenmiş KÜB (12) dosyalarını bul
    bad_pdfs = {}
    if PROFIL_FILE.exists():
        with open(PROFIL_FILE, "r", encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                fname = row["filename"]
                if row["status"] == "error":
                    bad_pdfs[fname] = "corrupted_0_pages"
                elif row.get("document_type") == "KUB":
                    bad_pdfs[fname] = "wrong_doc_type_KUB"

    # 2. Diskteki tüm PDF'lerin SHA256 içerik özetini çıkar
    hash_to_files = defaultdict(list)
    for pdf_path in sorted(KT_DIR.glob("*.pdf")):
        if pdf_path.name in bad_pdfs:
            continue
        h = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        hash_to_files[h].append(pdf_path.name)

    # İçeriği %100 aynı olan PDF'lerden ilkini bırak, diğerlerini silinecekler listesine al
    duplicate_file_map = {}  # {silinecek_pdf: kalici_pdf}
    for h, files in hash_to_files.items():
        if len(files) > 1:
            canonical = files[0]
            for dup in files[1:]:
                duplicate_file_map[dup] = canonical

    # 3. Kopya PDF'leri diskten sil, bozuk/KÜB olanları karantinaya taşı
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    removed_duplicates_count = 0
    removed_bad_count = 0

    for dup_fname in duplicate_file_map:
        src = KT_DIR / dup_fname
        if src.exists():
            src.unlink()
            removed_duplicates_count += 1

    for bad_fname, reason in bad_pdfs.items():
        src = KT_DIR / bad_fname
        if src.exists():
            target = QUARANTINE_DIR / f"{reason}__{bad_fname}"
            src.replace(target)
            removed_bad_count += 1

    # 4. JSON Temizliği: İçeriği (PDF'i) aynı olan kayıtları sil, 1 PDF = 1 Kayıt bırak
    cleaned_records = []
    seen_pdfs = {}  # {kt_pdf: kayit_referansi}
    removed_json_rows = 0

    for r in records:
        kt_pdf = r.get("kt_pdf")

        # İndirilememiş (diskte olmayan) veya bozuk/KÜB olan kayıtları temizle
        if not kt_pdf or kt_pdf in bad_pdfs:
            removed_json_rows += 1
            continue

        # Eğer bu kayıt silinen bir SHA256 kopya PDF'e bakıyorsa, kalan tek PDF'e eşitle
        if kt_pdf in duplicate_file_map:
            kt_pdf = duplicate_file_map[kt_pdf]
            r["kt_pdf"] = kt_pdf

        # Dosya fiziksel olarak diskte yoksa (indirilemeyen 70 hatalı dosya) JSON'dan da sil
        if not (KT_DIR / kt_pdf).exists():
            removed_json_rows += 1
            continue

        # Bu PDF daha önce eklendiyse (içerik %100 aynıysa), bu satırı JSON'dan sil!
        if kt_pdf in seen_pdfs:
            existing_record = seen_pdfs[kt_pdf]
            # Eğer mg/isim farkı varsa bilgi kaybolmasın diye alternatif isim olarak not düş
            if r["name"] != existing_record["name"]:
                alt_list = existing_record.setdefault("alternative_names", [])
                if r["name"] not in alt_list:
                    alt_list.append(r["name"])
            removed_json_rows += 1
            continue

        seen_pdfs[kt_pdf] = r
        cleaned_records.append(r)

    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(cleaned_records, f, ensure_ascii=False, indent=2)

    # 5. Profil JSONL dosyasından silinen PDF'leri düş
    kept_profile_lines = []
    removed_from_profile = 0
    if PROFIL_FILE.exists():
        with open(PROFIL_FILE, "r", encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                fname = row["filename"]
                if fname in duplicate_file_map or fname in bad_pdfs:
                    removed_from_profile += 1
                    continue
                kept_profile_lines.append(line)

        with open(PROFIL_FILE, "w", encoding="utf-8") as f:
            f.writelines(kept_profile_lines)

    remaining_pdfs = len(list(KT_DIR.glob("*.pdf")))

    print("=" * 65)
    print("🧹 TAM EŞLEŞMELİ KOPYA VE BOZUK DOSYA TEMİZLİĞİ TAMAMLANDI")
    print("=" * 65)
    print(f"Diskten Silinen %100 Aynı (SHA256) PDF  : {removed_duplicates_count}")
    print(f"Karantinaya Alınan (4 Bozuk + 12 KÜB)   : {removed_bad_count}")
    print(f"JSON'dan Silinen Kopya/Eksik Satır      : {removed_json_rows}")
    print(f"Profil Raporundan Düşülen Kayıt         : {removed_from_profile}")
    print("-" * 65)
    print(f"Klasörde Kalan Net Tekil KT PDF         : {remaining_pdfs}")
    print(f"JSON'da Kalan Net Tekil İlaç Kaydı      : {len(cleaned_records)}")
    print("=" * 65)


if __name__ == "__main__":
    main()