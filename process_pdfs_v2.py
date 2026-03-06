#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
İlaç Prospektüs PDF İşleyici - V2 (Optimize Edilmiş)

Bu script KT (Kullanma Talimatı) PDF dosyalarını okur ve veritabanına kaydeder.
İyileştirmeler:
- Satır kırılmalarını normalize ederek regex eşleşmelerini iyileştirir
- Bölüm numaralarından sonra boşluk olup olmadığını esnek şekilde yakalar
- Ruhsat Sahibi, Üretim Yeri gibi ek bilgileri çeker
- drug_sections tablosunda drug_name de tutulur (drug_id yanında)
- Detaylı loglama ve istatistikler
"""

import os
import sqlite3
import pdfplumber
import re
import glob
import logging
from datetime import datetime

# ==================== YAPILANDIRMA ====================
DB_NAME = "ilac_prospektus.db"
DATA_DIR = "data/kt"
LOG_FILE = "processing_errors.log"
PARTIAL_LOG_FILE = "partial_extraction.log"
STATS_FILE = "processing_stats.txt"

# Logging ayarları
logging.basicConfig(
    filename=LOG_FILE, 
    level=logging.ERROR, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)

# ==================== VERİTABANI ====================
def create_tables():
    """Veritabanı tablolarını oluşturur (iyileştirilmiş şema)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Drugs Table (Ana İlaç Bilgileri)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS drugs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        active_ingredient TEXT,
        excipients TEXT,
        license_holder TEXT,
        production_site TEXT,
        approval_date TEXT,
        pediatric_use TEXT,
        geriatric_use TEXT,
        file_path TEXT UNIQUE,
        is_ocr_required BOOLEAN DEFAULT 0,
        section_count INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Drug Sections Table (Bölüm İçerikleri)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS drug_sections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        drug_id INTEGER,
        drug_name TEXT,
        section_number INTEGER,
        section_title TEXT,
        content TEXT,
        content_length INTEGER,
        FOREIGN KEY (drug_id) REFERENCES drugs (id)
    )
    ''')
    
    # Ek Bilgiler Tablosu (Ruhsat, Üretim vb. detayları)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS drug_additional_info (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        drug_id INTEGER,
        info_type TEXT,
        info_content TEXT,
        FOREIGN KEY (drug_id) REFERENCES drugs (id)
    )
    ''')
    
    # Index'ler (performans için)
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_drugs_name ON drugs(name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_sections_drug_id ON drug_sections(drug_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_sections_number ON drug_sections(section_number)')

    conn.commit()
    conn.close()
    print("✓ Veritabanı tabloları hazır.")


def reset_database():
    """Veritabanını sıfırlar (dikkatli kullanın!)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('DROP TABLE IF EXISTS drug_additional_info')
    cursor.execute('DROP TABLE IF EXISTS drug_sections')
    cursor.execute('DROP TABLE IF EXISTS drugs')
    
    conn.commit()
    conn.close()
    print("⚠ Veritabanı sıfırlandı!")


# ==================== METİN TEMİZLEME ====================
def clean_text(text):
    """PDF'den gelen metni temizler."""
    if not text:
        return ""
    
    # Belge doğrulama kodlarını temizle
    text = re.sub(r"Belge Doğrulama Kodu:.*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"Belge Takip Adresi:.*", "", text, flags=re.IGNORECASE)
    
    # Sayfa numaralarını temizle
    text = re.sub(r"Sayfa\s*\d+\s*/\s*\d+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\d+\s*$", "", text, flags=re.MULTILINE)  # Tek başına sayfa numarası
    
    return text


def normalize_text(text):
    """
    Metni normalize eder - tüm boşlukları tek boşluğa çevirir.
    Bu, çok satırlı bölüm başlıklarını yakalamak için kritik!
    """
    return re.sub(r'\s+', ' ', text)


# ==================== İLAÇ İSMİ ÇIKARMA ====================
def extract_drug_name(text, filename):
    """
    İlaç ismini PDF içeriğinden çeker.
    Öncelik: KULLANMA TALİMATI altındaki satır > Dosya adı
    """
    lines = text.split('\n')
    
    for i, line in enumerate(lines):
        line_upper = line.upper().replace("İ", "I")
        
        if "KULLANMA TALIMATI" in line_upper:
            # Sonraki satırlara bak
            for j in range(1, 8):
                if i + j < len(lines):
                    candidate = lines[i + j].strip()
                    
                    # Geçersiz satırları atla
                    if len(candidate) < 3:
                        continue
                    if candidate.lower().startswith(("ağız", "steril", "apirojen", "etkin madde", "yardımcı")):
                        continue
                    if "•" in candidate or candidate.startswith("-"):
                        continue
                    
                    return candidate
    
    # Bulunamazsa dosya isminden üret
    fallback_name = filename.replace("_KT.pdf", "").replace("_KUB.pdf", "").replace("_", " ")
    return fallback_name


# ==================== PDF OKUMA ====================
def extract_text_from_pdf(pdf_path):
    """PDF'den metin çıkarır."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            full_text = ""
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    full_text += text + "\n"
            return clean_text(full_text)
    except Exception as e:
        logging.error(f"PDF Okuma Hatası: {pdf_path} - {e}")
        return None


# ==================== METADATA ÇIKARMA ====================
def parse_metadata(text, normalized_text):
    """İlaç metadata'sını çıkarır."""
    metadata = {
        "active_ingredient": None,
        "excipients": None,
        "license_holder": None,
        "production_site": None,
        "approval_date": None
    }
    
    # Etkin Madde (normalize edilmemiş metinden - format önemli)
    ai_match = re.search(
        r"Etkin\s+madde[s]?\s*:?\s*(.*?)(?:Yardımcı\s+madde|Bu\s+ilacı|•|\n\n|$)", 
        text, 
        re.DOTALL | re.IGNORECASE
    )
    if ai_match:
        content = ai_match.group(1).strip()
        if len(content) > 3 and len(content) < 1000:
            metadata["active_ingredient"] = content

    # Yardımcı Maddeler
    ex_match = re.search(
        r"Yardımcı\s+maddeler?\s*:?\s*(.*?)(?:Bu\s+ilacı|1\.|•|\n\n|$)", 
        text, 
        re.DOTALL | re.IGNORECASE
    )
    if ex_match:
        content = ex_match.group(1).strip()
        if len(content) > 3 and len(content) < 1000:
            metadata["excipients"] = content

    # Ruhsat Sahibi (normalize edilmiş metinden)
    lh_match = re.search(
        r"Ruhsat\s+Sahibi\s*:?\s*(.*?)(?:Üretim\s+[Yy]eri|Üretici|Bu\s+kullanma|Telefon|\d{2}[./]\d{2}[./]\d{4}|$)", 
        normalized_text, 
        re.IGNORECASE
    )
    if lh_match:
        content = lh_match.group(1).strip()
        if len(content) > 3 and len(content) < 500:
            metadata["license_holder"] = content

    # Üretim Yeri
    ps_match = re.search(
        r"Üretim\s+[Yy]eri\s*:?\s*(.*?)(?:Bu\s+kullanma|Ruhsat|Telefon|\d{2}[./]\d{2}[./]\d{4}|$)", 
        normalized_text, 
        re.IGNORECASE
    )
    if ps_match:
        content = ps_match.group(1).strip()
        if len(content) > 3 and len(content) < 500:
            metadata["production_site"] = content

    # Onay Tarihi
    date_match = re.search(
        r"(\d{2}[./]\d{2}[./]\d{4})\s+tarihinde\s+onaylanmıştır", 
        normalized_text, 
        re.IGNORECASE
    )
    if date_match:
        metadata["approval_date"] = date_match.group(1)
    
    return metadata


def parse_age_groups(text):
    """Çocuk ve yaşlı kullanım bilgilerini çıkarır."""
    age_info = {"pediatric": None, "geriatric": None}
    
    # Çocuklarda Kullanım
    pediatric_match = re.search(
        r"Çocuklarda\s+kullanımı?\s*:?\s*(.*?)(?:Yaşlılarda\s+kullanım|Özel\s+kullanım|4\.|Kullanmanız\s+gerekenden|$)", 
        text, 
        re.DOTALL | re.IGNORECASE
    )
    if pediatric_match:
        content = pediatric_match.group(1).strip()
        if len(content) > 10 and len(content) < 2000:
            age_info["pediatric"] = content

    # Yaşlılarda Kullanım
    geriatric_match = re.search(
        r"Yaşlılarda\s+kullanımı?\s*:?\s*(.*?)(?:Özel\s+kullanım|4\.|Kullanmanız\s+gerekenden|$)", 
        text, 
        re.DOTALL | re.IGNORECASE
    )
    if geriatric_match:
        content = geriatric_match.group(1).strip()
        if len(content) > 10 and len(content) < 2000:
            age_info["geriatric"] = content
        
    return age_info


# ==================== EK BİLGİLER ÇIKARMA ====================
def parse_additional_info(text, normalized_text):
    """
    Prospektüsün son kısmındaki ek bilgileri çıkarır:
    - Ruhsat sahibi adresi
    - Üretici/Üretim yeri adresi
    - Saklama koşulları
    - Son kullanma tarihi uyarısı
    - Ambalaj bilgisi
    """
    additional_info = []
    
    # Ruhsat Sahibi (tam bilgi - isim + adres)
    ruhsat_match = re.search(
        r"Ruhsat\s+[Ss]ahibi\s*:?\s*(.*?)(?:Üretim\s+[Yy]eri|Üretici|Bu\s+kullanma\s+talimatı|\d{2}[./]\d{2}[./]\d{4}\s+tarihinde)",
        normalized_text,
        re.IGNORECASE
    )
    if ruhsat_match:
        content = ruhsat_match.group(1).strip()
        if len(content) > 5:
            additional_info.append(("ruhsat_sahibi", content))
    
    # Üretici / Üretim Yeri (tam bilgi)
    uretim_match = re.search(
        r"(?:Üretim\s+[Yy]eri|Üretici)\s*:?\s*(.*?)(?:Bu\s+kullanma\s+talimatı|\d{2}[./]\d{2}[./]\d{4}\s+tarihinde|Ruhsat|$)",
        normalized_text,
        re.IGNORECASE
    )
    if uretim_match:
        content = uretim_match.group(1).strip()
        if len(content) > 5:
            additional_info.append(("uretim_yeri", content))
    
    # Saklama Koşulları (5. bölümden veya ayrı)
    saklama_match = re.search(
        r"(\d+\s*°?\s*C[''`]?\s*(?:nin|'nin|ın)?\s*(?:altında|üstünde)[^.]*(?:saklayınız|muhafaza\s+ediniz))",
        normalized_text,
        re.IGNORECASE
    )
    if saklama_match:
        additional_info.append(("saklama_kosullari", saklama_match.group(1).strip()))
    
    # Son Kullanma Tarihi Uyarısı
    skt_match = re.search(
        r"((?:Son\s+kullanma\s+tarih|Ambalajdaki\s+son\s+kullanma)[^.]*\.)",
        normalized_text,
        re.IGNORECASE
    )
    if skt_match:
        additional_info.append(("son_kullanma_uyarisi", skt_match.group(1).strip()))
    
    # Ambalaj Bilgisi (6. bölüm varsa veya ayrı)
    ambalaj_match = re.search(
        r"(?:6\.\s*)?(?:Ambalajın?\s+içeriği|Piyasada\s+mevcut)[^:]*:?\s*(.*?)(?:Ruhsat|Bu\s+kullanma|7\.|$)",
        normalized_text,
        re.IGNORECASE
    )
    if ambalaj_match:
        content = ambalaj_match.group(1).strip()
        if len(content) > 10 and len(content) < 1000:
            additional_info.append(("ambalaj_bilgisi", content))
    
    # Onay Tarihi
    onay_match = re.search(
        r"(\d{2}[./]\d{2}[./]\d{4})\s+tarihinde\s+onaylanmıştır",
        normalized_text,
        re.IGNORECASE
    )
    if onay_match:
        additional_info.append(("onay_tarihi", onay_match.group(1)))
    
    # İlaç Formu/Sunumu (ambalaj detayı)
    sunum_match = re.search(
        r"(\d+\s*(?:mg|ml|mcg|gr|g)['']?(?:lık|lik)?\s*(?:\d+\s*)?(?:tablet|kapsül|ampul|flakon|şişe|tüp|şurup|film|efervesan)[^.]*)",
        normalized_text,
        re.IGNORECASE
    )
    if sunum_match:
        additional_info.append(("ilac_sunumu", sunum_match.group(1).strip()))
    
    return additional_info


# ==================== BÖLÜM ÇIKARMA (KRİTİK!) ====================
def parse_sections(text, normalized_text):
    """
    Prospektüs metnini bölümlere ayırır.
    
    ÖNEMLİ:
    1. normalized_text kullanılır (satır kırılmaları tek boşluğa dönüştürülmüş)
    2. Her bölüm için SONUNCU eşleşme alınır (içindekiler değil, gerçek içerik)
    3. \s* ile boşluk opsiyonel yapılmış (1.İlaç veya 1. İlaç formatları)
    """
    sections = []
    
    # İYİLEŞTİRİLMİŞ REGEX DESENLERI
    # \s* = boşluk opsiyonel
    # .*? = ilaç ismi (herhangi bir uzunlukta)
    # Alternatif formatlar da yakalanır
    section_patterns = [
        (1, r"1\.\s*.*?nedir\s+ve\s+ne\s+için\s+kullanılır", "Nedir ve ne için kullanılır?"),
        (2, r"2\.\s*.*?kullanmadan\s+önce\s+dikkat", "Kullanmadan önce dikkat edilmesi gerekenler"),
        (3, r"3\.\s*.*?nasıl\s+kullanılır", "Nasıl kullanılır?"),
        (4, r"4\.\s*.*?olası\s+yan\s+etki", "Olası yan etkiler"),
        (5, r"5\.\s*.*?saklan", "Saklanması"),
        (6, r"6\.\s*.*?(ambalaj|içeriği|ek\s+bilgi|piyasa)", "Ambalajın içeriği ve diğer bilgiler"),
    ]
    
    # Her bölümü bul
    matches = []
    for sec_num, pattern, title in section_patterns:
        all_matches = list(re.finditer(pattern, normalized_text, re.IGNORECASE))
        if all_matches:
            # SONUNCU eşleşmeyi al (gerçek içerik, içindekiler değil)
            match = all_matches[-1]
            matches.append({
                'num': sec_num,
                'title': title,
                'header_start': match.start(),
                'content_start': match.end()
            })
    
    # Pozisyona göre sırala
    matches.sort(key=lambda x: x['header_start'])
    
    # Her bölümün içeriğini çıkar
    for i, m in enumerate(matches):
        start = m['content_start']
        
        # Bitiş: Sonraki bölüm veya Ruhsat Sahibi/metin sonu
        if i + 1 < len(matches):
            end = matches[i + 1]['header_start']
        else:
            # Son bölüm için bitiş noktası
            ruhsat_match = re.search(r"Ruhsat\s+Sahibi", normalized_text[start:], re.IGNORECASE)
            if ruhsat_match:
                end = start + ruhsat_match.start()
            else:
                end = len(normalized_text)
        
        content = normalized_text[start:end].strip()
        
        # Temizlik
        content = re.sub(r'^[\?\s:\.]+', '', content).strip()
        
        if len(content) > 20:  # Minimum içerik uzunluğu
            sections.append({
                "number": m['num'],
                "title": f"{m['num']}. {m['title']}",
                "content": content
            })
    
    return sections


# ==================== ANA İŞLEME FONKSİYONU ====================
def process_single_file(pdf_path, cursor, conn):
    """Tek bir PDF dosyasını işler ve veritabanına kaydeder."""
    filename = os.path.basename(pdf_path)
    result = {
        'success': False,
        'ocr_required': False,
        'section_count': 0,
        'drug_name': None,
        'error': None
    }
    
    # PDF'den metin çıkar
    text = extract_text_from_pdf(pdf_path)
    
    if not text or len(text.strip()) < 100:
        # OCR Gerekiyor
        drug_name = filename.replace("_KT.pdf", "").replace("_KUB.pdf", "").replace("_", " ")
        
        cursor.execute('''
            INSERT OR REPLACE INTO drugs (name, file_path, is_ocr_required, section_count, updated_at)
            VALUES (?, ?, 1, 0, ?)
        ''', (drug_name, pdf_path, datetime.now().isoformat()))
        
        result['ocr_required'] = True
        result['drug_name'] = drug_name
        return result
    
    try:
        # Metni normalize et
        normalized_text = normalize_text(text)
        
        # Verileri çıkar
        drug_name = extract_drug_name(text, filename)
        meta = parse_metadata(text, normalized_text)
        age = parse_age_groups(text)
        sections = parse_sections(text, normalized_text)
        
        # İlacı Kaydet
        cursor.execute('''
            INSERT OR REPLACE INTO drugs (
                name, active_ingredient, excipients, license_holder, production_site,
                approval_date, pediatric_use, geriatric_use, file_path, 
                is_ocr_required, section_count, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        ''', (
            drug_name, 
            meta["active_ingredient"], 
            meta["excipients"], 
            meta["license_holder"],
            meta["production_site"],
            meta["approval_date"],
            age["pediatric"],
            age["geriatric"],
            pdf_path,
            len(sections),
            datetime.now().isoformat()
        ))
        
        drug_id = cursor.lastrowid
        
        # Mevcut bölümleri sil (güncelleme için)
        cursor.execute('DELETE FROM drug_sections WHERE drug_id = ?', (drug_id,))
        
        # Yeni bölümleri kaydet
        for sec in sections:
            cursor.execute('''
                INSERT INTO drug_sections (drug_id, drug_name, section_number, section_title, content, content_length)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                drug_id, 
                drug_name,  # drug_name de ekleniyor!
                sec["number"], 
                sec["title"], 
                sec["content"],
                len(sec["content"])
            ))
        
        # Ek bilgileri çıkar ve kaydet
        additional_info = parse_additional_info(text, normalized_text)
        
        # Mevcut ek bilgileri sil (güncelleme için)
        cursor.execute('DELETE FROM drug_additional_info WHERE drug_id = ?', (drug_id,))
        
        # Yeni ek bilgileri kaydet
        for info_type, info_content in additional_info:
            cursor.execute('''
                INSERT INTO drug_additional_info (drug_id, info_type, info_content)
                VALUES (?, ?, ?)
            ''', (drug_id, info_type, info_content))
        
        result['success'] = True
        result['section_count'] = len(sections)
        result['drug_name'] = drug_name
        result['additional_info_count'] = len(additional_info)
        
    except Exception as e:
        result['error'] = str(e)
        logging.error(f"İşleme Hatası: {filename} - {e}")
    
    return result


def process_all_files(reset=False, limit=None):
    """Tüm PDF dosyalarını işler."""
    
    if reset:
        reset_database()
    
    create_tables()
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    pdf_files = glob.glob(os.path.join(DATA_DIR, "*.pdf"))
    if limit:
        pdf_files = pdf_files[:limit]
    
    print(f"\n{'='*60}")
    print(f"İLAÇ PROSPEKTÜS VERİTABANI OLUŞTURUCU v2")
    print(f"{'='*60}")
    print(f"Toplam PDF: {len(pdf_files)}")
    print(f"Veritabanı: {DB_NAME}")
    print(f"{'='*60}\n")
    
    # İstatistikler
    stats = {
        'total': len(pdf_files),
        'processed': 0,
        'success': 0,
        'ocr_required': 0,
        'errors': 0,
        'section_5': 0,
        'section_4': 0,
        'section_less': 0
    }
    
    partial_list = []
    
    for i, pdf_path in enumerate(pdf_files):
        filename = os.path.basename(pdf_path)
        
        # İlerleme göster
        if (i + 1) % 100 == 0 or i == 0:
            print(f"İşleniyor: {i+1}/{len(pdf_files)} (%{((i+1)/len(pdf_files))*100:.1f})")
        
        result = process_single_file(pdf_path, cursor, conn)
        stats['processed'] += 1
        
        if result['ocr_required']:
            stats['ocr_required'] += 1
        elif result['success']:
            stats['success'] += 1
            
            if result['section_count'] == 5:
                stats['section_5'] += 1
            elif result['section_count'] == 4:
                stats['section_4'] += 1
            else:
                stats['section_less'] += 1
                partial_list.append((filename, result['section_count'], result['drug_name']))
        else:
            stats['errors'] += 1
        
        # Her 100 dosyada commit
        if (i + 1) % 100 == 0:
            conn.commit()
    
    conn.commit()
    conn.close()
    
    # Sonuçları göster
    print(f"\n{'='*60}")
    print("İŞLEM TAMAMLANDI")
    print(f"{'='*60}")
    print(f"Toplam İşlenen: {stats['processed']}")
    print(f"Başarılı: {stats['success']}")
    print(f"  - 5 Bölüm: {stats['section_5']} ({stats['section_5']/max(stats['success'],1)*100:.1f}%)")
    print(f"  - 4 Bölüm: {stats['section_4']}")
    print(f"  - <4 Bölüm: {stats['section_less']}")
    print(f"OCR Gerekli: {stats['ocr_required']}")
    print(f"Hatalar: {stats['errors']}")
    print(f"{'='*60}\n")
    
    # Eksik bölümlü dosyaları log'a yaz
    if partial_list:
        with open(PARTIAL_LOG_FILE, 'w', encoding='utf-8') as f:
            f.write(f"Eksik Bölümlü Dosyalar ({len(partial_list)} adet)\n")
            f.write("="*60 + "\n\n")
            for filename, count, name in sorted(partial_list, key=lambda x: x[1]):
                f.write(f"{count} bölüm | {name[:40]} | {filename}\n")
        print(f"Eksik bölümlü dosyalar '{PARTIAL_LOG_FILE}' dosyasına yazıldı.")
    
    # İstatistikleri dosyaya kaydet
    with open(STATS_FILE, 'w', encoding='utf-8') as f:
        f.write(f"İşlem Tarihi: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Toplam: {stats['total']}\n")
        f.write(f"Başarılı: {stats['success']}\n")
        f.write(f"5 Bölüm: {stats['section_5']}\n")
        f.write(f"OCR Gerekli: {stats['ocr_required']}\n")
    
    return stats


# ==================== TEK İLAÇ GÜNCELLEMESİ ====================
def update_single_drug(drug_name_or_id):
    """Tek bir ilacı günceller."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # İlacı bul
    if isinstance(drug_name_or_id, int):
        cursor.execute('SELECT file_path FROM drugs WHERE id = ?', (drug_name_or_id,))
    else:
        cursor.execute('SELECT file_path FROM drugs WHERE name LIKE ?', (f'%{drug_name_or_id}%',))
    
    result = cursor.fetchone()
    if not result:
        print(f"İlaç bulunamadı: {drug_name_or_id}")
        conn.close()
        return
    
    pdf_path = result[0]
    print(f"Güncelleniyor: {pdf_path}")
    
    result = process_single_file(pdf_path, cursor, conn)
    conn.commit()
    conn.close()
    
    if result['success']:
        print(f"✓ Güncellendi: {result['drug_name']} ({result['section_count']} bölüm)")
    elif result['ocr_required']:
        print(f"⚠ OCR gerekli: {result['drug_name']}")
    else:
        print(f"✗ Hata: {result['error']}")


# ==================== VERİTABANI KONTROLÜ ====================
def check_database():
    """Veritabanı durumunu kontrol eder."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    print(f"\n{'='*60}")
    print("VERİTABANI DURUMU")
    print(f"{'='*60}")
    
    cursor.execute('SELECT COUNT(*) FROM drugs')
    total = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM drugs WHERE is_ocr_required = 1')
    ocr = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM drugs WHERE section_count = 5')
    full = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM drugs WHERE section_count < 5 AND is_ocr_required = 0')
    partial = cursor.fetchone()[0]
    
    print(f"Toplam İlaç: {total}")
    print(f"Tam (5 bölüm): {full}")
    print(f"Eksik (<5 bölüm): {partial}")
    print(f"OCR Gerekli: {ocr}")
    
    # Eksik bölümlü ilk 10
    print(f"\nEksik bölümlü ilaçlar (ilk 10):")
    cursor.execute('''
        SELECT id, name, section_count 
        FROM drugs 
        WHERE section_count < 5 AND is_ocr_required = 0
        ORDER BY section_count
        LIMIT 10
    ''')
    for row in cursor.fetchall():
        print(f"  ID {row[0]}: {row[1][:40]} -> {row[2]} bölüm")
    
    conn.close()


# ==================== ANA FONKSİYON ====================
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        
        if cmd == "reset":
            # Veritabanını sıfırla ve yeniden oluştur
            confirm = input("Veritabanı sıfırlanacak. Emin misiniz? (evet/hayır): ")
            if confirm.lower() == "evet":
                process_all_files(reset=True)
        
        elif cmd == "check":
            # Veritabanı durumunu kontrol et
            check_database()
        
        elif cmd == "update":
            # Tek ilaç güncelle
            if len(sys.argv) > 2:
                update_single_drug(sys.argv[2])
            else:
                print("Kullanım: python process_pdfs_v2.py update <ilaç_adı_veya_id>")
        
        elif cmd == "test":
            # Test modu - ilk 50 dosya
            process_all_files(reset=True, limit=50)
        
        else:
            print("Kullanım:")
            print("  python process_pdfs_v2.py          # Tüm dosyaları işle")
            print("  python process_pdfs_v2.py reset    # Sıfırla ve yeniden oluştur")
            print("  python process_pdfs_v2.py check    # Veritabanı durumunu kontrol et")
            print("  python process_pdfs_v2.py update <ilaç>  # Tek ilaç güncelle")
            print("  python process_pdfs_v2.py test     # Test (ilk 50 dosya)")
    
    else:
        # Normal çalıştırma - sadece yeni dosyaları ekle
        process_all_files(reset=False)
