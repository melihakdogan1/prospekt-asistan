"""
ProspektAsistan - Hybrid Search Backend
(SQL Name Search + Vector Concept Search)
"""
import os
import logging
import sqlite3
import re
from contextlib import asynccontextmanager
from pathlib import Path
import chromadb
from chromadb.utils import embedding_functions
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
try:
    from .rag_query_pipeline import ProspektRAG
except ImportError:
    from rag_query_pipeline import ProspektRAG

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Yollar
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "prospekt_metadata.db"
CHROMA_DB_PATH = BASE_DIR / "data" / "chroma_prospekt"
COLLECTION_NAME = "prospektus"
MODEL_NAME = "intfloat/multilingual-e5-large"

# Global State
app_state = {
    "collection": None,
    "embedding_function": None,
    "rag_pipeline": None,
}

# --- Yardımcı Fonksiyonlar ---
def turkish_lower(text: str) -> str:
    """Türkçe karakterleri doğru şekilde küçük harfe çevirir."""
    if not text:
        return ""
    tr_map = str.maketrans("ABCÇDEFGĞHIİJKLMNOÖPRSŞTUÜVYZ", "abcçdefgğhıijklmnoöprsştuüvyz")
    return text.translate(tr_map).lower()

def turkish_upper(text: str) -> str:
    """Türkçe karakterleri doğru şekilde büyük harfe çevirir."""
    if not text:
        return ""
    tr_map = str.maketrans("abcçdefgğhıijklmnoöprsştuüvyz", "ABCÇDEFGĞHIİJKLMNOÖPRSŞTUÜVYZ")
    return text.translate(tr_map).upper()

def turkish_title_case(text: str) -> str:
    """
    İlaç isimlerini okunabilir formata çevirir.
    'ASPİRİN 100 MG TABLET' -> 'Aspirin 100 mg Tablet'
    """
    if not text:
        return ""
    
    # Önce küçük harfe çevir
    text = turkish_lower(text)
    
    # Kelimeleri ayır
    words = text.split()
    result = []
    
    # Büyük kalması gereken kısaltmalar
    abbreviations = {'mg', 'ml', 'mcg', 'iu', 'gr', 'kg', 'g', 'l'}
    
    for i, word in enumerate(words):
        # Sayı ise olduğu gibi bırak
        if word.isdigit():
            result.append(word)
        # Kısaltma ise küçük harf
        elif word in abbreviations:
            result.append(word)
        # İlk kelime veya normal kelime ise baş harfi büyük
        else:
            # Türkçe baş harf büyütme
            if word:
                first_char = word[0]
                if first_char == 'i':
                    first_char = 'İ'
                elif first_char == 'ı':
                    first_char = 'I'
                else:
                    first_char = first_char.upper()
                result.append(first_char + word[1:])
    
    return ' '.join(result)

def parse_drug_name(name: str) -> dict:
    """
    İlaç ismini parçalara ayırır.
    'ASPİRİN 100 MG TABLET' -> {'brand': 'Aspirin', 'dosage': '100 mg', 'form': 'Tablet'}
    """
    if not name:
        return {'brand': '', 'dosage': '', 'form': ''}
    
    # Form türleri (sonda olabilecekler)
    forms = ['tablet', 'kapsül', 'şurup', 'damla', 'ampul', 'flakon', 
             'krem', 'jel', 'merhem', 'pomad', 'sprey', 'inhaler',
             'saşe', 'granül', 'toz', 'solüsyon', 'süspansiyon',
             'efervesan', 'fort', 'retard', 'sr', 'xr', 'cr']
    
    words = name.split()
    brand_parts = []
    dosage_parts = []
    form_parts = []
    
    i = 0
    # Marka ismini bul (sayı görene kadar)
    while i < len(words):
        word = words[i]
        if any(c.isdigit() for c in word):
            break
        brand_parts.append(word)
        i += 1
    
    # Dozaj ve form
    while i < len(words):
        word = words[i].lower()
        # Form mu kontrol et
        if any(f in word for f in forms):
            form_parts.extend(words[i:])
            break
        else:
            dosage_parts.append(words[i])
        i += 1
    
    return {
        'brand': turkish_title_case(' '.join(brand_parts)),
        'dosage': ' '.join(dosage_parts).lower() if dosage_parts else '',
        'form': turkish_title_case(' '.join(form_parts)) if form_parts else ''
    }

def clean_text(text: str) -> str:
    """Metindeki bozuk karakterleri temizler ve formatlar."""
    if not text:
        return ""
    # \uf0b7: PDF'lerde sık çıkan bozuk bullet point
    # \uf06c: Başka bir bullet varyasyonu
    # \uf0a7, \uf076, \uf0d8: Diğer potansiyel semboller
    cleaned = text.replace('☐', '•').replace('\uf06c', '•').replace('\uf0b7', '•').replace('\uf0a7', '•').replace('..', '.')
    
    # Bullet point'leri yeni satıra al (Okunabilirlik için)
    cleaned = cleaned.replace('•', '<br>• ')
    
    # PDF'ten gelen yapışık cümleleri ayır (Run-on sentences fix)
    # Örn: "...güçlükleri Bunların hepsi..." -> "...güçlükleri<br><br>Bunların hepsi..."
    split_phrases = [
        "Bunların hepsi", 
        "Bu çok ciddi", 
        "Eğer bunlardan biri", 
        "Aşağıdakilerden", 
        "Ciddi yan etkiler",
        "Bu yan etkiler"
    ]
    
    for phrase in split_phrases:
        # Case insensitive replace, öncesine çift satır başı ekle
        pattern = re.compile(f"(?i)(?<!<br>)({phrase})")
        cleaned = pattern.sub(r"<br><br>\1", cleaned)
        
    # Gereksiz çoklu <br>'leri temizle
    cleaned = cleaned.replace('<br><br><br>', '<br><br>')
    
    # Genel Run-on Sentence Fix (Küçük harf bitişi, Büyük harf başlangıcı)
    # Örn: "etkilerdirBunlar" -> "etkilerdir. Bunlar"
    # İstisna: pH, mL gibi birimler için kontrol eklenebilir ama şimdilik basit tutuyoruz.
    cleaned = re.sub(r'([a-zğüşıöç])([A-ZĞÜŞİÖÇ][a-zğüşıöç]+)', r'\1. \2', cleaned)
    
    return cleaned

def smart_truncate(text: str, limit: int = 600) -> str:
    """Metni mantıklı bir noktada keser."""
    if len(text) <= limit:
        return text
    
    # Limit sonrası ilk cümle bitişini ara (.!?)
    max_limit = limit + 150
    search_area = text[limit:max_limit]
    match = re.search(r'[.!?](?:\s|$|<)', search_area)
    
    if match:
        cut_point = limit + match.end()
        return text[:cut_point].rstrip()
    
    last_space = text.rfind(' ', 0, limit)
    if last_space != -1:
        return text[:last_space].rstrip()
        
    return text[:limit].rstrip()

def create_smart_summary(raw_text: str, active_ingredient: str = None) -> str:
    """
    Ham prospektüs metninden kullanıcı dostu özet oluşturur.
    Gereksiz bilgileri (renk, koku, ambalaj) filtreler.
    """
    if not raw_text:
        return "Özet bilgi bulunamadı."
    
    # Filtrelenecek gereksiz ifadeler
    noise_patterns = [
        r'[^.]*(?:pembe|beyaz|sarı|kırmızı|mavi|yeşil)\s*renkli[^.]*\.',
        r'[^.]*(?:aromatik|hoş|karakteristik)\s*kokulu[^.]*\.',
        r'[^.]*ambalaj(?:lan|lar)[^.]*\.',
        r'[^.]*(?:blister|şerit|kutu|ambalaj)\s*içerisinde[^.]*\.',
        r'[^.]*polipropilen[^.]*\.',
        r'\d+\s*(?:tablet|kapsül|ampul)lik\s*ambalaj[^.]*\.',
    ]
    
    cleaned = raw_text
    for pattern in noise_patterns:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
    
    # Çoklu boşlukları temizle
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    # Ana bilgileri çıkar
    ne_ise_yarar = ""
    kullanim_alani = ""
    etkin_madde = active_ingredient or ""
    
    # "Ne işe yarar" - ağrı kesici, ateş düşürücü vs.
    effect_match = re.search(
        r'(ağrı\s*kesici|ateş\s*düşürücü|kan\s*sulandırıcı|antibiyotik|antienflamatuar|'
        r'pıhtılaşmayı\s*önle[yr]|trombosit[^,\.]*önle[yr]|yangı\s*giderici|'
        r'alerji[^,\.]*|tansiyon[^,\.]*|şeker[^,\.]*düşür[^,\.]*)',
        cleaned, re.IGNORECASE
    )
    
    if effect_match:
        # Eşleşen terimi kullanıcı dostu hale getir
        effect = effect_match.group(1).lower()
        effect_map = {
            'trombosit': 'Kanın pıhtılaşmasını önler',
            'pıhtılaşmayı önle': 'Kanın pıhtılaşmasını önler',
            'ağrı kesici': 'Ağrı kesici',
            'ateş düşürücü': 'Ateş düşürücü', 
            'yangı giderici': 'İltihap giderici',
            'antienflamatuar': 'İltihap giderici',
            'kan sulandırıcı': 'Kan sulandırıcı',
        }
        for key, value in effect_map.items():
            if key in effect:
                ne_ise_yarar = value
                break
        if not ne_ise_yarar:
            ne_ise_yarar = effect.capitalize()
    
    # Kullanım alanı - hangi durumlarda kullanılır
    usage_patterns = [
        r'(baş\s*ağrısı|diş\s*ağrısı|kas\s*ağrısı|eklem\s*ağrısı|grip|soğuk\s*algınlığı|'
        r'ateş(?:li\s*hastalık)?|romatizma|artrit|anjin|kalp\s*krizi|inme|migren)',
    ]
    
    usages = []
    for pattern in usage_patterns:
        matches = re.findall(pattern, cleaned, re.IGNORECASE)
        usages.extend([m.strip().capitalize() for m in matches[:3]])  # Max 3
    
    if usages:
        kullanim_alani = ', '.join(list(dict.fromkeys(usages)))  # Unique
    
    # Yapılandırılmış özet oluştur
    summary_parts = []
    
    if ne_ise_yarar:
        summary_parts.append(f"<strong>💊 Ne İşe Yarar:</strong> {ne_ise_yarar}")
    
    if kullanim_alani:
        summary_parts.append(f"<strong>🎯 Kullanım Alanı:</strong> {kullanim_alani}")
    
    if etkin_madde:
        # Etkin maddeyi kısalt
        etkin_short = etkin_madde[:100] + "..." if len(etkin_madde) > 100 else etkin_madde
        summary_parts.append(f"<strong>🧪 Etkin Madde:</strong> {etkin_short}")
    
    # Eğer yapılandırılmış bilgi çıkaramadıysak, ham metni kullan
    if not summary_parts:
        # İlk 2-3 anlamlı cümleyi al
        sentences = re.split(r'[.!?]+', cleaned)
        meaningful = [s.strip() for s in sentences if len(s.strip()) > 20][:3]
        return ' '.join(meaningful) + '.' if meaningful else cleaned[:400]
    
    return '<br>'.join(summary_parts)

def format_traffic_lights(text: str) -> str:
    """Metindeki önemli uyarıları renklendirir."""
    if not text:
        return ""
    
    # Kritik Uyarılar (Kırmızı)
    critical_keywords = ["KULLANMAYINIZ", "Ciddi yan etkiler", "Acil tıbbi müdahale", "DERHAL", "Derhal"]
    for kw in critical_keywords:
        text = text.replace(kw, f'<span class="warning-critical">{kw}</span>')
        
    # Dikkat Uyarıları (Sarı)
    caution_keywords = ["DİKKATLİ KULLANINIZ", "Doktorunuza danışınız", "Eczacınıza danışınız", "Önlem alınız", "Dikkatli olunuz"]
    for kw in caution_keywords:
        text = text.replace(kw, f'<span class="warning-caution">{kw}</span>')
        
    # Bilgi (Mavi)
    info_keywords = ["Nasıl kullanılır", "Saklanması", "Özet Bilgi"]
    for kw in info_keywords:
        text = text.replace(kw, f'<span class="warning-info">{kw}</span>')
        
    return text

def format_side_effects(text: str) -> str:
    """Yan etkileri sıklık derecesine göre gruplar."""
    # Sıklık başlıkları (Sıralama önemli: En uzundan en kısaya)
    # (Başlık, CSS Class, Emoji)
    headers = [
        ("Çok yaygın", "critical", "🔴"),
        ("Yaygın olmayan", "caution", "🟡"),
        ("Yaygın", "warning", "🟠"),
        ("Çok seyrek", "info", "⚪"),
        ("Seyrek", "info", "🔵"),
        ("Bilinmiyor", "unknown", "❓")
    ]
    
    for header, severity, emoji in headers:
        # Regex: Satır başı, yeni satır veya noktalama sonrası gelen başlıklar
        # Case insensitive, opsiyonel iki nokta üst üste
        pattern = f"(?i)(?:^|\\n|\\.|<br>)\\s*({header}\\s*:?)"
        
        # Başlığı div içine al
        replacement = f'<div class="frequency-header {severity}">{emoji} \\1</div>'
        text = re.sub(pattern, replacement, text)
        
    return text

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_drug_summary(drug_id: int):
    """İlacın özetini (Bölüm 1) ve mevcut diğer bölümlerini getirir."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # İlaç bilgilerini al
    cursor.execute("SELECT *, active_substance as active_ingredient FROM ilaclar WHERE id = ?", (drug_id,))
    drug = cursor.fetchone()
    
    # Bölüm 1'i (Nedir?) al - Özet için
    cursor.execute("SELECT content FROM drug_sections WHERE drug_id = ? AND section_number = 1", (drug_id,))
    summary_row = cursor.fetchone()
    raw_text = summary_row['content'] if summary_row and summary_row['content'] else ""
    
    # Akıllı özet oluştur
    summary_text = create_smart_summary(raw_text, drug['active_ingredient'])
    
    # Mevcut bölümleri listele (Butonlar için)
    cursor.execute("SELECT section_number, section_title FROM drug_sections WHERE drug_id = ? AND content IS NOT NULL AND length(content) > 10 ORDER BY section_number", (drug_id,))
    sections = cursor.fetchall()
    
    section_list = [{"id": row['section_number'], "title": row['section_title']} for row in sections]
    
    conn.close()
    
    return {
        "type": "detail",
        "drug_name": drug['drug_name'],
        "active_ingredient": drug['active_ingredient'],
        "summary": summary_text,
        "sections": section_list,
        "drug_id": drug['id']
    }

def get_section_content(drug_id: int, section_number: int):
    """Belirli bir bölümün tam içeriğini getirir."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT section_title, content FROM drug_sections WHERE drug_id = ? AND section_number = ?", (drug_id, section_number))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        content = clean_text(row['content'])
        
        # Bölüm 4 (Yan Etkiler) için özel formatlama
        if section_number == 4:
            content = format_side_effects(content)
            
        content = format_traffic_lights(content)
        
        # Güvenlik Notu Ekle
        security_note = "\n\n<br><hr><small>⚠️ Bu bilgiler TİTCK onaylı resmi prospektüslerden alınmıştır. Kesin karar için doktorunuza danışınız.</small>"
        
        return {
            "type": "section_content",
            "title": row['section_title'],
            "content": content + security_note
        }
    return {"type": "error", "message": "İçerik bulunamadı."}

# --- Lifespan ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Sistem başlatılıyor...")

    # Legacy /search vektör yolu için embedding function
    try:
        if app_state["embedding_function"] is None:
            logger.info(f"🧠 Embedding modeli yükleniyor: {MODEL_NAME}")
            app_state["embedding_function"] = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=MODEL_NAME)
    except Exception as emb_error:
        app_state["embedding_function"] = None
        logger.warning(f"⚠️ Legacy embedding function yüklenemedi: {emb_error}")

    # ChromaDB bağlantısı (embedding conflict toleranslı)
    try:
        if os.path.exists(CHROMA_DB_PATH):
            client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))
            try:
                app_state["collection"] = client.get_collection(name=COLLECTION_NAME, embedding_function=app_state["embedding_function"])
                # Warm-up (text query)
                app_state["collection"].query(query_texts=["test"], n_results=1)
            except Exception as conflict_error:
                logger.warning(f"⚠️ Embedding conflict, fallback uygulanıyor: {conflict_error}")
                app_state["collection"] = client.get_collection(name=COLLECTION_NAME)
                # Legacy text query yerine query_embeddings kullanılacak
                app_state["embedding_function"] = None
            logger.info("✅ ChromaDB bağlandı.")
        else:
            app_state["collection"] = None
            logger.warning("⚠️ ChromaDB bulunamadı! Sadece isim araması çalışacak.")
    except Exception as chroma_error:
        app_state["collection"] = None
        logger.error(f"❌ ChromaDB başlatılamadı: {chroma_error}")

    # Yeni RAG pipeline (Chroma + SQLite + Gemini)
    try:
        app_state["rag_pipeline"] = ProspektRAG()
        logger.info("✅ RAG pipeline hazır.")
    except Exception as rag_error:
        app_state["rag_pipeline"] = None
        logger.error(f"❌ RAG pipeline başlatılamadı: {rag_error}")
    
    yield
    app_state["collection"] = None

# --- API ---
app = FastAPI(title="ProspektAsistan Hybrid API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class SearchRequest(BaseModel):
    query: str
    drug_id: int = None
    section_number: int = None


class RagAskRequest(BaseModel):
    query: str
    top_k: int = 8


@app.post("/rag/ask")
async def rag_ask(request: RagAskRequest):
    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Sorgu boş olamaz.")

    # 1. Seçim Ekranı Mantığı (Disambiguation)
    # Eğer sorgu kısaysa (sadece ilaç ismi yazılmış olma ihtimali) ve birden fazla ilaçla eşleşiyorsa liste döneriz
    if len(query.split()) <= 3:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        query_lower = turkish_lower(query)
        query_upper = turkish_upper(query)
        query_i_variants = [
            query,
            query.replace('i', 'İ').replace('I', 'ı'),
            query.replace('İ', 'i').replace('ı', 'I'),
            query_lower,
            query_upper
        ]
        
        placeholders = ' OR '.join(['drug_name LIKE ?' for _ in query_i_variants])
        cursor.execute(f"""
            SELECT DISTINCT drug_name, MIN(id) as id, active_substance as active_ingredient FROM ilaclar
            WHERE ({placeholders})
              AND length(drug_name) > 5
            GROUP BY drug_name
            ORDER BY drug_name
            LIMIT 10
        """, tuple(f'%{v}%' for v in query_i_variants))
        sql_results = cursor.fetchall()
        conn.close()

        # Eğer sorgu direkt birden fazla spesifik formu (100 mg, 500 mg vb) getiriyorsa RAG yapmadan seçim sun (Çorba olmayı engeller)
        if sql_results and len(sql_results) > 1:
            items = []
            for r in sql_results:
                parsed = parse_drug_name(r["drug_name"])
                items.append({
                    "id": r["id"],
                    "name": r["drug_name"],
                    "brand": parsed['brand'],
                    "dosage": parsed['dosage'],
                    "form": parsed['form'],
                    "active_ingredient": r["active_ingredient"]
                })
            return {"type": "list", "items": items}

    rag = app_state.get("rag_pipeline")
    if rag is None:
        raise HTTPException(status_code=503, detail="RAG pipeline hazır değil.")

    try:
        top_k = max(3, min(int(request.top_k), 12))
        result = rag.ask(query=query, top_k=top_k)
        return {
            "type": "rag",
            "query": result.get("query", query),
            "answer": result.get("answer", ""),
            "sources": result.get("sources", []),
            "retrieved_count": result.get("retrieved_count", 0),
        }
    except Exception as e:
        logger.exception("RAG cevap üretim hatası")
        raise HTTPException(status_code=500, detail=f"RAG hatası: {e}")

@app.post("/search")
async def search(request: SearchRequest):
    query = request.query.strip()
    
    # 1. Eğer spesifik bir bölüm isteniyorsa (Butona tıklandıysa)
    if request.drug_id and request.section_number:
        return get_section_content(request.drug_id, request.section_number)

    # 2. SQL İsim Araması (Öncelikli) - Türkçe karakter desteği ile
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Türkçe karakter varyasyonlarını oluştur (i/İ ve ı/I problemi için)
    query_lower = turkish_lower(query)
    query_upper = turkish_upper(query)
    
    # i -> İ ve ı -> I dönüşümlü varyasyonlar
    query_i_variants = [
        query,
        query.replace('i', 'İ').replace('I', 'ı'),
        query.replace('İ', 'i').replace('ı', 'I'),
        query_lower,
        query_upper
    ]
    
    # Birden fazla varyasyonla ara - DISTINCT ile duplicate'leri engelle
    # Sadece geçerli ilaç isimlerini al (en az 5 karakter, sayı veya harf ile başlayan)
    placeholders = ' OR '.join(['drug_name LIKE ?' for _ in query_i_variants])
    cursor.execute(f"""
        SELECT DISTINCT drug_name, MIN(id) as id, active_substance as active_ingredient FROM ilaclar 
        WHERE ({placeholders})
          AND length(drug_name) > 5
          AND (substr(drug_name, 1, 1) BETWEEN 'A' AND 'Z' 
               OR substr(drug_name, 1, 1) BETWEEN 'a' AND 'z'
               OR substr(drug_name, 1, 1) BETWEEN '0' AND '9'
               OR substr(drug_name, 1, 1) = '%')
        GROUP BY drug_name
        ORDER BY drug_name
        LIMIT 10
    """, tuple(f'%{v}%' for v in query_i_variants))
    sql_results = cursor.fetchall()
    conn.close()
    
    # Eğer isimle eşleşen ilaçlar varsa
    if sql_results:
        # Tek sonuç varsa direkt detayına git
        if len(sql_results) == 1:
            return get_drug_summary(sql_results[0]['id'])
        
        # Çok sonuç varsa listele (parse edilmiş isimlerle) - duplicate isimleri filtrele
        items = []
        seen_names = set()
        for row in sql_results:
            drug_name = row['drug_name']
            # Aynı isimli ilaçları tekrar ekleme
            if drug_name in seen_names:
                continue
            seen_names.add(drug_name)
            
            parsed = parse_drug_name(drug_name)
            items.append({
                "id": row['id'],
                "name": drug_name,  # Orijinal isim (arama için)
                "brand": parsed['brand'],
                "dosage": parsed['dosage'],
                "form": parsed['form'],
                "active_ingredient": row['active_ingredient']
            })
        
        # Filtreleme sonrası tek sonuç kaldıysa direkt detaya git
        if len(items) == 1:
            return get_drug_summary(items[0]['id'])
        
        return {
            "type": "list",
            "message": f"'{query}' ile eşleşen ilaçlar:",
            "items": items
        }

    # 3. Vektör Araması (Kavramsal - Sadece isim bulunamadıysa)
    collection = app_state["collection"]
    if collection and len(query) > 3:
        try:
            if app_state.get("embedding_function") is not None:
                results = collection.query(
                    query_texts=[query],
                    n_results=3,
                    include=["metadatas", "distances"]
                )
            elif app_state.get("rag_pipeline") is not None:
                query_emb = app_state["rag_pipeline"].embed_query(query)
                results = collection.query(
                    query_embeddings=query_emb.tolist(),
                    n_results=3,
                    include=["metadatas", "distances"]
                )
            else:
                results = None
        except Exception as vector_error:
            logger.warning(f"⚠️ Legacy vektör arama atlandı: {vector_error}")
            results = None

        if not results or not results.get("distances") or not results["distances"][0]:
            return {"type": "error", "message": "Aradığınız kriterlere uygun bir ilaç bulamadım."}
        
        # Eşik Değeri (Threshold) Kontrolü
        # Distance ne kadar küçükse o kadar benzerdir. 0.5'in üstü genelde alakasızdır.
        best_distance = results['distances'][0][0]
        if best_distance > 0.55: 
            return {
                "type": "error", 
                "message": "Bu konuyla ilgili net bir ilaç bilgisi bulamadım. Lütfen bir ilaç ismi yazmayı deneyin."
            }
            
        # En iyi eşleşen ilacı bul
        best_match_meta = results['metadatas'][0][0]
        drug_name = best_match_meta.get('drug_name')
        
        # Bu ilacı SQL'den bulup detayını getir
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM drugs WHERE drug_name = ?", (drug_name,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return get_drug_summary(row['id'])

    return {"type": "error", "message": "Aradığınız kriterlere uygun bir ilaç bulamadım."}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
