"""
ADIM 1: SQLite Metadata Veritabanı Oluşturma
=============================================
Tüm JSON'ları tek bir SQLite veritabanına yaz.
Tablolar:
  - ilaclar: Ana tablo (her ilaç 1 satır)
  - bolumler: Bölüm içerikleri (her ilaç × 5 bölüm)
"""
import json
import sqlite3
import os

ANALIZ_DIR = "analiz_sonuclari"
DB_PATH = "data/prospekt_metadata.db"

def load_json(name):
    with open(os.path.join(ANALIZ_DIR, name), "r", encoding="utf-8") as f:
        return json.load(f)

# ================================================================
# Veritabanı oluştur
# ================================================================
print("SQLite veritabanı oluşturuluyor...")

# Eski varsa sil, temiz başla
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# Ana ilaç tablosu
cur.execute("""
CREATE TABLE ilaclar (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name TEXT UNIQUE NOT NULL,
    drug_name TEXT NOT NULL,
    usage_route TEXT DEFAULT '',
    active_substance TEXT DEFAULT '',
    excipients TEXT DEFAULT '',
    warning_text TEXT DEFAULT '',
    license_holder TEXT DEFAULT '',
    manufacturer TEXT DEFAULT '',
    healthcare_professional_info TEXT DEFAULT ''
)
""")

# Bölüm içerikleri tablosu
cur.execute("""
CREATE TABLE bolumler (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ilac_id INTEGER NOT NULL,
    bolum_no INTEGER NOT NULL,
    section_title TEXT DEFAULT '',
    content TEXT DEFAULT '',
    content_length INTEGER DEFAULT 0,
    FOREIGN KEY (ilac_id) REFERENCES ilaclar(id),
    UNIQUE(ilac_id, bolum_no)
)
""")

# Hatalı dosyalar tablosu
cur.execute("""
CREATE TABLE hatali_dosyalar (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name TEXT NOT NULL,
    reason TEXT DEFAULT '',
    text_length INTEGER DEFAULT 0
)
""")

# Indexler
cur.execute("CREATE INDEX idx_ilaclar_drug_name ON ilaclar(drug_name)")
cur.execute("CREATE INDEX idx_ilaclar_active_substance ON ilaclar(active_substance)")
cur.execute("CREATE INDEX idx_ilaclar_usage_route ON ilaclar(usage_route)")
cur.execute("CREATE INDEX idx_bolumler_ilac_id ON bolumler(ilac_id)")
cur.execute("CREATE INDEX idx_bolumler_bolum_no ON bolumler(bolum_no)")

# FTS (Full-Text Search) tablosu - hızlı metin araması için
cur.execute("""
CREATE VIRTUAL TABLE ilaclar_fts USING fts5(
    drug_name,
    active_substance,
    content='ilaclar',
    content_rowid='id'
)
""")

# ================================================================
# Veri yükle
# ================================================================
print("JSON'lar okunuyor...")
ozet = load_json("kt_ozet.json")
ek_bilgi = load_json("kt_ek_bilgi.json")
hatali = load_json("hatali_dosyalar.json")

ek_map = {item["file_name"]: item for item in ek_bilgi}

# İlaçları ekle
print(f"  {len(ozet)} ilaç ekleniyor...")
for item in ozet:
    fn = item["file_name"]
    ek = ek_map.get(fn, {})
    
    cur.execute("""
    INSERT INTO ilaclar (file_name, drug_name, usage_route, active_substance, 
                         excipients, warning_text, license_holder, manufacturer,
                         healthcare_professional_info)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        fn,
        item.get("drug_name", ""),
        item.get("usage_route", ""),
        item.get("active_substance", ""),
        item.get("excipients", ""),
        item.get("warning_text", ""),
        ek.get("license_holder", ""),
        ek.get("manufacturer", ""),
        ek.get("healthcare_professional_info", ""),
    ))

conn.commit()

# İlaç ID'lerini al
cur.execute("SELECT id, file_name FROM ilaclar")
ilac_ids = {fn: ilac_id for ilac_id, fn in cur.fetchall()}

# Bölümleri ekle
for bolum_no in range(1, 6):
    data = load_json(f"kt_bolum{bolum_no}.json")
    print(f"  Bölüm {bolum_no}: {len(data)} kayıt ekleniyor...")
    
    for item in data:
        fn = item["file_name"]
        ilac_id = ilac_ids.get(fn)
        if not ilac_id:
            continue
        
        content = item.get("content", "")
        cur.execute("""
        INSERT INTO bolumler (ilac_id, bolum_no, section_title, content, content_length)
        VALUES (?, ?, ?, ?, ?)
        """, (
            ilac_id,
            bolum_no,
            item.get("section_title", ""),
            content,
            len(content),
        ))

conn.commit()

# FTS tablosunu doldur
cur.execute("""
INSERT INTO ilaclar_fts (rowid, drug_name, active_substance)
SELECT id, drug_name, active_substance FROM ilaclar
""")

# Hatalı dosyaları ekle
print(f"  {len(hatali)} hatalı dosya ekleniyor...")
for item in hatali:
    cur.execute("""
    INSERT INTO hatali_dosyalar (file_name, reason, text_length)
    VALUES (?, ?, ?)
    """, (
        item.get("file") or item.get("file_name", ""),
        item.get("reason", ""),
        item.get("text_length", 0),
    ))

conn.commit()

# ================================================================
# Doğrulama
# ================================================================
print(f"\n{'='*60}")
print("  DOGRULAMA")
print(f"{'='*60}")

cur.execute("SELECT COUNT(*) FROM ilaclar")
ilac_count = cur.fetchone()[0]

cur.execute("SELECT COUNT(*) FROM bolumler")
bolum_count = cur.fetchone()[0]

cur.execute("SELECT COUNT(*) FROM hatali_dosyalar")
hatali_count = cur.fetchone()[0]

cur.execute("SELECT bolum_no, COUNT(*), AVG(content_length) FROM bolumler GROUP BY bolum_no")
bolum_stats = cur.fetchall()

db_size = os.path.getsize(DB_PATH) / (1024 * 1024)

print(f"""
  Veritabani: {DB_PATH}
  Boyut     : {db_size:.1f} MB
  
  ilaclar        : {ilac_count} kayit
  bolumler       : {bolum_count} kayit
  hatali_dosyalar: {hatali_count} kayit
""")

print("  Bolum istatistikleri:")
for bolum_no, count, avg_len in bolum_stats:
    print(f"    B{bolum_no}: {count} kayit, ort {avg_len:.0f} char")

# Test sorgusu
print(f"\n  Test sorgusu: 'aspirin' araniyor...")
cur.execute("SELECT drug_name, usage_route, active_substance FROM ilaclar WHERE drug_name LIKE '%aspirin%' COLLATE NOCASE LIMIT 5")
results = cur.fetchall()
for name, route, active in results:
    print(f"    {name} | {route[:40]} | {active[:50]}")

# FTS test
print(f"\n  FTS sorgusu: 'parasetamol' araniyor...")
cur.execute("SELECT drug_name, active_substance FROM ilaclar WHERE id IN (SELECT rowid FROM ilaclar_fts WHERE ilaclar_fts MATCH 'parasetamol') LIMIT 5")
results = cur.fetchall()
for name, active in results:
    print(f"    {name} | {active[:60]}")

conn.close()
print(f"\n  SQLite veritabani hazir!")
