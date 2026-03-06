"""
Kapsamlı Veri Kalitesi Analizi
Tüm JSON dosyalarındaki sorunları detaylı tespit eder.
"""
import json
import re
import os
from collections import Counter, defaultdict

ANALIZ_DIR = "analiz_sonuclari"

def load_json(name):
    path = os.path.join(ANALIZ_DIR, name)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def analyze():
    ozet = load_json("kt_ozet.json")
    bolum1 = load_json("kt_bolum1.json")
    bolum2 = load_json("kt_bolum2.json")
    bolum3 = load_json("kt_bolum3.json")
    bolum4 = load_json("kt_bolum4.json")
    bolum5 = load_json("kt_bolum5.json")
    ek_bilgi = load_json("kt_ek_bilgi.json")
    hatali = load_json("hatali_dosyalar.json")
    
    print("=" * 70)
    print("KAPSAMLI VERİ KALİTESİ ANALİZİ")
    print("=" * 70)
    print(f"\nToplam başarılı: {len(ozet)}")
    print(f"Toplam hatalı: {len(hatali)}")
    
    # =========================================================================
    # 1. İLAÇ ADI KALİTESİ
    # =========================================================================
    print("\n" + "=" * 70)
    print("1. İLAÇ ADI KALİTESİ")
    print("=" * 70)
    
    drug_issues = {
        "bos_veya_none": [],
        "cok_kisa": [],       # < 3 karakter
        "cok_uzun": [],       # > 120 karakter
        "ocr_bozuk": [],      # garbled chars: $, @, [, ], {, }
        "kucuk_harf": [],     # tamamen küçük harf (olmamalı)
        "sayi_ile_baslar": [],  # sadece sayı
        "hat_iceriyor": [],    # \n içerenler
        "route_iceriyor": [],  # "uygulanır", "kullanılır" içerenler (hatalı çıkarım)
    }
    
    for item in ozet:
        name = item.get("drug_name", "")
        fn = item.get("file_name", "")
        
        if not name or name.strip() == "":
            drug_issues["bos_veya_none"].append(fn)
            continue
        
        if len(name) < 3:
            drug_issues["cok_kisa"].append((fn, name))
        
        if len(name) > 120:
            drug_issues["cok_uzun"].append((fn, name[:80] + "..."))
        
        if any(c in name for c in ['$', '@', '{', '}', '[', ']', '<', '>']):
            drug_issues["ocr_bozuk"].append((fn, name[:80]))
        
        if name == name.lower() and not any(c.isdigit() for c in name[:3]):
            drug_issues["kucuk_harf"].append((fn, name[:80]))
        
        if '\n' in name:
            drug_issues["hat_iceriyor"].append((fn, name.replace('\n', '\\n')[:80]))
        
        if re.search(r'uygulan[ıi]r|kullan[ıi]l[ıi]r', name, re.IGNORECASE):
            drug_issues["route_iceriyor"].append((fn, name[:80]))
    
    for issue_name, items in drug_issues.items():
        if items:
            print(f"\n  {issue_name}: {len(items)} adet")
            for item in items[:5]:
                if isinstance(item, tuple):
                    print(f"    - {item[0]}: {item[1]}")
                else:
                    print(f"    - {item}")
            if len(items) > 5:
                print(f"    ... ve {len(items)-5} tane daha")
    
    # =========================================================================
    # 2. ETKİN MADDE KALİTESİ
    # =========================================================================
    print("\n" + "=" * 70)
    print("2. ETKİN MADDE KALİTESİ")
    print("=" * 70)
    
    active_issues = {
        "bos_veya_none": 0,
        "cok_kisa": [],
        "cok_uzun": [],
        "ilac_adi_tekrari": [],  # etkin madde = ilaç adı
    }
    
    for item in ozet:
        active = item.get("active_substance", "")
        drug = item.get("drug_name", "")
        fn = item.get("file_name", "")
        
        if not active:
            active_issues["bos_veya_none"] += 1
            continue
        
        if len(active) < 10:
            active_issues["cok_kisa"].append((fn, active))
        
        if len(active) > 500:
            active_issues["cok_uzun"].append((fn, active[:80] + "..."))
        
        if active.strip() == drug.strip():
            active_issues["ilac_adi_tekrari"].append((fn, active[:60]))
    
    print(f"  Boş/None: {active_issues['bos_veya_none']}")
    print(f"  Çok kısa (<10 char): {len(active_issues['cok_kisa'])}")
    for fn, val in active_issues["cok_kisa"][:5]:
        print(f"    - {fn}: '{val}'")
    print(f"  Çok uzun (>500 char): {len(active_issues['cok_uzun'])}")
    for fn, val in active_issues["cok_uzun"][:3]:
        print(f"    - {fn}: '{val}'")
    
    # =========================================================================
    # 3. KULLANIM YOLU KALİTESİ
    # =========================================================================
    print("\n" + "=" * 70)
    print("3. KULLANIM YOLU (usage_route) KALİTESİ")
    print("=" * 70)
    
    route_counter = Counter()
    route_none = 0
    route_suspicious = []
    
    for item in ozet:
        route = item.get("usage_route")
        fn = item.get("file_name", "")
        
        if not route:
            route_none += 1
            continue
        
        route_counter[route] += 1
        
        # Şüpheli: çok uzun (cümle değil, paragraf olmamalı)
        if len(route) > 100:
            route_suspicious.append((fn, route[:80] + "..."))
        
        # Şüpheli: ilaç adı içeriyor (yanlış satır yakalanmış olabilir)
        drug = item.get("drug_name", "")
        if drug and len(drug) > 5 and drug[:10].upper() in route.upper():
            route_suspicious.append((fn, f"İlaç adı içeriyor: {route[:80]}"))
    
    print(f"  Dolu: {len(ozet) - route_none}/{len(ozet)} ({100*(len(ozet)-route_none)/len(ozet):.1f}%)")
    print(f"  Boş: {route_none}")
    
    print(f"\n  En sık 15 route:")
    for route, count in route_counter.most_common(15):
        print(f"    {count:>4}x | {route}")
    
    print(f"\n  Şüpheli route'lar: {len(route_suspicious)}")
    for fn, desc in route_suspicious[:10]:
        print(f"    - {fn}: {desc}")
    
    # =========================================================================
    # 4. BÖLÜM İÇERİK KALİTESİ (Detaylı)
    # =========================================================================
    print("\n" + "=" * 70)
    print("4. BÖLÜM İÇERİK KALİTESİ (Detaylı)")
    print("=" * 70)
    
    bolumler = {
        "Bölüm 1": bolum1,
        "Bölüm 2": bolum2,
        "Bölüm 3": bolum3,
        "Bölüm 4": bolum4,
        "Bölüm 5": bolum5,
    }
    
    for bolum_name, bolum_data in bolumler.items():
        print(f"\n  --- {bolum_name} ---")
        
        null_title = []
        null_content = []
        short_content = []
        content_with_next_section = []
        duplicate_content = Counter()
        
        for item in bolum_data:
            fn = item.get("file_name", "")
            title = item.get("title")
            content = item.get("content")
            
            if not title:
                null_title.append(fn)
            
            if not content:
                null_content.append(fn)
                continue
            
            if len(content) < 30:
                short_content.append((fn, content))
            
            # Sonraki bölüm sızıntısı kontrolü
            bolum_no = int(bolum_name.split()[1])
            next_no = bolum_no + 1
            if next_no <= 5:
                # Sonraki bölüm başlığı içeriyor mu?
                pattern = rf'{next_no}\s*\.\s*\S+.*(?:nedir|kullan|dikkat|nas[ıi]l|olası|saklan)'
                if re.search(pattern, content[-500:] if len(content) > 500 else content, re.IGNORECASE):
                    content_with_next_section.append(fn)
            
            # Hash ile tekrar kontrolü
            content_hash = content[:200]  # İlk 200 karakter
            duplicate_content[content_hash] += 1
        
        print(f"  Toplam: {len(bolum_data)}")
        print(f"  Boş başlık: {len(null_title)}")
        print(f"  Boş içerik: {len(null_content)}")
        if null_content:
            for fn in null_content[:3]:
                print(f"    - {fn}")
        print(f"  Kısa içerik (<30 char): {len(short_content)}")
        for fn, c in short_content[:3]:
            print(f"    - {fn}: '{c}'")
        print(f"  Sonraki bölüm sızıntısı olabilecek: {len(content_with_next_section)}")
        if content_with_next_section:
            for fn in content_with_next_section[:3]:
                print(f"    - {fn}")
        
        # Tam tekrar
        exact_dupes = sum(1 for v in duplicate_content.values() if v > 1)
        if exact_dupes:
            print(f"  Duplicate içerik (ilk 200 char): {exact_dupes} benzersiz metin, birden fazla ilacta")
    
    # =========================================================================
    # 5. EK BİLGİ KALİTESİ
    # =========================================================================
    print("\n" + "=" * 70)
    print("5. EK BİLGİ KALİTESİ")
    print("=" * 70)
    
    ek_issues = {
        "license_none": [],
        "license_short": [],
        "license_ocr_bozuk": [],
        "manufacturer_none": [],
        "manufacturer_short": [],
        "manufacturer_ocr_bozuk": [],
    }
    
    for item in ek_bilgi:
        fn = item.get("file_name", "")
        lic = item.get("license_holder")
        mfr = item.get("manufacturer")
        
        if not lic:
            ek_issues["license_none"].append(fn)
        elif len(lic) < 5:
            ek_issues["license_short"].append((fn, lic))
        elif any(c in lic for c in ['$', '@', '{', '}', '[', ']', '<', '>']):
            ek_issues["license_ocr_bozuk"].append((fn, lic[:60]))
        
        if not mfr:
            ek_issues["manufacturer_none"].append(fn)
        elif len(mfr) < 5:
            ek_issues["manufacturer_short"].append((fn, mfr))
        elif any(c in mfr for c in ['$', '@', '{', '}', '[', ']', '<', '>']):
            ek_issues["manufacturer_ocr_bozuk"].append((fn, mfr[:60]))
    
    for issue_name, items in ek_issues.items():
        print(f"\n  {issue_name}: {len(items)}")
        for item in items[:5]:
            if isinstance(item, tuple):
                print(f"    - {item[0]}: '{item[1]}'")
            else:
                print(f"    - {item}")
        if len(items) > 5:
            print(f"    ... ve {len(items)-5} tane daha")
    
    # =========================================================================
    # 6. UYARI METNİ KALİTESİ
    # =========================================================================
    print("\n" + "=" * 70)
    print("6. UYARI METNİ KALİTESİ")
    print("=" * 70)
    
    warn_none = 0
    warn_short = []
    warn_same = Counter()
    
    for item in ozet:
        fn = item.get("file_name", "")
        warn = item.get("warning_text")
        
        if not warn:
            warn_none += 1
            continue
        
        if len(warn) < 50:
            warn_short.append((fn, warn))
        
        # Tekrar kontrolü
        warn_key = warn[:80]
        warn_same[warn_key] += 1
    
    print(f"  Boş warning: {warn_none}")
    print(f"  Kısa warning (<50): {len(warn_short)}")
    for fn, w in warn_short[:5]:
        print(f"    - {fn}: '{w}'")
    
    # Kaç farklı uyarı metni var?
    unique_warnings = len(warn_same)
    print(f"  Benzersiz uyarı metni: {unique_warnings}")
    print(f"  En sık 3 uyarı (ilk 80 char):")
    for w, c in warn_same.most_common(3):
        print(f"    {c:>4}x | {w}")
    
    # =========================================================================
    # 7. YARDIMCI MADDE KALİTESİ
    # =========================================================================
    print("\n" + "=" * 70)
    print("7. YARDIMCI MADDE KALİTESİ")
    print("=" * 70)
    
    exc_none = 0
    exc_short = []
    exc_long = []
    
    for item in ozet:
        fn = item.get("file_name", "")
        exc = item.get("excipients")
        
        if not exc:
            exc_none += 1
            continue
        
        if len(exc) < 10:
            exc_short.append((fn, exc))
        
        if len(exc) > 500:
            exc_long.append((fn, len(exc)))
    
    print(f"  Boş: {exc_none}")
    print(f"  Kısa (<10): {len(exc_short)}")
    for fn, e in exc_short[:5]:
        print(f"    - {fn}: '{e}'")
    print(f"  Uzun (>500): {len(exc_long)}")
    
    # =========================================================================
    # 8. HATALI DOSYALAR ANALİZİ
    # =========================================================================
    print("\n" + "=" * 70)
    print("8. HATALI DOSYALAR ANALİZİ")
    print("=" * 70)
    
    error_categories = Counter()
    for item in hatali:
        error = item.get("error", "")
        if "OCR" in error or "metin çıkarılamadı" in error:
            error_categories["OCR gerekli (metin yok)"] += 1
        elif "Bölüm 1 başlığı bulunamadı" in error:
            error_categories["Bölüm 1 başlığı bulunamadı"] += 1
        elif "font" in error.lower() or "encoding" in error.lower():
            error_categories["Font/Encoding sorunu"] += 1
        else:
            error_categories[error[:60]] += 1
    
    print(f"  Toplam hatalı: {len(hatali)}")
    for cat, count in error_categories.most_common():
        print(f"    {count:>4} | {cat}")
    
    # =========================================================================
    # 9. BÖLÜMLER ARASI TUTARLILIK
    # =========================================================================
    print("\n" + "=" * 70)
    print("9. BÖLÜMLER ARASI TUTARLILIK")
    print("=" * 70)
    
    ozet_files = {item["file_name"] for item in ozet}
    bolum1_files = {item["file_name"] for item in bolum1}
    bolum2_files = {item["file_name"] for item in bolum2}
    ek_files = {item["file_name"] for item in ek_bilgi}
    
    print(f"  Özet'te olan ama Bölüm 1'de olmayan: {len(ozet_files - bolum1_files)}")
    print(f"  Bölüm 1'de olan ama Özet'te olmayan: {len(bolum1_files - ozet_files)}")
    print(f"  Özet'te olan ama Ek Bilgi'de olmayan: {len(ozet_files - ek_files)}")
    print(f"  Ek Bilgi'de olan ama Özet'te olmayan: {len(ek_files - ozet_files)}")
    
    # =========================================================================
    # 10. BÖLÜM 1 İÇERİĞİNDE BÖLÜM 2 SIZMASI DETAYLI
    # =========================================================================
    print("\n" + "=" * 70)
    print("10. BÖLÜM 1 İÇERİĞİNDE BÖLÜM 2 GEÇİŞ ANALİZİ")
    print("=" * 70)
    
    bolum1_has_bolum2 = 0
    samples = []
    for item in bolum1:
        content = item.get("content", "") or ""
        fn = item.get("file_name", "")
        # Bölüm 2 başlığı içeriyor mu?
        m = re.search(r'2\s*\.\s*\S+.*(?:kullanmadan\s+önce|dikkat\s+edilmesi)', content, re.IGNORECASE)
        if m:
            bolum1_has_bolum2 += 1
            if len(samples) < 3:
                start = max(0, m.start() - 50)
                samples.append((fn, content[start:m.end()+50]))
    
    print(f"  Bölüm 1'de Bölüm 2 başlığı bulunan: {bolum1_has_bolum2}")
    for fn, sample in samples:
        print(f"    - {fn}: ...{sample}...")
    
    # =========================================================================
    # ÖZET SKOR
    # =========================================================================
    print("\n" + "=" * 70)
    print("GENEL KALİTE SKORU")
    print("=" * 70)
    
    total = len(ozet)
    scores = {
        "İlaç Adı": total - len(drug_issues["bos_veya_none"]) - len(drug_issues["ocr_bozuk"]) - len(drug_issues["route_iceriyor"]),
        "Etkin Madde": total - active_issues["bos_veya_none"],
        "Kullanım Yolu": total - route_none,
        "Yardımcı Madde": total - exc_none,
        "Uyarı Metni": total - warn_none,
        "Bölüm 1 İçerik": total - len([i for i in bolum1 if not i.get("content")]),
        "Bölüm 2 İçerik": total - len([i for i in bolum2 if not i.get("content")]),
        "Bölüm 3 İçerik": total - len([i for i in bolum3 if not i.get("content")]),
        "Bölüm 4 İçerik": total - len([i for i in bolum4 if not i.get("content")]),
        "Bölüm 5 İçerik": total - len([i for i in bolum5 if not i.get("content")]),
        "Ruhsat Sahibi": total - len(ek_issues["license_none"]),
        "Üretici": total - len(ek_issues["manufacturer_none"]),
    }
    
    for name, score in scores.items():
        pct = 100 * score / total
        bar = "█" * int(pct // 2) + "░" * (50 - int(pct // 2))
        color_indicator = "✓" if pct >= 95 else "△" if pct >= 80 else "✗"
        print(f"  {color_indicator} {name:<20}: {score:>4}/{total} ({pct:>5.1f}%) {bar}")
    
    overall = sum(scores.values()) / (total * len(scores)) * 100
    print(f"\n  Genel Skor: {overall:.1f}%")

if __name__ == "__main__":
    analyze()
