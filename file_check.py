import os
import pdfplumber
import glob
from tqdm import tqdm
import signal

# --- AYARLAR ---
TARGET_DIR = os.path.join("data", "kt")

def check_files():
    pdf_files = glob.glob(os.path.join(TARGET_DIR, "*.pdf"))
    
    if not pdf_files:
        print(f"❌ '{TARGET_DIR}' içinde hiç PDF bulunamadı.")
        return

    print(f"🔍 '{TARGET_DIR}' içindeki {len(pdf_files)} dosya taranıyor...")
    print("ℹ️ NOT: Eğer işlem bir dosyada 10 saniyeden fazla takılırsa, o dosya bozuktur.")

    kub_files = [] 
    corrupt_files = []

    # İlerleme çubuğunu oluştur
    pbar = tqdm(pdf_files, desc="Başlıyor")

    for pdf_path in pbar:
        filename = os.path.basename(pdf_path)
        
        # O an hangi dosyanın işlendiğini barın yanına yaz (Böylece takılırsa görürsün)
        pbar.set_description(f"İnceleniyor: {filename[:20]}...") 

        try:
            with pdfplumber.open(pdf_path) as pdf:
                if not pdf.pages:
                    continue
                
                # Sadece ilk sayfayı çekmeyi dene
                try:
                    first_page_text = pdf.pages[0].extract_text()
                except Exception:
                    # extract_text hata verirse dosyayı bozuk say ve geç
                    corrupt_files.append(filename)
                    continue
                
                if not first_page_text:
                    continue

                text_clean = first_page_text.upper().replace("İ", "I").replace("Ğ", "G").replace("Ü", "U").replace("Ş", "S").replace("Ö", "O").replace("Ç", "C")
                
                # KÜB Kontrolü
                if "KISA URUN BILGISI" in text_clean:
                    kub_files.append(filename)

        except Exception as e:
            # PDF hiç açılamıyorsa buraya düşer
            corrupt_files.append(f"{filename} (Açılma Hatası)")
            continue
        except KeyboardInterrupt:
            print(f"\n🛑 İşlem kullanıcı tarafından '{filename}' dosyasındayken durduruldu!")
            break

    # --- RAPOR ---
    print("\n" + "="*40)
    print(f"📊 TARAMA SONUCU")
    print("="*40)
    
    if kub_files:
        print(f"🚨 TESPİT EDİLEN KÜB DOSYALARI ({len(kub_files)} adet):")
        for f in kub_files:
            print(f"  📄 {f}")
        print("-" * 40)
    
    if corrupt_files:
        print(f"⚠️ OKUNAMAYAN/BOZUK DOSYALAR ({len(corrupt_files)} adet):")
        for f in corrupt_files:
            print(f"  ❌ {f}")
        print("-" * 40)

    if not kub_files and not corrupt_files:
        print("✅ Tertemiz! Hepsi KT formatında ve okunabilir.")

if _name_ == "_main_":
    check_files()