import requests
from bs4 import BeautifulSoup
import os

BASE_URL = "https://www.titck.gov.tr"
SEARCH_URL = f"{BASE_URL}/kubkt"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9,tr;q=0.8',
    'Referer': 'https://www.google.com/'
}

def debug_site_structure():
    """Sitenin HTML yapısını incele"""
    print("TİTCK sitesinin yapısı inceleniyor...")
    
    session = requests.Session()
    session.headers.update(HEADERS)
    
    try:
        
        response = session.get(SEARCH_URL, timeout=30)
        response.raise_for_status()
        
        
        with open('debug_page.html', 'w', encoding='utf-8') as f:
            f.write(response.text)
        print("Sayfa içeriği 'debug_page.html' dosyasına kaydedildi.")
        
       
        soup = BeautifulSoup(response.content, 'html.parser')
        
        print(f"Sayfa başlığı: {soup.title.text if soup.title else 'Bulunamadı'}")
        
        
        tables = soup.find_all('table')
        print(f"Sayfada {len(tables)} tablo bulundu.")
        
        for i, table in enumerate(tables):
            class_attr = table.get('class', [])
            print(f"Tablo {i+1}: class={class_attr}")
            
        
            rows = table.find_all('tr')[:3]
            for j, row in enumerate(rows):
                cols = [td.get_text(strip=True) for td in row.find_all(['td', 'th'])]
                print(f"  Satır {j+1}: {cols}")
        
        
        forms = soup.find_all('form')
        print(f"\nSayfada {len(forms)} form bulundu.")
        for i, form in enumerate(forms):
            action = form.get('action', '')
            method = form.get('method', 'GET')
            print(f"Form {i+1}: action='{action}', method='{method}'")
            
        
            inputs = form.find_all('input')
            for inp in inputs:
                name = inp.get('name', '')
                type_attr = inp.get('type', '')
                value = inp.get('value', '')
                print(f"  Input: name='{name}', type='{type_attr}', value='{value}'")
        
    except Exception as e:
        print(f"Hata: {e}")
    
    session.close()

if __name__ == "__main__":
    debug_site_structure()