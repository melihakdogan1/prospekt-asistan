import json, re
from collections import Counter

with open('analiz_sonuclari/hatali_dosyalar.json','r',encoding='utf-8') as f:
    errors = json.load(f)

# Bolum 1 bulunamadi
bolum1_err = [e for e in errors if 'Bölüm 1' in e.get('reason','')]
print(f"Bolum 1 bulunamadi: {len(bolum1_err)}")

# Analyze text patterns for these files
section_patterns = Counter()

for e in bolum1_err[:30]:
    tp = e.get('text_preview','')
    fname = e['file']
    
    lines = tp.split('\n')
    found = False
    for i, line in enumerate(lines):
        line_stripped = line.strip()
        # Look for section 1 patterns
        if re.search(r'^\s*1\s*[.\-\)]\s*', line_stripped):
            section_patterns[line_stripped[:80]] += 1
            found = True
            break
    
    if not found:
        # Check if text has Bu ilac nedir pattern
        if re.search(r'nedir\s+ve\s+ne\s+i[çc]in', tp, re.IGNORECASE):
            section_patterns['HAS_NEDIR_PATTERN_BUT_NOT_NUMBERED'] += 1
        elif re.search(r'nedir', tp, re.IGNORECASE):
            section_patterns['HAS_NEDIR_ONLY'] += 1
        else:
            section_patterns['NO_SECTION_PATTERN'] += 1

print("\n=== Section 1 patterns in Bolum 1 errors ===")
for p, c in section_patterns.most_common(20):
    print(f"  {c:3d} | {p}")

# Show some actual text for files without section pattern
print("\n\n=== Sample texts for NO_SECTION_PATTERN ===")
count = 0
for e in bolum1_err:
    tp = e.get('text_preview','')
    if not re.search(r'nedir', tp, re.IGNORECASE) and not re.search(r'^\s*1\s*[.\-\)]\s*', tp, re.MULTILINE):
        if count < 5:
            print(f"\n--- {e['file']} ---")
            print(tp[:400])
            count += 1

# Show some actual text for HAS_NEDIR_PATTERN_BUT_NOT_NUMBERED
print("\n\n=== Sample texts with nedir but not numbered ===")
count = 0
for e in bolum1_err:
    tp = e.get('text_preview','')
    if re.search(r'nedir\s+ve\s+ne\s+i[çc]in', tp, re.IGNORECASE) and not re.search(r'^\s*1\s*[.\-\)]\s*', tp, re.MULTILINE):
        if count < 5:
            print(f"\n--- {e['file']} ---")
            # Find the nedir line and context
            lines = tp.split('\n')
            for i, line in enumerate(lines):
                if re.search(r'nedir', line, re.IGNORECASE):
                    start = max(0, i-2)
                    end = min(len(lines), i+3)
                    for j in range(start, end):
                        marker = ">>>" if j == i else "   "
                        print(f"  {marker} L{j}: {lines[j][:120]}")
                    break
            count += 1
