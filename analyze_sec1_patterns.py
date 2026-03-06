import json, re, pdfplumber, os
from collections import Counter

with open('analiz_sonuclari/hatali_dosyalar.json','r',encoding='utf-8') as f:
    errors = json.load(f)

bolum1_err = [e for e in errors if 'Bölüm 1' in e.get('reason','')]
kt_dir = 'data/kt'

# Current pattern that should match:
current_pattern = r'1\s*[.\-\)]\s*[^\n]*?nedir[^\n]*?kullan[ıi]l[ıi]r[^\n]*'

# Analyze what ACTUAL patterns exist in these PDFs
pattern_variants = Counter()
no_nedir_files = []
has_nedir_but_no_kullanilir = []
multiline_patterns = []

for e in bolum1_err:
    fname = e['file']
    fpath = os.path.join(kt_dir, fname)
    if not os.path.exists(fpath):
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
            continue
        
        # Check if current pattern matches
        if re.search(current_pattern, full_text, re.IGNORECASE):
            pattern_variants['SHOULD_MATCH_CURRENT_PATTERN'] += 1
            continue
        
        # Find "1." or "1)" or "1-" followed by anything
        sec1_matches = list(re.finditer(r'(?:^|\n)\s*(1\s*[.\-\)]\s*[^\n]{0,150})', full_text))
        
        if not sec1_matches:
            pattern_variants['NO_1_DOT_AT_ALL'] += 1
            continue
        
        # Take the last match (most likely the real section, not TOC)
        last_match = sec1_matches[-1]
        line_text = last_match.group(1).strip()
        
        has_nedir = 'nedir' in line_text.lower()
        has_kullanilir = bool(re.search(r'kullan[ıi]l[ıi]r', line_text, re.IGNORECASE))
        
        if has_nedir and has_kullanilir:
            pattern_variants['HAS_BOTH_BUT_STILL_FAILS'] += 1
            # Show the actual text
            print(f"WEIRD: {fname}")
            print(f"  Pattern: {line_text[:120]}")
        elif has_nedir and not has_kullanilir:
            pattern_variants['HAS_NEDIR_NO_KULLANILIR'] += 1
            has_nedir_but_no_kullanilir.append((fname, line_text[:120]))
            # Check if kullanilir is on next line
            pos = last_match.end()
            next_lines = full_text[pos:pos+200].split('\n')[:3]
            for nl in next_lines:
                if re.search(r'kullan[ıi]l[ıi]r', nl, re.IGNORECASE):
                    multiline_patterns.append((fname, line_text[:80], nl.strip()[:80]))
                    break
        elif not has_nedir:
            # Section 1 exists but doesn't have "nedir"
            pattern_variants['NO_NEDIR_KEYWORD'] += 1
            no_nedir_files.append((fname, line_text[:120]))
        
    except Exception as ex:
        pattern_variants['PDF_ERROR'] += 1

print("=== Pattern Variants ===")
for p, c in pattern_variants.most_common():
    print(f"  {c:3d} | {p}")

print(f"\n=== No 'nedir' keyword (first 15) ===")
for fname, pattern in no_nedir_files[:15]:
    print(f"  {fname}")
    print(f"    {pattern}")

print(f"\n=== Has 'nedir' but no 'kullanilir' (first 15) ===")
for fname, pattern in has_nedir_but_no_kullanilir[:15]:
    print(f"  {fname}")
    print(f"    {pattern}")

print(f"\n=== Multiline patterns (kullanilir on next line) ===")
for fname, line1, line2 in multiline_patterns[:10]:
    print(f"  {fname}")
    print(f"    L1: {line1}")
    print(f"    L2: {line2}")
