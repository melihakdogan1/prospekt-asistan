#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KT Analiz Scripti - v5 (TAM YENİDEN YAZIM)

DÜZELTMELER (v5):
- TOC (içindekiler) ile gerçek bölüm ayrımı yapıldı
- Her bölüm içeriği = o başlıktan sonraki başlığa kadar TÜM metin
- Yardımcı maddeler "Bu ilacı kullanmaya başlamadan önce" ile kesilir
- Kullanım yolu: İlaç adının hemen altındaki satır
- Ek bilgi: "Bu kullanma talimatı ... onaylanmıştır" cümlesi HARİÇ
- Sağlık personeli bilgisi ayrı alan olarak eklendi

ÇIKTILAR:
- kt_ozet.json: İlaç ismi, kullanım yolu, etkin madde, yardımcı madde
- kt_bolum1.json ... kt_bolum5.json: Her bölümün başlığı ve TAM içeriği
- kt_ek_bilgi.json: Ruhsat sahibi, üretici bilgileri
- hatali_dosyalar.json: OCR/font encoding sorunlu dosyalar
"""

import os
import re
import json
import pdfplumber
from tqdm import tqdm
from glob import glob

# ================== PATHS ==================
DATA_DIR = "data/kt"
OUTPUT_DIR = "analiz_sonuclari"
os.makedirs(OUTPUT_DIR, exist_ok=True)

FILE_SUMMARY = f"{OUTPUT_DIR}/kt_ozet.json"
FILE_SECTION_1 = f"{OUTPUT_DIR}/kt_bolum1.json"
FILE_SECTION_2 = f"{OUTPUT_DIR}/kt_bolum2.json"
FILE_SECTION_3 = f"{OUTPUT_DIR}/kt_bolum3.json"
FILE_SECTION_4 = f"{OUTPUT_DIR}/kt_bolum4.json"
FILE_SECTION_5 = f"{OUTPUT_DIR}/kt_bolum5.json"
FILE_EXTRA = f"{OUTPUT_DIR}/kt_ek_bilgi.json"
FILE_FAILURES = f"{OUTPUT_DIR}/hatali_dosyalar.json"
FILE_REPORT = f"{OUTPUT_DIR}/rapor.json"

# Türkçe özel karakterler
TURKISH_SPECIAL = set("çÇğĞıİöÖşŞüÜ")
LATIN_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


# ================== YARDIMCI FONKSİYONLAR ==================

def detect_font_encoding_issue(text):
    """
    Font encoding sorunu var mı kontrol et.
    Sorun: Sadece Türkçe karakterler görünüyor (ç, ı, ğ, vs.), Latin harfler yok.
    """
    if not text or len(text) < 50:
        return True  # Çok kısa metin = sorunlu
    
    # İlk 500 karakteri analiz et
    sample = text[:500]
    
    turkish_count = sum(1 for c in sample if c in TURKISH_SPECIAL)
    latin_count = sum(1 for c in sample if c in LATIN_CHARS)
    
    # Eğer Türkçe karakter var ama Latin harf çok az ise = font sorunu
    if turkish_count > 10 and latin_count < 20:
        return True
    
    # Sadece whitespace ve Türkçe karakter varsa
    non_whitespace = [c for c in sample if not c.isspace()]
    if len(non_whitespace) > 0:
        turkish_ratio = turkish_count / len(non_whitespace)
        if turkish_ratio > 0.7:  # %70'ten fazla Türkçe özel = font sorunu
            return True
    
    return False


def extract_pdf_text(pdf_path):
    """PDF'den metin çıkar."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages_text = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages_text.append(text)
            return "\n".join(pages_text)
    except Exception:
        return None


def clean_page_noise(text):
    """
    Sayfa gürültüsü temizliği:
    - Sayfa numaraları (1/5, Sayfa 1, vb.)
    - QR kod artıkları
    """
    if not text:
        return None
    
    cleaned_lines = []
    for line in text.split('\n'):
        clean_line = line.strip()
        if not clean_line:
            cleaned_lines.append("")
            continue
        
        # Sayfa numarası desenleri
        if re.match(r'^\d+\s*/\s*\d+$', clean_line):
            continue
        if re.match(r'^Sayfa\s*\d+', clean_line, re.IGNORECASE):
            continue
        if re.match(r'^\d{1,2}\s*$', clean_line):
            continue
        
        # QR kod artıkları
        if re.match(r'^[A-Z0-9]{20,}$', clean_line):
            continue
        
        cleaned_lines.append(clean_line)
    
    return '\n'.join(cleaned_lines)


def clean_substance_text(text):
    """
    Madde (etkin/yardımcı) metni temizliği:
    - "ler:" / "si:" prefix'lerini kaldır
    - "Etkin madde:" etiketlerini kaldır
    """
    if not text:
        return None
    
    # Başlangıçtaki "ler:", "si:" prefix'lerini kaldır
    text = re.sub(r'^[\s:•\-]*ler\s*:\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^[\s:•\-]*si\s*:\s*', '', text, flags=re.IGNORECASE)
    
    # Etkin/Yardımcı madde etiketlerini kaldır
    patterns_to_remove = [
        r'^[\W_]*(Etkin|Etken)\s*madde(ler|si)?\s*:?\s*',
        r'^[\W_]*Yard[ıi]mc[ıi]\s*madde(ler|si)?\s*:?\s*',
    ]
    
    for pattern in patterns_to_remove:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    
    # Başındaki/sonundaki gereksiz karakterleri temizle
    text = re.sub(r'^[\s:;\-•·]+', '', text)
    text = re.sub(r'[\s:;\-•·]+$', '', text)
    
    return text.strip() if text.strip() else None


# ================== METADATA ÇIKARMA ==================

def find_toc_end_position(text):
    """
    İçindekiler tablosu bitiş pozisyonunu bul.
    "Başlıkları yer almaktadır" ifadesi ile biter.
    """
    match = re.search(r'Ba[şs]l[ıi]klar[ıi]\s*yer\s*almaktad[ıi]r', text, re.IGNORECASE)
    if match:
        return match.end()
    return None


def parse_header_zone(text):
    """
    Header zone'u analiz et:
    - İlaç adı
    - Kullanım yolu  
    - Etkin madde
    - Yardımcı maddeler (Bu ilacı kullanmaya başlamadan önce'ye kadar)
    """
    result = {
        "drug_name": None,
        "usage_route": None,
        "active_substance": None,
        "excipients": None,
        "warning_text": None
    }
    
    lines = text.split('\n')
    
    # Önemli pozisyonları bul
    header_idx = None
    active_idx = None
    excipient_idx = None
    warning_idx = None  # "Bu ilacı kullanmaya başlamadan önce"
    toc_idx = None      # "Bu Kullanma Talimatında:"
    
    for i, line in enumerate(lines):
        line_stripped = line.strip()
        
        # NOT: .lower() ile Türkçe İ→i̇ (combining dot) sorunu yaşanıyor.
        # Bu yüzden tüm karşılaştırmalar re.IGNORECASE ile yapılıyor.
        
        if header_idx is None and re.search(r'^KULLANMA\s+TAL.MAT.$', line_stripped, re.IGNORECASE):
            # Sadece tek başına "KULLANMA TALİMATI" satırı (TALĐMATI varyantı dahil)
            header_idx = i
        elif active_idx is None and re.search(r'(etkin|etken)\s*madde', line_stripped, re.IGNORECASE):
            active_idx = i
        elif excipient_idx is None and re.search(r'yard[ıi\u0131]mc[ıi\u0131]\s*madde', line_stripped, re.IGNORECASE):
            excipient_idx = i
        elif warning_idx is None and re.search(r'bu\s+ilac[ıi\u0131]\s+kullanmaya\s+ba[şs]lamadan\s+[öo]nce', line_stripped, re.IGNORECASE):
            warning_idx = i
        elif toc_idx is None and re.search(r'bu\s+kullanma\s+talimat[ıi\u0131]nda', line_stripped, re.IGNORECASE):
            toc_idx = i
    
    # ===== İLAÇ ADI =====
    if header_idx is not None:
        # KULLANMA TALİMATI'ndan hemen sonraki satır
        name_end = active_idx if active_idx else (excipient_idx if excipient_idx else min(header_idx + 10, len(lines)))
        
        for i in range(header_idx + 1, name_end):
            line = lines[i].strip()
            if not line:
                continue
            # Kullanım yolu değilse
            if re.search(r'(a[ğg][ıi]zdan|damar|kas\s*i[çc]ine|deri|burun|g[öo]z|inhalasyon|rektal|vajinal|topikal|oral|parenteral|uygulan[ıi]r|kullan[ıi]l[ıi]r)', line, re.IGNORECASE):
                continue
            # Steril/apirojen satırı değilse
            if re.match(r'^steril[,\s]|^apirojen', line, re.IGNORECASE):
                continue
            # Etkin/yardımcı madde değilse
            if re.search(r'(etkin|etken|yard[ıi]mc[ıi])\s*madde', line, re.IGNORECASE):
                continue
            result["drug_name"] = line
            break
    
    # ===== KULLANIM YOLU =====
    # Kullanım yolu pattern'leri (geniş kapsam)
    usage_patterns = [
        r'a[ğg][ıi]zdan\s*(al[ıi]n[ıi]r|kullan[ıi]l[ıi]r)',
        r'damar\s*(?:i[çc]ine|yolu(?:yla|ndan))\s*(?:uygulan[ıi]r|kullan[ıi]l[ıi]r)',
        r'kas\s*i[çc]ine\s*(?:uygulan[ıi]r|enjekte)',
        r'deri\s*alt[ıi]na\s*(?:uygulan[ıi]r|enjekte)',
        r'deri\s*[üu]zerine\s*(?:uygulan[ıi]r|s[üu]r[üu]l[üu]r)',
        r'topikal\s*(?:yoldan|olarak)?\s*(?:kullan[ıi]l[ıi]r|uygulan[ıi]r)',
        r'burun\s*(?:yoluyla|i[çc]ine)\s*(?:kullan[ıi]l[ıi]r|uygulan[ıi]r)',
        r'inhalasyon\s*(?:yoluyla|i[çc]in)?\s*(?:kullan[ıi]l[ıi]r|uygulan[ıi]r)',
        r'oral\s*yoldan\s*(?:al[ıi]n[ıi]r|kullan[ıi]l[ıi]r)',
        r'rektal\s*yoldan\s*(?:kullan[ıi]l[ıi]r|uygulan[ıi]r)',
        r'vajinal\s*yoldan\s*(?:kullan[ıi]l[ıi]r|uygulan[ıi]r)',
        r'g[öo]ze\s*(?:uygulan[ıi]r|damla)',
        r'intraven[öo]z',
        r'subkutan\s*(?:uygulan[ıi]r|enjekte)',
        r'intram[üu]sk[üu]ler\s*(?:uygulan[ıi]r|enjekte)',
        r'kulak.*(?:i[çc]ine|yoluyla)\s*(?:uygulan[ıi]r|kullan[ıi]l[ıi]r)',
        r'cilt\s*[üu]zerine\s*(?:uygulan[ıi]r|s[üu]r[üu]l[üu]r)',
        r'a[ğg][ıi]z\s*i[çc]ine\s*(?:uygulan[ıi]r|kullan[ıi]l[ıi]r)',
        r'sprey\s*olarak\s*(?:uygulan[ıi]r|kullan[ıi]l[ıi]r)',
        r'^[^.]{0,50}(?:uygulan[ıi]r|kullan[ıi]l[ıi]r)\s*\.?\s*$',  # Kısa satır + uygulanır/kullanılır
    ]
    
    if header_idx is not None:
        start = header_idx + 1
        end = active_idx if active_idx else min(header_idx + 10, len(lines))
        
        for i in range(start, end):
            line = lines[i].strip()
            if not line:
                continue
            for pattern in usage_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    result["usage_route"] = line
                    break
            if result["usage_route"]:
                break
    
    # Fallback: header bulunamazsa, metnin ilk 2000 karakterinde ara
    if not result["usage_route"]:
        first_part = text[:2000]
        first_lines = first_part.split('\n')
        for line in first_lines:
            line_s = line.strip()
            if not line_s or len(line_s) > 80:  # Çok uzun satırlar route değildir
                continue
            for pattern in usage_patterns:
                if re.search(pattern, line_s, re.IGNORECASE):
                    result["usage_route"] = line_s
                    break
            if result["usage_route"]:
                break
    
    # ===== ETKİN MADDE =====
    if active_idx is not None:
        end_idx = excipient_idx if excipient_idx else (warning_idx if warning_idx else min(active_idx + 15, len(lines)))
        
        active_lines = []
        for i in range(active_idx, end_idx):
            line = lines[i].strip()
            if not line:
                continue
            # Yardımcı madde başladıysa dur
            if re.search(r'yard[ıi]mc[ıi]\s*madde', line, re.IGNORECASE):
                break
            active_lines.append(line)
        
        if active_lines:
            raw_text = ' '.join(active_lines)
            result["active_substance"] = clean_substance_text(raw_text)
    
    # ===== YARDIMCI MADDELER =====
    # "Bu ilacı kullanmaya başlamadan önce" ifadesine KADAR
    if excipient_idx is not None:
        end_idx = warning_idx if warning_idx else (toc_idx if toc_idx else min(excipient_idx + 20, len(lines)))
        
        excipient_lines = []
        for i in range(excipient_idx, end_idx):
            line = lines[i].strip()
            if not line:
                continue
            # "Bu ilacı kullanmaya başlamadan önce" geldiğinde DUR
            if re.search(r'bu\s+ilac.\s+kullanmaya\s+ba.lamadan', line, re.IGNORECASE):
                break
            # "Bu kullanma talimatını/talimatında" geldiğinde DUR
            if re.search(r'bu\s+kullanma\s+talimat', line, re.IGNORECASE):
                break
            excipient_lines.append(line)
        
        if excipient_lines:
            raw_text = ' '.join(excipient_lines)
            result["excipients"] = clean_substance_text(raw_text)
    
    # ===== UYARI METNİ =====
    # "Bu ilacı kullanmaya başlamadan önce" ile "Bu Kullanma Talimatında:" arası
    if warning_idx is not None:
        end_idx = toc_idx if toc_idx else min(warning_idx + 20, len(lines))
        warning_lines = []
        for i in range(warning_idx, end_idx):
            line = lines[i].strip()
            if not line:
                continue
            if re.search(r'bu\s+kullanma\s+talimat.nda', line, re.IGNORECASE):
                break
            # TOC bitişi
            if re.search(r'ba.l.klar.\s*yer\s*almaktad', line, re.IGNORECASE):
                break
            warning_lines.append(line)
        if warning_lines:
            result["warning_text"] = ' '.join(warning_lines)
    
    return result


# ================== BÖLÜM ÇIKARMA (YENİ ALGORİTMA) ==================

def find_real_section_positions(text):
    """
    GERÇEK bölüm başlıklarının pozisyonlarını bul.
    
    NOT: TOC (içindekiler) içindeki başlıklar DEĞİL, 
    "Başlıkları yer almaktadır." SONRASINDA gelen gerçek başlıklar.
    
    KRİTİK: Bölümler SIRAYLA (1→2→3→4→5) aranır.
    Her bölüm, önceki bölümün bitişinden SONRA aranır.
    Bu sayede cross-reference eşleşmeleri (ör: "bkz. 4. Olası yan etkilere")
    yanlışlıkla bölüm başlığı olarak algılanmaz.
    
    TOC marker bulunamazsa, TÜM eşleşmeleri bulup SON olanı kullanır
    (ilk eşleşme TOC'taki, son eşleşme gerçek başlık olur).
    """
    sections = {}
    
    # TOC bitiş pozisyonu
    toc_end = find_toc_end_position(text)
    
    # Bölüm başlık pattern'leri
    # NOT: Çok satırlı başlıklar için devam satırları eklendi:
    #  - Bölüm 2: "dikkat edilmesi\ngerekenler" → gerekenler dahil
    #  - Bölüm 4: "yan etkiler\nnelerdir?" → nelerdir dahil
    # NOT: "kullan[ıi]l[ıi]r" zorunlu değil - OCR bozukluğu nedeniyle
    # "kullamhr", "kullanrhr", "kullanlr" gibi varyantlar var.
    # Bölüm 1 için sadece "nedir" yeterli. Çok satırlı başlıkları da yakala.
    # Bölüm 3 için de "kullan" yeterli (OCR "kullanılır" bozabiliyor).
    patterns = {
        "1": r'1\s*[.\-\)]\s*[^\n]*?nedir[^\n]*(?:\n[^\n]*?kullan[^\n]*)?',
        "2": r'2\s*[.\-\)]\s*[^\n]*?(?:kullanmadan|kullanmaya\s*ba[şs]lamadan)\s*[öo]nce[^\n]*(?:\n\s*gerekenler[^\n]*)?',
        "3": r'3\s*[.\-\)]\s*[^\n]*?nas[ıi]l\s*kullan[^\n]*',
        "4": r'4\s*[.\-\)]\s*[^\n]*?yan\s*etki[^\n]*(?:\n\s*nelerdir[^\n]*)?',
        "5": r'5\s*[.\-\)]\s*[^\n]*?saklan[^\n]*',
    }
    
    # Sıralı bölüm listesi - HER ZAMAN bu sırayla işle
    section_order = ["1", "2", "3", "4", "5"]
    
    if toc_end:
        # TOC bulundu - SIRAYLA her bölümü bul
        # Her bölüm, önceki bölümün bitişinden sonra aranır
        current_pos = toc_end
        
        for sec_num in section_order:
            pattern = patterns[sec_num]
            search_text = text[current_pos:]
            match = re.search(pattern, search_text, re.IGNORECASE)
            if match:
                abs_start = current_pos + match.start()
                title_end = current_pos + match.end()
                sections[sec_num] = {
                    "start": abs_start,
                    "title_end": title_end,
                    "title": match.group(0).strip()
                }
                # Sonraki bölüm bu bölümün bitişinden sonra aranacak
                current_pos = title_end
    else:
        # TOC marker BULUNAMADI - tüm eşleşmeleri bul
        # Yine sıralı işle: her bölümün son eşleşmesini al,
        # ama önceki bölümden sonra olmasını garanti et
        min_pos = 0
        for sec_num in section_order:
            pattern = patterns[sec_num]
            all_matches = list(re.finditer(pattern, text, re.IGNORECASE))
            if all_matches:
                # min_pos'tan sonraki eşleşmeler arasından SON olanı al
                valid_matches = [m for m in all_matches if m.start() >= min_pos]
                if valid_matches:
                    match = valid_matches[-1]
                else:
                    # Fallback: tüm eşleşmelerden son
                    match = all_matches[-1]
                sections[sec_num] = {
                    "start": match.start(),
                    "title_end": match.end(),
                    "title": match.group(0).strip()
                }
                min_pos = match.end()
    
    return sections


def find_extra_info_position(text):
    """
    Ek bilgi bölümünün başlangıç pozisyonunu bul.
    Genelde "6. Diğer Bilgiler" veya "Ruhsat sahibi" ile başlar.
    TOC içindeki değil, gerçek ek bilgi bölümünü bul.
    
    Bölüm 5 content'inin Bölüm 6/7/8 + Ruhsat/Üretici bilgilerini
    içermemesi için ekstra sınır noktaları eklendi.
    """
    # TOC bitiş pozisyonundan sonra ara
    toc_end = find_toc_end_position(text)
    search_start = toc_end if toc_end else 0
    search_text = text[search_start:]
    
    patterns = [
        # "6. Diğer Bilgiler" başlığı (en güvenilir sınır)
        r'(?:^|\n)\s*6\s*[.\-\)]\s*[Dd]i[ğg]er\s*[Bb]ilgi',
        r'Ruhsat\s*[Ss]ahibi',
        r'Üretim\s*[Yy]eri',
        r'Üretici\s*:',
    ]
    
    earliest = None
    for pattern in patterns:
        match = re.search(pattern, search_text, re.IGNORECASE)
        if match:
            abs_pos = search_start + match.start()
            # Eğer \n ile başlıyorsa, \n'den sonrasını al
            matched_text = match.group(0)
            if matched_text.startswith('\n'):
                abs_pos += 1
            if earliest is None or abs_pos < earliest:
                earliest = abs_pos
    
    return earliest


def extract_sections(text, drug_name=None):
    """
    Bölümleri ayır ve içeriklerini çıkar.
    
    Her bölümün içeriği = o başlıktan sonraki başlığa kadar TÜM metin
    """
    result = {
        "1": {"title": None, "content": None},
        "2": {"title": None, "content": None},
        "3": {"title": None, "content": None},
        "4": {"title": None, "content": None},
        "5": {"title": None, "content": None},
        "extra": {"title": "Ek Bilgiler", "content": None}
    }
    
    if not text:
        return result
    
    # Bölüm pozisyonlarını bul
    section_positions = find_real_section_positions(text)
    extra_start = find_extra_info_position(text)
    
    if extra_start:
        section_positions["extra"] = {
            "start": extra_start,
            "title": "Ek Bilgiler"
        }
    
    # Pozisyonlara göre sırala
    sorted_sections = sorted(section_positions.items(), key=lambda x: x[1]["start"])
    
    # Her bölümün içeriğini çıkar
    for i, (sec_num, pos_info) in enumerate(sorted_sections):
        # İçerik başlangıcı: title_end kullan (başlık sonrası), yoksa başlık uzunluğu ekle
        if sec_num == "extra":
            # Extra bölüm için: başlangıç pozisyonundan itibaren TÜM metni al
            # ("Ruhsat sahibi" satırını atlamamız LAZIM DEĞİL - extract_extra_info_details bunu parse eder)
            content_start = pos_info["start"]
        elif "title_end" in pos_info:
            content_start = pos_info["title_end"]
        else:
            # Başlık pozisyonundan sonraki satıra geç
            title_match = re.search(r'\n', text[pos_info["start"]:pos_info["start"]+200])
            if title_match:
                content_start = pos_info["start"] + title_match.end()
            else:
                content_start = pos_info["start"] + len(pos_info.get("title", ""))
        
        # Bitiş: Bir sonraki bölümün başlangıcı
        if i + 1 < len(sorted_sections):
            content_end = sorted_sections[i + 1][1]["start"]
        else:
            content_end = len(text)
        
        # İçeriği al
        content = text[content_start:content_end].strip()
        
        # "Bu kullanma talimatı ... tarihinde onaylanmıştır" satırını çıkar
        content = re.sub(r'Bu\s*kullanma\s*talimat[ıi]\s*[^\n]*tarihinde\s*onaylanm[ıi][şs]t[ıi]r[^\n]*', '', content, flags=re.IGNORECASE)
        
        # Sağlık personeli bilgisini ayır (extra için)
        if sec_num == "extra":
            # Sağlık personeli bilgisi var mı?
            hp_match = re.search(r'A[ŞS]A[ĞG]IDAK[İI]\s*B[İI]LG[İI]LER\s*BU\s*[İI]LACI\s*UYGULAYACAK\s*SA[ĞG]LIK\s*PERSONEL[İI]\s*[İI][ÇC][İI]ND[İI]R', content, re.IGNORECASE)
            if hp_match:
                # Sağlık personeli bilgisini ayrı tut
                result["extra"]["healthcare_info"] = content[hp_match.start():].strip()
                content = content[:hp_match.start()].strip()
        
        # Fazla boş satırları temizle
        content = re.sub(r'\n\s*\n\s*\n+', '\n\n', content)
        content = content.strip()
        
        title = pos_info.get("title", "").strip()
        
        # POST-PROCESSING: Başlık-content kayması düzeltmeleri
        if content:
            # Bölüm 2: Content "gerekenler" ile başlıyorsa, başlığa ekle
            if sec_num == "2" and re.match(r'^gerekenler\b', content, re.IGNORECASE):
                title = title + ' ' + content.split('\n')[0].strip()
                content = '\n'.join(content.split('\n')[1:]).strip()
            
            # Bölüm 4: Content "nelerdir" ile başlıyorsa, başlığa ekle
            if sec_num == "4" and re.match(r'^nelerdir', content, re.IGNORECASE):
                first_line = content.split('\n')[0].strip()
                title = title + ' ' + first_line
                content = '\n'.join(content.split('\n')[1:]).strip()
        
        result[sec_num]["title"] = title
        result[sec_num]["content"] = content if content else None
    
    return result


def extract_extra_info_details(text):
    """
    Ek bilgiden Ruhsat sahibi ve Üretici bilgilerini ayrı ayrı çıkar.
    "Bu kullanma talimatı ... tarihinde onaylanmıştır" cümlesini ATLA.
    """
    result = {
        "license_holder": None,
        "manufacturer": None
    }
    
    if not text:
        return result
    
    # Ruhsat sahibi
    # "Ruhsat sahibi ve üretici:" birleşik formatını da yakala
    # "ve üretici" kısmını lookahead ile sınır olarak kullanma!
    license_match = re.search(
        r'Ruhsat\s*[Ss]ahibi\s*(?:ve\s*[üu]retici)?\s*[:\s]*\n?\s*(?:Ad[ıi]\s*:\s*)?(.+?)(?=\n\s*(?:[Üü]ret(?:im|ici)|[İI]mal|Ad(?:res)?[iı]\s*:)|Bu\s*kullanma\s*talimat|A[ŞS]A[ĞG]IDAK[İI]|Tel\s*:|Faks\s*:|$)',
        text, re.IGNORECASE | re.DOTALL
    )
    if license_match:
        holder = license_match.group(1).strip()
        # Çok satırlı olabilir, normalize et
        holder = ' '.join(holder.split())
        # "ve" gibi anlamsız kısa değerleri at
        if holder and len(holder) > 5:
            result["license_holder"] = holder[:500]
        else:
            result["license_holder"] = None
    
    # "Ruhsat sahibi ve üretici:" birleşik format - holder = üretici ile aynı
    if not result["license_holder"]:
        combined_match = re.search(
            r'Ruhsat\s*[Ss]ahibi\s*ve\s*[üu]retici\s*[:\s]*\n?\s*(.+?)(?=\n\s*(?:Bu\s*kullanma|Tel\s*:|Faks\s*:|A[ŞS]A[ĞG]IDAK[İI])|$)',
            text, re.IGNORECASE | re.DOTALL
        )
        if combined_match:
            holder = combined_match.group(1).strip()
            holder = ' '.join(holder.split())
            if holder and len(holder) > 5:
                result["license_holder"] = holder[:500]
    
    # Üretici / Üretim yeri
    manufacturer_match = re.search(
        r'(?:Üretim\s*[Yy]eri|Üretici|[İI]mal\s*[Yy]eri)\s*[:\s]*(.+?)(?=Bu\s*kullanma\s*talimat|Ruhsat|A[ŞS]A[ĞG]IDAK[İI]|$)',
        text, re.IGNORECASE | re.DOTALL
    )
    if manufacturer_match:
        mfr = manufacturer_match.group(1).strip()
        mfr = ' '.join(mfr.split())
        result["manufacturer"] = mfr[:500] if mfr else None
    
    return result


# ================== ANA FONKSİYON ==================

def main():
    pdf_files = sorted(glob(f"{DATA_DIR}/*.pdf"))
    
    if not pdf_files:
        print(f"HATA: {DATA_DIR} dizininde PDF bulunamadı!")
        return
    
    # Veri yapıları
    summary_data = []
    section_files = {"1": [], "2": [], "3": [], "4": [], "5": [], "extra": []}
    
    success_count = 0
    fail_count = 0
    failures = []
    
    print(f"Toplam {len(pdf_files)} dosya analiz ediliyor...")
    
    for pdf_file in tqdm(pdf_files):
        fname = os.path.basename(pdf_file)
        
        # ===== METİN ÇIKARMA =====
        raw_text = extract_pdf_text(pdf_file)
        
        # ===== FONT ENCODİNG KONTROLÜ =====
        if not raw_text or len(raw_text) < 100:
            fail_count += 1
            failures.append({
                "file": fname,
                "reason": "OCR Required - Metin çıkarılamadı veya çok kısa",
                "text_length": len(raw_text) if raw_text else 0
            })
            continue
        
        if detect_font_encoding_issue(raw_text):
            fail_count += 1
            failures.append({
                "file": fname,
                "reason": "Font Encoding Sorunu - Sadece Türkçe karakterler görünüyor",
                "text_preview": raw_text[:200] if raw_text else None
            })
            continue
        
        # ===== METİN TEMİZLİĞİ =====
        clean_txt = clean_page_noise(raw_text)
        
        # ===== METADATA ÇIKARMA =====
        meta = parse_header_zone(clean_txt)
        
        # İsim bulunamazsa dosya adından çıkar
        if not meta["drug_name"]:
            name_from_file = fname.replace("_KT.pdf", "").replace("_KUB.pdf", "").replace(".pdf", "")
            name_from_file = name_from_file.replace("_", " ").strip()
            meta["drug_name"] = name_from_file
        
        # ===== BÖLÜM ÇIKARMA =====
        sections = extract_sections(clean_txt, meta["drug_name"])
        
        # ===== EK BİLGİ ÇIKARMA =====
        extra_details = extract_extra_info_details(sections["extra"]["content"])
        
        # ===== VALİDASYON =====
        # En azından 1. bölüm başlığı bulunmalı
        if not sections["1"]["title"]:
            fail_count += 1
            failures.append({
                "file": fname,
                "reason": "Bölüm 1 başlığı bulunamadı",
                "meta_found": meta,
                "text_preview": clean_txt[:500] if clean_txt else None
            })
            continue
        
        success_count += 1
        
        # ===== JSON'LARA EKLE =====
        
        # Özet
        summary_entry = {
            "file_name": fname,
            "drug_name": meta["drug_name"],
            "usage_route": meta["usage_route"],
            "active_substance": meta["active_substance"],
            "excipients": meta["excipients"]
        }
        if meta.get("warning_text"):
            summary_entry["warning_text"] = meta["warning_text"]
        summary_data.append(summary_entry)
        
        # Bölümler (drug_name, section_title, content)
        for sec_key in ["1", "2", "3", "4", "5"]:
            section_files[sec_key].append({
                "file_name": fname,
                "drug_name": meta["drug_name"],
                "section_title": sections[sec_key]["title"],
                "content": sections[sec_key]["content"]
            })
        
        # Ek bilgi
        extra_entry = {
            "file_name": fname,
            "drug_name": meta["drug_name"],
            "license_holder": extra_details["license_holder"],
            "manufacturer": extra_details["manufacturer"],
        }
        # Sağlık personeli bilgisi varsa ekle
        if sections["extra"].get("healthcare_info"):
            extra_entry["healthcare_professional_info"] = sections["extra"]["healthcare_info"]
        
        section_files["extra"].append(extra_entry)
    
    # ===== KAYDETME =====
    print("\nSonuçlar kaydediliyor...")
    
    def save_json(path, data):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    save_json(FILE_SUMMARY, summary_data)
    save_json(FILE_SECTION_1, section_files["1"])
    save_json(FILE_SECTION_2, section_files["2"])
    save_json(FILE_SECTION_3, section_files["3"])
    save_json(FILE_SECTION_4, section_files["4"])
    save_json(FILE_SECTION_5, section_files["5"])
    save_json(FILE_EXTRA, section_files["extra"])
    save_json(FILE_FAILURES, failures)
    
    # Rapor
    total = len(pdf_files)
    report = {
        "total_files": total,
        "successful": success_count,
        "failed": fail_count,
        "success_rate": f"{(success_count/total)*100:.2f}%" if total > 0 else "0%"
    }
    save_json(FILE_REPORT, report)
    
    print(f"\n{'='*50}")
    print(f"İŞLEM TAMAMLANDI")
    print(f"{'='*50}")
    print(f"Başarılı: {success_count}")
    print(f"Hatalı/Atlanan: {fail_count}")
    print(f"Başarı Oranı: {report['success_rate']}")
    print(f"\nÇıktılar: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
