import json, re, pdfplumber, os
from collections import Counter

with open('analiz_sonuclari/hatali_dosyalar.json','r',encoding='utf-8') as f:
    errors = json.load(f)

bolum1_err = [e for e in errors if 'Bölüm 1' in e.get('reason','')]
font_err = [e for e in errors if 'Font' in e.get('reason','')]
ocr_err = [e for e in errors if 'OCR' in e.get('reason','')]

print(f"=== Hata Dagılımı ===")
print(f"OCR gerekli: {len(ocr_err)}")
print(f"Font encoding: {len(font_err)}")
print(f"Bölüm 1 bulunamadı: {len(bolum1_err)}")

# Analyze Bolum 1 errors by reading actual PDF
kt_dir = 'data/kt'
print(f"\n=== Bölüm 1 Bulunamadı - PDF Analizi ===")

pattern_analysis = Counter()
fixable_files = []

for e in bolum1_err:
    fname = e['file']
    fpath = os.path.join(kt_dir, fname)
    if not os.path.exists(fpath):
        pattern_analysis['FILE_NOT_FOUND'] += 1
        continue
    
    try:
        with pdfplumber.open(fpath) as pdf:
            full_text = ""
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    full_text += t + "\n"
        
        if len(full_text.strip()) < 100:
            pattern_analysis['TOO_SHORT_TEXT'] += 1
            continue
        
        # Check for "1." section pattern variants
        has_1_dot = bool(re.search(r'(?:^|\n)\s*1\s*[.\)]\s*', full_text))
        has_nedir = bool(re.search(r'nedir\s+ve\s+ne\s+i[çc]in', full_text, re.IGNORECASE))
        has_bu_ilac_nedir = bool(re.search(r'[Bb]u\s+ila[çc]\s+nedir', full_text, re.IGNORECASE))
        has_toc = bool(re.search(r'yer\s+almaktadır', full_text, re.IGNORECASE))
        has_numbered = bool(re.search(r'(?:^|\n)\s*1\s*[.\-\)]\s*.*(?:nedir|ne\s+i[çc]in)', full_text, re.IGNORECASE))
        
        if has_numbered:
            # Find what the actual pattern looks like
            m = re.search(r'(?:^|\n)(\s*1\s*[.\-\)]\s*[^\n]{0,80})', full_text, re.IGNORECASE)
            if m:
                pattern_analysis[f'NUMBERED_EXISTS: {m.group(1).strip()[:60]}'] += 1
                fixable_files.append(fname)
        elif has_bu_ilac_nedir:
            pattern_analysis['HAS_BU_ILAC_NEDIR_NO_NUMBER'] += 1
            fixable_files.append(fname)
        elif has_nedir:
            pattern_analysis['HAS_NEDIR_NO_NUMBER'] += 1
            fixable_files.append(fname)
        elif has_toc:
            pattern_analysis['HAS_TOC_NO_SECTION1'] += 1
        else:
            pattern_analysis['NO_STANDARD_PATTERNS'] += 1
            
    except Exception as ex:
        pattern_analysis[f'ERROR: {str(ex)[:50]}'] += 1

print()
for p, c in pattern_analysis.most_common(30):
    print(f"  {c:3d} | {p}")

print(f"\nPotentially fixable: {len(fixable_files)}")

# Show some detailed analysis of numbered_exists files
print("\n\n=== Detailed: Files where section 1 pattern exists but not found ===")
count = 0
for e in bolum1_err:
    if e['file'] not in fixable_files:
        continue
    fname = e['file']
    fpath = os.path.join(kt_dir, fname)
    if not os.path.exists(fpath):
        continue
    
    try:
        with pdfplumber.open(fpath) as pdf:
            full_text = ""
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    full_text += t + "\n"
        
        # Find all section-like patterns
        matches = list(re.finditer(r'(?:^|\n)(\s*\d\s*[.\-\)]\s*[^\n]{0,100})', full_text))
        if matches and count < 8:
            print(f"\n--- {fname} ---")
            for m in matches[:8]:
                print(f"  pos={m.start():5d} | {m.group(1).strip()[:100]}")
            count += 1
    except:
        pass
