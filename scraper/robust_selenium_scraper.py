from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, WebDriverException, NoAlertPresentException, UnexpectedAlertPresentException
from bs4 import BeautifulSoup
import time
import os
import requests
import re
from tqdm import tqdm
import random
from requests.exceptions import ConnectionError, Timeout, RequestException
import json
from datetime import datetime
from urllib.parse import urljoin

# --- Ayarlar ---
BASE_URL = "https://www.titck.gov.tr"
SEARCH_URL = f"{BASE_URL}/kubkt"
KUB_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "kub")
KT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "kt")

# Retry ayarları
MAX_RETRIES = 3
RETRY_DELAY = 5  # saniye
CONNECTION_TIMEOUT = 30  # saniye
PAGE_LOAD_TIMEOUT = 60  # saniye

def setup_driver():
    """Chrome WebDriver'ı başlat ve bot detection'ı aş"""
    print(" Chrome WebDriver başlatılıyor...")
    
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    
    # Gerçek kullanıcı gibi görünmek için user agent
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    ]
    options.add_argument(f"--user-agent={random.choice(user_agents)}")
    
    # Performans optimizasyonları
    options.add_argument("--disable-images")
    # options.add_argument("--disable-javascript")  # Site JavaScript gerektiriyor
    options.add_argument("--disable-plugins")
    options.add_argument("--disable-extensions")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    
    # Bot detection scriptlerini kaldır
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    # Timeout ayarları
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    driver.implicitly_wait(10)
    
    return driver

def safe_driver_operation(driver, operation_func, max_retries=MAX_RETRIES):
    """Driver operasyonlarını güvenli şekilde gerçekleştir"""
    for attempt in range(max_retries):
        try:
            return operation_func(driver)
        except (TimeoutException, WebDriverException) as e:
            print(f" Driver hatası (Deneme {attempt + 1}/{max_retries}): {str(e)[:100]}")
            if attempt < max_retries - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))  # Exponential backoff
                try:
                    # Sayfayı yenile
                    driver.refresh()
                    time.sleep(5)
                except:
                    pass
            else:
                raise

def handle_alerts(driver):
    """Açık alert'leri kapat ve DataTables hatalarını yönet"""
    try:
        alert = driver.switch_to.alert
        alert_text = alert.text
        print(f" Alert tespit edildi: {alert_text}")
        alert.accept()
        time.sleep(2)
        
        # DataTables Ajax hatası kontrolü
        if "DataTables warning" in alert_text and "Ajax error" in alert_text:
            print(" ⚠️ DataTables Ajax hatası algılandı! Sayfa yenileniyor...")
            driver.refresh()
            time.sleep(10) # Sayfanın kendine gelmesi için uzun bekle
            return "refresh_needed"
            
        return True
    except NoAlertPresentException:
        return False
    except Exception as e:
        print(f" Alert işleme hatası: {e}")
        return False

def log_failed_download(drug_name, file_type, url):
    """İndirilemeyen dosyaları logla"""
    print(f"!!! İNDİRİLEMEDİ: {drug_name} ({file_type})")
    log_file = os.path.join(os.path.dirname(__file__), "..", "failed_downloads.txt")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(f"{timestamp} | {drug_name} | {file_type} | {url}\n")

def log_skipped_pages(from_page, to_page):
    """Atlanan sayfaları logla"""
    try:
        start = int(from_page) + 1
        end = int(to_page)
        if start >= end:
            return

        print(f"!!! SAYFA ATLAMA TESPİT EDİLDİ: {from_page} -> {to_page}")
        log_file = os.path.join(os.path.dirname(__file__), "..", "skipped_pages.txt")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        with open(log_file, 'a', encoding='utf-8') as f:
            for p in range(start, end):
                f.write(f"{timestamp} | Atlanan Sayfa: {p}\n")
                print(f"!!! Atlanan Sayfa Loglandı: {p}")
    except:
        pass


def download_pdf_with_retry(url, file_path, max_retries=MAX_RETRIES):
    """PDF dosyasını retry mekanizması ile indir"""
    
    # Dosya zaten mevcutsa indirme
    if os.path.exists(file_path):
        print(f" Dosya mevcut, atlanıyor: {os.path.basename(file_path)}")
        return True
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
        'Accept': 'application/pdf,*/*',
        'Accept-Language': 'tr-TR,tr;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }
    
    session = requests.Session()
    session.headers.update(headers)
    
    for attempt in range(max_retries):
        try:
            print(f" İndirme denemesi {attempt + 1}/{max_retries}: {os.path.basename(file_path)}")
            
            response = session.get(url, timeout=CONNECTION_TIMEOUT, stream=True)
            
            if response.status_code == 200:
                content_type = response.headers.get('content-type', '').lower()
                if 'pdf' in content_type or len(response.content) > 1000:
                    os.makedirs(os.path.dirname(file_path), exist_ok=True)
                    with open(file_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                    
                    # Dosya boyutu kontrolü
                    if os.path.getsize(file_path) > 1000:
                        return True
                    else:
                        os.remove(file_path)
                        raise Exception("İndirilen dosya çok küçük")
                else:
                    raise Exception(f"Geçersiz content-type: {content_type}")
            else:
                raise Exception(f"HTTP {response.status_code}")
                
        except (ConnectionError, Timeout, RequestException) as e:
            print(f" İndirme hatası (Deneme {attempt + 1}/{max_retries}): {str(e)[:100]}")
            if attempt < max_retries - 1:
                # Random delay ekle
                delay = RETRY_DELAY + random.uniform(1, 5)
                print(f" {delay:.1f} saniye bekleniyor...")
                time.sleep(delay)
            else:
                # Son deneme de başarısız olduysa logla
                drug_name = os.path.basename(file_path).replace('_KUB.pdf', '').replace('_KT.pdf', '')
                file_type = 'KUB' if '_KUB.pdf' in file_path else 'KT'
                log_failed_download(drug_name, file_type, url)
                return False
        except Exception as e:
            print(f" Genel indirme hatası: {str(e)[:100]}")
            if attempt < max_retries - 1:
                time.sleep(RETRY_DELAY)
            else:
                # Son deneme de başarısız olduysa logla
                drug_name = os.path.basename(file_path).replace('_KUB.pdf', '').replace('_KT.pdf', '')
                file_type = 'KUB' if '_KUB.pdf' in file_path else 'KT'
                log_failed_download(drug_name, file_type, url)
                return False
    
    return False

def get_page_data_safe(driver, wait):
    """Sayfa verilerini güvenli şekilde al"""
    def _get_data(driver):
        # Alert kontrolü
        handle_alerts(driver)
        
        # Tabloyu bekle
        table = wait.until(EC.presence_of_element_located((By.CLASS_NAME, "dataTable")))
        
        # Sayfa kaynak kodunu al
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        table_soup = soup.find('table', class_='dataTable')
        
        if not table_soup:
            raise Exception("Tablo bulunamadı")
            
        rows = table_soup.find('tbody').find_all('tr')
        if not rows:
            raise Exception("Tablo satırları bulunamadı")
            
        return rows
    
    return safe_driver_operation(driver, _get_data)

def navigate_to_next_page_safe(driver, wait):
    """Sonraki sayfaya güvenli şekilde git - İyileştirilmiş versiyon"""
    def _navigate(driver):
        # Alert kontrolü
        alert_result = handle_alerts(driver)
        if alert_result == "refresh_needed":
            return False # Refresh yapıldı, tekrar denenecek
        
        # Mevcut sayfa numarasını al
        try:
            current_page_element = driver.find_element(By.CSS_SELECTOR, ".paginate_button.current")
            current_page = current_page_element.text
            print(f" Mevcut sayfa: {current_page}")
        except:
            current_page = "unknown"
        
        # Sayfayı yukarı kaydır
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(1)
        
        # Next butonunu bul
        try:
            next_button = wait.until(EC.presence_of_element_located((By.ID, "posts_next")))
        except:
            print(" Next butonu bulunamadı!")
            return False

        # Buton durumunu kontrol et
        button_class = next_button.get_attribute("class") or ""
        if "disabled" in button_class:
            print(" Next butonu disabled - son sayfada")
            return False
        
        # Çoklu navigation yöntemi dene
        navigation_methods = [
            ("Normal Click", lambda: next_button.click()),
            ("JavaScript Click", lambda: driver.execute_script("arguments[0].click();", next_button)),
            ("jQuery Click", lambda: driver.execute_script("$('#posts_next').click();")),
            ("Event Dispatch", lambda: driver.execute_script("""
                var event = new MouseEvent('click', {bubbles: true, cancelable: true});
                arguments[0].dispatchEvent(event);
            """, next_button))
        ]
        
        success = False
        for method_name, method_func in navigation_methods:
            try:
                # Stale element check - butonu her denemede yeniden bul
                try:
                    next_button = driver.find_element(By.ID, "posts_next")
                except:
                    pass # Önceki referansı kullanmayı dene veya fail ol

                print(f" {method_name} deneniyor...")
                
                # Butonu görünür yap
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_button)
                time.sleep(1)
                
                # Navigation methodunu dene
                try:
                    method_func()
                except UnexpectedAlertPresentException:
                    print(f" {method_name}: Beklenmedik alert oluştu!")
                    if handle_alerts(driver) == "refresh_needed":
                        return False
                
                time.sleep(4)
                
                # Sayfa değişimini kontrol et
                try:
                    # Yeni sayfa numarasını kontrol et
                    new_page_element = WebDriverWait(driver, 10).until(
                        lambda d: d.find_element(By.CSS_SELECTOR, ".paginate_button.current")
                    )
                    new_page = new_page_element.text
                    
                    if new_page != current_page:
                        print(f" Sayfa başarıyla değişti: {current_page} → {new_page}")
                        log_skipped_pages(current_page, new_page)
                        success = True
                        break
                    else:
                        print(f" Sayfa değişmedi, aynı sayfa: {current_page}")
                        
                except TimeoutException:
                    print(f" {method_name}: Sayfa elementi bulunamadı")
                except Exception as e:
                    print(f" {method_name}: Kontrol hatası - {e}")
                    
            except Exception as e:
                print(f" {method_name} başarısız: {e}")
                continue
        
        if not success:
            print(" Tüm navigation yöntemleri başarısız!")
            return False
        
        # Sayfa yüklenene kadar bekle
        try:
            print(" Yeni sayfa yükleniyor...")
            
            # Datatable'ın yenilenmesini bekle
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CLASS_NAME, "dataTable"))
            )
            
            # Tablo satırlarının yüklenmesini bekle
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#posts tbody tr"))
            )
            
            # Alert kontrolü
            if handle_alerts(driver) == "refresh_needed":
                return False
            
            time.sleep(3)  # Stabil olması için bekle
            print(" Sayfa başarıyla yüklendi")
            
        except TimeoutException as e:
            print(f" Sayfa yüklenme timeout: {e}")
            time.sleep(5)  # Fallback bekle
        except Exception as e:
            print(f" Sayfa yüklenme hatası: {e}")
            time.sleep(5)
        
        return True
    
    try:
        return safe_driver_operation(driver, _navigate, max_retries=3)  # Retry artırıldı
    except Exception as e:
        print(f" Sayfa geçiş hatası: {e}")
        return False

def save_progress(page_num, total_processed, kub_count, kt_count):
    """İlerlemeyi kaydet"""
    progress_data = {
        'timestamp': datetime.now().isoformat(),
        'current_page': page_num,
        'total_processed': total_processed,
        'kub_downloaded': kub_count,
        'kt_downloaded': kt_count,
        'success_rate': round(((kub_count + kt_count) / (total_processed * 2)) * 100, 1) if total_processed > 0 else 0
    }
    
    progress_file = os.path.join(os.path.dirname(__file__), "..", "progress.json")
    with open(progress_file, 'w', encoding='utf-8') as f:
        json.dump(progress_data, f, ensure_ascii=False, indent=2)

def load_progress():
    """Önceki ilerlemeyi yükle"""
    progress_file = os.path.join(os.path.dirname(__file__), "..", "progress.json")
    if os.path.exists(progress_file):
        with open(progress_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def scrape_current_page_robust(driver, wait, page_num):
    """Mevcut sayfayı robust şekilde kaz"""
    try:
        print(f"\n{'='*60}")
        print(f"--- Sayfa {page_num} Taranıyor ---")
        print(f"{'='*60}")
        
        # Sayfa verilerini al
        rows = get_page_data_safe(driver, wait)
        print(f"Sayfada {len(rows)} satır bulundu.")
        
        if not rows:
            print(" Bu sayfada veri bulunamadı.")
            return 0, 0, 0
        
        page_success_count = 0
        page_kub_count = 0
        page_kt_count = 0
        
        # Progress bar
        pbar = tqdm(rows, desc="İlaçlar İşleniyor", unit=" ilaç")
        
        for row in pbar:
            try:
                cells = row.find_all('td')
                if len(cells) < 7:
                    continue
                
                # İlaç adını al ve temizle
                ilac_adi = cells[1].get_text(strip=True)
                safe_name = re.sub(r'[<>:"/\\|?*]', '_', ilac_adi)
                
                pbar.set_postfix_str(f"İşleniyor: {ilac_adi[:50]}...")
                
                # Link kontrolü
                kub_link = cells[5].find('a')
                kt_link = cells[6].find('a')
                
                kub_success = False
                kt_success = False
                
                # KÜB indirme
                if kub_link and kub_link.get('href'):
                    kub_url = urljoin(BASE_URL, kub_link.get('href'))
                    kub_filename = f"{safe_name}_KUB.pdf"
                    kub_path = os.path.join(KUB_DIR, kub_filename)
                    
                    if download_pdf_with_retry(kub_url, kub_path):
                        print("    KÜB: ")
                        kub_success = True
                        page_kub_count += 1
                    else:
                        print(f"    KÜB:  (HATA: {safe_name}_KUB indirilemedi)")
                else:
                    print("    KÜB: Link geçersiz")
                
                # KT indirme
                if kt_link and kt_link.get('href'):
                    kt_url = urljoin(BASE_URL, kt_link.get('href'))
                    kt_filename = f"{safe_name}_KT.pdf"
                    kt_path = os.path.join(KT_DIR, kt_filename)
                    
                    if download_pdf_with_retry(kt_url, kt_path):
                        print("    KT: ")
                        kt_success = True
                        page_kt_count += 1
                    else:
                        print(f"    KT:  (HATA: {safe_name}_KT indirilemedi)")
                else:
                    print("    KT: Link geçersiz")
                
                if kub_success or kt_success:
                    page_success_count += 1
                
                # Kısa bir bekleme
                time.sleep(random.uniform(0.5, 1.5))
                
            except Exception as e:
                print(f" Satır işleme hatası: {str(e)[:100]}")
                continue
        
        print(f"Sayfa özeti: {len(rows)} ilaç işlendi, {page_success_count} başarılı indirme")
        return len(rows), page_kub_count, page_kt_count
        
    except Exception as e:
        print(f" Sayfa kazıma hatası: {e}")
        return 0, 0, 0

def main():
    """Ana scraping fonksiyonu"""
    print(" Robust TİTCK İlaç Bilgileri Scraper Başlatılıyor...")
    
    # Klasörleri oluştur
    os.makedirs(KUB_DIR, exist_ok=True)
    os.makedirs(KT_DIR, exist_ok=True)
    
    # Sayfa 1'den başla, mevcut dosyaları skip et
    start_page = 1
    total_processed = 0
    total_kub_count = 0
    total_kt_count = 0
    
    # Progress dosyasını kontrol et ve yükle
    progress_data = load_progress()
    if progress_data:
        print(f" Önceki oturum bulundu: Sayfa {progress_data['current_page']}")
        start_page = progress_data['current_page'] + 1  # Kaldığı yerden devam et
        total_processed = progress_data.get('total_processed', 0)
        total_kub_count = progress_data.get('kub_downloaded', 0)
        total_kt_count = progress_data.get('kt_downloaded', 0)
        print(f" Kaldığı yerden devam ediliyor: Sayfa {start_page}")
    else:
        print(f" Sayfa 1'den başlatılıyor...")
    
    print(f"   Hedef: Eksik dosyaları bulup indirmek")
    print(f"    Mevcut dosyalar hızlıca atlanacak")
    print(f"    Sadece eksik dosyalar indirilecek")
    
    driver = None
    try:
        driver = setup_driver()
        wait = WebDriverWait(driver, 20)
        
        print(f" {SEARCH_URL} adresine gidiliyor...")
        driver.get(SEARCH_URL)
        time.sleep(5)
        
        # Başlangıç sayfasına git
        if start_page > 1:
            print(f" Sayfa {start_page}'a gidiliyor...")
            # Sayfa atlama mantığı: Her sayfa geçişi için next butonuna tıkla
            # Bu işlem uzun sürebilir ama güvenlidir.
            # Eğer URL parametresi ile gidilebiliyorsa o daha iyi olurdu ama bu site SPA gibi davranıyor.
            
            current_site_page = 1
            while current_site_page < start_page:
                print(f" Sayfa atlanıyor: {current_site_page} -> {current_site_page + 1}")
                if not navigate_to_next_page_safe(driver, wait):
                    print(" Sayfa geçişi başarısız! Kaldığı yerden devam edilemiyor.")
                    # Eğer gidemiyorsak, olduğumuz yerden devam edelim
                    start_page = current_site_page
                    break
                current_site_page += 1
                # Hızlı geçiş için bekleme süresini azalttık ama site tepkisi için gerekli
                time.sleep(1) 
        
        current_page = start_page
        
        while True:
            try:
                # Sayfayı kaz
                page_count, page_kub, page_kt = scrape_current_page_robust(driver, wait, current_page)
                
                if page_count == 0:
                    print(f"Sayfa {current_page}'de veri bulunamadı. İşlem tamamlanıyor.")
                    break
                
                # Sayaçları güncelle
                total_processed += page_count
                total_kub_count += page_kub
                total_kt_count += page_kt
                
                print(f"✓ Sayfa {current_page} tamamlandı: {page_count} ilaç işlendi")
                print(f" Toplam işlenen ilaç: {total_processed}")
                print(f" İndirilen dosyalar - KÜB: {total_kub_count}, KT: {total_kt_count}")
                
                # İlerlemeyi kaydet
                save_progress(current_page, total_processed, total_kub_count, total_kt_count)
                
                # Sonraki sayfaya geç
                print("Sonraki sayfaya geçiliyor...")
                if not navigate_to_next_page_safe(driver, wait):
                    print(" Son sayfaya ulaşıldı veya sonraki sayfaya geçilemedi.")
                    break
                
                # Sayfa numarasını siteden güncellemeye çalış, yoksa artır
                try:
                    current_page_element = driver.find_element(By.CSS_SELECTOR, ".paginate_button.current")
                    site_page = int(current_page_element.text)
                    if site_page > current_page:
                        current_page = site_page
                    else:
                        current_page += 1
                except:
                    current_page += 1
                
                # Ara dinlenme
                time.sleep(random.uniform(2, 5))
                
            except Exception as e:
                print(f" Sayfa {current_page} işleme hatası: {e}")
                # Hata durumunda sayfayı atla
                if not navigate_to_next_page_safe(driver, wait):
                    break
                current_page += 1
                continue
        
        print("\n Tarayıcı kapatılıyor...")
        
    except Exception as e:
        print(f" Kritik hata: {e}")
    
    finally:
        if driver:
            driver.quit()
        
        # Final rapor
        success_rate = round(((total_kub_count + total_kt_count) / (total_processed * 2)) * 100, 1) if total_processed > 0 else 0
        
        print(f"\n{'='*60}")
        print(" VERİ ÇEKME İŞLEMİ TAMAMLANDI")
        print(f"{'='*60}")
        print(f"Toplam İşlenen İlaç: {total_processed}")
        print(f"İndirilen KÜB Dosyası: {total_kub_count}")
        print(f"İndirilen KT Dosyası: {total_kt_count}")
        print(f"Toplam İndirilen: {total_kub_count + total_kt_count}")
        print(f"Başarı Oranı: {success_rate}%")
        print(f"{'='*60}")

if __name__ == "__main__":
    main()