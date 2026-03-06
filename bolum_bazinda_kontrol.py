"""
BOLUM BAZINDA PDF vs JSON KARAKTER KARSILASTIRMASI
===================================================
Her ilac icin, her bolumun:
  - PDF'deki gercek uzunlugu
  - JSON'daki uzunlugu
  - Yakalama orani (%)
"""
import json
import os
import re
import pdfplumber
from collections import defaultdict

ANALIZ_DIR = "analiz_sonuclari"
PDF_DIR = "data/kt"

def load_json(name):
    with open(os.path.join(ANALIZ_DIR, name), "r", encoding="utf-8") as f:
        return json.load(f)

def extract_text(pdf_path):
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

# Bolum baslık patternleri
SECTION_HEADERS = {
    1: re.compile(r'1\s*[\.\)\-]\s*\S.{5,100}?(?:nedir|ne\s+i[cçg]in|kullan[ıi]l[ıi]r)', re.IGNORECASE | re.DOTALL),
    2: re.compile(r'2\s*[\.\)\-]\s*\S.{5,100}?(?:kullan\S{0,8}[dm]\S{0,3}\s|dikkat|[öo]nce|gerekenler)', re.IGNORECASE | re.DOTALL),
    3: re.compile(r'3\s*[\.\)\-]\s*\S.{5,100}?(?:nas[ıi]l\s+kullan|kullan[ıi]l[ıi]r|kullanmal)', re.IGNORECASE | re.DOTALL),
    4: re.compile(r'4\s*[\.\)\-]\s*\S.{5,100}?(?:yan\s+etki|olas[ıi]|etkiler|istenmey)', re.IGNORECASE | re.DOTALL),
    5: re.compile(r'5\s*[\.\)\-]\s*\S.{5,100}?(?:saklan|muhafaza|ambalaj|depolama)', re.IGNORECASE | re.DOTALL),
}

EK_BILGI_RE = re.compile(r'(?:ruhsat\s*sahibi|izin\s*sahibi|üretici|imalatç|üretim\s*yeri|son\s+güncelleme)', re.IGNORECASE)

def find_content_start(text):
    m = re.search(r'[Bb]a[şs]l[ıi]klar[ıi]\s+yer\s+almaktad[ıi]r', text)
    if m:
        return m.end()
    m2 = re.search(r'[Bb]u\s+kullanma\s+talimat[ıi]', text)
    if m2:
        return m2.end()
    positions = []
    for i in range(1, 6):
        m = SECTION_HEADERS[i].search(text)
        if m:
            positions.append(m.end())
    if len(positions) >= 3:
        return max(positions)
    return 0

def find_pdf_section_lengths(text):
    """PDF'deki her bolumun gercek uzunlugunu bul"""
    content_start = find_content_start(text)
    search_text = text[content_start:]
    
    positions = {}
    for bolum_no in range(1, 6):
        m = SECTION_HEADERS[bolum_no].search(search_text)
        if m:
            positions[bolum_no] = content_start + m.start()
    
    # Ek bilgi
    if 5 in positions:
        after_b5 = text[positions[5]:]
        m = EK_BILGI_RE.search(after_b5)
        if m:
            positions[6] = positions[5] + m.start()
    elif positions:
        last_b = max(positions.keys())
        after = text[positions[last_b]:]
        m = EK_BILGI_RE.search(after)
        if m:
            positions[6] = positions[last_b] + m.start()
    
    # Her bolumun uzunlugunu hesapla
    lengths = {}
    for bolum_no in range(1, 6):
        if bolum_no not in positions:
            lengths[bolum_no] = 0
            continue
        start = positions[bolum_no]
        end = len(text)
        for next_b in range(bolum_no + 1, 7):
            if next_b in positions and positions[next_b] > start:
                end = positions[next_b]
                break
        lengths[bolum_no] = end - start
    
    return lengths

# ================================================================
# VERi YUKLE
# ================================================================
print("Veriler yukleniyor...")
ozet = load_json("kt_ozet.json")
bolum_maps = {}
for i in range(1, 6):
    data = load_json(f"kt_bolum{i}.json")
    bolum_maps[i] = {item["file_name"]: item for item in data}

all_files = sorted(set(item["file_name"] for item in ozet))
print(f"Toplam ilac: {len(all_files)}")

# ================================================================
# HER ILAC ICIN BOLUM BAZINDA KARSILASTIRMA
# ================================================================
print(f"\nPDF'ler okunuyor ({len(all_files)} dosya)...\n")

# Bolum bazinda istatistikler
stats = {i: {"ratios": [], "pdf_lens": [], "json_lens": [], "missing_pdf": 0, "missing_json": 0} for i in range(1, 6)}
problem_files = []  # (fn, bolum, pdf_len, json_len, ratio)

for idx, fn in enumerate(all_files):
    if (idx + 1) % 500 == 0:
        print(f"  ... {idx+1}/{len(all_files)}")
    
    pdf_path = os.path.join(PDF_DIR, fn)
    if not os.path.exists(pdf_path):
        continue
    
    text = extract_text(pdf_path)
    if not text or len(text) < 50:
        continue
    
    pdf_lengths = find_pdf_section_lengths(text)
    
    for bolum_no in range(1, 6):
        pdf_len = pdf_lengths.get(bolum_no, 0)
        item = bolum_maps[bolum_no].get(fn, {})
        json_len = len(item.get("content", "")) if item.get("content") else 0
        
        if pdf_len == 0:
            stats[bolum_no]["missing_pdf"] += 1
            continue
        
        if json_len == 0:
            stats[bolum_no]["missing_json"] += 1
            if pdf_len > 100:
                problem_files.append((fn, bolum_no, pdf_len, json_len, 0.0))
            continue
        
        ratio = json_len / pdf_len
        stats[bolum_no]["ratios"].append(ratio)
        stats[bolum_no]["pdf_lens"].append(pdf_len)
        stats[bolum_no]["json_lens"].append(json_len)
        
        # Sorunlu: %50'den az veya %150'den fazla
        if ratio < 0.50 or ratio > 1.50:
            problem_files.append((fn, bolum_no, pdf_len, json_len, ratio))

# ================================================================
# SONUCLAR
# ================================================================
print(f"\n{'='*80}")
print("  BOLUM BAZINDA PDF vs JSON YAKALAMA ORANI")
print(f"{'='*80}\n")

print(f"  {'Bolum':8s} {'Ort %':>7s} {'Med %':>7s} {'Min %':>7s} {'Max %':>7s} {'PDF ort':>8s} {'JSON ort':>9s} {'PDF yok':>8s} {'JSON yok':>9s} {'N':>6s}")
print(f"  {'-'*82}")

for i in range(1, 6):
    s = stats[i]
    ratios = s["ratios"]
    if not ratios:
        print(f"  B{i}       veri yok")
        continue
    
    avg_r = sum(ratios) / len(ratios) * 100
    sorted_r = sorted(ratios)
    med_r = sorted_r[len(sorted_r) // 2] * 100
    min_r = min(ratios) * 100
    max_r = max(ratios) * 100
    avg_pdf = sum(s["pdf_lens"]) / len(s["pdf_lens"])
    avg_json = sum(s["json_lens"]) / len(s["json_lens"])
    
    print(f"  B{i}      {avg_r:6.1f}% {med_r:6.1f}% {min_r:6.1f}% {max_r:6.1f}% {avg_pdf:7.0f} {avg_json:8.0f} {s['missing_pdf']:8d} {s['missing_json']:9d} {len(ratios):6d}")

# Dagilim bantlari
print(f"\n  Yakalama orani dagilimi (her bolum icin):")
print(f"  {'Bant':12s}", end="")
for i in range(1, 6):
    print(f" {'B'+str(i):>8s}", end="")
print()
print(f"  {'-'*52}")

bands = ["0-25%", "25-50%", "50-75%", "75-100%", "100-125%", "125-150%", "150%+"]
band_ranges = [(0, 0.25), (0.25, 0.50), (0.50, 0.75), (0.75, 1.00), (1.00, 1.25), (1.25, 1.50), (1.50, 999)]

for band_name, (lo, hi) in zip(bands, band_ranges):
    print(f"  {band_name:12s}", end="")
    for i in range(1, 6):
        count = sum(1 for r in stats[i]["ratios"] if lo <= r < hi)
        print(f" {count:>8d}", end="")
    print()

# ================================================================
# SORUNLU DOSYALAR
# ================================================================
print(f"\n{'='*80}")
print(f"  SORUNLU DOSYALAR (oran <%50 veya >%150)")
print(f"{'='*80}")

# <%50 - eksik icerik
under50 = [(fn, b, pl, jl, r) for fn, b, pl, jl, r in problem_files if r < 0.50]
print(f"\n  Eksik icerik (<%50 yakalama): {len(under50)}")
under50.sort(key=lambda x: x[4])
for fn, b, pl, jl, r in under50[:20]:
    print(f"    B{b} {r*100:5.1f}% | PDF={pl:6d} JSON={jl:6d} | {fn[:55]}")
if len(under50) > 20:
    print(f"    ... ve {len(under50)-20} tane daha")

# >%150 - fazla icerik
over150 = [(fn, b, pl, jl, r) for fn, b, pl, jl, r in problem_files if r > 1.50]
print(f"\n  Fazla icerik (>%150 yakalama): {len(over150)}")
over150.sort(key=lambda x: x[4], reverse=True)
for fn, b, pl, jl, r in over150[:20]:
    print(f"    B{b} {r*100:5.1f}% | PDF={pl:6d} JSON={jl:6d} | {fn[:55]}")
if len(over150) > 20:
    print(f"    ... ve {len(over150)-20} tane daha")

# ================================================================
# GENEL KALITE SKORU
# ================================================================
print(f"\n{'='*80}")
print("  GENEL KALITE SKORU")
print(f"{'='*80}")

all_ratios = []
for i in range(1, 6):
    all_ratios.extend(stats[i]["ratios"])

if all_ratios:
    # %75-%125 arasi "iyi" kabul et
    good = sum(1 for r in all_ratios if 0.75 <= r <= 1.25)
    fair = sum(1 for r in all_ratios if (0.50 <= r < 0.75) or (1.25 < r <= 1.50))
    poor = sum(1 for r in all_ratios if r < 0.50 or r > 1.50)
    total = len(all_ratios)
    
    total_missing_json = sum(stats[i]["missing_json"] for i in range(1, 6))
    total_missing_pdf = sum(stats[i]["missing_pdf"] for i in range(1, 6))
    
    avg_all = sum(all_ratios) / len(all_ratios) * 100
    
    print(f"""
  Toplam bolum-ilac cifti : {total + total_missing_json + total_missing_pdf}
  Karsilastirilan         : {total}
  PDF'de bolum yok        : {total_missing_pdf}
  JSON'da icerik yok      : {total_missing_json}
  
  Ortalama yakalama       : {avg_all:.1f}%
  
  Iyi   (%75-%125)        : {good:5d} ({good/total*100:.1f}%)
  Orta  (%50-%75, %125-150): {fair:5d} ({fair/total*100:.1f}%)
  Kotu  (<%50 veya >%150) : {poor:5d} ({poor/total*100:.1f}%)
  
  KALITE SKORU: {good/total*100:.1f}% (iyi yakalama orani)
""")
