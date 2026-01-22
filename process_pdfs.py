import os
import sqlite3
import pdfplumber
import re
import glob
import logging

# Yapılandırma
DB_NAME = "ilac_prospektus.db"
DATA_DIR = "data/kt"
LOG_FILE = "processing_errors.log"
PARTIAL_LOG_FILE = "partial_extraction.log"

# Logging ayarları
logging.basicConfig(filename=LOG_FILE, level=logging.ERROR, 
                    format='%(asctime)s - %(levelname)s - %(message)s')

def create_tables():
    """Veritabanı tablolarını oluşturur (Eğer yoksa)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # 1. Drugs Table (Metadata)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS drugs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        active_ingredient TEXT,
        excipients TEXT,
        license_holder TEXT,
        approval_date TEXT,
        pediatric_use TEXT,
        geriatric_use TEXT,
        file_path TEXT UNIQUE,
        is_ocr_required BOOLEAN DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # 2. Drug Sections Table (Content)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS drug_sections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        drug_id INTEGER,
        section_number INTEGER,
        section_title TEXT,
        content TEXT,
        FOREIGN KEY (drug_id) REFERENCES drugs (id)
    )
    ''')

    conn.commit()
    conn.close()
    print("Veritabanı tabloları kontrol edildi/oluşturuldu.")

def clean_text(text):
    """PDF alt bilgilerini ve teknik kodları temizler."""
    if not text: return ""
    
    # 1. Belge Doğrulama ve Takip kodlarını temizle
    text = re.sub(r"Belge Doğrulama Kodu:.*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"Belge Takip Adresi:.*", "", text, flags=re.IGNORECASE)
    
    # 2. Sayfa numaralarını temizle (Örn: Sayfa 1 / 5)
    text = re.sub(r"Sayfa \d+\s*/\s*\d+", "", text, flags=re.IGNORECASE)
    
    # 3. Teknik kodlar (Örn: A500-1234 gibi satır sonu kodları)
    # Genellikle sayfa sonlarında olur, bu yüzden satır sonlarını kontrol ediyoruz
    # Bu regex biraz riskli olabilir, şimdilik sadece çok belirgin olanları alalım.
    
    return text

def extract_drug_name(text, filename):
    """
    İlaç ismini PDF içeriğinden ('KULLANMA TALİMATI' altından) çeker.
    Bulamazsa dosya ismini kullanır.
    """
    lines = text.split('\n')
    for i, line in enumerate(lines):
        # "KULLANMA TALİMATI" ibaresini bul
        if "KULLANMA TALİMATI" in line.upper().replace("İ", "I"):
            # Sonraki 5 satıra bak, boş olmayan ilk satır ilaç ismidir
            for j in range(1, 6):
                if i + j < len(lines):
                    candidate = lines[i+j].strip()
                    # Çok kısa veya anlamsız satırları atla
                    if len(candidate) > 3 and not candidate.lower().startswith("ağız"):
                        return candidate
    
    # Bulunamazsa dosya isminden üret
    fallback_name = filename.replace("_KT.pdf", "").replace("_", " ")
    
    # Manuel kontrol için dosyaya yaz
    try:
        with open("manual_review_drug_names.txt", "a", encoding="utf-8") as f:
            f.write(f"{filename} -> {fallback_name}\n")
    except Exception:
        pass # Loglama hatası akışı bozmasın
        
    return fallback_name

def extract_text_from_pdf(pdf_path):
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

def parse_metadata(text):
    metadata = {
        "active_ingredient": None,
        "excipients": None,
        "license_holder": None,
        "approval_date": None
    }
    
    # Etkin Madde
    ai_match = re.search(r"Etkin madde:(.*?)(Yardımcı madde|•|$)", text, re.DOTALL | re.IGNORECASE)
    if ai_match:
        metadata["active_ingredient"] = ai_match.group(1).strip()

    # Yardımcı Maddeler
    ex_match = re.search(r"Yardımcı maddeler:(.*?)(Bu ilacı kullanmaya|1\.|•|$)", text, re.DOTALL | re.IGNORECASE)
    if ex_match:
        metadata["excipients"] = ex_match.group(1).strip()

    # Ruhsat Sahibi
    lh_match = re.search(r"Ruhsat Sahibi:(.*?)(Üretim yeri|Bu kullanma talimatı|$)", text, re.DOTALL | re.IGNORECASE)
    if lh_match:
        metadata["license_holder"] = lh_match.group(1).strip()

    # Onay Tarihi
    date_match = re.search(r"(\d{2}[./]\d{2}[./]\d{4})\s+tarihinde\s+onaylanmıştır", text, re.IGNORECASE)
    if date_match:
        metadata["approval_date"] = date_match.group(1)
    
    return metadata

def parse_age_groups(text):
    age_info = {
        "pediatric": None,
        "geriatric": None
    }
    
    # Regex Stratejisi:
    # 1. "Çocuklarda kullanımı" başlığını bul.
    # 2. Bitiş için "Yaşlılarda kullanımı", "Özel kullanım", "4. Olası yan etkiler" veya "Kullanmanız gerekenden" ara.
    
    # Daha esnek regex
    pediatric_match = re.search(r"Çocuklarda kullanımı\s*:?(.*?)(Yaşlılarda kullanımı|Özel kullanım durumları|4\.|Kullanmanız gerekenden|$)", text, re.DOTALL | re.IGNORECASE)
    if pediatric_match:
        age_info["pediatric"] = pediatric_match.group(1).strip()

    geriatric_match = re.search(r"Yaşlılarda kullanımı\s*:?(.*?)(Özel kullanım durumları|4\.|Kullanmanız gerekenden|$)", text, re.DOTALL | re.IGNORECASE)
    if geriatric_match:
        age_info["geriatric"] = geriatric_match.group(1).strip()
        
    return age_info

def parse_sections(text):
    """
    Prospektüs metnini 5 ana bölüme ayırır.
    ÖNEMLİ: PDF'lerde önce içindekiler listesi var, sonra gerçek içerik.
    Bu yüzden her bölüm için SONUNCU eşleşmeyi buluyoruz (findall + son eleman).
    """
    sections = []
    
    # Bölüm başlıkları için regex desenleri
    section_patterns = [
        (1, r"1\.\s*[^\n]*nedir[^\n]*kullanılır[^\n]*\??", "Nedir ve ne için kullanılır?"),
        (2, r"2\.\s*[^\n]*kullanmadan[^\n]*önce[^\n]*dikkat[^\n]*", "Kullanmadan önce dikkat edilmesi gerekenler"),
        (3, r"3\.\s*[^\n]*nasıl\s*kullanılır[^\n]*\??", "Nasıl kullanılır?"),
        (4, r"4\.\s*[^\n]*yan\s*etki[^\n]*", "Olası yan etkiler"),
        (5, r"5\.\s*[^\n]*saklan[^\n]*", "Saklanması"),
    ]
    
    # Tüm bölüm eşleşmelerini bul - SONUNCU eşleşmeyi al (içindekiler değil, gerçek içerik)
    matches = []
    for sec_num, pattern, title in section_patterns:
        # finditer ile TÜM eşleşmeleri bul
        all_matches = list(re.finditer(pattern, text, re.IGNORECASE))
        if all_matches:
            # SONUNCU eşleşmeyi al (gerçek içerik başlığı)
            match = all_matches[-1]
            matches.append({
                'num': sec_num,
                'title': title,
                'header_start': match.start(),  # Başlık başlangıcı
                'content_start': match.end()    # İçerik başlangıcı (başlıktan sonra)
            })
    
    # Pozisyona göre sırala
    matches.sort(key=lambda x: x['header_start'])
    
    # Her bölümün içeriğini çıkar
    for i, m in enumerate(matches):
        # İçerik başlangıcı: Bu bölümün başlığından sonrası
        start = m['content_start']
        
        # İçerik bitişi: Bir sonraki bölümün başlığının başlangıcı
        if i + 1 < len(matches):
            end = matches[i + 1]['header_start']
        else:
            # Son bölüm için "Ruhsat Sahibi" veya metin sonuna kadar
            ruhsat_match = re.search(r"Ruhsat\s*Sahibi", text[start:], re.IGNORECASE)
            if ruhsat_match:
                end = start + ruhsat_match.start()
            else:
                end = len(text)
        
        content = text[start:end].strip()
        
        # Temizlik: Baştaki soru işareti, noktalama vb.
        content = re.sub(r'^[\?\s:]+', '', content).strip()
        
        if len(content) > 10:  # Çok kısa içerikleri alma
            sections.append({
                "number": m['num'],
                "title": f"{m['num']}. {m['title']}",
                "content": content
            })
    
    return sections

def process_files(limit=None):
    create_tables() # Tabloları oluştur
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    pdf_files = glob.glob(os.path.join(DATA_DIR, "*.pdf"))
    if limit:
        pdf_files = pdf_files[:limit]
        
    print(f"Toplam {len(pdf_files)} PDF dosyası bulundu. İşlem başlıyor...")
    
    count = 0
    success_count = 0
    ocr_required_count = 0
    
    for pdf_path in pdf_files:
        filename = os.path.basename(pdf_path)
        
        # Zaten işlenmiş mi kontrol et
        cursor.execute("SELECT id FROM drugs WHERE file_path = ?", (pdf_path,))
        if cursor.fetchone():
            # print(f"Atlanıyor: {filename} (Zaten işlenmiş)")
            continue
            
        print(f"İşleniyor ({count+1}/{len(pdf_files)}): {filename}")
        
        text = extract_text_from_pdf(pdf_path)
        
        if not text or not text.strip():
            # OCR Gerekiyor (Resim PDF)
            # İlaç ismini dosya adından alıyoruz çünkü içerik okunamadı
            drug_name = filename.replace("_KT.pdf", "").replace("_", " ")
            
            cursor.execute('''
                INSERT INTO drugs (name, file_path, is_ocr_required)
                VALUES (?, ?, 1)
            ''', (drug_name, pdf_path))
            print(f"  -> [UYARI] Metin bulunamadı (OCR Gerekli)")
            ocr_required_count += 1
        else:
            try:
                # Verileri ayrıştır
                drug_name = extract_drug_name(text, filename)
                meta = parse_metadata(text)
                age = parse_age_groups(text)
                sections = parse_sections(text)
                
                # İlacı Kaydet
                cursor.execute('''
                    INSERT INTO drugs (name, active_ingredient, excipients, license_holder, approval_date, pediatric_use, geriatric_use, file_path, is_ocr_required)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                ''', (
                    drug_name, 
                    meta["active_ingredient"], 
                    meta["excipients"], 
                    meta["license_holder"], 
                    meta["approval_date"],
                    age["pediatric"],
                    age["geriatric"],
                    pdf_path
                ))
                
                drug_id = cursor.lastrowid
                
                # Bölümleri Kaydet
                for sec in sections:
                    cursor.execute('''
                        INSERT INTO drug_sections (drug_id, section_number, section_title, content)
                        VALUES (?, ?, ?, ?)
                    ''', (drug_id, sec["number"], sec["title"], sec["content"]))
                
                # Eksik bölüm kontrolü ve loglama
                if len(sections) < 5:
                    try:
                        with open(PARTIAL_LOG_FILE, "a", encoding="utf-8") as pf:
                            pf.write(f"{filename} -> Bulunan Bölüm Sayısı: {len(sections)}\n")
                    except Exception:
                        pass

                print(f"  -> Başarılı. İsim: {drug_name[:30]}... | Bölüm: {len(sections)}")
                success_count += 1
                
            except Exception as e:
                print(f"  -> [HATA] İşleme hatası: {e}")
                logging.error(f"Veri İşleme Hatası: {filename} - {e}")

        conn.commit()
        count += 1
        
    conn.close()
    print(f"\nİşlem Tamamlandı.")
    print(f"Toplam İşlenen: {count}")
    print(f"Başarılı: {success_count}")
    print(f"OCR Gerekli: {ocr_required_count}")
    print(f"Hatalar için '{LOG_FILE}' dosyasına bakabilirsiniz.")

if __name__ == "__main__":
    # Tüm dosyaları işle
    process_files(limit=None)