"""
NULL TEMİZLEME ve HATALI DOSYALARA TAŞIMA
==========================================
Kriter:
  1. Bölüm 1+2 İKİSİ DE null → kesinlikle hatali_dosyalar'a taşı
  2. 3+ bölüm null → hatali_dosyalar'a taşı  
  3. Kalan null'lar → boş string "" yap (ChromaDB "field yok" yerine "alan boş" görsün)
  4. Bölüm 3,4,5 tek başına null → kabul edilebilir (RAG'da eksik bölüm = "bilgi yok" cevabı)
"""
import json
import os
from collections import defaultdict

ANALIZ_DIR = "analiz_sonuclari"

def load_json(name):
    path = os.path.join(ANALIZ_DIR, name)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(name, data):
    path = os.path.join(ANALIZ_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ================================================================
# ADIM 1: Hangi dosyaların kritik bölümleri null analiz et
# ================================================================
print("=" * 70)
print("  NULL TEMİZLEME İŞLEMİ")
print("=" * 70)

ozet = load_json("kt_ozet.json")
ek_bilgi = load_json("kt_ek_bilgi.json")
hatali = load_json("hatali_dosyalar.json")

bolum_data = {}
for i in range(1, 6):
    bolum_data[i] = load_json(f"kt_bolum{i}.json")

# Her dosya için null bölüm say
file_null_map = defaultdict(lambda: {"null_bolums": [], "null_ozet": [], "null_ek": []})

ozet_map = {item["file_name"]: item for item in ozet}
ek_map = {item["file_name"]: item for item in ek_bilgi}
bolum_maps = {}
for i in range(1, 6):
    bolum_maps[i] = {item["file_name"]: item for item in bolum_data[i]}

all_files = set(ozet_map.keys())

for fn in all_files:
    # Bölüm null kontrolü
    for i in range(1, 6):
        item = bolum_maps[i].get(fn, {})
        if not item.get("content"):
            file_null_map[fn]["null_bolums"].append(i)
    
    # Özet null kontrolü
    oz = ozet_map.get(fn, {})
    for field in ["usage_route", "active_substance", "excipients", "warning_text"]:
        if not oz.get(field):
            file_null_map[fn]["null_ozet"].append(field)
    
    # Ek bilgi null kontrolü
    ek = ek_map.get(fn, {})
    for field in ["license_holder", "manufacturer"]:
        if not ek.get(field):
            file_null_map[fn]["null_ek"].append(field)

# ================================================================
# ADIM 2: Hatalı dosyalara taşınacakları belirle
# ================================================================
to_move_hatali = []
reasons = {}

for fn in all_files:
    info = file_null_map[fn]
    null_bolums = info["null_bolums"]
    
    # Kriter 1: B1 + B2 ikisi de null
    if 1 in null_bolums and 2 in null_bolums:
        to_move_hatali.append(fn)
        reasons[fn] = f"B1+B2 ikisi de null (toplam {len(null_bolums)} bolum null)"
        continue
    
    # Kriter 2: 3+ bölüm null
    if len(null_bolums) >= 3:
        to_move_hatali.append(fn)
        reasons[fn] = f"{len(null_bolums)} bolum null: B{',B'.join(str(b) for b in null_bolums)}"
        continue

print(f"\n  Hatalı dosyalara taşınacak: {len(to_move_hatali)} dosya")
print(f"  {'='*60}")
for fn in sorted(to_move_hatali):
    drug = (ozet_map.get(fn, {}).get("drug_name") or "")[:40]
    print(f"  - {fn[:55]}")
    print(f"    Sebep: {reasons[fn]}")
    nb = file_null_map[fn]["null_bolums"]
    no = file_null_map[fn]["null_ozet"]
    ne = file_null_map[fn]["null_ek"]
    print(f"    Null bolumler: {nb}, Null ozet: {no}, Null ek: {ne}")

# ================================================================
# ADIM 3: Taşıma işlemi
# ================================================================
print(f"\n{'='*70}")
print("  TAŞIMA İŞLEMİ")
print(f"{'='*70}")

# Eski hatalı sayısını kaydet
eski_hatali = len(hatali)

# Yeni hatalı dosya ekle
for fn in to_move_hatali:
    hatali.append({
        "file": fn,
        "reason": f"Veri kalite denetimi - {reasons[fn]}",
        "text_length": -1  # bilinmiyor, zaten işlenmiş metin var
    })

# Tüm JSON'lardan çıkar
for fn in to_move_hatali:
    ozet[:] = [item for item in ozet if item["file_name"] != fn]
    ek_bilgi[:] = [item for item in ek_bilgi if item["file_name"] != fn]
    for i in range(1, 6):
        bolum_data[i][:] = [item for item in bolum_data[i] if item["file_name"] != fn]

print(f"  Hatalı dosyalar: {eski_hatali} → {len(hatali)} (+{len(to_move_hatali)})")
print(f"  Kalan başarılı kayıt: {len(ozet)}")

# ================================================================
# ADIM 4: Kalan null'ları boş string yap
# ================================================================
print(f"\n{'='*70}")
print("  KALAN NULL'LARI TEMİZLE (null → boş string)")
print(f"{'='*70}")

null_to_empty_count = 0

# Özet null → ""
for item in ozet:
    for field in ["usage_route", "active_substance", "excipients", "warning_text"]:
        if item.get(field) is None:
            item[field] = ""
            null_to_empty_count += 1

# Bölüm null → ""
for i in range(1, 6):
    for item in bolum_data[i]:
        if item.get("content") is None:
            item["content"] = ""
            null_to_empty_count += 1
        if item.get("section_title") is None:
            item["section_title"] = ""
            null_to_empty_count += 1

# Ek bilgi null → ""
for item in ek_bilgi:
    for field in ["license_holder", "manufacturer", "healthcare_professional_info"]:
        if item.get(field) is None:
            item[field] = ""
            null_to_empty_count += 1

print(f"  null → '' dönüştürülen alan: {null_to_empty_count}")

# ================================================================
# ADIM 5: Kaydet
# ================================================================
print(f"\n{'='*70}")
print("  KAYDET")
print(f"{'='*70}")

save_json("kt_ozet.json", ozet)
save_json("kt_ek_bilgi.json", ek_bilgi)
save_json("hatali_dosyalar.json", hatali)
for i in range(1, 6):
    save_json(f"kt_bolum{i}.json", bolum_data[i])

print(f"  Tüm dosyalar kaydedildi.")

# ================================================================
# ADIM 6: Doğrulama - Hiç null kaldı mı?
# ================================================================
print(f"\n{'='*70}")
print("  DOĞRULAMA - NULL KONTROL")
print(f"{'='*70}")

remaining_nulls = 0

# Özet null check
ozet_check = load_json("kt_ozet.json")
for item in ozet_check:
    for field in ["file_name", "drug_name", "usage_route", "active_substance", "excipients", "warning_text"]:
        if item.get(field) is None:
            print(f"  ✗ HATA: Özet'te null kaldı! {item['file_name']} → {field}")
            remaining_nulls += 1

# Bölüm null check
for i in range(1, 6):
    b = load_json(f"kt_bolum{i}.json")
    for item in b:
        for field in ["file_name", "drug_name", "section_title", "content"]:
            if item.get(field) is None:
                print(f"  ✗ HATA: Bölüm {i}'de null kaldı! {item['file_name']} → {field}")
                remaining_nulls += 1

# Ek bilgi null check
ek_check = load_json("kt_ek_bilgi.json")
for item in ek_check:
    for field in ["file_name", "drug_name", "license_holder", "manufacturer"]:
        if item.get(field) is None:
            print(f"  ✗ HATA: Ek Bilgi'de null kaldı! {item['file_name']} → {field}")
            remaining_nulls += 1

if remaining_nulls == 0:
    print(f"  ✓ HİÇ NULL KALMADI! Tüm alanlar dolu veya boş string.")
else:
    print(f"  ✗ {remaining_nulls} null alan kaldı!")

# ================================================================
# ADIM 7: Final Rapor
# ================================================================
print(f"\n{'='*70}")
print("  FİNAL RAPOR")
print(f"{'='*70}")

final_ozet = load_json("kt_ozet.json")
final_hatali = load_json("hatali_dosyalar.json")
final_ek = load_json("kt_ek_bilgi.json")

total_pdf = 3165
basarili = len(final_ozet)
hatali_count = len(final_hatali)

print(f"""
  Toplam PDF          : {total_pdf}
  Başarılı            : {basarili} ({basarili/total_pdf*100:.1f}%)
  Hatalı              : {hatali_count} ({hatali_count/total_pdf*100:.1f}%)
  Taşınan             : {len(to_move_hatali)}
  Null → '' yapılan   : {null_to_empty_count}
  Kalan null          : {remaining_nulls}
""")

# Her alan için final durum
print(f"  {'Alan':25s} {'Dolu':>6s} {'Bos':>6s} {'Toplam':>7s} {'Oran':>8s}")
print(f"  {'-'*56}")

for field_name, field_key, dataset in [
    ("İlaç Adı", "drug_name", final_ozet),
    ("Etkin Madde", "active_substance", final_ozet),
    ("Kullanım Yolu", "usage_route", final_ozet),
    ("Yardımcı Madde", "excipients", final_ozet),
    ("Uyarı Metni", "warning_text", final_ozet),
    ("Ruhsat Sahibi", "license_holder", final_ek),
    ("Üretici", "manufacturer", final_ek),
]:
    total = len(dataset)
    dolu = sum(1 for i in dataset if i.get(field_key))
    bos = total - dolu
    oran = dolu / total * 100
    print(f"  {field_name:25s} {dolu:6d} {bos:6d} {total:7d} {oran:7.1f}%")

for i in range(1, 6):
    b = load_json(f"kt_bolum{i}.json")
    total = len(b)
    dolu_c = sum(1 for item in b if item.get("content"))
    dolu_t = sum(1 for item in b if item.get("section_title"))
    bos_c = total - dolu_c
    bos_t = total - dolu_t
    print(f"  {'Bölüm '+str(i)+' İçerik':25s} {dolu_c:6d} {bos_c:6d} {total:7d} {dolu_c/total*100:7.1f}%")
    print(f"  {'Bölüm '+str(i)+' Başlık':25s} {dolu_t:6d} {bos_t:6d} {total:7d} {dolu_t/total*100:7.1f}%")

print(f"\n  ✓ VERİ RAG EĞİTİMİ İÇİN HAZIR")
print(f"  (Null olan alanlar '' olarak bırakıldı - ChromaDB'de 'bilgi mevcut değil' olarak işlenecek)")
