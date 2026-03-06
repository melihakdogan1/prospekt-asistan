#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PDF Yapı Analizi - KT dosyalarının sayfa sayfa yapısını anlamak için
"""

import os
import pdfplumber
import re
import json

DATA_DIR = "data/kt"
OUTPUT_FILE = "pdf_structure_analysis.json"

# Örnek dosyalar seç (farklı tiplerden)
SAMPLE_FILES = [
    "% 20 SODYUM KLORÜR ÇÖZELTİSİ İÇEREN AMPUL_KT.pdf",
    "A-FERİN FORTE 650 MG_4 MG FİLM KAPLI TABLET_KT.pdf",
    "ASPIRIN 100 MG TABLET_KT.pdf",
    "ADASUVE 9.1 mg inhalasyon için kullanımahazır toz_KT.pdf",
    "%1 SULKONAZOL NİTRAT_KT.pdf"
]

def analyze_single_pdf(pdf_path):
    """Tek bir PDF'in yapısını detaylı analiz et"""
    result = {
        "file": os.path.basename(pdf_path),
        "total_pages": 0,
        "pages": []
    }
    
    try:
        with pdfplumber.open(pdf_path) as pdf:
            result["total_pages"] = len(pdf.pages)
            
            for page_num, page in enumerate(pdf.pages, 1):
                page_text = page.extract_text() or ""
                lines = [l.strip() for l in page_text.split('\n') if l.strip()]
                
                page_analysis = {
                    "page_number": page_num,
                    "line_count": len(lines),
                    "char_count": len(page_text),
                    "first_20_lines": lines[:20],
                    "detected_elements": []
                }
                
                # Önemli elementleri tespit et
                for i, line in enumerate(lines):
                    # KULLANMA TALİMATI başlığı
                    if "KULLANMA TALİMATI" in line.upper():
                        page_analysis["detected_elements"].append({
                            "type": "HEADER",
                            "line_num": i,
                            "text": line
                        })
                    
                    # Etkin/Etken madde
                    if re.search(r'(Etkin|Etken)\s*madde', line, re.IGNORECASE):
                        page_analysis["detected_elements"].append({
                            "type": "ACTIVE_SUBSTANCE",
                            "line_num": i,
                            "text": line
                        })
                    
                    # Yardımcı madde
                    if re.search(r'Yardımcı\s*madde', line, re.IGNORECASE):
                        page_analysis["detected_elements"].append({
                            "type": "EXCIPIENTS",
                            "line_num": i,
                            "text": line
                        })
                    
                    # "Bu ilacı kullanmaya başlamadan önce" uyarısı
                    if "Bu ilacı kullanmaya başlamadan önce" in line:
                        page_analysis["detected_elements"].append({
                            "type": "WARNING_BOX_START",
                            "line_num": i,
                            "text": line[:100]
                        })
                    
                    # "Bu Kullanma Talimatında:" TOC başlığı
                    if "Bu Kullanma Talimatında" in line:
                        page_analysis["detected_elements"].append({
                            "type": "TOC_START",
                            "line_num": i,
                            "text": line
                        })
                    
                    # "Başlıkları yer almaktadır" TOC sonu
                    if "Başlıkları yer almaktadır" in line:
                        page_analysis["detected_elements"].append({
                            "type": "TOC_END",
                            "line_num": i,
                            "text": line
                        })
                    
                    # Bölüm başlıkları (1. 2. 3. 4. 5.)
                    sec1_match = re.search(r'^1\s*[.)\s]+.*?(nedir|ne için kullanılır)', line, re.IGNORECASE)
                    if sec1_match:
                        page_analysis["detected_elements"].append({
                            "type": "SECTION_1_TITLE",
                            "line_num": i,
                            "text": line
                        })
                    
                    sec2_match = re.search(r'^2\s*[.)\s]+.*?(kullanmadan önce|dikkat)', line, re.IGNORECASE)
                    if sec2_match:
                        page_analysis["detected_elements"].append({
                            "type": "SECTION_2_TITLE",
                            "line_num": i,
                            "text": line
                        })
                    
                    sec3_match = re.search(r'^3\s*[.)\s]+.*?(nasıl kullanılır)', line, re.IGNORECASE)
                    if sec3_match:
                        page_analysis["detected_elements"].append({
                            "type": "SECTION_3_TITLE",
                            "line_num": i,
                            "text": line
                        })
                    
                    sec4_match = re.search(r'^4\s*[.)\s]+.*?(yan etki)', line, re.IGNORECASE)
                    if sec4_match:
                        page_analysis["detected_elements"].append({
                            "type": "SECTION_4_TITLE",
                            "line_num": i,
                            "text": line
                        })
                    
                    sec5_match = re.search(r'^5\s*[.)\s]+.*?(saklan)', line, re.IGNORECASE)
                    if sec5_match:
                        page_analysis["detected_elements"].append({
                            "type": "SECTION_5_TITLE",
                            "line_num": i,
                            "text": line
                        })
                    
                    # Ruhsat sahibi
                    if re.search(r'Ruhsat\s*sahibi', line, re.IGNORECASE):
                        page_analysis["detected_elements"].append({
                            "type": "LICENSE_HOLDER",
                            "line_num": i,
                            "text": line
                        })
                    
                    # Üretim yeri/Üretici
                    if re.search(r'(Üretim\s*yeri|Üretici)', line, re.IGNORECASE):
                        page_analysis["detected_elements"].append({
                            "type": "MANUFACTURER",
                            "line_num": i,
                            "text": line
                        })
                    
                    # Sağlık personeli bilgisi
                    if "SAĞLIK PERSONELİ İÇİNDİR" in line.upper():
                        page_analysis["detected_elements"].append({
                            "type": "HEALTHCARE_PROFESSIONAL_INFO",
                            "line_num": i,
                            "text": line
                        })
                    
                    # Onay tarihi (bu çekilmeyecek)
                    if "tarihinde onaylanmıştır" in line.lower():
                        page_analysis["detected_elements"].append({
                            "type": "APPROVAL_DATE_SKIP",
                            "line_num": i,
                            "text": line
                        })
                
                result["pages"].append(page_analysis)
                
    except Exception as e:
        result["error"] = str(e)
    
    return result

def main():
    results = []
    
    # Önce örnek dosyaları analiz et
    print("Örnek dosyalar analiz ediliyor...")
    for fname in SAMPLE_FILES:
        fpath = os.path.join(DATA_DIR, fname)
        if os.path.exists(fpath):
            print(f"  Analiz: {fname}")
            analysis = analyze_single_pdf(fpath)
            results.append(analysis)
        else:
            print(f"  BULUNAMADI: {fname}")
    
    # Sonuçları kaydet
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"\nAnaliz tamamlandı: {OUTPUT_FILE}")
    
    # Özet bilgi yazdır
    print("\n" + "="*60)
    print("ÖZET BİLGİ")
    print("="*60)
    
    for r in results:
        print(f"\n📄 {r['file']}")
        print(f"   Sayfa sayısı: {r['total_pages']}")
        
        all_elements = []
        for page in r.get('pages', []):
            for elem in page.get('detected_elements', []):
                all_elements.append(f"Sayfa {page['page_number']}: {elem['type']}")
        
        if all_elements:
            print("   Tespit edilen elementler:")
            for e in all_elements[:15]:  # İlk 15'i göster
                print(f"      - {e}")

if __name__ == "__main__":
    main()
