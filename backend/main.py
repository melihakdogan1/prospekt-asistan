"""
ProspektAsistan - AI-Powered Drug Information Assistant Backend

Bu backend, ilaç prospektüsleri veritabanını kullanarak kullanıcı sorularına
akıllı cevaplar veren bir RAG (Retrieval-Augmented Generation) sistemi sağlar.

Özellikler:
- ChromaDB ile vektör arama
- FastAPI ile REST API
- CORS desteği ile frontend entegrasyonu
- 6,425+ ilaç prospektüsü verisi
"""
import os
import logging
import re
from pathlib import Path
import chromadb
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# Logging yapılandırması
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
collection = None

# Bazı marka isimlerinin etkin madde eşleştirmeleri
BRAND_SYNONYMS = {
    "parol": ["parasetamol", "paracetamol"],
}

# --- API Modelleri ---
class SearchRequest(BaseModel):
    """Arama isteği modeli"""
    query: str

class SearchResponse(BaseModel):
    """Arama yanıtı modeli"""
    llm_answer: str

# --- FastAPI Uygulaması ---
app = FastAPI(
    title=" ProspektAsistan API", 
    version="1.0.0",
    description="AI-powered drug information assistant API"
)

# CORS ayarları - Frontend domainleri için
origins = [
    "https://taupe-quokka-9701fc.netlify.app",  # Netlify production
    "http://localhost:8000",  # Development frontend
    "http://127.0.0.1:4040",  # Local development
    "*"  # Demo için tüm domainlere izin
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Demo için tüm domainlere izin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup_event():
    """Uygulama başlangıcında veritabanı bağlantısını kurar"""
    global collection
    try:
        logger.info(" ChromaDB veritabanına bağlanılıyor...")
        backend_dir = Path(__file__).resolve().parent
        db_path = str(backend_dir.parent / "data" / "veritabani_optimized")
        
        if not os.path.exists(db_path):
            logger.critical(f" KRİTİK HATA: Veritabanı '{db_path}' yolunda bulunamadı!")
            logger.critical(f"   Lütfen veritabanını şu yola yerleştirin: {db_path}")
            raise FileNotFoundError(f"Veritabanı bulunamadı: {db_path}")
            
        client = chromadb.PersistentClient(path=db_path)
        collection = client.get_collection("ilac_prospektusleri")
        logger.info(f" Veritabanı başarıyla yüklendi: {collection.count():,} döküman")
        
    except Exception as e:
        logger.error(f" Veritabanı başlatma hatası: {e}")
        raise

@app.get("/health")
async def health_check():
    """API sağlık durumunu kontrol eder"""
    return {
        "status": "healthy",
        "database_status": "connected" if collection else "disconnected",
        "total_documents": collection.count() if collection else 0
    }

@app.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    """Kullanıcı sorgusuna göre ilaç bilgisi arar ve prospektüs bilgisi döndürür"""
    if not collection:
        raise HTTPException(status_code=503, detail="Veritabanı hazır değil. Lütfen sunucuyu yeniden başlatın.")
    
    logger.info(f"🔍 Arama yapılıyor: '{request.query}'")
    
    try:
        # 10 sonuç al, en iyilerini seçeceğiz
        results = collection.query(query_texts=[request.query], n_results=10)
        
        if not results['documents'][0]:
            return SearchResponse(
                llm_answer="❌ Üzgünüm, bu konuda prospektüs veritabanımızda bilgi bulamadım.\n\n"
                          "⚕️ **Lütfen doktorunuza veya eczacınıza danışın.**"
            )
        
        # En alakalı sonuçları birleştir
        query_tokens = [token for token in re.findall(r"\w+", request.query.lower()) if token]
        expanded_tokens = set(query_tokens)
        for token in query_tokens:
            for synonym in BRAND_SYNONYMS.get(token, []):
                for piece in re.findall(r"\w+", synonym.lower()):
                    expanded_tokens.add(piece)
        expanded_tokens = [token for token in expanded_tokens if token]

        distances = results.get('distances', [[]])[0] if 'distances' in results else []

        useless_indicators = [
            'laktoz (inek sütü kaynaklı)',
            'mikropellet içerir',
            'susuz sitrik asit',
            'jelatin (sığır kaynaklı)',
            'mısır nişastası'
        ]
        skip_keywords = [
            'tarihinde onaylanmıştır',
            'Serbest Bölge',
            'parsel',
            'KULLANMA TALİMATI',
            'Bu ilacı kullanmaya başlamadan önce',
            'çünkü sizin için önemli bilgiler içermektedir',
            'Bu kullanma talimatını saklayınız',
            'Daha sonra tekrar okumaya ihtiyaç duyabilirsiniz',
            'Ağızdan alınır',
            'Ağızdan yutarak alınır',
            'Etkin madde:',
            'Yardımcı maddeler:',
            'mikropellet içerir',
            'içeren poşet'
        ]

        primary_candidates = []
        fallback_candidates = []
        seen_entries = set()

        for idx, (doc, metadata, distance) in enumerate(zip(results['documents'][0], results['metadatas'][0], distances if distances else [0] * len(results['documents'][0])), 1):
            if distances and distance > 0.8:
                continue

            source_file = metadata.get('source', 'Bilinmeyen')
            drug_name = source_file.replace('_KT', '').replace('_KUB', '').strip()

            clean_text = doc.strip().replace('_', '')
            drug_name_lower = drug_name.lower()
            clean_text_lower = clean_text.lower()

            if expanded_tokens and len(query_tokens) <= 3:
                if not any(token in drug_name_lower or token in clean_text_lower for token in expanded_tokens):
                    continue

            if any(indicator in clean_text_lower for indicator in useless_indicators):
                if len(clean_text) < 300 or clean_text.count(',') > 10:
                    continue

            lines = clean_text.split('\n')
            formatted_lines = []

            for line in lines:
                line = line.strip()
                if any(keyword in line for keyword in skip_keywords):
                    continue
                if line and len(line) > 10:
                    if line.startswith('•') or line.startswith('-') or line.startswith('*'):
                        formatted_lines.append(f"\n• {line.lstrip('•-* ')}")
                    else:
                        if formatted_lines and line[0].islower() and not line.startswith('ş') and not line.startswith('ç'):
                            formatted_lines[-1] += ' ' + line
                        else:
                            formatted_lines.append(f"\n{line}")

            formatted_text = ''.join(formatted_lines).strip()

            if len(formatted_text) < 50:
                continue

            formatted_text = formatted_text.strip('_-.,;: \n')

            source_type = "KT" if "_KT" in source_file else "KUB"
            entry_key = (drug_name_lower, source_type, formatted_text[:80].lower())
            if entry_key in seen_entries:
                continue
            seen_entries.add(entry_key)

            entry = {
                "drug_name": drug_name,
                "formatted_text": formatted_text,
                "source_type": source_type,
                "source_label": f"📄 {drug_name} ({source_type})"
            }

            if source_type == "KUB":
                fallback_candidates.append(entry)
            else:
                primary_candidates.append(entry)

            if len(primary_candidates) >= 3 and len(fallback_candidates) >= 3:
                break

        # Sadece KT kayıtlarını tercih et (KUB'lar için ayrı endpoint olacak)
        selected_entries = primary_candidates if primary_candidates else []

        if not selected_entries:
            return SearchResponse(
                llm_answer="❌ Üzgünüm, bu ilaç için prospektüs (KT) bilgisi bulamadım.\n\n"
                          "⚕️ **Lütfen eczacınıza veya doktorunuza danışın.**"
            )

        # TEK ilaç döndür - en iyi eşleşmeyi seç
        best_entry = selected_entries[0]
        
        # Eğer aynı ilaçtan farklı formlar varsa (tablet/şurup/saşe) bilgilendir
        same_drug_variants = [e for e in selected_entries if e['drug_name'].split('_')[0] == best_entry['drug_name'].split('_')[0]]
        
        response_parts = []
        response_parts.append("⚕️ **TIBBİ UYARI:** Bu bilgiler resmi prospektüsten alınmıştır ve tıbbi tavsiye yerine geçmez. Lütfen mutlaka doktorunuza danışın.\n")
        
        # Eğer birden fazla form varsa kullanıcıya sor
        if len(same_drug_variants) > 1:
            response_parts.append(f"\n💊 **{best_entry['drug_name'].split('_')[0].upper()}** için birden fazla form bulundu:\n")
            for idx, variant in enumerate(same_drug_variants[:3], 1):
                response_parts.append(f"  {idx}. {variant['drug_name']}")
            response_parts.append("\n📌 **Hangi formu öğrenmek istersiniz?** Lütfen tam adını yazın.\n")
        else:
            response_parts.append(f"\n💊 **{best_entry['drug_name'].upper()}**\n")
            response_parts.append(f"{'─'*60}\n")
            
            # İçeriği düzenle - sadece ilk 1000 karakter ve sade Türkçe
            content = best_entry['formatted_text'][:1000].strip()
            if not content[0].isupper():
                # Eksik başlangıç varsa düzelt
                content = "..." + content
            
            response_parts.append(content)
            if len(best_entry['formatted_text']) > 1000:
                response_parts.append("...\n")
            
            response_parts.append(f"\n{'─'*60}\n")
            response_parts.append(f"\n📄 Kaynak: {best_entry['drug_name']} Kullanma Talimatı")

        response_parts.append("\n\n⚠️ **ÖNEMLİ:** İlaç kullanımı hakkında mutlaka hekiminize danışın.")

        answer = "\n".join(response_parts)
        return SearchResponse(llm_answer=answer)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Arama sırasında hata: {e}")
        raise HTTPException(status_code=500, detail=f"Arama sırasında bir hata oluştu: {str(e)}")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)