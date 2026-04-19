import json
import re
import argparse
from pathlib import Path

import pdfplumber


ROOT = Path(__file__).resolve().parent
KATEGORI_PATH = ROOT / "kategori_raporu.json"
KT_DIR = ROOT / "data" / "kt"

OUT_RESULT = ROOT / "adim3_kategori_a_sonuclari.json"
OUT_LOG = ROOT / "adim3_islem_log.json"


PHRASE_REPLACEMENTS = [
    ("TALiMATINI", "TALİMATINI"),
    ("TALiMATI", "TALİMATI"),
    ("TALiMAT", "TALİMAT"),
    ("EEer", "Eğer"),
    ("igermektedir", "içermektedir"),
    ("iqin", "için"),
    ("KULLANMA TALIMATI", "KULLANMA TALİMATI"),
]

CHAR_MAP = {
    "Đ": "İ",
    "ð": "ğ",
    "þ": "ş",
    "ý": "ı",
    "Þ": "Ş",
    "Ð": "Ğ",
    "Ý": "İ",
}


def load_category_a_files():
    data = json.loads(KATEGORI_PATH.read_text(encoding="utf-8"))
    files = data.get("kategori_a", [])
    return [x["file_name"] for x in files if "KÜB'E BAKINIZ" not in x["file_name"]]


def norm_name(name: str):
    name = re.sub(r"\.pdf$", "", name, flags=re.IGNORECASE)
    s = name.translate(str.maketrans({
        "İ": "I", "Ş": "S", "Ğ": "G", "Ü": "U", "Ö": "O", "Ç": "C",
        "ı": "i", "ş": "s", "ğ": "g", "ü": "u", "ö": "o", "ç": "c",
    }))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]", "", s)
    return s


def resolve_pdf_path(file_name: str):
    normalized_name = file_name.replace("%", "_").replace(";", "_")

    p = KT_DIR / normalized_name
    if p.exists():
        return p

    p = KT_DIR / file_name
    if p.exists():
        return p

    # Case-insensitive direct match
    lower_map = {f.name.lower(): f for f in KT_DIR.glob("*.pdf")}
    if file_name.lower() in lower_map:
        return lower_map[file_name.lower()]

    # Normalized fallback (Turkish chars and spacing)
    target = norm_name(file_name)
    for f in KT_DIR.glob("*.pdf"):
        if norm_name(f.name) == target:
            return f

    return None


def extract_pdf_text(pdf_path: Path):
    text_parts = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            if t:
                text_parts.append(t)
    return "\n".join(text_parts).strip()


def normalize_text(text: str):
    out = text
    for src, dst in PHRASE_REPLACEMENTS:
        out = out.replace(src, dst)
    for bad, good in CHAR_MAP.items():
        out = out.replace(bad, good)

    # Line merge and whitespace cleanup
    out = re.sub(r"(\w)-\n(\w)", r"\1\2", out)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def get_easyocr_reader():
    import easyocr  # type: ignore

    return easyocr.Reader(["tr", "en"], gpu=True)


def ocr_with_easyocr(pdf_path: Path, reader):
    import pypdfium2 as pdfium  # type: ignore

    doc = pdfium.PdfDocument(str(pdf_path))
    n = min(len(doc), 15)
    out_parts = []
    for i in range(n):
        page = doc[i]
        bmp = page.render(scale=2.0)
        img = bmp.to_numpy()
        chunks = reader.readtext(img, detail=0, paragraph=True)
        if chunks:
            out_parts.append("\n".join(chunks))
        del img, bmp, page  # belleği hemen serbest bırak
    doc.close()
    return "\n".join(out_parts).strip()


def normalize_for_compare(s):
    return s.replace("ı", "i").replace("ğ", "g").replace("ş", "s").replace("ü", "u").replace("ö", "o").replace("ç", "c")


def clean_drug_name(value: str):
    s = (value or "").replace("@", " ").replace("®", " ")
    # OCR küçük i düzeltmesi
    s = re.sub(r"(?<=[A-ZÇĞIİÖŞÜ])i(?=[A-ZÇĞIİÖŞÜ0-9 ])", "İ", s)
    s = re.sub(r"(?<=[A-ZÇĞIİÖŞÜ])i(?=\d)", "İ", s)
    # Rakam ile harf arasına boşluk ekle: "50mg" -> "50 mg"
    s = re.sub(r"(\d)([a-zA-ZÇĞIİÖŞÜçğışöü])", r"\1 \2", s)
    s = re.sub(r"([a-zA-ZÇĞIİÖŞÜçğışöü])(\d)", r"\1 \2", s)
    # "mgtablet" -> "mg tablet", "mgkapsul" vb.
    s = re.sub(r"(mg|ml|mcg|iu)([a-zA-ZÇĞIİÖŞÜçğışöü])", r"\1 \2", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def is_valid_drug_name(value: str):
    if not value:
        return False
    if re.match(r'^\d+[\.\)]\s+', value):
        return False
    if len(value) > 150:
        return False
    lowered = value.lower()
    if any(term in lowered for term in ["dikkat edilmesi", "kullanmadan önce", "nasıl kullanılır", "yan etkiler"]):
        return False
    if re.search(r'[\$\@\#\|\\]', value):
        return False
    return True


def parse_record(text: str, file_name: str):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    drug_name = ""
    usage_route = ""
    active_substance = ""
    excipients = ""
    warning_text = ""
    license_holder = ""
    manufacturer = ""
    healthcare_professional_info = ""

    header_idx = -1
    for i, ln in enumerate(lines):
        if re.search(r"KULLANMA\s+TAL[İI]MATI", ln, re.IGNORECASE):
            header_idx = i
            break
    if header_idx != -1:
        for j in range(header_idx + 1, min(header_idx + 8, len(lines))):
            ln = lines[j]
            if len(ln) < 3 or len(ln) > 150:
                continue
            if re.search(r"(etkin|etken|yard[ıi]mc[ıi])\s*madde", ln, re.IGNORECASE):
                continue
            if re.search(r"(uygulan|kullan[ıi]l[ıi]r|al[ıi]n[ıi]r)", ln, re.IGNORECASE):
                continue
            drug_name = ln
            break
    if not drug_name:
        drug_name = re.sub(r"_KT\.pdf$|_KUB\.pdf$|\.pdf$", "", file_name, flags=re.IGNORECASE).replace("_", " ")
    drug_name = clean_drug_name(drug_name)
    if not is_valid_drug_name(drug_name):
        drug_name = re.sub(r'_KT\.pdf$|_KUB\.pdf$|\.pdf$', '', file_name, flags=re.IGNORECASE).replace('_', ' ').strip()

    route_phrases = [
        "damar içine uygulanır",
        "ağızdan alınır",
        "deri üzerine uygulanır",
        "göze damlatılır",
        "inhalasyon yoluyla kullanılır",
        "kas içine uygulanır",
        "agizdan alinir",
        "agizdan kullanilir",
        "ağızdan alınır",
        "damar icine uygulanir",
        "deri uzerine uygulanir",
    ]
    sentence_candidates = []
    for ln in lines:
        sentence_candidates.extend(re.split(r"(?<=[.!?])\s+", ln))
    for sent in sentence_candidates:
        candidate = re.sub(r"\s+", " ", sent).strip()
        if len(candidate) > 100 or len(candidate) < 3:
            continue
        low = normalize_for_compare(candidate.lower())
        if any(phrase in low for phrase in route_phrases):
            usage_route = candidate
            break

    if not usage_route:
        fn = normalize_for_compare(file_name.lower())
        if any(x in fn for x in ["tablet", "kapsul", "surup"]):
            usage_route = "Ağızdan alınır."
        elif any(x in fn for x in ["solusyon", "solusyon", "infuzyon", "serum"]):
            usage_route = "Damar içine uygulanır."
        elif any(x in fn for x in ["ampul", "enjektabl", "i.v."]):
            usage_route = "Damar içine uygulanır."
        elif any(x in fn for x in ["irigasyon"]):
            usage_route = "İrrigasyon amaçlı kullanılır."
        elif any(x in fn for x in ["krem", "merhem", "jel", "losyon"]):
            usage_route = "Deri üzerine uygulanır."
        elif any(x in fn for x in ["damla", "goz"]):
            usage_route = "Göze damlatılır."
        elif any(x in fn for x in ["inhalasyon", "toz"]):
            usage_route = "İnhalasyon yoluyla kullanılır."

    for i, ln in enumerate(lines[:180]):
        if re.search(r"(etkin|etken)\s*madde", ln, re.IGNORECASE):
            part = ln.split(":", 1)[1].strip() if ":" in ln else ""
            nxt = ""
            if i + 1 < len(lines) and not re.search(r"yard[ıi]mc[ıi]\s*madde", lines[i + 1], re.IGNORECASE):
                nxt = " " + lines[i + 1]
            active_substance = (part + nxt).strip()
            break
    for ln in lines[:220]:
        if re.search(r"yard[ıi]mc[ıi]\s*madde", ln, re.IGNORECASE):
            excipients = ln.split(":", 1)[1].strip() if ":" in ln else ""
            break

    wm = re.search(r"(Bu\s+ilac[ıi]\s+kullanmaya\s+ba[şs]lamadan\s+[öo]nce[\s\S]{0,1800})", text, re.IGNORECASE)
    if wm:
        warning_text = wm.group(1).strip()

    lm = re.search(r"Ruhsat\s+[Ss]ahibi\s*:?\s*(.+)", text, re.IGNORECASE)
    if lm:
        license_holder = lm.group(1).split("\n", 1)[0].strip()

    mm = re.search(r"(?:Üretim\s+[Yy]eri|Üretici)\s*:?\s*(.+)", text, re.IGNORECASE)
    if mm:
        manufacturer = mm.group(1).split("\n", 1)[0].strip()

    hm = re.search(r"(A[ŞS]A[ĞG]IDAK[İI]\s+B[İI]LG[İI]LER[\s\S]{0,4000})", text, re.IGNORECASE)
    if hm:
        healthcare_professional_info = hm.group(1).strip()

    sec_matches = []
    for n in range(1, 6):
        m = re.search(rf"(?:^|\n)\s*{n}\s*[\.\)]\s*([^\n]+)", text, re.IGNORECASE)
        if m:
            sec_matches.append((n, m.start(), m.group(0).strip()))
    sec_matches.sort(key=lambda x: x[1])

    sections = []
    for idx, (n, start, title) in enumerate(sec_matches):
        end = len(text)
        if idx + 1 < len(sec_matches):
            end = sec_matches[idx + 1][1]
        content = text[start:end].strip()
        sections.append(
            {
                "bolum_no": n,
                "section_title": title,
                "content": content,
                "content_length": len(content),
            }
        )

    return {
        "file_name": file_name,
        "drug_name": drug_name or "",
        "usage_route": usage_route or "",
        "active_substance": active_substance or "",
        "excipients": excipients or "",
        "warning_text": warning_text or "",
        "license_holder": license_holder or "",
        "manufacturer": manufacturer or "",
        "healthcare_professional_info": healthcare_professional_info or "",
        "sections": sections,
    }


def main(limit: int | None = None):
    files = load_category_a_files()
    if limit is not None:
        files = files[:limit]
    logs = []
    results = []

    reader = None
    for fn in files:
        p = resolve_pdf_path(fn)
        if p is None:
            logs.append({"file_name": fn, "durum": "basarisiz", "metin_kaynagi": "", "text_length": 0, "neden": "PDF bulunamadı"})
            continue

        try:
            text = extract_pdf_text(p)
            source = "pdfplumber"

            if len(text) < 100:
                if reader is None:
                    reader = get_easyocr_reader()
                text = ocr_with_easyocr(p, reader)
                source = "easyocr"

            text = normalize_text(text)

            if len(text) < 100:
                logs.append(
                    {
                        "file_name": fn,
                        "durum": "basarisiz",
                        "metin_kaynagi": source,
                        "text_length": len(text),
                        "neden": "OCR sonrası metin < 100 karakter",
                    }
                )
                continue

            parsed = parse_record(text, fn)
            results.append(parsed)
            logs.append({"file_name": fn, "durum": "basarili", "metin_kaynagi": source, "text_length": len(text), "neden": ""})

        except Exception as e:
            logs.append({"file_name": fn, "durum": "basarisiz", "metin_kaynagi": "", "text_length": 0, "neden": str(e)})

    OUT_RESULT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_LOG.write_text(json.dumps(logs, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "toplam": len(files),
                "basarili": sum(1 for x in logs if x["durum"] == "basarili"),
                "basarisiz": sum(1 for x in logs if x["durum"] == "basarisiz"),
            },
            ensure_ascii=False,
        )
    )
    print(f"Sonuc: {OUT_RESULT}")
    print(f"Log: {OUT_LOG}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kategori A OCR pipeline")
    parser.add_argument("--limit", type=int, default=None, help="Sadece ilk N dosyayi islemek icin")
    args = parser.parse_args()
    main(limit=args.limit)
