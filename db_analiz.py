# filepath: c:\Users\Feyza\Desktop\PROSPEKT\PROSPEKT_ASİSTAN\db_analiz.py
import sqlite3

DB_PATH = "ilac_prospektus.db"

def analiz_yap():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print("=" * 60)
    print("📊 VERİTABANI ANALİZİ")
    print("=" * 60)
    
    # Tablo listesi
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tablolar = cursor.fetchall()
    print(f"\n📁 Tablolar: {[t[0] for t in tablolar]}")
    
    # drugs tablosu
    cursor.execute("SELECT COUNT(*) FROM drugs")
    ilac_sayisi = cursor.fetchone()[0]
    print(f"\n💊 Toplam ilaç sayısı: {ilac_sayisi}")
    
    # Örnek ilaçlar
    print("\n📋 İlk 5 ilaç:")
    cursor.execute("SELECT id, name, active_ingredient FROM drugs LIMIT 5")
    for row in cursor.fetchall():
        print(f"   ID:{row[0]} | {row[1]} | Etkin: {row[2][:50] if row[2] else 'YOK'}...")
    
    # drug_sections tablosu
    cursor.execute("SELECT COUNT(*) FROM drug_sections")
    bolum_sayisi = cursor.fetchone()[0]
    print(f"\n📑 Toplam bölüm sayısı: {bolum_sayisi}")
    
    # Bölüm dağılımı
    print("\n📊 Bölüm dağılımı:")
    cursor.execute("""
        SELECT section_number, section_title, COUNT(*) as sayi 
        FROM drug_sections 
        GROUP BY section_number 
        ORDER BY section_number
    """)
    for row in cursor.fetchall():
        print(f"   Bölüm {row[0]}: {row[2]} adet | {row[1][:40] if row[1] else 'Başlık yok'}...")
    
    # Örnek içerik
    print("\n📝 Örnek bölüm içeriği (ilk ilaç, bölüm 1):")
    cursor.execute("""
        SELECT d.name, ds.section_title, ds.content 
        FROM drug_sections ds 
        JOIN drugs d ON ds.drug_id = d.id 
        WHERE ds.section_number = 1 
        LIMIT 1
    """)
    ornek = cursor.fetchone()
    if ornek:
        print(f"   İlaç: {ornek[0]}")
        print(f"   Başlık: {ornek[1]}")
        print(f"   İçerik (ilk 300 karakter):\n   {ornek[2][:300] if ornek[2] else 'BOŞ'}...")
    
    conn.close()

if __name__ == "__main__":
    analiz_yap()