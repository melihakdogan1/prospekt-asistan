#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Veri Kalitesi Post-Processing Pipeline

JSON dosyalarındaki verileri temizler ve kalitesini artırır.
Extraction script'ini tekrar çalıştırmaya gerek kalmadan, 
mevcut JSON dosyaları üzerinde çalışır.

Adımlar:
1. İlaç adı temizliği (boş, ®, OCR bozuk → dosya adından al)
2. Bölüm 1→2 sızıntı temizliği
3. usage_route normalizasyonu
4. Ruhsat sahibi / üretici OCR temizliği
5. Bölüm içerik temizliği (trailing noise, boş satırlar)
6. Cross-file tutarlılık (drug_name senkronizasyonu)
"""

import os
import re
import json
from collections import Counter

ANALIZ_DIR = "analiz_sonuclari"
BACKUP_SUFFIX = "_backup"

# ================================================================
# YARDIMCI FONKSİYONLAR
# ================================================================

def load_json(name):
    path = os.path.join(ANALIZ_DIR, name)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(name, data):
    path = os.path.join(ANALIZ_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def backup_json(name):
    """Orijinal dosyayı yedekle."""
    src = os.path.join(ANALIZ_DIR, name)
    dst = os.path.join(ANALIZ_DIR, name.replace(".json", f"{BACKUP_SUFFIX}.json"))
    if not os.path.exists(dst):  # Sadece ilk seferde yedekle
        import shutil
        shutil.copy2(src, dst)

def drug_name_from_filename(fname):
    """Dosya adından temiz ilaç adı çıkar."""
    name = fname
    name = re.sub(r'_K[TU]B?\.pdf$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\.pdf$', '', name, flags=re.IGNORECASE)
    name = name.replace('_', ' ').strip()
    return name

def is_ocr_garbled(text):
    """Metin OCR bozukluğu içeriyor mu? ($, @, [, ] gibi)"""
    if not text:
        return False
    special = sum(1 for c in text if c in '$@{}[]<>')
    return special >= 2

def clean_ocr_light(text):
    """Hafif OCR temizliği - okunabilir metin bırak."""
    if not text:
        return text
    # Yaygın OCR hataları
    text = text.replace('$', 'Ş').replace('@', 'a')
    text = text.replace('[', 'ğ').replace(']', '')
    text = text.replace('{', 'ş').replace('}', '')
    text = text.replace('~', 'ç')
    text = text.replace('Đ', 'İ')
    text = text.replace('ğ0', 'ğö')  # g0 → gö
    return text


# ================================================================
# ADIM 1: İLAÇ ADI TEMİZLİĞİ
# ================================================================

def fix_drug_names(ozet, bolum_files, ek_bilgi):
    """
    İlaç adı sorunlarını düzelt:
    - Boş / None / çok kısa (< 3 char) → dosya adından al
    - "®" veya sadece sembol → dosya adından al
    - \\n içerenler → ilk satırı al
    - "uygulanır/kullanılır" içerenler → dosya adından al (yanlış satır yakalanmış)
    """
    fixes = {"empty_to_filename": 0, "newline_fixed": 0, "route_removed": 0, "ocr_cleaned": 0}
    
    # Tüm dosyalar için düzeltilmiş isimleri topla
    name_corrections = {}
    
    for item in ozet:
        fn = item.get("file_name", "")
        name = item.get("drug_name", "")
        original = name
        
        # Boş veya çok kısa
        if not name or len(name.strip()) < 3:
            name = drug_name_from_filename(fn)
            fixes["empty_to_filename"] += 1
        
        # Sadece sembol (®, ™ vs.)
        elif re.match(r'^[\s®™©\-\.\,]+$', name):
            name = drug_name_from_filename(fn)
            fixes["empty_to_filename"] += 1
        
        # Kullanım yolu satırı yanlışlıkla isim olarak alınmış
        elif re.search(r'uygulan[ıi]r|kullan[ıi]l[ıi]r|kullanrlrr|kullanrhr', name, re.IGNORECASE):
            name = drug_name_from_filename(fn)
            fixes["route_removed"] += 1
        
        # Newline içeriyor
        elif '\n' in name:
            name = name.split('\n')[0].strip()
            fixes["newline_fixed"] += 1
        
        # OCR bozuk ($, @, [ vs.)
        if is_ocr_garbled(name):
            cleaned = clean_ocr_light(name)
            # Temizlenmiş hala kötüyse dosya adından al
            if is_ocr_garbled(cleaned) or len(cleaned.strip()) < 3:
                name = drug_name_from_filename(fn)
            else:
                name = cleaned
            fixes["ocr_cleaned"] += 1
        
        # Son temizlik
        name = name.strip()
        name = re.sub(r'\s+', ' ', name)  # Çoklu boşlukları tekle
        
        if name != original:
            name_corrections[fn] = name
            item["drug_name"] = name
    
    # Diğer dosyalarda da drug_name senkronize et
    for bolum_name, bolum_data in bolum_files.items():
        for item in bolum_data:
            fn = item.get("file_name", "")
            if fn in name_corrections:
                item["drug_name"] = name_corrections[fn]
    
    for item in ek_bilgi:
        fn = item.get("file_name", "")
        if fn in name_corrections:
            item["drug_name"] = name_corrections[fn]
    
    return fixes


# ================================================================
# ADIM 2: BÖLÜM 1→2 SIZINTI TEMİZLİĞİ
# ================================================================

def _extract_section_title(text):
    """
    Bölüm 2 içeriğinin ilk satırından section_title çıkar.
    Örn: '2. DRUG kullanmadan önce dikkat edilmesi gerekenler'
    """
    if not text:
        return None
    # İlk satır veya "gerekenler" ile biten kısmı al
    m = re.search(r'^(.*?gerekenler\??)', text, re.IGNORECASE | re.DOTALL)
    if m:
        title = m.group(1).replace('\n', ' ').strip()
        # Çok uzunsa (>120 char) ilk satırı al
        if len(title) > 120:
            title = text.split('\n')[0].strip()
        return title
    # Fallback: ilk satır
    first_line = text.split('\n')[0].strip()
    return first_line if len(first_line) > 5 else None


def fix_bolum1_leakage(bolum1, bolum2):
    """
    Bölüm 1 içeriğinde Bölüm 2 başlığı varsa, 
    Bölüm 2 kısmını Bölüm 1'den kes.
    Eğer Bölüm 2 boşsa, kesilen kısmı Bölüm 2'ye ekle.
    
    Çok katmanlı strateji:
      Tier 1: Standart "2. DRUG kullanmadan/kullanmaya başlamadan önce" (DOTALL)
      Tier 2: OCR-tolerant regex (6nce, iince, itnce, ёnce, vb.)
      Tier 3: "KULLANMAYINIZ" anchor → geriye "2." ara
    """
    fixes = {"leakage_trimmed": 0, "bolum2_filled": 0, "section_title_set": 0}
    
    # file_name → bolum2 item mapping
    b2_map = {item["file_name"]: item for item in bolum2}
    
    # Tier 1: Standart regex - DOTALL ile (satır atlama desteği)
    TIER1_REGEX = re.compile(
        r'2\s*[\.\)\-]\s*\S+[\s\S]*?'
        r'(?:kullanmadan|kullanmaya\s*ba[şs]lamadan)\s*[öo]nce',
        re.IGNORECASE
    )
    
    # Tier 2: OCR-tolerant - "kul*madan/an" + OCR "önce" varyantları
    TIER2_REGEX = re.compile(
        r'2\s*[\.\)\-\s]\s*\S+[\s\S]{1,300}?'
        r'kul\S{0,8}[dm]\S?[dn]\s+\S{0,5}[öo6iё][iıü]?n?ce',
        re.IGNORECASE
    )
    
    # Tier 3: "KULLANMAYINIZ" varyantları (anchor olarak)
    KULLANMAYINIZ_VARIANTS = re.compile(
        r'KULLANMAYINIZ|KIJLLANMAYINIZ|KULLAI.?MAYINI?Z|'
        r'KULLAI.?IMAYI.?[MN]|KULLATIVI?AYI|KI\]?LLAN?MA|'
        r'KULLAI\S{0,4}MAYI\S{0,3}Z',
        re.IGNORECASE
    )
    
    # Tier 4: "gerekenler" varyantı anchor → geriye "2." ara
    GEREKENLER_REGEX = re.compile(
        r'di\S{1,6}\s+edil\S{0,6}\s+g\S?erek\S*ler',
        re.IGNORECASE
    )
    
    for item in bolum1:
        content = item.get("content", "") or ""
        fn = item.get("file_name", "")
        
        if not content:
            continue
        
        match_pos = None
        tier_used = None
        
        # Tier 1: Standart (DOTALL)
        m = TIER1_REGEX.search(content)
        if m:
            match_pos = m.start()
            tier_used = "tier1"
        
        # Tier 2: OCR-tolerant (sadece B2 boşsa dene)
        if match_pos is None:
            b2_item = b2_map.get(fn)
            if b2_item and not b2_item.get("content"):
                m = TIER2_REGEX.search(content)
                if m:
                    match_pos = m.start()
                    tier_used = "tier2"
        
        # Tier 3: KULLANMAYINIZ anchor (sadece B2 boşsa)
        if match_pos is None:
            b2_item = b2_map.get(fn)
            if b2_item and not b2_item.get("content"):
                m_k = KULLANMAYINIZ_VARIANTS.search(content)
                if m_k:
                    # Geriye doğru "2." veya "2\n" ara (max 500 char geriye)
                    search_start = max(0, m_k.start() - 500)
                    before = content[search_start:m_k.start()]
                    # Son "2." veya "2 " satır başını bul
                    positions = []
                    for mm in re.finditer(r'(?:^|\n)\s*2[\.\s\)\-]', before):
                        positions.append(search_start + mm.start())
                    if positions:
                        # En son "2." pozisyonunu al (B2 başlığına en yakın)
                        raw_pos = positions[-1]
                        # \n atla
                        if content[raw_pos] == '\n':
                            raw_pos += 1
                        match_pos = raw_pos
                        tier_used = "tier3"
        
        # Tier 4: "gerekenler" anchor → geriye "2." ara (sadece B2 boşsa)
        if match_pos is None:
            b2_item = b2_map.get(fn)
            if b2_item and not b2_item.get("content"):
                m_g = GEREKENLER_REGEX.search(content)
                if m_g:
                    search_start = max(0, m_g.start() - 500)
                    before = content[search_start:m_g.start()]
                    positions = []
                    for mm in re.finditer(r'(?:^|\n)\s*2[\.\s\)\-]', before):
                        positions.append(search_start + mm.start())
                    if positions:
                        raw_pos = positions[-1]
                        if content[raw_pos] == '\n':
                            raw_pos += 1
                        match_pos = raw_pos
                        tier_used = "tier4"
        
        if match_pos is not None:
            # Bölüm 2 başlığından itibaren kes
            leaking_content = content[match_pos:].strip()
            item["content"] = content[:match_pos].strip()
            fixes["leakage_trimmed"] += 1
            
            # Eğer Bölüm 2 boşsa, sızan kısmı oraya taşı + section_title ayarla
            b2_item = b2_map.get(fn)
            if b2_item and not b2_item.get("content"):
                b2_item["content"] = leaking_content
                fixes["bolum2_filled"] += 1
                
                # section_title çıkar
                title = _extract_section_title(leaking_content)
                if title and not b2_item.get("section_title"):
                    b2_item["section_title"] = title
                    fixes["section_title_set"] += 1
    
    return fixes


# ================================================================
# ADIM 3: USAGE_ROUTE NORMALİZASYONU
# ================================================================

ROUTE_NORMALIZATIONS = {
    # Ağızdan
    r'a[ğg][ıi]zdan\s*al[ıi]n[ıi]r\.?': "Ağızdan alınır.",
    r'a[ğg][ıi]z\s*yolu\s*ile\s*kullan[ıi]l[ıi]r\.?': "Ağız yolu ile kullanılır.",
    r'oral\s*yoldan\s*(?:al[ıi]n[ıi]r|kullan[ıi]l[ıi]r)\.?': "Ağızdan alınır.",
    
    # Damar
    r'damar\s*i[çc]ine\s*uygulan[ıi]r\.?': "Damar içine uygulanır.",
    r'damar\s*yolu(?:yla|ndan)\s*(?:uygulan[ıi]r|kullan[ıi]l[ıi]r)\.?': "Damar yoluyla uygulanır.",
    r'intraven[öo]z\s*(?:yoldan\s*)?(?:uygulan[ıi]r|kullan[ıi]l[ıi]r)\.?': "Damar içine uygulanır.",
    
    # Kas
    r'kas\s*i[çc]ine\s*(?:uygulan[ıi]r|enjekte\s*edilir)\.?': "Kas içine uygulanır.",
    r'intram[üu]sk[üu]ler\s*(?:yoldan\s*)?(?:uygulan[ıi]r|enjekte)\.?': "Kas içine uygulanır.",
    
    # Deri altı
    r'deri\s*alt[ıi]na\s*(?:uygulan[ıi]r|enjekte)\.?': "Deri altına uygulanır.",
    r'subkutan\s*(?:yoldan\s*)?(?:uygulan[ıi]r|enjekte)\.?': "Deri altına uygulanır.",
    
    # Cilt/Deri üzerine
    r'cilt\s*[üu]zerine\s*(?:uygulan[ıi]r|s[üu]r[üu]l[üu]r)\.?': "Cilt üzerine uygulanır.",
    r'deri\s*[üu]zerine\s*(?:uygulan[ıi]r|s[üu]r[üu]l[üu]r)\.?': "Deri üzerine uygulanır.",
    
    # Haricen
    r'haricen\s*(?:uygulan[ıi]r|kullan[ıi]l[ıi]r)\.?': "Haricen uygulanır.",
    
    # Göz
    r'g[öo]ze\s*(?:damla|uygulan[ıi]r).*': "Göze uygulanır.",
    
    # İnhalasyon
    r'a[ğg][ıi]zdan\s*solunarak\s*kullan[ıi]l[ıi]r\.?': "Ağızdan solunarak kullanılır.",
    r'inhalasyon\s*yolu(?:yla|ile)\s*(?:kullan[ıi]l[ıi]r|uygulan[ıi]r)\.?': "İnhalasyon yoluyla kullanılır.",
    
    # Burun
    r'burun\s*(?:yoluyla|i[çc]ine)\s*(?:uygulan[ıi]r|kullan[ıi]l[ıi]r)\.?': "Burun yoluyla uygulanır.",
    
    # Rektal
    r'rektal\s*yoldan\s*(?:kullan[ıi]l[ıi]r|uygulan[ıi]r)\.?': "Rektal yoldan uygulanır.",
    
    # Vajinal
    r'vajinal\s*yoldan\s*(?:kullan[ıi]l[ıi]r|uygulan[ıi]r)\.?': "Vajinal yoldan uygulanır.",
}


def normalize_usage_route(ozet):
    """
    Kullanım yollarını standart formlara normalize et.
    - Yazım farklarını birleştir
    - "kullanılır." → düzgün cümleye çevir
    - Cümle sonuna nokta ekle
    """
    fixes = {"normalized": 0, "bare_cleaned": 0}
    
    for item in ozet:
        route = item.get("usage_route")
        if not route:
            continue
        
        original = route
        
        # Normalize et
        matched = False
        for pattern, normalized in ROUTE_NORMALIZATIONS.items():
            if re.match(pattern, route.strip(), re.IGNORECASE):
                item["usage_route"] = normalized
                matched = True
                fixes["normalized"] += 1
                break
        
        if not matched:
            # "kullanılır." tek başına → anlamsız, None yap
            if re.match(r'^kullan[ıi]l[ıi]r\.?\s*$', route.strip(), re.IGNORECASE):
                item["usage_route"] = None
                fixes["bare_cleaned"] += 1
            else:
                # Nokta yoksa ekle
                route = route.strip()
                if route and not route.endswith('.'):
                    item["usage_route"] = route + '.'
    
    return fixes


# ================================================================
# ADIM 3B: USAGE_ROUTE BOŞ OLANLARI DOLDUR (Bölüm3/1/İsim fallback)
# ================================================================

# İçerikten route bulmak için kullanılan geniş pattern seti
CONTENT_ROUTE_PATTERNS = [
    (r'a[ğg][ıi]zdan\s*(?:al[ıi]n[ıi]r|kullan[ıi]l[ıi]r)', "Ağızdan alınır."),
    (r'a[ğg][ıi]z\s*yolu\s*ile\s*kullan', "Ağız yolu ile kullanılır."),
    (r'oral\s*yoldan', "Ağızdan alınır."),
    (r'damar\s*i[çc]ine\s*(?:uygulan|veril)', "Damar içine uygulanır."),
    (r'damar\s*yolu(?:yla|ndan)', "Damar yoluyla uygulanır."),
    (r'intraven[öo]z\s*(?:yoldan|olarak)?\s*(?:inf[üu]zyon|enjeksiyon|uygulan|kullan)', "Damar içine uygulanır."),
    (r'kas\s*i[çc]ine\s*(?:uygulan|enjekte)', "Kas içine uygulanır."),
    (r'intram[üu]sk[üu]ler', "Kas içine uygulanır."),
    (r'deri\s*alt[ıi]na\s*(?:uygulan|enjekte)', "Deri altına uygulanır."),
    (r'subkutan', "Deri altına uygulanır."),
    (r'deri\s*[üu]zerine\s*(?:uygulan|s[üu]r[üu]l)', "Deri üzerine uygulanır."),
    (r'cilt\s*[üu]zerine\s*(?:uygulan|s[üu]r[üu]l)', "Cilt üzerine uygulanır."),
    (r'topikal\s*(?:olarak|yoldan)?\s*(?:uygulan|kullan)', "Cilt üzerine uygulanır."),
    (r'haricen\s*(?:uygulan|kullan)', "Haricen uygulanır."),
    (r'g[öo]ze?\s*(?:damlatılarak|i[çc]ine)?\s*(?:uygulan|damla)', "Göze uygulanır."),
    (r'g[öo]z\s*damlas[ıi]', "Göze uygulanır."),
    (r'burun\s*(?:yoluyla|i[çc]ine|spreyi)', "Burun yoluyla uygulanır."),
    (r'nazal\s*(?:yoldan|olarak)', "Burun yoluyla uygulanır."),
    (r'inhalasyon\s*(?:yolu(?:yla|ile)|i[çc]in)', "İnhalasyon yoluyla kullanılır."),
    (r'a[ğg][ıi]zdan\s*solunarak', "Ağızdan solunarak kullanılır."),
    (r'solunarak\s*kullan', "Ağızdan solunarak kullanılır."),
    (r'rektal\s*(?:yoldan|olarak)', "Rektal yoldan uygulanır."),
    (r'makat(?:tan)?\s*(?:yoluyla|yoldan)', "Rektal yoldan uygulanır."),
    (r'vajinal\s*(?:yoldan|olarak)', "Vajinal yoldan uygulanır."),
    (r'kulak.*?(?:i[çc]ine|yoluyla)\s*(?:uygulan|damla)', "Kulağa uygulanır."),
    (r'a[ğg][ıi]z\s*i[çc]ine?\s*(?:uygulan|kullan|p[üu]sk[üu]rt)', "Ağız içine uygulanır."),
    (r'dil\s*alt[ıi]na?\s*(?:uygulan|konul|al[ıi]n)', "Dil altına uygulanır."),
    (r'toplardamar.*?(?:uygulan|kullan|veril)', "Damar içine uygulanır."),
    (r'(?:i\.?v\.?\s*|IV\s+)(?:inf[üu]zyon|enjeksiyon)', "Damar içine uygulanır."),
    (r'(?:i\.?m\.?\s*|IM\s+)enjeksiyon', "Kas içine uygulanır."),
    (r'(?:s\.?c\.?\s*|SC\s+)enjeksiyon', "Deri altına uygulanır."),
]

# İlaç adındaki form bilgisinden route çıkarma
FORM_TO_ROUTE = [
    (r'tablet|kaps[üu]l|draje|pastil|gran[üu]l|[şs]urup|s[üu]spansiyon|damla.*oral|oral.*damla|sa[şs]e|toz.*oral|oral.*toz|[çc]i[ğg]neme|oral\b', "Ağızdan alınır."),
    (r'(?:i\.?v\.?\s|IV\s|inf[üu]zyon|parenteral)', "Damar içine uygulanır."),
    (r'(?:i\.?m\.?\s|IM\s)', "Kas içine uygulanır."),
    (r'krem|merhem|jel\b|losyon|pomad', "Cilt üzerine uygulanır."),
    (r'g[öo]z\s*damla|oftalmik', "Göze uygulanır."),
    (r'inhaler|inhalasyon|inhale|nebul|diskair|turbuhaler|handihaler|aerosol', "İnhalasyon yoluyla kullanılır."),
    (r'burun|nazal|nasal', "Burun yoluyla uygulanır."),
    (r'fitil|supozituar|rektal', "Rektal yoldan uygulanır."),
    (r'vajinal|ov[üu]l', "Vajinal yoldan uygulanır."),
    (r'enjektabl|enjeksiyon|flakon', "Enjeksiyon yoluyla uygulanır."),
    (r'kulak\s*damla', "Kulağa uygulanır."),
    (r'sprey', "Sprey olarak uygulanır."),
]


def fill_missing_routes(ozet, bolum3, bolum1):
    """
    usage_route None olan ilaçlar için:
    1. Bölüm 3 içeriğinin ilk 1000 char'ında route pattern'i ara
    2. Bölüm 1 içeriğinin ilk 500 char'ında ara
    3. İlaç adındaki form bilgisinden çıkar (tablet→ağızdan, krem→cilt)
    """
    fixes = {"from_bolum3": 0, "from_bolum1": 0, "from_drug_name": 0}
    
    b3_map = {item["file_name"]: item.get("content", "") or "" for item in bolum3}
    b1_map = {item["file_name"]: item.get("content", "") or "" for item in bolum1}
    
    for item in ozet:
        if item.get("usage_route"):
            continue
        
        fn = item.get("file_name", "")
        drug = item.get("drug_name", "") or ""
        
        # 1. Bölüm 3'ten ara
        b3_content = b3_map.get(fn, "")[:1000]
        found = False
        for pattern, normalized in CONTENT_ROUTE_PATTERNS:
            if re.search(pattern, b3_content, re.IGNORECASE):
                item["usage_route"] = normalized
                fixes["from_bolum3"] += 1
                found = True
                break
        if found:
            continue
        
        # 2. Bölüm 1'den ara
        b1_content = b1_map.get(fn, "")[:500]
        for pattern, normalized in CONTENT_ROUTE_PATTERNS:
            if re.search(pattern, b1_content, re.IGNORECASE):
                item["usage_route"] = normalized
                fixes["from_bolum1"] += 1
                found = True
                break
        if found:
            continue
        
        # 3. İlaç adından çıkar
        # Hem dosya adını hem de çıkarılan drug_name'i kontrol et
        search_text = drug.lower() + " " + fn.lower()
        for pattern, normalized in FORM_TO_ROUTE:
            if re.search(pattern, search_text, re.IGNORECASE):
                item["usage_route"] = normalized
                fixes["from_drug_name"] += 1
                found = True
                break
    
    return fixes


# ================================================================
# ADIM 4: EK BİLGİ TEMİZLİĞİ
# ================================================================

def clean_extra_info(ek_bilgi):
    """
    Ruhsat sahibi ve üretici bilgilerini temizle:
    - OCR bozukluklarını düzelt
    - Adres/telefon bilgisini ayır
    - Çok kısa/anlamsız değerleri None yap
    """
    fixes = {"license_ocr_cleaned": 0, "manufacturer_ocr_cleaned": 0,
             "license_addr_trimmed": 0, "manufacturer_addr_trimmed": 0,
             "invalid_removed": 0}
    
    for item in ek_bilgi:
        for field in ["license_holder", "manufacturer"]:
            val = item.get(field)
            if not val:
                continue
            
            original = val
            
            # OCR temizliği
            if is_ocr_garbled(val):
                val = clean_ocr_light(val)
                fixes[f"{field.split('_')[0]}_ocr_cleaned"] += 1
            
            # Adres/telefon/faks kısmını kes - sadece firma adını tut
            # Firma adları genellikle "A.Ş.", "Ltd.", "San." ile biter
            addr_match = re.search(
                r'(A\.[ŞS]\.|Ltd\.\s*[ŞS]t[iı]\.|San\.\s*(?:ve)?\s*Tic\.\s*(?:A\.[ŞS]\.|Ltd\.))',
                val, re.IGNORECASE
            )
            if addr_match:
                # Firma adı sonrası adres bilgisini temizle
                company_end = addr_match.end()
                remaining = val[company_end:].strip()
                
                # Adres gibi görünüyorsa kes
                if remaining and re.match(r'^[\s,\.]*(?:\d|Cad|Sok|Mah|İst|Ank|İzm|Ümr|Geb|OSB|Tel|Faks|Adres)', remaining, re.IGNORECASE):
                    val = val[:company_end].strip()
                    fixes[f"{field.split('_')[0]}_addr_trimmed"] += 1
            
            # Çok kısa ya da anlamsız
            if len(val.strip()) < 3 or val.strip() in [':', 've', 'yok', '-']:
                val = None
                fixes["invalid_removed"] += 1
            
            # Trailing/leading noise
            if val:
                val = re.sub(r'^[\s:,\-\.]+', '', val)
                val = re.sub(r'[\s:,\-\.]+$', '', val)
                val = val.strip()
            
            item[field] = val if val else None
    
    return fixes


# ================================================================
# ADIM 5: BÖLÜM İÇERİK TEMİZLİĞİ
# ================================================================

def clean_section_contents(bolum_data, bolum_no):
    """
    Bölüm içeriklerini temizle:
    - Trailing boşluklar
    - Çoklu boş satırlar → tek boş satır
    - Sayfanın altındaki QR/barkod artıkları
    """
    fixes = {"whitespace_cleaned": 0, "qr_removed": 0}
    
    for item in bolum_data:
        content = item.get("content")
        if not content:
            continue
        
        original = content
        
        # Çoklu boş satırları temizle
        content = re.sub(r'\n\s*\n\s*\n+', '\n\n', content)
        
        # Her satırın sonundaki boşlukları temizle
        content = '\n'.join(line.rstrip() for line in content.split('\n'))
        
        # QR/barkod artıkları (20+ karakter uzunluğunda rakam-harf dizisi)
        content = re.sub(r'\n[A-Z0-9]{20,}\s*$', '', content)
        
        content = content.strip()
        
        if content != original:
            fixes["whitespace_cleaned"] += 1
        
        item["content"] = content if content else None
    
    return fixes


# ================================================================
# ADIM 6: UYARI METNİ TEMİZLİĞİ
# ================================================================

def clean_warning_texts(ozet):
    """
    Uyarı metinlerini temizle ve normalize et.
    - Đ → İ düzelt
    - Bullet noktalarını normalize et
    """
    fixes = {"ocr_fixed": 0}
    
    for item in ozet:
        warn = item.get("warning_text")
        if not warn:
            continue
        
        original = warn
        
        # OCR düzeltmeleri
        warn = warn.replace('Đ', 'İ')
        warn = warn.replace('đ', 'i')
        
        # Bullet normalize
        warn = re.sub(r'\s*[•·]\s*', ' • ', warn)
        
        # Çoklu boşluk
        warn = re.sub(r'\s+', ' ', warn).strip()
        
        if warn != original:
            fixes["ocr_fixed"] += 1
        
        item["warning_text"] = warn
    
    return fixes


# ================================================================
# ADIM 7: ETKİN MADDE TEMİZLİĞİ
# ================================================================

def clean_active_substances(ozet):
    """
    Etkin madde metinlerini temizle.
    - Çok uzun olanları kırp (ilk anlamlı cümleyi al)
    - Tekrarlayan kelime kalıplarını temizle
    """
    fixes = {"long_trimmed": 0}
    
    for item in ozet:
        active = item.get("active_substance")
        if not active:
            continue
        
        # 500 karakterden uzunsa, ilk cümleyi al
        if len(active) > 500:
            # İlk cümle sonunu bul
            sentences = re.split(r'(?<=[.\!])\s+', active)
            if sentences:
                # İlk 2-3 cümleyi al (genellikle etkin madde bilgisi burada)
                trimmed = '. '.join(sentences[:3])
                if not trimmed.endswith('.'):
                    trimmed += '.'
                item["active_substance"] = trimmed
                fixes["long_trimmed"] += 1
    
    return fixes


# ================================================================
# ADIM 8: YARDIMCI MADDE TEMİZLİĞİ
# ================================================================

def clean_excipients(ozet):
    """
    Yardımcı madde metinlerini temizle.
    """
    fixes = {"cleaned": 0}
    
    for item in ozet:
        exc = item.get("excipients")
        if not exc:
            continue
        
        original = exc
        
        # Çoklu boşluk
        exc = re.sub(r'\s+', ' ', exc).strip()
        
        # Trailing/leading noise
        exc = re.sub(r'^[\s:;\-•·,]+', '', exc)
        exc = re.sub(r'[\s:;\-•·,]+$', '', exc)
        
        if exc != original:
            fixes["cleaned"] += 1
        
        item["excipients"] = exc.strip() if exc.strip() else None
    
    return fixes


# ================================================================
# ANA PIPELINE
# ================================================================

def main():
    print("=" * 70)
    print("VERİ KALİTESİ POST-PROCESSING PİPELİNE")
    print("=" * 70)
    
    # Dosyaları yükle
    print("\n[1/9] Dosyalar yükleniyor...")
    ozet = load_json("kt_ozet.json")
    bolum1 = load_json("kt_bolum1.json")
    bolum2 = load_json("kt_bolum2.json")
    bolum3 = load_json("kt_bolum3.json")
    bolum4 = load_json("kt_bolum4.json")
    bolum5 = load_json("kt_bolum5.json")
    ek_bilgi = load_json("kt_ek_bilgi.json")
    
    bolum_files = {"1": bolum1, "2": bolum2, "3": bolum3, "4": bolum4, "5": bolum5}
    
    # Yedekle
    print("[2/9] Yedekleme yapılıyor...")
    for name in ["kt_ozet.json", "kt_bolum1.json", "kt_bolum2.json", "kt_bolum3.json",
                  "kt_bolum4.json", "kt_bolum5.json", "kt_ek_bilgi.json"]:
        backup_json(name)
    
    # ADIM 1: İlaç adı temizliği
    print("\n[3/10] İlaç adı temizliği...")
    drug_fixes = fix_drug_names(ozet, bolum_files, ek_bilgi)
    for key, count in drug_fixes.items():
        if count > 0:
            print(f"  ✓ {key}: {count}")
    
    # ADIM 2: Bölüm 1→2 sızıntı temizliği
    print("\n[4/10] Bölüm 1→2 sızıntı temizliği...")
    leak_fixes = fix_bolum1_leakage(bolum1, bolum2)
    for key, count in leak_fixes.items():
        if count > 0:
            print(f"  ✓ {key}: {count}")
    
    # ADIM 3: Kullanım yolu normalizasyonu
    print("\n[5/10] Kullanım yolu normalizasyonu...")
    route_fixes = normalize_usage_route(ozet)
    for key, count in route_fixes.items():
        if count > 0:
            print(f"  ✓ {key}: {count}")
    
    # ADIM 3B: Boş usage_route'ları doldur
    print("\n[6/10] Boş kullanım yollarını doldurma (Bölüm3/1/İsim fallback)...")
    fill_fixes = fill_missing_routes(ozet, bolum3, bolum1)
    for key, count in fill_fixes.items():
        if count > 0:
            print(f"  ✓ {key}: {count}")
    
    # ADIM 4: Ek bilgi temizliği
    print("\n[7/10] Ek bilgi temizliği...")
    extra_fixes = clean_extra_info(ek_bilgi)
    for key, count in extra_fixes.items():
        if count > 0:
            print(f"  ✓ {key}: {count}")
    
    # ADIM 5: Bölüm içerik temizliği
    print("\n[8/10] Bölüm içerik temizliği...")
    for sec_num, data in bolum_files.items():
        sec_fixes = clean_section_contents(data, sec_num)
        total_fixed = sum(sec_fixes.values())
        if total_fixed > 0:
            print(f"  ✓ Bölüm {sec_num}: {total_fixed} düzeltme")
    
    # ADIM 6: Uyarı metni temizliği
    print("\n[9/10] Uyarı/etkin madde/yardımcı madde temizliği...")
    warn_fixes = clean_warning_texts(ozet)
    if warn_fixes["ocr_fixed"]:
        print(f"  ✓ Uyarı OCR düzeltme: {warn_fixes['ocr_fixed']}")
    
    active_fixes = clean_active_substances(ozet)
    if active_fixes["long_trimmed"]:
        print(f"  ✓ Etkin madde kırpma: {active_fixes['long_trimmed']}")
    
    exc_fixes = clean_excipients(ozet)
    if exc_fixes["cleaned"]:
        print(f"  ✓ Yardımcı madde temizleme: {exc_fixes['cleaned']}")
    
    # KAYDETME
    print("\n[10/10] Temizlenmiş dosyalar kaydediliyor...")
    save_json("kt_ozet.json", ozet)
    save_json("kt_bolum1.json", bolum1)
    save_json("kt_bolum2.json", bolum2)
    save_json("kt_bolum3.json", bolum3)
    save_json("kt_bolum4.json", bolum4)
    save_json("kt_bolum5.json", bolum5)
    save_json("kt_ek_bilgi.json", ek_bilgi)
    
    # =================================================================
    # NİHAİ KALİTE RAPORU
    # =================================================================
    print("\n" + "=" * 70)
    print("NİHAİ KALİTE RAPORU")
    print("=" * 70)
    
    total = len(ozet)
    
    # --- İlaç Adı ---
    empty_names = sum(1 for item in ozet if not item.get("drug_name") or len(item["drug_name"].strip()) < 3)
    ocr_names = sum(1 for item in ozet if is_ocr_garbled(item.get("drug_name", "")))
    
    # --- Kullanım Yolu ---
    route_filled = sum(1 for item in ozet if item.get("usage_route"))
    route_none = total - route_filled
    routes = Counter(item.get("usage_route") for item in ozet if item.get("usage_route"))
    
    # --- Etkin Madde ---
    active_filled = sum(1 for item in ozet if item.get("active_substance"))
    
    # --- Yardımcı Madde ---
    exc_filled = sum(1 for item in ozet if item.get("excipients"))
    
    # --- Uyarı Metni ---
    warn_filled = sum(1 for item in ozet if item.get("warning_text"))
    
    # --- Bölüm İçerikleri ---
    bolum_stats = {}
    for sec_num, data in bolum_files.items():
        filled = sum(1 for item in data if item.get("content"))
        bolum_stats[sec_num] = filled
    
    # --- Ek Bilgi ---
    lic_filled = sum(1 for item in ek_bilgi if item.get("license_holder"))
    mfr_filled = sum(1 for item in ek_bilgi if item.get("manufacturer"))
    
    # --- Bölüm 1→2 sızıntı ---
    remaining_leaks = sum(1 for item in bolum1 
                         if re.search(r'2\s*\.\s*\S+.*?(?:kullanmadan|kullanmaya\s*ba[şs]lamadan)\s*[öo]nce', 
                                     item.get("content", "") or "", re.IGNORECASE))
    
    # SKOR TABLOSU
    scores = {
        "İlaç Adı":        (total - empty_names - ocr_names, total),
        "Etkin Madde":      (active_filled, total),
        "Kullanım Yolu":    (route_filled, total),
        "Yardımcı Madde":   (exc_filled, total),
        "Uyarı Metni":      (warn_filled, total),
        "Bölüm 1 İçerik":  (bolum_stats.get("1", 0), total),
        "Bölüm 2 İçerik":  (bolum_stats.get("2", 0), total),
        "Bölüm 3 İçerik":  (bolum_stats.get("3", 0), total),
        "Bölüm 4 İçerik":  (bolum_stats.get("4", 0), total),
        "Bölüm 5 İçerik":  (bolum_stats.get("5", 0), total),
        "Ruhsat Sahibi":    (lic_filled, total),
        "Üretici":          (mfr_filled, total),
    }
    
    print(f"\n  Toplam İlaç: {total}")
    print(f"  Bölüm 1→2 Sızıntı: {remaining_leaks}\n")
    
    total_score_sum = 0
    for name, (filled, tot) in scores.items():
        pct = 100 * filled / tot if tot > 0 else 0
        total_score_sum += pct
        bar_len = 40
        bar = "█" * int(pct / 100 * bar_len) + "░" * (bar_len - int(pct / 100 * bar_len))
        indicator = "✓" if pct >= 95 else "△" if pct >= 80 else "✗"
        print(f"  {indicator} {name:<20}: {filled:>4}/{tot} ({pct:>5.1f}%) {bar}")
    
    overall = total_score_sum / len(scores)
    print(f"\n  ══════════════════════════════════════")
    print(f"  GENEL KALİTE SKORU: {overall:.1f}%")
    print(f"  ══════════════════════════════════════")
    
    # Kullanım yolu dağılımı
    print(f"\n  Kullanım Yolu Dağılımı ({route_filled} dolu, {route_none} boş):")
    for route, count in routes.most_common(15):
        print(f"    {count:>4}x | {route}")
    if route_none > 0:
        print(f"    {route_none:>4}x | (boş)")
    
    # Düzeltme özeti
    total_fixes = (
        sum(drug_fixes.values()) + 
        sum(leak_fixes.values()) + 
        sum(route_fixes.values()) + 
        sum(fill_fixes.values()) +
        sum(extra_fixes.values()) +
        warn_fixes["ocr_fixed"] +
        active_fixes["long_trimmed"] +
        exc_fixes["cleaned"]
    )
    print(f"\n  Toplam Düzeltme: {total_fixes}")
    print(f"  Yedekler: analiz_sonuclari/*_backup.json")
    
    # Raporu güncelle
    hatali = load_json("hatali_dosyalar.json")
    report = {
        "total_files": 3165,
        "successful": total,
        "failed": len(hatali),
        "success_rate": f"{(total/3165)*100:.2f}%",
        "quality_score": f"{overall:.1f}%",
        "usage_route_coverage": f"{route_filled}/{total} ({100*route_filled/total:.1f}%)",
        "license_holder_coverage": f"{lic_filled}/{total} ({100*lic_filled/total:.1f}%)",
        "active_substance_coverage": f"{active_filled}/{total} ({100*active_filled/total:.1f}%)",
    }
    save_json("rapor.json", report)
    print(f"\n  Rapor güncellendi: analiz_sonuclari/rapor.json")


if __name__ == "__main__":
    main()
