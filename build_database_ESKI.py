#!/usr/bin/env python3
"""
İlaç Veritabanı Oluşturucu - V2 (Yeniden Yapılandırılmış + OPTİMİZE)
====================================================================
Özellikler:
- Sadece data/kt klasörünü işler
- Boilerplate temizliği (KULLANMA TALİMATI, Sayfa X/Y vb.)
- Esnek başlık tespiti (regex + anahtar kelime)
- Akıllı Context Injection: [İlaç: X | Bölüm: Y] prefix
- Strict storage_info regex (sadece gerçek saklama koşulları)
- collection.upsert ile güncelleme
- Hibrit OCR desteği

PERFORMANS OPTİMİZASYONLARI:
- Embedding modeli SINGLETON olarak yüklenir (tek seferlik)
- Manuel batch embedding ile bellek kontrolü
- Lazy loading ile gecikmeli başlatma
- Bellek güvenli veri işleme
"""

import os
import re
import time
import logging
import gc  # Bellek yönetimi için
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import threading  # Thread-safe singleton için

import chromadb
from chromadb.utils import embedding_functions
from chromadb.config import Settings
import PyPDF2
import pdfplumber
from tqdm import tqdm

# OCR için gerekli kütüphaneler (opsiyonel)
try:
    from paddleocr import PaddleOCR
    PADDLE_OCR_AVAILABLE = True
except ImportError:
    PADDLE_OCR_AVAILABLE = False

try:
    import pytesseract
    from PIL import Image
    import pdf2image
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False

# ============================================================
# KONFİGÜRASYON
# ============================================================
EMBEDDING_MODEL = "sentence-transformers/all-mpnet-base-v2"
DISTANCE_FUNCTION = "cosine"

# Script'in bulunduğu dizini baz al
SCRIPT_DIR = Path(__file__).parent.resolve()
DB_PATH = str(SCRIPT_DIR / "data" / "veritabani_optimized")
COLLECTION_NAME = "ilac_prospektusleri"

# Sadece KT klasörü (mutlak yol)
KT_PATH = str(SCRIPT_DIR / "data" / "kt")

# Metin parçalama ayarları
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 50
MIN_TEXT_LENGTH = 100

# TEST MODU - İlk N dosyayı işle (None = tümünü işle)
TEST_MODE_LIMIT = 2  # Güvenli test için 5, production için None

# ============================================================
# GÜVENLİ MOD - KAYNAK YÖNETİMİ
# ============================================================
#  UYARI: OCR çok fazla RAM tüketir ve sistemi çökertebilir!
OCR_ENABLED = False  # OCR'yi TAMAMEN DEVRE DIŞI BIRAK (True yapma!)
OCR_DPI = 72  # Çok düşük DPI (sadece OCR açıksa kullanılır)
OCR_MAX_PAGES = 1  # Sadece 1 sayfa (sadece OCR açıksa)
BATCH_SIZE = 8  # Daha küçük batch = daha az RAM
FORCE_GC_INTERVAL = 3  # Her 3 PDF'de bir zorla garbage collection
MAX_FILE_SIZE_MB = 10  # 10 MB'dan büyük PDF'leri atla

# ============================================================
# BOİLERPLATE TEMİZLEME DESENLARI
# ============================================================
BOILERPLATE_PATTERNS = [
    # Sayfa numaraları
    r'\b\d+\s*/\s*\d+\b',  # 1/12, 5 / 10
    r'Sayfa\s*\d+',  # Sayfa 1
    r'Page\s*\d+',
    # Tekrar eden başlıklar
    r'KULLANMA\s+TALİMATI',
    r'Bu\s+kullanma\s+talimatını\s+saklayınız\.?',
    r'Bu\s+ilacı\s+kullanmaya\s+başlamadan\s+önce.*?okuyunuz.*?',
    # Şirket bilgileri (genel)
    r'(?:Tel|Fax|Faks)[\s:]*[\d\s\-\(\)]+',
    r'www\.[a-zA-Z0-9\-\.]+\.[a-zA-Z]{2,}',
    r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
    # Tarih formatları
    r'\d{2}[./]\d{2}[./]\d{4}',
    # Ruhsat/onay bilgileri
    r'Ruhsat\s+(?:sahibi|numarası).*',
    r'Bu\s+kullanma\s+talimatı.*?tarihinde\s+onaylanmıştır\.?',
    # Boş satırlar ve fazla boşluklar
    r'\n{3,}',
    r'[ \t]{3,}',
]

# ============================================================
# BÖLÜM TESPİT DESENLARI (Esnek)
# ============================================================
# Ana desen: Sayı + Nokta/Parantez + Büyük harf ile başlayan metin
SECTION_NUMBER_PATTERN = r'^\s*(\d+)\s*[\.\)]\s*([A-ZÇĞİÖŞÜ].*?)$'

# Anahtar kelime bazlı bölüm tespiti (sayı olmasa bile)
SECTION_KEYWORDS = {
    'nedir': '1. NEDİR VE NE İÇİN KULLANILIR',
    'ne için kullanılır': '1. NEDİR VE NE İÇİN KULLANILIR',
    'kullanmadan önce': '2. KULLANMADAN ÖNCE',
    'dikkat edilmesi gerekenler': '2. KULLANMADAN ÖNCE',
    'nasıl kullanılır': '3. NASIL KULLANILIR',
    'kullanım şekli': '3. NASIL KULLANILIR',
    'yan etki': '4. OLASI YAN ETKİLER',
    'istenmeyen etki': '4. OLASI YAN ETKİLER',
    'saklanması': '5. SAKLANMASI',
    'saklama koşulları': '5. SAKLANMASI',
    'ambalaj içeriği': '6. AMBALAJ İÇERİĞİ',
    'piyasada mevcut': '6. AMBALAJ İÇERİĞİ',
}

# Standart bölüm isimleri
STANDARD_SECTIONS = {
    '1': 'NEDİR VE NE İÇİN KULLANILIR',
    '2': 'KULLANMADAN ÖNCE',
    '3': 'NASIL KULLANILIR',
    '4': 'OLASI YAN ETKİLER',
    '5': 'SAKLANMASI',
    '6': 'AMBALAJ İÇERİĞİ',
}

# ============================================================
# LOGGING SETUP
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('veritabani_olusturma.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ============================================================
# SINGLETON EMBEDDING MODEL - TEK SEFERLIK YÜKLEME
# ============================================================
# Bu sınıf embedding modelini SADECE BİR KEZ yükler ve tüm uygulama
# boyunca aynı instance'ı kullanır. Bu sayede:
# 1. RAM tasarrufu: Model tekrar tekrar yüklenmez
# 2. CPU tasarrufu: İlk yüklemeden sonra sadece inference yapılır
# 3. Thread-safe: Çoklu thread'den güvenle erişilebilir

class EmbeddingModelSingleton:
    """
    Thread-safe Singleton pattern ile embedding model yönetimi.
    Model sadece ilk erişimde yüklenir, sonraki tüm erişimler
    aynı instance'ı kullanır.
    """
    _instance = None
    _lock = threading.Lock()
    _embedding_fn = None
    _is_initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                # Double-check locking pattern
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def get_embedding_function(self):
        """
        Embedding fonksiyonunu döndürür. İlk çağrıda yükler,
        sonraki çağrılarda cache'den döndürür.
        """
        if not self._is_initialized:
            with self._lock:
                if not self._is_initialized:
                    logger.info(f" Embedding modeli yükleniyor (TEK SEFERLIK): {EMBEDDING_MODEL}")
                    try:
                        self._embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                            model_name=EMBEDDING_MODEL,
                            device="cpu"  # GPU varsa "cuda" yapılabilir
                        )
                        self._is_initialized = True
                        logger.info(" Embedding modeli başarıyla yüklendi ve cache'lendi.")
                    except Exception as e:
                        logger.error(f"✗ Embedding modeli yüklenemedi: {e}")
                        raise
        return self._embedding_fn
    
    def compute_embeddings_batch(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        """
        Metinleri BATCH halinde embedding'e çevirir.
        Bu yöntem bellek kullanımını kontrol altında tutar.
        
        Args:
            texts: Embedding'e çevrilecek metin listesi
            batch_size: Her batch'te işlenecek metin sayısı (düşük = az RAM)
        
        Returns:
            Embedding vektörleri listesi
        """
        embedding_fn = self.get_embedding_function()
        all_embeddings = []
        
        # Metinleri batch'ler halinde işle
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            try:
                # ChromaDB'nin embedding fonksiyonu __call__ ile çağrılır
                batch_embeddings = embedding_fn(batch)
                all_embeddings.extend(batch_embeddings)
                
                # Her batch sonrası belleği temizle
                if i > 0 and i % (batch_size * 4) == 0:
                    gc.collect()
                    
            except Exception as e:
                logger.error(f"Batch embedding hatası (index {i}): {e}")
                # Hata durumunda boş vektörler döndür
                all_embeddings.extend([[0.0] * 768] * len(batch))
        
        return all_embeddings
    
    @classmethod
    def cleanup(cls):
        """Model instance'ını temizle (gerekirse)."""
        with cls._lock:
            if cls._embedding_fn is not None:
                del cls._embedding_fn
                cls._embedding_fn = None
                cls._is_initialized = False
                gc.collect()
                logger.info(" Embedding modeli bellekten temizlendi.")


# Global fonksiyon - kolay erişim için
def get_embedding_model() -> EmbeddingModelSingleton:
    """Singleton embedding model instance'ını döndürür."""
    return EmbeddingModelSingleton()


# ============================================================
# OCR İŞLEMCİ
# ============================================================
class OCRProcessor:
    """Hibrit OCR işlemci - PaddleOCR veya Tesseract kullanır."""
    
    def __init__(self):
        self.paddle_ocr = None
        if PADDLE_OCR_AVAILABLE:
            try:
                self.paddle_ocr = PaddleOCR(use_angle_cls=True, lang='tr', show_log=False)
                logger.info("PaddleOCR başarıyla yüklendi.")
            except Exception as e:
                logger.warning(f"PaddleOCR yüklenemedi: {e}")
    
    def ocr_with_paddle(self, pdf_path: str, max_pages: int = OCR_MAX_PAGES) -> str:
        """PaddleOCR ile PDF'den metin çıkar - KAYNAK GÜVENLİ."""
        if not self.paddle_ocr or not TESSERACT_AVAILABLE:
            return ""
        
        images = None
        img_array = None
        
        try:
            import numpy as np
            # DÜşÜK DPI ve SAYFA KISITLAMASI ile görüntü oluştur
            images = pdf2image.convert_from_path(
                pdf_path, 
                dpi=OCR_DPI,  # 150 DPI (eskisi 200 idi)
                first_page=1,
                last_page=max_pages  # Sadece ilk N sayfa
            )
            full_text = ""
            
            for idx, image in enumerate(images):
                img_array = np.array(image)
                result = self.paddle_ocr.ocr(img_array, cls=True)
                if result and result[0]:
                    for line in result[0]:
                        if line and len(line) > 1:
                            full_text += line[1][0] + "\n"
                    full_text += "\n"
                
                # HER SAYFA SONRASI BELLEK TEMİZLİĞİ
                del img_array
                img_array = None
                
            return full_text.strip()
        except Exception as e:
            logger.warning(f"PaddleOCR hatası: {e}")
            return ""
        finally:
            # BELLEK TEMİZLİĞİ - Ağır nesneleri sil
            if images is not None:
                for img in images:
                    del img
                del images
            if img_array is not None:
                del img_array
            gc.collect()  # Zorla garbage collection
    
    def ocr_with_tesseract(self, pdf_path: str, max_pages: int = OCR_MAX_PAGES) -> str:
        """Tesseract ile PDF'den metin çıkar - KAYNAK GÜVENLİ."""
        if not TESSERACT_AVAILABLE:
            return ""
        
        images = None
        
        try:
            # DÜŞÜK DPI ve SAYFA KISITLAMASI
            images = pdf2image.convert_from_path(
                pdf_path, 
                dpi=OCR_DPI,  # 150 DPI
                first_page=1,
                last_page=max_pages  # Sadece ilk N sayfa
            )
            full_text = ""
            
            for idx, image in enumerate(images):
                page_text = pytesseract.image_to_string(image, lang='tur')
                full_text += page_text + "\n"
                # Her sayfa sonrası görüntüyü sil
                del image
                
            return full_text.strip()
        except Exception as e:
            logger.warning(f"Tesseract OCR hatası: {e}")
            return ""
        finally:
            # BELLEK TEMİZLİĞİ
            if images is not None:
                for img in images:
                    try:
                        del img
                    except:
                        pass
                del images
            gc.collect()
    
    def process(self, pdf_path: str) -> str:
        """OCR işlemi - önce PaddleOCR, sonra Tesseract."""
        if self.paddle_ocr:
            text = self.ocr_with_paddle(pdf_path)
            if text and len(text) > MIN_TEXT_LENGTH:
                return text
        
        if TESSERACT_AVAILABLE:
            text = self.ocr_with_tesseract(pdf_path)
            if text and len(text) > MIN_TEXT_LENGTH:
                return text
        
        return ""


# Global OCR processor (lazy loading)
_ocr_processor = None

def get_ocr_processor() -> OCRProcessor:
    global _ocr_processor
    if _ocr_processor is None:
        _ocr_processor = OCRProcessor()
    return _ocr_processor


# ============================================================
# METİN ÇIKARMA VE TEMİZLEME
# ============================================================
def extract_text_from_pdf(pdf_path: str) -> str:
    """
    GÜVENLİ PDF metin çıkarma - OCR DEVRE DIŞI.
    Sadece pdfplumber ve PyPDF2 kullanır.
    """
    text = ""
    
    # DOSYA BOYUTU KONTROLÜ - Büyük dosyaları atla
    try:
        file_size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
        if file_size_mb > MAX_FILE_SIZE_MB:
            logger.warning(f" Dosya çok büyük ({file_size_mb:.1f} MB), atlanıyor: {pdf_path}")
            return ""
    except Exception as e:
        logger.warning(f"Dosya boyutu kontrol hatası: {e}")
    
    # 1. pdfplumber ile dene (en güvenilir)
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
                # Her sayfa sonrası belleği temizle
                gc.collect()
        
        if text.strip() and len(text.strip()) > MIN_TEXT_LENGTH:
            return text.strip()
    except Exception as e:
        logger.warning(f"pdfplumber hatası {pdf_path}: {e}")
    
    # 2. PyPDF2 ile dene (fallback)
    try:
        with open(pdf_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            for page in pdf_reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        
        if text.strip() and len(text.strip()) > MIN_TEXT_LENGTH:
            return text.strip()
    except Exception as e:
        logger.warning(f"PyPDF2 hatası {pdf_path}: {e}")
    
    # 3. OCR - SADECE AÇIKÇA ETKİNLEŞTİRİLDİYSE
    if OCR_ENABLED and (not text.strip() or len(text.strip()) < MIN_TEXT_LENGTH):
        logger.info(f" Metin yetersiz, OCR deneniyor (DİKKAT: yüksek RAM): {pdf_path}")
        try:
            processor = get_ocr_processor()
            ocr_text = processor.process(pdf_path)
            if ocr_text:
                return ocr_text
        except Exception as e:
            logger.error(f"OCR hatası (atlanıyor): {e}")
        finally:
            gc.collect()
    elif not text.strip() or len(text.strip()) < MIN_TEXT_LENGTH:
        logger.warning(f" Metin çıkarılamadı (OCR devre dışı): {pdf_path}")
    
    return text.strip() if text else ""


def clean_boilerplate(text: str) -> Tuple[str, Dict[str, str]]:
    """
    Boilerplate metinleri temizler.
    Returns: (temizlenmiş_metin, saklanan_boilerplate_dict)
    """
    saved_boilerplate = {}
    cleaned_text = text
    
    # Boilerplate'leri bul ve sakla
    for i, pattern in enumerate(BOILERPLATE_PATTERNS):
        matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
        if matches:
            saved_boilerplate[f"boilerplate_{i}"] = str(matches[:3])  # İlk 3'ü sakla
        cleaned_text = re.sub(pattern, ' ', cleaned_text, flags=re.IGNORECASE | re.MULTILINE)
    
    # Fazla boşlukları temizle
    cleaned_text = re.sub(r'\n{2,}', '\n\n', cleaned_text)
    cleaned_text = re.sub(r' {2,}', ' ', cleaned_text)
    cleaned_text = cleaned_text.strip()
    
    return cleaned_text, saved_boilerplate


def extract_real_drug_name(text: str, filename: str) -> str:
    """
    PDF içeriğinden gerçek ilaç ismini dinamik olarak çıkarır.
    Örn: "Lidodeks %0.4 IV İnfüzyon İçin Çözelti"
    """
    # Yöntem 1: KULLANMA TALİMATI'ndan sonraki satır
    patterns = [
        r"KULLANMA\s+TALİMATI\s*\n+\s*([A-ZÇĞİÖŞÜa-zçğıöşü][^\n]{5,100})",
        r"KULLANMA\s+TALİMATI\s*[:\-]?\s*([A-ZÇĞİÖŞÜ][^\n]{5,100})",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.MULTILINE)
        if match:
            drug_name = match.group(1).strip()
            # "Ağızdan alınır" gibi kullanım talimatlarını atla
            if not any(skip in drug_name.lower() for skip in ['ağızdan', 'haricen', 'kullanılır', 'alınır']):
                drug_name = re.sub(r'\s+', ' ', drug_name)
                if 5 < len(drug_name) < 150:
                    return drug_name
    
    # Yöntem 2: Doz bilgisi içeren satırı bul
    dose_pattern = r'^([A-ZÇĞİÖŞÜ][A-ZÇĞİÖŞÜa-zçğıöşü\s\d\.,\/%\-\+®]+(?:mg|ml|mcg|iu|g|%)[^\n]{0,80})'
    for line in text.split('\n')[:30]:
        line = line.strip()
        match = re.match(dose_pattern, line, re.IGNORECASE)
        if match and len(match.group(1)) > 5:
            return match.group(1).strip()
    
    # Fallback: Dosya adından
    name = Path(filename).stem
    name = re.sub(r'_KT$', '', name, flags=re.IGNORECASE)
    return name


def extract_storage_info_strict(text: str) -> str:
    """
    STRICT saklama bilgisi çıkarma.
    Sadece gerçek saklama koşullarını alır, yan etkilerdeki "saklayınız" kelimelerini ALMAZ.
    """
    storage_conditions = []
    
    # Strict regex desenleri - sadece gerçek saklama koşulları
    strict_patterns = [
        # Sıcaklık koşulları
        r'(\d+\s*°?\s*C[\'"]?\s*(?:nin|nın)?\s*(?:altında|üstünde|arasında)[^.]*)',
        r'((?:oda\s+)?sıcaklığında\s+sakla[^\n.]*)',
        r'(buzdolabında\s+sakla[^\n.]*)',
        r'(dondurmayınız[^\n.]*)',
        r'(2\s*-?\s*8\s*°?\s*C[^\n.]*)',
        # Işık/nem koşulları
        r'(ışıktan\s+koruyarak[^\n.]*)',
        r'(nemden\s+koruyarak[^\n.]*)',
        r'(orijinal\s+ambalajında\s+sakla[^\n.]*)',
        r'(kuru\s+(?:bir\s+)?yerde\s+sakla[^\n.]*)',
        # Çocuklardan uzak tutma
        r'(çocukların\s+(?:göremeyeceği|erişemeyeceği|ulaşamayacağı)[^\n.]*)',
    ]
    
    for pattern in strict_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            clean_match = match.strip()
            # Yan etki bölümündeki metinleri filtrele
            if not any(skip in clean_match.lower() for skip in 
                      ['yan etki', 'istenmeyen', 'belirt', 'semptom', 'görülen', 'hissed']):
                if clean_match and clean_match not in storage_conditions:
                    storage_conditions.append(clean_match)
    
    # Maksimum 3 koşul, toplam 300 karakter
    result = " | ".join(storage_conditions[:3])
    return result[:300] if result else ""


# ============================================================
# BÖLÜM TESPİTİ VE AKILLI CHUNKING
# ============================================================
def detect_sections(text: str) -> List[Dict[str, Any]]:
    """
    Metindeki bölümleri tespit eder.
    Returns: [{"start": int, "end": int, "name": str, "number": str}, ...]
    """
    sections = []
    lines = text.split('\n')
    current_pos = 0
    
    for i, line in enumerate(lines):
        line_stripped = line.strip()
        
        # Yöntem 1: Sayı + Nokta + Büyük harf deseni
        match = re.match(SECTION_NUMBER_PATTERN, line_stripped, re.MULTILINE)
        if match:
            section_num = match.group(1)
            section_text = match.group(2).strip()
            section_name = STANDARD_SECTIONS.get(section_num, section_text)
            sections.append({
                "start": current_pos,
                "line_num": i,
                "number": section_num,
                "name": f"{section_num}. {section_name}",
                "raw": line_stripped
            })
        else:
            # Yöntem 2: Anahtar kelime bazlı tespit
            line_lower = line_stripped.lower()
            for keyword, section_name in SECTION_KEYWORDS.items():
                if keyword in line_lower and len(line_stripped) < 100:
                    # Duplicate kontrolü
                    if not sections or sections[-1]["name"] != section_name:
                        sections.append({
                            "start": current_pos,
                            "line_num": i,
                            "number": section_name.split('.')[0] if '.' in section_name else "",
                            "name": section_name,
                            "raw": line_stripped
                        })
                    break
        
        current_pos += len(line) + 1  # +1 for newline
    
    # End pozisyonlarını hesapla
    for i, section in enumerate(sections):
        if i + 1 < len(sections):
            section["end"] = sections[i + 1]["start"]
        else:
            section["end"] = len(text)
    
    return sections


def get_current_section(sections: List[Dict], position: int) -> str:
    """Verilen pozisyon için geçerli bölüm adını döndürür."""
    current_section = ""
    for section in sections:
        if section["start"] <= position:
            current_section = section["name"]
        else:
            break
    return current_section


def smart_chunk_with_context(text: str, med_name: str, chunk_size: int = CHUNK_SIZE, 
                              overlap: int = CHUNK_OVERLAP) -> List[Dict[str, Any]]:
    """
    Akıllı chunking with context injection.
    Her chunk'ın başına [İlaç: X | Bölüm: Y] eklenir.
    """
    # Önce bölümleri tespit et
    sections = detect_sections(text)
    
    if len(text) <= chunk_size:
        section_name = sections[0]["name"] if sections else "Genel"
        context_prefix = f"[İlaç: {med_name} | Bölüm: {section_name}]\n"
        return [{
            "content": context_prefix + text,
            "section": section_name,
            "med_name": med_name
        }]
    
    chunks = []
    start = 0
    
    while start < len(text):
        end = min(start + chunk_size, len(text))
        
        # Kelime/cümle sınırında kes
        if end < len(text):
            search_start = max(start + chunk_size // 2, start)
            best_break = end
            
            # Paragraf sonu ara
            for i in range(end, search_start, -1):
                if i < len(text) and text[i] == '\n' and (i + 1 >= len(text) or text[i + 1] == '\n'):
                    best_break = i
                    break
            
            # Cümle sonu ara
            if best_break == end:
                for i in range(end, search_start, -1):
                    if i < len(text) and text[i] in '.!?':
                        best_break = i + 1
                        break
            
            # Kelime sonu ara
            if best_break == end:
                for i in range(end, search_start, -1):
                    if i < len(text) and text[i] in ' \n':
                        best_break = i
                        break
            
            end = best_break
        
        chunk_text = text[start:end].strip()
        
        if chunk_text:
            # Bu chunk için geçerli bölümü bul
            section_name = get_current_section(sections, start)
            if not section_name:
                section_name = "Genel"
            
            # Context prefix ekle - KRİTİK: Arama alaka düzeyini artırır
            context_prefix = f"[İlaç: {med_name} | Bölüm: {section_name}]\n"
            
            chunks.append({
                "content": context_prefix + chunk_text,
                "section": section_name,
                "med_name": med_name
            })
        
        start = end - overlap
        if start >= len(text):
            break
    
    return chunks


# ============================================================
# PDF BULMA
# ============================================================
def find_all_pdfs() -> List[Dict[str, str]]:
    """data/kt klasöründeki TÜM PDF'leri bulur."""
    pdfs = []
    
    if not os.path.exists(KT_PATH):
        logger.error(f"'{KT_PATH}' klasörü bulunamadı!")
        return pdfs
    
    logger.info(f"PDF'ler aranıyor: {KT_PATH}")
    
    for pdf_file in Path(KT_PATH).glob("*.pdf"):
        pdfs.append({
            "path": str(pdf_file),
            "type": "KT",
            "name": pdf_file.stem
        })
    
    logger.info(f"Toplam {len(pdfs)} PDF dosyası bulundu.")
    return pdfs


# ============================================================
# ANA VERİTABANI OLUŞTURMA FONKSİYONU (OPTİMİZE EDİLMİŞ)
# ============================================================
def create_database():
    """
    Ana veritabanı oluşturma/güncelleme fonksiyonu.
    
    OPTİMİZASYONLAR:
    1. Embedding modeli SINGLETON olarak kullanılır (tekrar yüklenmez)
    2. Embedding'ler MANUEL BATCH halinde hesaplanır
    3. ChromaDB'ye embedding'ler hazır olarak gönderilir (tekrar hesaplanmaz)
    4. Bellek kullanımı kontrol altında tutulur
    """
    logger.info("=" * 70)
    logger.info("İLAÇ VERİTABANI OLUŞTURUCU - V2 (OPTİMİZE EDİLMİŞ)")
    logger.info("=" * 70)
    start_time = time.time()
    
    # --- 1. Embedding Fonksiyonunu SINGLETON'dan Al ---
    # ÖNEMLİ: Model burada TEKRAR YÜKLENMEZ, zaten yüklüyse cache'den gelir
    logger.info(f"Embedding modeli kontrol ediliyor: {EMBEDDING_MODEL}")
    try:
        embedding_manager = get_embedding_model()
        embedding_fn = embedding_manager.get_embedding_function()
        logger.info(" Embedding modeli hazır (singleton).")
    except Exception as e:
        logger.error(f"✗ Embedding modeli yüklenemedi: {e}")
        return False
    
    # --- 2. ChromaDB İstemcisini Başlat ---
    os.makedirs(DB_PATH, exist_ok=True)
    client = chromadb.PersistentClient(
        path=DB_PATH,
        settings=Settings(allow_reset=True, anonymized_telemetry=False)
    )
    
    # Koleksiyonu al veya oluştur
    # ÖNEMLİ: embedding_function=None yaparak ChromaDB'nin otomatik
    # embedding hesaplamasını DEVRE DIŞI bırakıyoruz. Embedding'leri
    # kendimiz hesaplayıp göndereceğiz (daha verimli).
    try:
        collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": DISTANCE_FUNCTION},
            embedding_function=embedding_fn  # Sorgu için gerekli
        )
        logger.info(f" Koleksiyon hazır: {COLLECTION_NAME}")
    except Exception as e:
        logger.error(f"✗ Koleksiyon hatası: {e}")
        return False
    
    # --- 3. PDF'leri Bul ---
    pdf_files = find_all_pdfs()
    
    if not pdf_files:
        logger.error("Hiç PDF dosyası bulunamadı!")
        return False
    
    # TEST MODU
    if TEST_MODE_LIMIT is not None:
        pdf_files = pdf_files[:TEST_MODE_LIMIT]
        logger.info(f"⚠ TEST MODU: Sadece ilk {TEST_MODE_LIMIT} PDF işlenecek.")
    
    # --- 4. PDF'leri İşle ---
    stats = {
        "total_chunks": 0,
        "processed_pdfs": 0,
        "failed_pdfs": 0,
        "upserted_chunks": 0
    }
    
    # BATCH için listeler
    batch_docs = []
    batch_ids = []
    batch_metadatas = []
    
    # OPTİMİZE EDİLMİŞ BATCH BOYUTU
    # Daha küçük batch = daha az anlık RAM kullanımı
    EMBEDDING_BATCH_SIZE = 8  # Embedding hesaplama için
    UPSERT_BATCH_SIZE = BATCH_SIZE  # ChromaDB upsert için
    
    # Başlangıçta belleği temizle
    gc.collect()
    logger.info("=" * 50)
    logger.info("⚙ OPTİMİZE MOD AKTİF")
    logger.info(f"  • Embedding: SINGLETON (tek yükleme) ")
    logger.info(f"  • Embedding Batch: {EMBEDDING_BATCH_SIZE}")
    logger.info(f"  • OCR: {'AÇIK ' if OCR_ENABLED else 'KAPALI '}")
    logger.info(f"  • Upsert Batch: {UPSERT_BATCH_SIZE}")
    logger.info(f"  • Max dosya: {MAX_FILE_SIZE_MB} MB")
    logger.info(f"  • GC aralığı: Her {FORCE_GC_INTERVAL} PDF")
    logger.info("=" * 50)
    
    for i, pdf_info in enumerate(tqdm(pdf_files, desc="PDF'ler işleniyor")):
        pdf_path = pdf_info["path"]
        pdf_name = pdf_info["name"]
        
        logger.debug(f"[{i+1}/{len(pdf_files)}] İşleniyor: {pdf_name}")
        
        # 4.1 Metin çıkar
        raw_text = extract_text_from_pdf(pdf_path)
        if not raw_text:
            logger.warning(f"Metin çıkarılamadı: {pdf_name}")
            stats["failed_pdfs"] += 1
            continue
        
        # 4.2 İlaç adını çıkar
        med_name = extract_real_drug_name(raw_text, pdf_name)
        
        # 4.3 Boilerplate temizle (metadata'da sakla)
        cleaned_text, saved_boilerplate = clean_boilerplate(raw_text)
        
        # 4.4 Saklama bilgisini çıkar (STRICT)
        storage_info = extract_storage_info_strict(raw_text)
        
        # 4.5 Akıllı chunking with context injection
        chunks = smart_chunk_with_context(cleaned_text, med_name)
        
        if not chunks:
            logger.warning(f"Chunk oluşturulamadı: {pdf_name}")
            stats["failed_pdfs"] += 1
            continue
        
        stats["processed_pdfs"] += 1
        stats["total_chunks"] += len(chunks)
        
        # 4.6 Her chunk için batch'e ekle
        for j, chunk_data in enumerate(chunks):
            chunk_id = f"{pdf_name}_{j}"
            
            metadata = {
                "source": pdf_name,
                "type": "KT",
                "med_name": med_name,
                "section": chunk_data["section"],
                "chunk_index": j,
                "total_chunks": len(chunks),
                "pdf_path": pdf_path,
            }
            
            # Saklama bilgisi varsa ekle
            if storage_info:
                metadata["storage_info"] = storage_info
            
            batch_docs.append(chunk_data["content"])
            batch_ids.append(chunk_id)
            batch_metadatas.append(metadata)
            
            # Batch dolduğunda UPSERT yap
            if len(batch_docs) >= UPSERT_BATCH_SIZE:
                try:
                    # OPTİMİZASYON: Embedding'leri manuel hesapla
                    # Bu sayede batch boyutunu kontrol edebiliyoruz
                    batch_embeddings = embedding_manager.compute_embeddings_batch(
                        batch_docs, 
                        batch_size=EMBEDDING_BATCH_SIZE
                    )
                    
                    # Hazır embedding'lerle upsert (ChromaDB tekrar hesaplamaz)
                    collection.upsert(
                        documents=batch_docs,
                        ids=batch_ids,
                        metadatas=batch_metadatas,
                        embeddings=batch_embeddings  # ÖNEMLİ: Hazır embedding
                    )
                    stats["upserted_chunks"] += len(batch_docs)
                    logger.debug(f"Upsert: {len(batch_docs)} chunk")
                    
                    # Batch listelerini temizle
                    batch_docs, batch_ids, batch_metadatas = [], [], []
                    
                    # Embedding sonrası bellek temizliği
                    del batch_embeddings
                    gc.collect()
                    
                except Exception as e:
                    logger.error(f"Upsert hatası: {e}")
                    batch_docs, batch_ids, batch_metadatas = [], [], []
        
        # HER PDF SONRASI BELLEK TEMİZLİĞİ
        del raw_text, cleaned_text, chunks
        
        # Belirli aralıklarla zorla garbage collection
        if (i + 1) % FORCE_GC_INTERVAL == 0:
            gc.collect()
            logger.debug(f" GC çalıştırıldı ({i+1}. PDF sonrası)")
    
    # Kalan batch'i upsert et
    if batch_docs:
        try:
            # Son batch için de embedding hesapla
            batch_embeddings = embedding_manager.compute_embeddings_batch(
                batch_docs, 
                batch_size=EMBEDDING_BATCH_SIZE
            )
            
            collection.upsert(
                documents=batch_docs,
                ids=batch_ids,
                metadatas=batch_metadatas,
                embeddings=batch_embeddings
            )
            stats["upserted_chunks"] += len(batch_docs)
            
            del batch_embeddings
            gc.collect()
        except Exception as e:
            logger.error(f"Son batch upsert hatası: {e}")
    
    # --- 5. Özet ---
    end_time = time.time()
    duration = end_time - start_time
    
    logger.info("=" * 70)
    logger.info("VERİTABANI GÜNCELLEME TAMAMLANDI")
    logger.info("=" * 70)
    logger.info(f"Toplam PDF sayısı    : {len(pdf_files)}")
    logger.info(f"Başarıyla işlenen    : {stats['processed_pdfs']}")
    logger.info(f"Başarısız            : {stats['failed_pdfs']}")
    logger.info(f"Toplam chunk         : {stats['total_chunks']}")
    logger.info(f"Upsert edilen chunk  : {stats['upserted_chunks']}")
    logger.info(f"İşlem süresi         : {duration:.2f} saniye")
    logger.info(f"Veritabanı konumu    : {DB_PATH}")
    logger.info(f"Koleksiyon boyutu    : {collection.count()} chunk")
    
    return True


# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    try:
        # Veritabanını oluştur/güncelle
        success = create_database()
        
        if success:
            logger.info(" İşlem başarıyla tamamlandı.")
        else:
            logger.error(" İşlem başarısız.")
            
    except KeyboardInterrupt:
        logger.warning(" Kullanıcı tarafından iptal edildi.")
    except Exception as e:
        logger.error(f" Beklenmeyen hata: {e}")
    finally:
        # BELLEK TEMİZLİĞİ - Programdan çıkarken
        try:
            EmbeddingModelSingleton.cleanup()
        except:
            pass
        gc.collect()
        logger.info(" Bellek temizlendi.")