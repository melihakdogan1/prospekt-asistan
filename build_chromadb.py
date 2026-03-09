"""
ADIM 2: ChromaDB Vektör Veritabanı Oluşturma
=============================================
Model: intfloat/multilingual-e5-large (Türkçe'de güçlü)
Strateji: Bölüm içeriklerini ~400 token chunk'lara ayır, overlap ile embed et.
"""
import json
import os
import time
import sqlite3
import chromadb
from sentence_transformers import SentenceTransformer

# ================================================================
# Ayarlar
# ================================================================
MODEL_NAME = "intfloat/multilingual-e5-large"
CHROMA_PATH = "data/chroma_prospekt"
SQLITE_PATH = "data/prospekt_metadata.db"
CHUNK_SIZE = 1500      # Karakter (yaklaşık 350-400 token Türkçe, modelin 512 token sınırına uygun)
CHUNK_OVERLAP = 200    # Karakter overlap
BATCH_SIZE = 64        # Embedding batch boyutu

# ================================================================
# Model yükle
# ================================================================
print(f"Model yükleniyor: {MODEL_NAME}")
print("  (İlk çalıştırmada ~2.2 GB indirilecek, sabırlı olun)")
t0 = time.time()
model = SentenceTransformer(MODEL_NAME)
print(f"  Model hazır ({time.time()-t0:.1f}s)")

# ================================================================
# ChromaDB bağlantısı
# ================================================================
if os.path.exists(CHROMA_PATH):
    import shutil
    shutil.rmtree(CHROMA_PATH)

client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = client.create_collection(
    name="prospektus",
    metadata={"hnsw:space": "cosine"}
)

# ================================================================
# Yardımcı fonksiyonlar
# ================================================================
def chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Metni overlap ile chunk'lara ayır."""
    if not text or len(text.strip()) < 50:
        return []
    
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        
        # Cümle sınırında kes (son nokta veya satır sonu)
        if end < len(text):
            last_period = chunk.rfind('.')
            last_newline = chunk.rfind('\n')
            break_at = max(last_period, last_newline)
            if break_at > chunk_size * 0.3:
                chunk = chunk[:break_at + 1]
                end = start + break_at + 1
        
        chunk = chunk.strip()
        if len(chunk) > 30:
            chunks.append(chunk)
        
        start = end - overlap
        if start <= (end - chunk_size):
            start = end
    
    return chunks

def encode_texts(texts):
    """multilingual-e5 'passage: ' prefix'i istiyor."""
    prefixed = [f"passage: {t}" for t in texts]
    return model.encode(prefixed, show_progress_bar=False, normalize_embeddings=True)

# ================================================================
# SQLite'dan veri oku
# ================================================================
print("\nSQLite'dan veri okunuyor...")
conn = sqlite3.connect(SQLITE_PATH)
cur = conn.cursor()

cur.execute("""
SELECT i.id, i.file_name, i.drug_name, i.active_substance, i.usage_route,
       b.bolum_no, b.section_title, b.content
FROM ilaclar i
JOIN bolumler b ON i.id = b.ilac_id
WHERE b.content_length > 50
ORDER BY i.id, b.bolum_no
""")

rows = cur.fetchall()
conn.close()
print(f"  {len(rows)} bölüm okundu (>50 char)")

# ================================================================
# Chunk'la ve embed et
# ================================================================
BOLUM_ADLARI = {
    1: "İlacın Kullanım Bilgileri",
    2: "Uyarılar ve Yan Etkiler",
    3: "Kullanım Şekli ve Dozu",
    4: "Saklama Koşulları ve Ambalaj",
    5: "Ruhsat Sahibi Bilgileri",
}

all_ids = []
all_texts = []
all_embeddings = []
all_metadatas = []

print("\nChunk'lama başlıyor...")
t0 = time.time()
total_chunks = 0

for row in rows:
    ilac_id, file_name, drug_name, active_substance, usage_route, bolum_no, section_title, content = row
    
    chunks = chunk_text(content)
    if not chunks:
        continue
    
    # Bağlam prefix'i: retrieval kalitesini artırır
    context_prefix = f"{drug_name}"
    if active_substance:
        context_prefix += f" ({active_substance})"
    context_prefix += f" - {BOLUM_ADLARI.get(bolum_no, '')}:\n"
    
    for i, chunk in enumerate(chunks):
        doc_id = f"{file_name}_B{bolum_no}_C{i}"
        enriched_chunk = context_prefix + chunk
        
        meta = {
            "ilac_id": ilac_id,
            "file_name": file_name,
            "drug_name": drug_name,
            "active_substance": active_substance or "",
            "usage_route": usage_route or "",
            "bolum_no": bolum_no,
            "bolum_adi": BOLUM_ADLARI.get(bolum_no, ""),
            "section_title": section_title or "",
            "chunk_index": i,
            "total_chunks": len(chunks),
        }
        
        all_ids.append(doc_id)
        all_texts.append(enriched_chunk)
        all_metadatas.append(meta)
        total_chunks += 1

print(f"  {total_chunks} chunk oluşturuldu ({time.time()-t0:.1f}s)")
print(f"  Ort chunk/bölüm: {total_chunks/len(rows):.1f}")

# ================================================================
# Batch embedding ve ChromaDB'ye yazma
# ================================================================
print(f"\nEmbedding başlıyor ({total_chunks} chunk, batch={BATCH_SIZE})...")
t0 = time.time()

for batch_start in range(0, total_chunks, BATCH_SIZE):
    batch_end = min(batch_start + BATCH_SIZE, total_chunks)
    batch_texts = all_texts[batch_start:batch_end]
    batch_ids = all_ids[batch_start:batch_end]
    batch_metas = all_metadatas[batch_start:batch_end]
    
    embeddings = encode_texts(batch_texts)
    
    collection.upsert(
        ids=batch_ids,
        documents=batch_texts,
        embeddings=embeddings.tolist(),
        metadatas=batch_metas,
    )
    
    done = batch_end
    elapsed = time.time() - t0
    speed = done / elapsed if elapsed > 0 else 0
    eta = (total_chunks - done) / speed if speed > 0 else 0
    
    if done % (BATCH_SIZE * 10) == 0 or done == total_chunks:
        print(f"  {done}/{total_chunks} ({done*100/total_chunks:.1f}%) - {speed:.0f} chunk/s - ETA {eta:.0f}s")

elapsed_total = time.time() - t0

# ================================================================
# Doğrulama
# ================================================================
print(f"\n{'='*60}")
print("  DOGRULAMA")
print(f"{'='*60}")

count = collection.count()

# ChromaDB dizin boyutu
chroma_size = 0
for dirpath, dirnames, filenames in os.walk(CHROMA_PATH):
    for f in filenames:
        chroma_size += os.path.getsize(os.path.join(dirpath, f))

print(f"""
  ChromaDB : {CHROMA_PATH}
  Boyut    : {chroma_size / (1024*1024):.1f} MB
  Model    : {MODEL_NAME}
  Chunk'lar: {count}
  Süre     : {elapsed_total:.0f}s
""")

# Test sorgusu
print("  Test: 'aspirin yan etkileri nelerdir?' sorguluyor...")
query_embedding = model.encode(["query: aspirin yan etkileri nelerdir?"], normalize_embeddings=True)
results = collection.query(
    query_embeddings=query_embedding.tolist(),
    n_results=3,
)

for i, (doc, meta, dist) in enumerate(zip(results["documents"][0], results["metadatas"][0], results["distances"][0])):
    print(f"\n  #{i+1} (skor: {1-dist:.3f}) - {meta['drug_name']} B{meta['bolum_no']}")
    print(f"     {doc[:120]}...")

print(f"\n  Test: 'parasetamol hamilelikte kullanılır mı?' sorguluyor...")
query_embedding = model.encode(["query: parasetamol hamilelikte kullanılır mı?"], normalize_embeddings=True)
results = collection.query(
    query_embeddings=query_embedding.tolist(),
    n_results=3,
)

for i, (doc, meta, dist) in enumerate(zip(results["documents"][0], results["metadatas"][0], results["distances"][0])):
    print(f"\n  #{i+1} (skor: {1-dist:.3f}) - {meta['drug_name']} B{meta['bolum_no']}")
    print(f"     {doc[:120]}...")

print(f"\n  ChromaDB vektör veritabanı hazır!")
