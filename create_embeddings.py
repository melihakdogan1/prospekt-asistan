import sqlite3
import chromadb
from chromadb.utils import embedding_functions
import os
from tqdm import tqdm

# Ayarlar
SQLITE_DB = "ilac_prospektus.db"
CHROMA_DB_PATH = "data/chroma_db"
COLLECTION_NAME = "ilac_prospektusleri"

def create_embeddings():
    print(f"1. SQLite veritabanına bağlanılıyor: {SQLITE_DB}")
    if not os.path.exists(SQLITE_DB):
        print("HATA: SQLite veritabanı bulunamadı!")
        return

    conn = sqlite3.connect(SQLITE_DB)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Verileri çek
    print("2. Veriler çekiliyor...")
    cursor.execute("""
        SELECT 
            ds.id as section_id,
            d.name as drug_name,
            d.active_ingredient,
            ds.section_title,
            ds.content
        FROM drug_sections ds
        JOIN drugs d ON ds.drug_id = d.id
        WHERE ds.content IS NOT NULL AND ds.content != ''
    """)
    rows = cursor.fetchall()
    print(f"   Toplam {len(rows)} bölüm (paragraf) işlenecek.")

    # ChromaDB Hazırlığı
    print(f"3. ChromaDB hazırlanıyor: {CHROMA_DB_PATH}")
    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    
    # Varsa eski koleksiyonu silip yeniden oluşturuyoruz
    try:
        client.delete_collection(COLLECTION_NAME)
        print("   Eski koleksiyon temizlendi.")
    except Exception:
        pass # Koleksiyon yoksa sorun değil

    # Embedding fonksiyonu (Türkçe için çok dilli model)
    # Öneri üzerine: paraphrase-multilingual-MiniLM-L12-v2
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="paraphrase-multilingual-MiniLM-L12-v2")
    
    collection = client.create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"}
    )

    # Batch işlemi
    BATCH_SIZE = 100
    ids = []
    documents = []
    metadatas = []

    print("4. Embeddingler oluşturuluyor ve kaydediliyor...")
    for row in tqdm(rows):
        # Metin birleştirme: İlaç adı + Başlık + İçerik
        # Bu sayede "Parol yan etkileri" aramasında hem ilaç adı hem başlık eşleşir.
        combined_text = f"İlaç: {row['drug_name']}\nEtken Madde: {row['active_ingredient']}\nBölüm: {row['section_title']}\nİçerik: {row['content']}"
        
        ids.append(str(row['section_id']))
        documents.append(combined_text)
        metadatas.append({
            "drug_name": row['drug_name'],
            "section_title": row['section_title'],
            "active_ingredient": row['active_ingredient'] or "",
            "original_content": row['content'] # Orijinal içeriği metadata'da saklayalım
        })

        if len(ids) >= BATCH_SIZE:
            collection.add(ids=ids, documents=documents, metadatas=metadatas)
            ids = []
            documents = []
            metadatas = []

    # Kalanları ekle
    if ids:
        collection.add(ids=ids, documents=documents, metadatas=metadatas)

    print("\n✅ İşlem tamamlandı!")
    print(f"   Veritabanı konumu: {os.path.abspath(CHROMA_DB_PATH)}")
    conn.close()

if __name__ == "__main__":
    create_embeddings()
