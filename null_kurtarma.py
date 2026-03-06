"""
NULL KURTARMA DENEMESİ
======================
Null olan bölümleri PDF'den tekrar okuyup doldurmaya çalış.
Önce analiz, sonra kurtarma.
"""
import json
import os
import re
import pdfplumber
from collections import defaultdict

ANALIZ_DIR = "analiz_sonuclari"
PDF_DIR = "data/kt"

def load_json(name):
    path = os.path.join(ANALIZ_DIR, name)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(name, data):
    path = os.path.join(ANALIZ_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def extract_text(pdf_path):
    """PDF'den metin çıkar"""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = ""
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text += t + "\n"
        return text
    except:
        return ""

# ================================================================
# Null olan dosyaları tespit et
# ================================================================
ozet = load_json("kt_ozet.json")
ek_bilgi = load_json("kt_ek_bilgi.json")
bolum_data = {}
for i in range(1, 6):
    bolum_data[i] = load_json(f"kt_bolum{i}.json")

ozet_map = {item["file_name"]: item for item in ozet}
ek_map = {item["file_name"]: item for item in ek_bilgi}
bolum_maps = {}
for i in range(1, 6):
    bolum_maps[i] = {item["file_name"]: item for item in bolum_data[i]}

# Null bölüm content olanları topla
null_content_files = defaultdict(list)  # file -> [bolum_no, ...]
for fn in ozet_map:
    for i in range(1, 6):
        item = bolum_maps[i].get(fn, {})
        if not item.get("content"):
            null_content_files[fn].append(i)

print(f"Null content olan dosya sayisi: {len(null_content_files)}")
for fn in sorted(null_content_files.keys())[:5]:
    print(f"  {fn[:60]} -> B{null_content_files[fn]}")

# ================================================================
# OCR-tolerant bölüm ayırıcı regex'ler
# ================================================================
# Standart bölüm başlıkları:
# 1. X nedir ve ne için kullanılır?
# 2. X kullanmadan önce dikkat edilmesi gerekenler
# 3. X nasıl kullanılır?
# 4. Olası yan etkiler nelerdir?
# 5. X'in saklanması

BOLUM_PATTERNS = {
    1: re.compile(r'(1\s*[\.\)\-]\s*.{5,120}?(?:nedir|ne\s*i[cçg]in|kullan[ıi]l[ıi]r))', re.IGNORECASE),
    2: re.compile(r'(2\s*[\.\)\-]\s*.{5,120}?(?:kullan\S{0,6}(?:dan|maya)|dikkat|[öo6]nce|gerekenler))', re.IGNORECASE),
    3: re.compile(r'(3\s*[\.\)\-]\s*.{5,120}?(?:nas[ıi]l\s*kullan|kullan[ıi]l[ıi]r|kullanmal))', re.IGNORECASE),
    4: re.compile(r'(4\s*[\.\)\-]\s*.{5,120}?(?:yan\s*etki|olas[ıi]|etkiler))', re.IGNORECASE),
    5: re.compile(r'(5\s*[\.\)\-]\s*.{5,120}?(?:saklan|muhafaza|ambalaj|depolama))', re.IGNORECASE),
}

# OCR-tolerant versiyonlar
BOLUM_PATTERNS_OCR = {
    1: re.compile(r'(1\s*[\.\)\-\s]\s*\S.{3,120}?(?:ned[il1]r|ne\s+\S{1,5}[iı][nrl]\s+kull))', re.IGNORECASE),
    2: re.compile(r'(2\s*[\.\)\-\s]\s*\S.{3,120}?(?:kul\S{2,8}[dn]\S{0,3}\s+\S{0,5}[öo6]\S?n|gere[ck]\S*ler|dikk\S{1,4}\s+ed))', re.IGNORECASE),
    3: re.compile(r'(3\s*[\.\)\-\s]\s*\S.{3,120}?(?:nas[ıi1]\S?\s+kull|kull\S{2,8}[ıi]r|kullanmal))', re.IGNORECASE),
    4: re.compile(r'(4\s*[\.\)\-\s]\s*\S.{3,120}?(?:yan\s+etk|olas[ıi1]|etkiler|etk[il1]ler))', re.IGNORECASE),
    5: re.compile(r'(5\s*[\.\)\-\s]\s*\S.{3,120}?(?:sakla|muhaf|ambala|depola))', re.IGNORECASE),
}

# Bölüm sonu ayırıcılar: bir sonraki bölüm başlangıcı
BOLUM_START = {}
for i in range(1, 6):
    BOLUM_START[i] = re.compile(rf'{i}\s*[\.\)\-\s]', re.IGNORECASE)

def find_section_boundaries(text):
    """Metinde tüm bölüm başlangıç pozisyonlarını bul"""
    positions = {}
    
    for bolum_no in range(1, 6):
        # Önce standart pattern dene
        m = BOLUM_PATTERNS[bolum_no].search(text)
        if m:
            positions[bolum_no] = m.start()
            continue
        # OCR pattern dene
        m = BOLUM_PATTERNS_OCR[bolum_no].search(text)
        if m:
            positions[bolum_no] = m.start()
    
    return positions

def extract_section(text, bolum_no, positions):
    """Belirli bir bölümün içeriğini çıkar"""
    if bolum_no not in positions:
        return None, None
    
    start = positions[bolum_no]
    
    # Son: bir sonraki bölümün başlangıcı veya metin sonu
    end = len(text)
    for next_b in range(bolum_no + 1, 7):
        if next_b in positions and positions[next_b] > start:
            end = positions[next_b]
            break
    
    content = text[start:end].strip()
    if not content or len(content) < 10:
        return None, None
    
    # Başlık: ilk satır
    first_line = content.split('\n')[0].strip()
    title = first_line[:200] if first_line else ""
    
    return title, content

# ================================================================
# Kurtarma denemesi
# ================================================================
print(f"\n{'='*70}")
print("  NULL BOLUM KURTARMA DENEMESİ")
print(f"{'='*70}")

recovered = 0
failed = 0
failed_files = []

for fn, null_bolums in sorted(null_content_files.items()):
    pdf_path = os.path.join(PDF_DIR, fn)
    if not os.path.exists(pdf_path):
        print(f"  [!] PDF bulunamadi: {fn}")
        failed += len(null_bolums)
        for nb in null_bolums:
            failed_files.append((fn, nb, "PDF yok"))
        continue
    
    text = extract_text(pdf_path)
    if not text or len(text) < 50:
        for nb in null_bolums:
            failed_files.append((fn, nb, f"Metin cikarilamadi (len={len(text)})"))
        failed += len(null_bolums)
        continue
    
    positions = find_section_boundaries(text)
    
    for bolum_no in null_bolums:
        title, content = extract_section(text, bolum_no, positions)
        
        if content and len(content) >= 20:
            # Kurtarıldı!
            item = bolum_maps[bolum_no].get(fn)
            if item:
                item["content"] = content
                item["section_title"] = title or ""
                recovered += 1
                print(f"  [OK] B{bolum_no} kurtarildi: {fn[:55]} ({len(content)} char)")
            else:
                failed_files.append((fn, bolum_no, "JSON'da kayit yok"))
                failed += 1
        else:
            # PDF'de de bulunamadı
            # Debug: metnin ne kadarını görelim
            has_marker = bool(re.search(rf'{bolum_no}\s*[\.\)\-]', text))
            failed_files.append((fn, bolum_no, f"PDF'de B{bolum_no} bulunamadi (marker={'var' if has_marker else 'yok'}, text_len={len(text)})"))
            failed += 1

print(f"\n  Sonuc: {recovered} kurtarildi, {failed} basarisiz")

# ================================================================
# Özet alanları da deneyelim
# ================================================================
print(f"\n{'='*70}")
print("  NULL OZET ALANLARI KURTARMA DENEMESİ")
print(f"{'='*70}")

# Kullanım yolu (usage_route), etkin madde, yardımcı madde, uyarı
# Bunlar genelde Bölüm 1'den çıkarılıyor, ayrı bi' regex deneyelim

ROUTE_PATTERNS = [
    re.compile(r'(?:oral|a[gğ][ıi]z)\s*(?:yol|dan|ile|yolla)', re.IGNORECASE),
    re.compile(r'a[gğ][ıi]zdan\s+(?:al[ıi]n[ıi]r|kullan[ıi]l[ıi]r)', re.IGNORECASE),
    re.compile(r'(?:intravenöz|i\.v\.|iv\s)', re.IGNORECASE),
    re.compile(r'(?:intramüsküler|i\.m\.|im\s)', re.IGNORECASE),
    re.compile(r'(?:subkutan|s\.c\.|sc\s)', re.IGNORECASE),
    re.compile(r'(?:inhalasyon|inhaler)', re.IGNORECASE),
    re.compile(r'(?:topikal|cilt|deri)\s*(?:üzer|e\s+uygulan)', re.IGNORECASE),
    re.compile(r'(?:rektal|vajinal|nazal|oküler|oftalmik)', re.IGNORECASE),
    re.compile(r'(?:tablet|kapsül|draje|film\s+kapl|şurup|süspansiyon|damla)', re.IGNORECASE),
    re.compile(r'(?:enjeksiyon|infüzyon|flakon|ampul)', re.IGNORECASE),
    re.compile(r'(?:krem|merhem|jel|losyon|solüsyon)', re.IGNORECASE),
]

ROUTE_MAP = {
    "oral": ["oral", "ağızdan", "ağız", "tablet", "kapsül", "draje", "film kapl", "şurup", "süspansiyon", "damla", "saşe", "granül", "toz"],
    "parenteral": ["intravenöz", "i.v.", "intramüsküler", "i.m.", "subkutan", "s.c.", "enjeksiyon", "infüzyon", "flakon", "ampul"],
    "inhalasyon": ["inhalasyon", "inhaler", "nebül"],
    "topikal": ["topikal", "cilt", "deri", "krem", "merhem", "jel", "losyon"],
    "rektal": ["rektal", "supozituvar"],
    "vajinal": ["vajinal"],
    "nazal": ["nazal", "burun"],
    "oküler": ["oküler", "oftalmik", "göz"],
}

def guess_route_from_filename(fn):
    """Dosya adından kullanım yolunu tahmin et"""
    fn_lower = fn.lower()
    for route, keywords in ROUTE_MAP.items():
        for kw in keywords:
            if kw in fn_lower:
                return route
    return None

def guess_route_from_text(text):
    """Metinden kullanım yolunu tahmin et"""
    text_lower = text[:3000].lower()  # İlk 3000 char yeterli
    for route, keywords in ROUTE_MAP.items():
        for kw in keywords:
            if kw in text_lower:
                return route
    return None

# usage_route null olanları doldur
ozet_null_route = [item for item in ozet if not item.get("usage_route")]
route_recovered = 0
for item in ozet_null_route:
    fn = item["file_name"]
    # Dosya adından tahmin et
    route = guess_route_from_filename(fn)
    if not route:
        # PDF'den oku
        pdf_path = os.path.join(PDF_DIR, fn)
        if os.path.exists(pdf_path):
            text = extract_text(pdf_path)
            route = guess_route_from_text(text)
    
    if route:
        item["usage_route"] = route
        route_recovered += 1

print(f"  usage_route kurtarilan: {route_recovered}/{len(ozet_null_route)}")

# active_substance null olanları doldur
ETKIN_MADDE_RE = re.compile(
    r'(?:etkin\s*madde|aktif\s*madde|her\s+(?:bir\s+)?(?:tablet|kapsül|ml|film|draje|ampul|flakon|saşe|ölçek)\S*\s+)[\s:]*(.{10,300}?)(?:\n|Yard[ıi]mc[ıi])',
    re.IGNORECASE | re.DOTALL
)

ozet_null_active = [item for item in ozet if not item.get("active_substance")]
active_recovered = 0
for item in ozet_null_active:
    fn = item["file_name"]
    pdf_path = os.path.join(PDF_DIR, fn)
    if os.path.exists(pdf_path):
        text = extract_text(pdf_path)
        m = ETKIN_MADDE_RE.search(text)
        if m:
            val = m.group(1).strip()
            if len(val) >= 5:
                item["active_substance"] = val
                active_recovered += 1

print(f"  active_substance kurtarilan: {active_recovered}/{len(ozet_null_active)}")

# excipients null olanları doldur
YARDIMCI_RE = re.compile(
    r'(?:yard[ıi]mc[ıi]\s*madde\S*|yardlmcl\s*madde\S*)[\s:]*(.{10,1500}?)(?:\n\s*(?:Bu\s+ilac|Bu\s+t[ıi]bbi|Doktor|Eger|Lütfen|Dikkat|Bu\s+prospektüs)|$)',
    re.IGNORECASE | re.DOTALL
)

ozet_null_excip = [item for item in ozet if not item.get("excipients")]
excip_recovered = 0
for item in ozet_null_excip:
    fn = item["file_name"]
    pdf_path = os.path.join(PDF_DIR, fn)
    if os.path.exists(pdf_path):
        text = extract_text(pdf_path)
        m = YARDIMCI_RE.search(text)
        if m:
            val = m.group(1).strip()
            if len(val) >= 5:
                item["excipients"] = val
                excip_recovered += 1

print(f"  excipients kurtarilan: {excip_recovered}/{len(ozet_null_excip)}")

# license_holder null olanları doldur
RUHSAT_RE = re.compile(
    r'(?:ruhsat\s*sahibi|ruhsat\s*_\s*sahibi|izin\s*sahibi)[\s:]*(.{10,300}?)(?:\n\s*(?:Üret|imal|Imal|Title|$))',
    re.IGNORECASE | re.DOTALL
)

ek_null_license = [item for item in ek_bilgi if not item.get("license_holder")]
license_recovered = 0
for item in ek_null_license:
    fn = item["file_name"]
    pdf_path = os.path.join(PDF_DIR, fn)
    if os.path.exists(pdf_path):
        text = extract_text(pdf_path)
        m = RUHSAT_RE.search(text)
        if m:
            val = m.group(1).strip()
            if len(val) >= 5:
                item["license_holder"] = val
                license_recovered += 1

print(f"  license_holder kurtarilan: {license_recovered}/{len(ek_null_license)}")

# manufacturer null olanları doldur
URETICI_RE = re.compile(
    r'(?:üretici|imalatç[ıi]|üret[il1]c[il1]|imal|üretim\s*yeri)[\s:]*(.{10,300}?)(?:\n\s*(?:Ruhsat|Bu\s+k|Son\s+güncelleme|$))',
    re.IGNORECASE | re.DOTALL
)

ek_null_mfr = [item for item in ek_bilgi if not item.get("manufacturer")]
mfr_recovered = 0
for item in ek_null_mfr:
    fn = item["file_name"]
    pdf_path = os.path.join(PDF_DIR, fn)
    if os.path.exists(pdf_path):
        text = extract_text(pdf_path)
        m = URETICI_RE.search(text)
        if m:
            val = m.group(1).strip()
            if len(val) >= 5:
                item["manufacturer"] = val
                mfr_recovered += 1

print(f"  manufacturer kurtarilan: {mfr_recovered}/{len(ek_null_mfr)}")

# ================================================================
# KAYDET
# ================================================================
print(f"\n{'='*70}")
print("  KAYDET")
print(f"{'='*70}")

save_json("kt_ozet.json", ozet)
save_json("kt_ek_bilgi.json", ek_bilgi)
for i in range(1, 6):
    save_json(f"kt_bolum{i}.json", bolum_data[i])

print("  Tum dosyalar kaydedildi.")

# ================================================================
# OZET
# ================================================================
print(f"\n{'='*70}")
print("  KURTARMA SONUCU")
print(f"{'='*70}")

print(f"""
  Bolum icerikleri:
    Kurtarilan: {recovered}
    Basarisiz : {failed}
  
  Ozet alanlari:
    usage_route   : {route_recovered}/{len(ozet_null_route)} kurtarildi 
    active_subst  : {active_recovered}/{len(ozet_null_active)} kurtarildi
    excipients    : {excip_recovered}/{len(ozet_null_excip)} kurtarildi
    license_holder: {license_recovered}/{len(ek_null_license)} kurtarildi
    manufacturer  : {mfr_recovered}/{len(ek_null_mfr)} kurtarildi
""")

# Hala null kalanları listele
print("  Hala null kalan bolum icerik detaylari:")
still_null = [(fn, nb, reason) for fn, nb, reason in failed_files]
for fn, nb, reason in sorted(still_null)[:30]:
    print(f"    B{nb}: {fn[:55]} | {reason}")
if len(still_null) > 30:
    print(f"    ... ve {len(still_null)-30} tane daha")

# Hala null olan ana alanlar
print(f"\n  Hala null kalan ozet/ek alanlari:")
for field_name, field_key, dataset in [
    ("usage_route", "usage_route", ozet),
    ("active_substance", "active_substance", ozet),
    ("excipients", "excipients", ozet),
    ("license_holder", "license_holder", ek_bilgi),
    ("manufacturer", "manufacturer", ek_bilgi),
]:
    nulls = [item["file_name"] for item in dataset if not item.get(field_key)]
    if nulls:
        print(f"    {field_name}: {len(nulls)} hala null")
