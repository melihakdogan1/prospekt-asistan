import argparse
import json
from pathlib import Path

import adim3_kategori_a_ocr as a3


ROOT = Path(__file__).resolve().parent
OUT_RESULT = ROOT / "adim3_kategori_a_sonuclari.json"
OUT_LOG = ROOT / "adim3_islem_log.json"


def load_json_list(path: Path):
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def update_log_entry(logs, file_name, durum, metin_kaynagi, text_length, neden):
    for entry in logs:
        if entry.get("file_name") == file_name:
            entry["durum"] = durum
            entry["metin_kaynagi"] = metin_kaynagi
            entry["text_length"] = text_length
            entry["neden"] = neden
            return


def main(limit: int | None = None):
    logs = load_json_list(OUT_LOG)
    results = load_json_list(OUT_RESULT)

    failed_files = [x["file_name"] for x in logs if x.get("durum") == "basarisiz"]
    if limit is not None:
        failed_files = failed_files[:limit]

    existing_result_files = {x.get("file_name") for x in results if x.get("file_name")}

    reader = None
    retried = 0
    recovered = 0
    still_failed = 0
    appended = 0

    for fn in failed_files:
        retried += 1
        p = a3.resolve_pdf_path(fn)
        if p is None:
            update_log_entry(logs, fn, "basarisiz", "", 0, "PDF bulunamadı")
            still_failed += 1
            continue

        try:
            text = a3.extract_pdf_text(p)
            source = "pdfplumber"

            if len(text) < 100:
                if reader is None:
                    reader = a3.get_easyocr_reader()
                text = a3.ocr_with_easyocr(p, reader)
                source = "easyocr"

            text = a3.normalize_text(text)

            if len(text) < 100:
                update_log_entry(logs, fn, "basarisiz", source, len(text), "OCR sonrası metin < 100 karakter")
                still_failed += 1
                continue

            parsed = a3.parse_record(text, fn)
            if fn not in existing_result_files:
                results.append(parsed)
                existing_result_files.add(fn)
                appended += 1

            update_log_entry(logs, fn, "basarili", source, len(text), "")
            recovered += 1

        except Exception as e:
            update_log_entry(logs, fn, "basarisiz", "", 0, str(e))
            still_failed += 1

        # Release cached GPU memory between retries when CUDA is available.
        try:
            import torch  # type: ignore

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    OUT_RESULT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_LOG.write_text(json.dumps(logs, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "retry_toplam": retried,
                "retry_basarili": recovered,
                "retry_basarisiz": still_failed,
                "sonuca_append": appended,
            },
            ensure_ascii=False,
        )
    )
    print(f"Sonuc: {OUT_RESULT}")
    print(f"Log: {OUT_LOG}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Adim3 basarisiz dosyalarini yeniden isle")
    parser.add_argument("--limit", type=int, default=None, help="Sadece ilk N basarisiz kayit icin retry")
    args = parser.parse_args()
    main(limit=args.limit)
