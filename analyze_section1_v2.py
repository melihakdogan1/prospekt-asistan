import os
import glob
import pdfplumber
import re
import json
import logging
from tqdm import tqdm

# Configure logging
logging.basicConfig(
    filename='section1_analysis_v2.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

DATA_DIR = "data/kt"
OUTPUT_FILE = "kt_bolum1_analiz_v2.json"

def normalize_text(text):
    if not text:
        return ""
    return text.strip()

def extract_section_1_info(pdf_path):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text_content = ""
            # Extract first 4 pages to cover TOC and Section 1 fully
            for i, page in enumerate(pdf.pages[:4]):
                page_text = page.extract_text()
                if page_text:
                    text_content += page_text + "\n"
            
            if not text_content.strip():
                return {"file": os.path.basename(pdf_path), "error": "No text extracted (OCR needed?)"}

            lines = [line.strip() for line in text_content.split('\n') if line.strip()]
            
            # --- 1. Header & Drug Info ---
            drug_name = None
            route_of_admin = None
            active_substance = None
            
            kt_header_idx = -1
            for i, line in enumerate(lines):
                if "KULLANMA TALİMATI" in line.upper():
                    kt_header_idx = i
                    break
            
            if kt_header_idx != -1:
                # Attempt to find drug name in next non-empty lines
                # Sometimes there is extra whitespace or sub-headers
                current_idx = kt_header_idx + 1
                while current_idx < len(lines) and current_idx < kt_header_idx + 5:
                    line = lines[current_idx]
                    if line: # Found a potential drug name line
                        drug_name = line
                        # Route is usually the NEXT line
                        if current_idx + 1 < len(lines):
                            route_of_admin = lines[current_idx + 1]
                        break
                    current_idx += 1

                # Search for Active Substance
                # Look in the first 20 lines after header
                search_end_idx = min(kt_header_idx + 25, len(lines))
                for i in range(kt_header_idx, search_end_idx):
                    line = lines[i]
                    if re.match(r"(Etkin|Etken)\s*madde", line, re.IGNORECASE):
                        active_substance = line
                        # Sometimes active substance continues to next line?
                        # For now, take the line.
                        break

            # --- 2. Locate Section 1 (Skipping TOC) ---
            
            # Find TOC marker
            toc_marker = "Bu Kullanma Talimatında:"
            toc_match = re.search(re.escape(toc_marker), text_content, re.IGNORECASE)
            
            search_start_pos = 0
            if toc_match:
                # TOC found. We need to skip it.
                # TOC ends with "Başlıkları yer almaktadır" usually.
                toc_end_marker = "Başlıkları yer almaktadır"
                toc_end_match = re.search(re.escape(toc_end_marker), text_content, re.IGNORECASE)
                
                if toc_end_match:
                    search_start_pos = toc_end_match.end()
                else:
                    # Fallback: TOC usually has 5 items. Skip 5-10 lines or a chunk of text
                    search_start_pos = toc_match.end() + 200 # arbitrary chars skip
            
            # Search for Section 1 Title AFTER TOC
            # Pattern: 1. [TEXT] nedir ve ne için kullanılır?
            # Use DOTALL to match newlines in title
            sec1_pattern = re.compile(r"1\s*\.\s*(?:(?!2\.).)*?nedir\s*ve\s*ne\s*için\s*kullanılır\s*[?:]?", re.IGNORECASE | re.DOTALL)
            
            sec1_match = sec1_pattern.search(text_content, pos=search_start_pos)
            
            section_1_title = None
            section_1_content = None
            
            if sec1_match:
                section_1_title = sec1_match.group(0).strip()
                content_start = sec1_match.end()
                
                # Search for Section 2 Title (Stop Marker)
                # Pattern: 2. [TEXT] kullanmadan önce
                # Look for "2." at start of line OR preceded by newline
                sec2_pattern = re.compile(r"(?:^|\n)\s*2\s*\.\s*.*?\s*kullanmadan\s*önce", re.IGNORECASE)
                sec2_match = sec2_pattern.search(text_content, pos=content_start)
                
                content_end = -1
                if sec2_match:
                    content_end = sec2_match.start()
                else:
                    # Fallback stop markers
                    fallback_stops = [
                        r"(?:^|\n)\s*2\s*\.", # Just "2."
                        r"BÖLÜM 2",
                        r"2\.\s+Bölüm"
                    ]
                    for fs in fallback_stops:
                        fm = re.search(fs, text_content[content_start:], re.IGNORECASE)
                        if fm:
                            content_end = content_start + fm.start()
                            break
                
                if content_end != -1:
                    section_1_content = text_content[content_start:content_end].strip()
                else:
                    # No clear end found, take a chunk
                    section_1_content = text_content[content_start:content_start+2000].strip()

            return {
                "file": os.path.basename(pdf_path),
                "extracted_drug_name": drug_name,
                "extracted_route": route_of_admin,
                "extracted_active_substance": active_substance,
                "section_1_title": normalize_text(section_1_title),
                "section_1_content": normalize_text(section_1_content)
            }

    except Exception as e:
        logging.error(f"Error processing {pdf_path}: {e}")
        return {"file": os.path.basename(pdf_path), "error": str(e)}

def main():
    pdf_files = glob.glob(os.path.join(DATA_DIR, "*.pdf"))
    
    results = []
    print(f"Analyzing {len(pdf_files)} files (V2)...")
    
    for pdf_file in tqdm(pdf_files):
        data = extract_section_1_info(pdf_file)
        if data:
            results.append(data)
            
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
        
    print(f"Analysis complete. Results saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
