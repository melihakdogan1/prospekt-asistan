#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDF Sayfa Yapısı Analiz Aracı

Rastgele birkaç KT PDF dosyasını alıp her sayfasını ayrı ayrı analiz eder.
Her sayfada hangi bölümlerin, etiketlerin, başlıkların olduğunu gösterir.
Bu bilgiyi kullanarak analyze_kt_structure.py'yi iyileştirebiliriz.
"""

import os
import re
import json
import random
import pdfplumber
from glob import glob

DATA_DIR = "data/kt"
OUTPUT_FILE = "pdf_sayfa_yapisi_analiz.json"

# Kaç dosya analiz edelim
SAMPLE_COUNT = 10  # Rastgele 10 dosya


def detect_zones(page_text, page_num):
    """Bir sayfa metninde hangi zone'lar var tespit et."""
    zones = []
    
    if not page_text:
        return zones
    
    lines = page_text.split('\n')
    
    for i, line in enumerate(lines):
        clean = line.strip()
        if not clean:
            continue
        
        line_lower = clean.lower()
        
        # KULLANMA TALİMATI header
        if 'kullanma talimatı' in line_lower and 'kullanma talimatını' not in line_lower and 'bu kullanma talimatında' not in line_lower:
            zones.append({"line": i+1, "type": "HEADER", "text": clean[:100]})
        
        # Etkin madde
        if re.search(r'(etkin|etken)\s*madde', line_lower):
            zones.append({"line": i+1, "type": "ETKİN_MADDE", "text": clean[:150]})
        
        # Yardımcı madde
        if re.search(r'yard[ıi]mc[ıi]\s*madde', line_lower):
            zones.append({"line": i+1, "type": "YARDIMCI_MADDE", "text": clean[:150]})
        
        # Kullanım yolu
        usage_patterns = [
            r'ağızdan\s*(alınır|kullanılır)',
            r'damar\s*içine\s*uygulanır',
            r'kas\s*içine\s*uygulanır',
            r'deri\s*(altına|üzerine)\s*uygulanır',
            r'topikal\s*yoldan',
            r'burun\s*yoluyla',
            r'inhalasyon\s*yoluyla',
            r'oral\s*yoldan',
            r'rektal\s*yoldan',
            r'vajinal\s*yoldan',
            r'göze\s*uygulanır',
            r'kulak\s*yoluyla',
        ]
        for pat in usage_patterns:
            if re.search(pat, line_lower):
                zones.append({"line": i+1, "type": "KULLANIM_YOLU", "text": clean[:100]})
                break
        
        # Uyarı kutusu
        if 'bu ilacı kullanmaya başlamadan önce' in line_lower:
            zones.append({"line": i+1, "type": "UYARI_KUTUSU_BAŞI", "text": clean[:120]})
        
        # TOC başlangıcı
        if 'bu kullanma talimatında' in line_lower:
            zones.append({"line": i+1, "type": "TOC_BAŞLANGIÇ", "text": clean[:120]})
        
        # TOC bitişi
        if re.search(r'ba[şs]l[ıi]klar[ıi]\s*yer\s*almaktad[ıi]r', line_lower):
            zones.append({"line": i+1, "type": "TOC_BİTİŞ", "text": clean[:120]})
        
        # Bölüm 1 başlığı
        if re.search(r'^\s*1\s*[.\-\)]\s*.*nedir.*kullan[ıi]l[ıi]r', line_lower):
            zones.append({"line": i+1, "type": "BÖLÜM_1_BAŞLIK", "text": clean[:150]})
        
        # Bölüm 2 başlığı
        if re.search(r'^\s*2\s*[.\-\)]\s*.*kullanmadan\s*[öo]nce', line_lower):
            zones.append({"line": i+1, "type": "BÖLÜM_2_BAŞLIK", "text": clean[:150]})
        
        # Bölüm 3 başlığı
        if re.search(r'^\s*3\s*[.\-\)]\s*.*nas[ıi]l\s*kullan[ıi]l[ıi]r', line_lower):
            zones.append({"line": i+1, "type": "BÖLÜM_3_BAŞLIK", "text": clean[:150]})
        
        # Bölüm 4 başlığı
        if re.search(r'^\s*4\s*[.\-\)]\s*.*yan\s*etki', line_lower):
            zones.append({"line": i+1, "type": "BÖLÜM_4_BAŞLIK", "text": clean[:150]})
        
        # Bölüm 5 başlığı
        if re.search(r'^\s*5\s*[.\-\)]\s*.*saklan', line_lower):
            zones.append({"line": i+1, "type": "BÖLÜM_5_BAŞLIK", "text": clean[:150]})
        
        # Ruhsat sahibi
        if re.search(r'ruhsat\s*sahibi', line_lower):
            zones.append({"line": i+1, "type": "RUHSAT_SAHİBİ", "text": clean[:150]})
        
        # Üretici / Üretim yeri
        if re.search(r'(üretim\s*yeri|üretici|imal\s*yeri)', line_lower):
            zones.append({"line": i+1, "type": "ÜRETİCİ", "text": clean[:150]})
        
        # Onay tarihi
        if re.search(r'bu\s*kullanma\s*talimat[ıi]\s*.*tarihinde\s*onaylanm[ıi]', line_lower):
            zones.append({"line": i+1, "type": "ONAY_TARİHİ", "text": clean[:150]})
        
        # Sağlık personeli bilgisi
        if re.search(r'a[şs]a[ğg][ıi]daki\s*bilgiler.*sa[ğg]l[ıi]k\s*personeli', line_lower):
            zones.append({"line": i+1, "type": "SAĞLIK_PERSONELİ", "text": clean[:150]})
        
        # Sayfa numarası
        if re.match(r'^\d+\s*/\s*\d+$', clean):
            zones.append({"line": i+1, "type": "SAYFA_NO", "text": clean})
    
    return zones


def analyze_single_pdf(pdf_path):
    """Tek bir PDF'yi sayfa sayfa analiz et."""
    fname = os.path.basename(pdf_path)
    result = {
        "file_name": fname,
        "total_pages": 0,
        "pages": []
    }
    
    try:
        with pdfplumber.open(pdf_path) as pdf:
            result["total_pages"] = len(pdf.pages)
            
            for page_num, page in enumerate(pdf.pages, 1):
                text = page.extract_text()
                
                page_info = {
                    "page_number": page_num,
                    "char_count": len(text) if text else 0,
                    "line_count": len(text.split('\n')) if text else 0,
                    "zones_found": detect_zones(text, page_num),
                    "first_5_lines": [],
                    "last_5_lines": []
                }
                
                if text:
                    lines = [l.strip() for l in text.split('\n') if l.strip()]
                    page_info["first_5_lines"] = lines[:5]
                    page_info["last_5_lines"] = lines[-5:] if len(lines) > 5 else lines
                
                result["pages"].append(page_info)
    
    except Exception as e:
        result["error"] = str(e)
    
    return result


def main():
    pdf_files = sorted(glob(f"{DATA_DIR}/*.pdf"))
    
    if not pdf_files:
        print(f"HATA: {DATA_DIR} dizininde PDF bulunamadı!")
        return
    
    print(f"Toplam {len(pdf_files)} PDF mevcut.")
    
    # İlk birkaç dosya + rastgele birkaç dosya seç
    # İlk 3 + rastgele 7 = 10 dosya
    selected = pdf_files[:3]
    remaining = pdf_files[3:]
    if remaining:
        random_pick = random.sample(remaining, min(SAMPLE_COUNT - 3, len(remaining)))
        selected.extend(random_pick)
    
    print(f"{len(selected)} dosya analiz ediliyor...\n")
    
    results = []
    for pdf_path in selected:
        fname = os.path.basename(pdf_path)
        print(f"  Analiz: {fname}")
        result = analyze_single_pdf(pdf_path)
        results.append(result)
    
    # Kaydet
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    # Özet yazdır
    print(f"\n{'='*70}")
    print(f"PDF SAYFA YAPISI ANALİZİ")
    print(f"{'='*70}")
    
    for r in results:
        print(f"\n--- {r['file_name']} ({r['total_pages']} sayfa) ---")
        if "error" in r:
            print(f"  HATA: {r['error']}")
            continue
        
        for pg in r["pages"]:
            zone_types = [z["type"] for z in pg["zones_found"]]
            zone_summary = ", ".join(zone_types) if zone_types else "(hiç zone bulunamadı)"
            print(f"  Sayfa {pg['page_number']}: {pg['char_count']} karakter, {pg['line_count']} satır")
            print(f"    Zone'lar: {zone_summary}")
            
            # Başlık zone'larının metinlerini göster
            for z in pg["zones_found"]:
                if "BAŞLIK" in z["type"] or z["type"] in ["HEADER", "RUHSAT_SAHİBİ", "ÜRETİCİ", "ONAY_TARİHİ", "SAĞLIK_PERSONELİ"]:
                    print(f"      [{z['type']}] {z['text']}")
    
    print(f"\nDetaylı sonuç: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
