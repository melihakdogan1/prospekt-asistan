import json
import os
from pathlib import Path

import pdfplumber


ROOT = Path(__file__).resolve().parent
TXT_PATH = ROOT / "kt_ocr_gerekli.txt"
KT_DIR = ROOT / "data" / "kt"
OUT_PATH = ROOT / "kategori_raporu.json"


ENCODING_HINTS = [
    "kucuk i",
    "küçük i",
    "bozuk",
    "eeer",
    "igermektedir",
    "talimati",
    "$i$",
]


def parse_txt(path: Path):
    entries = []
    if not path.exists():
        raise FileNotFoundError(f"Bulunamadi: {path}")

    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

    current_file = None
    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        if line.startswith("===") or line.lower().startswith("sebep"):
            if line.lower().startswith("sebep") and current_file:
                reason = line.split(":", 1)[1].strip() if ":" in line else ""
                entries.append({"file_name": current_file, "neden": reason})
                current_file = None
            continue

        if line.lower().startswith("sebep:"):
            # Defensive: covered above, but kept for clarity.
            if current_file:
                reason = line.split(":", 1)[1].strip()
                entries.append({"file_name": current_file, "neden": reason})
                current_file = None
            continue

        # File line
        if line.lower().endswith(".pdf"):
            current_file = line

    return entries


def find_pdf(file_name: str):
    p = KT_DIR / file_name
    if p.exists():
        return p

    # Fallback: case-insensitive lookup
    lower_map = {f.name.lower(): f for f in KT_DIR.glob("*.pdf")}
    return lower_map.get(file_name.lower())


def extract_len(pdf_path: Path):
    try:
        txt_parts = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                if t:
                    txt_parts.append(t)
        text = "\n".join(txt_parts).strip()
        return len(text)
    except Exception:
        return -1


def classify(entry, text_len):
    reason = (entry.get("neden") or "").lower()

    # Kategori A: metin yok/cok kisa veya pdf metni <100
    if "metin yok" in reason or "çok kısa" in reason or text_len == -1 or text_len < 100:
        return "a"

    # Kategori B: encoding/font bozukluguna dair ipuclari
    if any(h in reason for h in ENCODING_HINTS):
        return "b"

    # Kategori C: digerleri
    return "c"


def main():
    entries = parse_txt(TXT_PATH)

    kategori_a = []
    kategori_b = []
    kategori_c = []

    for e in entries:
        file_name = e["file_name"]
        pdf_path = find_pdf(file_name)
        text_len = extract_len(pdf_path) if pdf_path else -1

        item = {
            "file_name": file_name,
            "neden": e.get("neden", ""),
        }

        k = classify(e, text_len)
        if k == "a":
            kategori_a.append(item)
        elif k == "b":
            kategori_b.append(item)
        else:
            kategori_c.append(item)

    result = {
        "kategori_a": kategori_a,
        "kategori_b": kategori_b,
        "kategori_c": kategori_c,
        "ozet": {
            "a_count": len(kategori_a),
            "b_count": len(kategori_b),
            "c_count": len(kategori_c),
        },
    }

    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Kategori raporu oluşturuldu:", OUT_PATH)
    print(json.dumps(result["ozet"], ensure_ascii=False))


if __name__ == "__main__":
    main()
