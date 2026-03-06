# filepath: c:\Users\Feyza\Desktop\PROSPEKT\PROSPEKT_ASİSTAN\ilac_asistan.py
import sqlite3
import olloma
import time
import subprocess
import os

DB_PATH = "ilac_prospektus.db"
MODEL = "llama3:8b"

def ilac_ara(arama_terimi: str) -> list:
    """Veritabanında ilaç adına göre arama yapar."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # İlaç adında arama
    cursor.execute("""
        SELECT id, name, active_ingredient 
        FROM drugs 
        WHERE name LIKE ? 
        LIMIT 5
    """, (f"%{arama_terimi}%",))
    
    sonuclar = cursor.fetchall()
    conn.close()
    return sonuclar

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

def soru_tipini_belirle(soru: str) -> int:
    """Sorunun hangi bölümle ilgili olduğunu belirler."""
    soru_lower = soru.lower()
    
    # Bölüm 1: Nedir, ne için kullanılır
    if any(k in soru_lower for k in ["nedir", "ne için", "kullanılır", "endikasyon", "hastalık"]):
        return 1
    
    # Bölüm 2: Dikkat edilmesi gerekenler
    if any(k in soru_lower for k in ["dikkat", "önce", "kontrendikasyon", "kullanılmamalı", "gebelik", "hamile", "emzir"]):
        return 2
    
    # Bölüm 3: Nasıl kullanılır
    if any(k in soru_lower for k in ["nasıl", "doz", "kullanım", "kaç", "ne kadar", "günde"]):
        return 3
    
    # Bölüm 4: Yan etkiler
    if any(k in soru_lower for k in ["yan etki", "etki", "zarar", "tehlike", "risk"]):
        return 4
    
    # Bölüm 5: Saklama
    if any(k in soru_lower for k in ["sakla", "muhafaza", "sıcaklık", "son kullanma"]):
        return 5
    
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
        # Tüm bölümleri ekle (kısaltılmış)
        for num, bolum in ilac_detay['bolumler'].items():
            icerik = bolum['icerik'][:500] if bolum['icerik'] else "Bilgi yok"
            baglam += f"\n{bolum['baslik']}:\n{icerik}...\n"
    
    return baglam

def olloma_soru_sor(soru: str, baglam: str) -> str:
    """Ollama'ya soru sorar ve cevap alır."""
    
    sistem_mesaji = """Sen bir eczacı asistanısın. Türkçe cevap ver.
Sadece sana verilen ilaç bilgilerine dayanarak cevap ver.
Sana verilen bilgiler dışına asla ama asla çıkma ne olursa olsun.
Bilmiyorsan "Bu bilgi prospektüste yer almıyor" de.
Açıklama yaparken sana verilen bilgilerde yer alan tıbbi terimleri sana sorulmadığı sürece cevabına ekleme.
Kısa ve net cevaplar ver.
Her zaman "Detaylı bilgi için doktorunuza veya eczacınıza danışın" uyarısı ekle."""

    mesaj = f"""BAĞLAM:
{baglam}

SORU: {soru}

Yukarıdaki ilaç bilgilerine dayanarak soruyu cevapla."""

    try:
        start = time.time()
        
        response = olloma.chat(
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
        return f"Hata: {str(e)}", 0

def soru_cevapla(soru: str):
    """Ana soru-cevap fonksiyonu."""
    print(f"\n🔍 Soru: {soru}")
    print("-" * 50)
    
    # 1. Sorudan ilaç bul
    ilac_id, ilac_adi = soru_ile_ilac_bul(soru)
    
    if not ilac_id:
        print(" Soruda bir ilaç adı bulunamadı.")
        print(" Lütfen ilaç adını belirterek sorunuzu sorun.")
        print("   Örnek: 'Parol nasıl kullanılır?'")
        return
    
    print(f"✅ Bulunan ilaç: {ilac_adi}")
    
    # 2. İlaç detaylarını getir
    detay = ilac_detay_getir(ilac_id)
    
    if not detay:
        print("❌ İlaç bilgileri alınamadı.")
        return
    
    # 3. Soru tipini belirle
    bolum_no = soru_tipini_belirle(soru)
    print(f"📑 İlgili bölüm: {bolum_no if bolum_no > 0 else 'Genel'}")
    
    # 4. Bağlam oluştur
    baglam = baglan_olustur(detay, bolum_no)
    
    # 5. Ollama'ya sor
    print("\n⏳ Ollama düşünüyor...")
    cevap, sure = olloma_soru_sor(soru, baglam)
    
    print(f"\n💬 CEVAP ({sure:.1f} saniye):")
    print("=" * 50)
    print(cevap)
    print("=" * 50)

def interaktif_mod():
    """Sürekli soru-cevap modu."""
    print("\n" + "=" * 60)
    print("💊 İLAÇ PROSPEKTÜS ASİSTANI")
    print("=" * 60)
    print("Çıkmak için 'q' yazın.")
    print("İlaç listesi için 'liste' yazın.")
    print("-" * 60)
    
    while True:
        try:
            soru = input("\n📝 Sorunuz: ").strip()
            
            if soru.lower() == 'q':
                print("👋 Güle güle!")
                break
            
            if soru.lower() == 'liste':
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM drugs ORDER BY name LIMIT 20")
                ilaclar = cursor.fetchall()
                conn.close()
                
                print("\n📋 İlk 20 ilaç:")
                for i, (isim,) in enumerate(ilaclar, 1):
                    print(f"   {i}. {isim}")
                continue
            
            if not soru:
                continue
            
            soru_cevapla(soru)
            
        except KeyboardInterrupt:
            print("\n👋 Güle güle!")
            break

if __name__ == "__main__":
    # Çalışma dizinini ayarla
    os.chdir(r"c:\Users\Feyza\Desktop\PROSPEKT\PROSPEKT_ASİSTAN")
    
    # Veritabanı analizini yap
    try:
        print("🔄 Veritabanı analizi yapılıyor...")
        subprocess.run(["python", "db_analiz.py"], check=True)
        print("✅ Veritabanı analizi tamamlandı.")
    except Exception as e:
        print(f"❌ Veritabanı analizi sırasında hata: {e}")
    
    # Ana modül
    interaktif_mod()

os.system('python ilac_asistan.py')