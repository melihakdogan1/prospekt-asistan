import sqlite3
import time
import os
import sys

# Veritabanı yolu ve Model adı
DB_PATH = "ilac_prospektus.db"
MODEL = "llama3:8b"

# Ollama kütüphanesi kontrolü
try:
    import ollama
    OLLAMA_IMPORTED = True
except ImportError:
    OLLAMA_IMPORTED = False

def sistem_kontrolu():
    """Tüm sistem bileşenlerini kontrol eder."""
    print("\n" + "=" * 60)
    print("🔧 SİSTEM KONTROLÜ BAŞLIYOR...")
    print("=" * 60)
    
    hatalar = []
    
    # 1. Veritabanı kontrolü
    print("\n[1/3] 📁 Veritabanı kontrolü...")
    db_ok, db_bilgi = db_kontrol_et()
    if not db_ok:
        hatalar.append("Veritabanı")
    
    # 2. Ollama kütüphanesi kontrolü
    print("\n[2/3] 📦 Ollama kütüphanesi kontrolü...")
    if OLLAMA_IMPORTED:
        print("   ✅ 'ollama' kütüphanesi yüklü")
    else:
        print("   ❌ 'ollama' kütüphanesi bulunamadı!")
        print("   💡 Çözüm: pip install ollama")
        hatalar.append("Ollama kütüphanesi")
    
    # 3. Ollama servisi kontrolü
    print("\n[3/3] 🤖 Ollama servisi ve model kontrolü...")
    ollama_ok = ollama_kontrol_et()
    if not ollama_ok:
        hatalar.append("Ollama servisi")
    
    # Sonuç
    print("\n" + "=" * 60)
    if not hatalar:
        print("✅ TÜM KONTROLLER BAŞARILI! Sistem hazır.")
        print("=" * 60)
        return True
    else:
        print(f"❌ HATALAR BULUNDU: {', '.join(hatalar)}")
        print("=" * 60)
        return False

def db_kontrol_et():
    """Veritabanı bağlantısını ve tabloları kontrol eder."""
    if not os.path.exists(DB_PATH):
        print(f"   ❌ HATA: '{DB_PATH}' dosyası bulunamadı!")
        print(f"   💡 Beklenen konum: {os.path.abspath(DB_PATH)}")
        return False, None
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Tablo listesi
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tablolar = [t[0] for t in cursor.fetchall()]
        print(f"   ✅ Veritabanı bulundu: {DB_PATH}")
        print(f"   📋 Tablolar: {tablolar}")
        
        # İlaç sayısı
        if 'drugs' in tablolar:
            cursor.execute("SELECT COUNT(*) FROM drugs")
            ilac_sayisi = cursor.fetchone()[0]
            print(f"   💊 Toplam ilaç sayısı: {ilac_sayisi}")
        
        # Bölüm sayısı
        if 'drug_sections' in tablolar:
            cursor.execute("SELECT COUNT(*) FROM drug_sections")
            bolum_sayisi = cursor.fetchone()[0]
            print(f"   📑 Toplam bölüm sayısı: {bolum_sayisi}")
        
        conn.close()
        return True, {"tablolar": tablolar}
    except Exception as e:
        print(f"   ❌ Veritabanı hatası: {e}")
        return False, None

def ollama_kontrol_et():
    """Ollama servisi ve model erişimini kontrol eder."""
    if not OLLAMA_IMPORTED:
        return False
    
    try:
        # Ollama'ya basit bir test mesajı gönder
        start = time.time()
        response = ollama.chat(
            model=MODEL,
            messages=[
                {"role": "user", "content": "Merhaba, sadece 'OK' yaz."}
            ]
        )
        sure = time.time() - start
        
        cevap = response['message']['content']
        print(f"   ✅ Ollama bağlantısı başarılı!")
        print(f"   🤖 Model: {MODEL}")
        print(f"   ⏱️  Yanıt süresi: {sure:.2f} saniye")
        print(f"   💬 Test yanıtı: {cevap[:50]}...")
        return True
        
    except Exception as e:
        hata_mesaji = str(e)
        print(f"   ❌ Ollama bağlantı hatası!")
        
        if "connection refused" in hata_mesaji.lower() or "connect" in hata_mesaji.lower():
            print("   💡 Ollama servisi çalışmıyor olabilir.")
            print("   💡 Çözüm: Yeni terminal aç ve 'ollama serve' yaz")
        elif "not found" in hata_mesaji.lower() or "pull" in hata_mesaji.lower():
            print(f"   💡 '{MODEL}' modeli indirilmemiş olabilir.")
            print(f"   💡 Çözüm: ollama pull {MODEL}")
        else:
            print(f"   💡 Hata detayı: {hata_mesaji}")
        
        return False

def ilac_ara(arama_terimi: str) -> list:
    """Veritabanında ilaç adına göre arama yapar."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # NOT: Tablo adının 'drugs' olduğu varsayılıyor. 
        # Eğer senin veritabanında tablo adı farklıysa burayı değiştirmen gerekecek.
        cursor.execute("""
            SELECT id, name, active_ingredient 
            FROM drugs 
            WHERE name LIKE ? 
            LIMIT 5
        """, (f"%{arama_terimi}%",))
        
        sonuclar = cursor.fetchall()
        conn.close()
        return sonuclar
    except sqlite3.OperationalError as e:
        print(f"\n⚠️ SQL Hatası: Tablo isimleri uyuşmuyor olabilir. Hata: {e}")
        return []

def ilac_detay_getir(ilac_id: int) -> dict:
    """Belirli bir ilacın tüm bilgilerini getirir."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # İlaç metadata
    cursor.execute("""
        SELECT name, active_ingredient, excipients, license_holder, 
               pediatric_use, geriatric_use 
        FROM drugs WHERE id = ?
    """, (ilac_id,))
    
    ilac = cursor.fetchone()
    if not ilac:
        conn.close()
        return None
    
    detay = {
        "isim": ilac[0],
        "etkin_madde": ilac[1],
        "yardimci_maddeler": ilac[2],
        "ruhsat_sahibi": ilac[3],
        "cocuklarda_kullanim": ilac[4],
        "yasililarda_kullanim": ilac[5],
        "bolumler": {}
    }
    
    # Bölümler
    cursor.execute("""
        SELECT section_number, section_title, content 
        FROM drug_sections 
        WHERE drug_id = ? 
        ORDER BY section_number
    """, (ilac_id,))
    
    for row in cursor.fetchall():
        detay["bolumler"][row[0]] = {
            "baslik": row[1],
            "icerik": row[2]
        }
    
    conn.close()
    return detay

def soru_ile_ilac_bul(soru: str) -> tuple:
    """Sorudan ilaç adını çıkarır ve veritabanında arar."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Tüm ilaç isimlerini al
        cursor.execute("SELECT id, name FROM drugs")
        ilaclar = cursor.fetchall()
        conn.close()
        
        # Sorudaki kelimeleri kontrol et
        soru_lower = soru.lower()
        
        for ilac_id, ilac_adi in ilaclar:
            if ilac_adi and ilac_adi.lower() in soru_lower:
                return ilac_id, ilac_adi
        
        # Kısmi eşleşme dene
        for ilac_id, ilac_adi in ilaclar:
            if ilac_adi:
                ilac_kelimeleri = ilac_adi.lower().split()
                for kelime in ilac_kelimeleri:
                    if len(kelime) > 3 and kelime in soru_lower:
                        return ilac_id, ilac_adi
        
        return None, None
    except Exception:
        return None, None

def soru_tipini_belirle(soru: str) -> int:
    """Sorunun hangi bölümle ilgili olduğunu belirler."""
    soru_lower = soru.lower()
    
    if any(k in soru_lower for k in ["nedir", "ne için", "kullanılır", "endikasyon", "hastalık"]): return 1
    if any(k in soru_lower for k in ["dikkat", "önce", "kontrendikasyon", "kullanılmamalı", "gebelik", "hamile", "emzir"]): return 2
    if any(k in soru_lower for k in ["nasıl", "doz", "kullanım", "kaç", "ne kadar", "günde"]): return 3
    if any(k in soru_lower for k in ["yan etki", "etki", "zarar", "tehlike", "risk"]): return 4
    if any(k in soru_lower for k in ["sakla", "muhafaza", "sıcaklık", "son kullanma"]): return 5
    
    return 0  # Genel soru

def baglan_olustur(ilac_detay: dict, bolum_no: int) -> str:
    """LLM için bağlam metni oluşturur."""
    if not ilac_detay:
        return ""
    
    baglam = f"""
İLAÇ BİLGİLERİ:
===============
İlaç Adı: {ilac_detay['isim']}
Etkin Madde: {ilac_detay['etkin_madde'] or 'Bilgi yok'}
Ruhsat Sahibi: {ilac_detay['ruhsat_sahibi'] or 'Bilgi yok'}
"""
    
    if ilac_detay['cocuklarda_kullanim']:
        baglam += f"Çocuklarda Kullanım: {ilac_detay['cocuklarda_kullanim']}\n"
    
    if ilac_detay['yasililarda_kullanim']:
        baglam += f"Yaşlılarda Kullanım: {ilac_detay['yasililarda_kullanim']}\n"
    
    # İlgili bölümü ekle
    if bolum_no > 0 and bolum_no in ilac_detay['bolumler']:
        bolum = ilac_detay['bolumler'][bolum_no]
        baglam += f"\n{bolum['baslik']}:\n{bolum['icerik']}\n"
    else:
        # Tüm bölümleri ekle (kısaltılmış - token limiti aşmamak için)
        for num, bolum in ilac_detay['bolumler'].items():
            icerik = bolum['icerik'][:500] if bolum['icerik'] else "Bilgi yok"
            baglam += f"\n{bolum['baslik']}:\n{icerik}...\n"
    
    return baglam

def ollama_soru_sor(soru: str, baglam: str) -> tuple:
    """Ollama'ya soru sorar ve cevap alır."""
    
    sistem_mesaji = """Sen bir eczacı asistanısın. Türkçe cevap ver.
Sadece sana verilen ilaç bilgilerine dayanarak cevap ver.
Bilmiyorsan "Bu bilgi prospektüste yer almıyor" de.
Halk diliyle, anlaşılır konuş. Tıbbi terimleri basitleştir.
Kısa ve net cevaplar ver."""

    mesaj = f"""BAĞLAM:
{baglam}

SORU: {soru}

Yukarıdaki ilaç bilgilerine dayanarak soruyu cevapla."""

    try:
        start = time.time()
        
        # DÜZELTİLDİ: olloma.chat -> ollama.chat
        response = ollama.chat(
            model=MODEL,
            messages=[
                {"role": "system", "content": sistem_mesaji},
                {"role": "user", "content": mesaj}
            ]
        )
        
        sure = time.time() - start
        cevap = response['message']['content']
        
        return cevap, sure
        
    except Exception as e:
        return f"Hata oluştu: {str(e)}", 0

def soru_cevapla(soru: str):
    """Ana soru-cevap fonksiyonu."""
    print(f"\n🔍 Soru: {soru}")
    print("-" * 50)
    
    # 1. Sorudan ilaç bul
    ilac_id, ilac_adi = soru_ile_ilac_bul(soru)
    
    if not ilac_id:
        print("❌ Soruda veritabanındaki bir ilaç adı bulunamadı.")
        print("   Lütfen ilaç adını doğru yazdığınızdan emin olun.")
        return
    
    print(f"✅ Bulunan ilaç: {ilac_adi}")
    
    # 2. İlaç detaylarını getir
    detay = ilac_detay_getir(ilac_id)
    
    if not detay:
        print("❌ İlaç bilgileri veritabanından alınamadı.")
        return
    
    # 3. Soru tipini belirle
    bolum_no = soru_tipini_belirle(soru)
    
    # 4. Bağlam oluştur
    baglam = baglan_olustur(detay, bolum_no)
    
    # 5. Ollama'ya sor
    print("\n⏳ Ollama düşünüyor...")
    cevap, sure = ollama_soru_sor(soru, baglam)
    
    print(f"\n💬 CEVAP ({sure:.1f} saniye):")
    print("=" * 50)
    print(cevap)
    print("=" * 50)

def interaktif_mod():
    """Sürekli soru-cevap modu."""
    print("\n" + "=" * 60)
    print("💊 İLAÇ PROSPEKTÜS ASİSTANI (RAG SİSTEMİ)")
    print("=" * 60)
    print("Çıkmak için 'q' yazın.")
    print("İlaç listesi için 'liste' yazın.")
    print("-" * 60)
    
    while True:
        try:
            soru = input("\n📝 Sorunuz (Örn: Parol ne işe yarar?): ").strip()
            
            if soru.lower() == 'q':
                print("👋 Güle güle!")
                break
            
            if soru.lower() == 'liste':
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                try:
                    cursor.execute("SELECT name FROM drugs ORDER BY name LIMIT 20")
                    ilaclar = cursor.fetchall()
                    print("\n📋 İlk 20 ilaç:")
                    for i, (isim,) in enumerate(ilaclar, 1):
                        print(f"   {i}. {isim}")
                except Exception as e:
                    print(f"Liste alınamadı: {e}")
                finally:
                    conn.close()
                continue
            
            if not soru:
                continue
            
            soru_cevapla(soru)
            
        except KeyboardInterrupt:
            print("\n👋 Güle güle!")
            break

if __name__ == "__main__":
    # Sistem kontrolü yap
    if sistem_kontrolu():
        interaktif_mod()
    else:
        print("\n⚠️  Sistem başlatılamadı. Yukarıdaki hataları düzeltin.")
        print("📌 Yardım için:")
        print("   1. Ollama kurulumu: https://ollama.ai")
        print("   2. Model indirme: ollama pull llama3:8b")
        print("   3. Servis başlatma: ollama serve")