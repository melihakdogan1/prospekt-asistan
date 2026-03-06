import json, re, pdfplumber, os, sys
from collections import Counter

with open('analiz_sonuclari/hatali_dosyalar.json','r',encoding='utf-8') as f:
    errors = json.load(f)

bolum1_err = [e for e in errors if 'Bölüm 1' in e.get('reason','')]
font_err = [e for e in errors if 'Font' in e.get('reason','')]
ocr_err = [e for e in errors if 'OCR' in e.get('reason','')]

print(f"=== Hata Dagilimi ===")
print(f"OCR gerekli: {len(ocr_err)}")
print(f"Font encoding: {len(font_err)}")
print(f"Bolum 1 bulunamadi: {len(bolum1_err)}")

kt_dir = 'data/kt'
pattern_analysis = Counter()
fixable_files = []
details = []

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
                try:
                    t = page.extract_text()
                    if t:
                        full_text += t + "\n"
                except:
                    pass
        
        if len(full_text.strip()) < 100:
            pattern_analysis['TOO_SHORT_TEXT'] += 1
            continue
        
        # Check for different patterns
        # Standard: "1. Bu ilac nedir ve ne icin kullanilir?"
        has_numbered = bool(re.search(r'(?:^|\n)\s*1\s*[.\-\)]\s*.*(?:nedir|ne\s+i[çc]in)', full_text, re.IGNORECASE))
        has_bu_ilac_nedir = bool(re.search(r'[Bb]u\s+ila[çc]\s+nedir', full_text, re.IGNORECASE))
        has_toc = bool(re.search(r'yer\s+almaktad[ıi]r|başlıkları', full_text, re.IGNORECASE))
        has_sections234 = bool(re.search(r'(?:^|\n)\s*[234]\s*[.\-\)]\s*', full_text))
        
        # Find the actual section patterns
        all_section_matches = list(re.finditer(r'(?:^|\n)(\s*(\d)\s*[.\-\)]\s*[^\n]{0,100})', full_text))
        section_nums = [m.group(2) for m in all_section_matches]
        
        if has_numbered:
            m = re.search(r'(?:^|\n)(\s*1\s*[.\-\)]\s*[^\n]{0,80})', full_text, re.IGNORECASE)
            pattern_text = m.group(1).strip()[:60] if m else "?"
            pattern_analysis['NUMBERED_BUT_NOT_DETECTED'] += 1
            fixable_files.append(fname)
            details.append({'file': fname, 'type': 'numbered', 'pattern': pattern_text})
        elif has_bu_ilac_nedir:
            pattern_analysis['HAS_BU_ILAC_NEDIR_NO_NUMBER'] += 1
            fixable_files.append(fname)
        elif has_sections234:
            pattern_analysis['HAS_SEC234_BUT_NO_SEC1'] += 1
            # Check what sections exist
            sec_nums = sorted(set(section_nums))
            details.append({'file': fname, 'type': 'no_sec1', 'sections': sec_nums})
            fixable_files.append(fname)
        elif has_toc:
            pattern_analysis['HAS_TOC_NO_SECTIONS'] += 1
        else:
            pattern_analysis['NO_STANDARD_PATTERNS'] += 1
            
    except Exception as ex:
        pattern_analysis['PDF_ERROR'] += 1

print(f"\n=== Pattern Analysis ===")  
for p, c in pattern_analysis.most_common(20):
    print(f"  {c:3d} | {p}")

print(f"\nPotentially fixable: {len(fixable_files)}")

# Now check Font encoding files
print(f"\n\n=== Font Encoding Sorunlu Dosyalar ===")
font_patterns = Counter()
for e in font_err[:20]:
    tp = e.get('text_preview','')
    if tp:
        # Check if mostly garbled
        turkish_chars = sum(1 for c in tp if c in 'abcçdefgğhıijklmnoöprsştuüvyzABCÇDEFGĞHIİJKLMNOÖPRSŞTUÜVYZ0123456789 .,;:!?()-')
        total_chars = len(tp)
        ratio = turkish_chars / total_chars if total_chars > 0 else 0
        if ratio > 0.7:
            font_patterns['MOSTLY_READABLE'] += 1
        else:
            font_patterns['MOSTLY_GARBLED'] += 1
    else:
        font_patterns['NO_TEXT'] += 1

for p, c in font_patterns.most_common():
    print(f"  {c:3d} | {p}")

# Show sample font encoding texts
print("\n=== Sample Font Encoding Texts ===")
for e in font_err[:3]:
    tp = e.get('text_preview','')
    print(f"\n--- {e['file']} ---")
    print(tp[:300] if tp else "(no text)")

# Check what section patterns exist in bolum1_err files 
print(f"\n\n=== Detailed: numbered but not detected ===")
for d in details[:10]:
    if d['type'] == 'numbered':
        print(f"  {d['file']}: {d['pattern']}")

print(f"\n=== Has sec 2/3/4 but no sec 1 ===")
for d in details:
    if d['type'] == 'no_sec1':
        print(f"  {d['file']}: sections={d['sections']}")
