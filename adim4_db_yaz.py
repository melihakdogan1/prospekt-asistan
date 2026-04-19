import argparse
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "prospekt_metadata.db"
ADIM2_RESULT_PATH = ROOT / "adim2_kategori_b_sonuclari.json"
ADIM3_RESULT_PATH = ROOT / "adim3_kategori_a_sonuclari.json"
ADIM2_LOG_PATH = ROOT / "adim2_islem_log.json"
ADIM3_LOG_PATH = ROOT / "adim3_islem_log.json"


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_log_entries(log_entries):
    failures = []
    for entry in log_entries:
        if entry.get("durum") != "basarisiz":
            continue
        file_name = entry.get("file_name") or entry.get("file") or ""
        if not file_name:
            continue
        failures.append(
            {
                "file_name": file_name,
                "reason": entry.get("neden", ""),
                "text_length": entry.get("text_length", 0),
            }
        )
    return failures


def load_sources():
    adim2 = load_json(ADIM2_RESULT_PATH, [])
    adim3 = load_json(ADIM3_RESULT_PATH, [])
    adim2_logs = load_json(ADIM2_LOG_PATH, [])
    adim3_logs = load_json(ADIM3_LOG_PATH, [])

    combined = {}
    for item in adim2 + adim3:
        file_name = item.get("file_name")
        if not file_name:
            continue
        combined[file_name] = item

    failures = normalize_log_entries(adim2_logs) + normalize_log_entries(adim3_logs)
    failure_map = {}
    for item in failures:
        failure_map[item["file_name"]] = item

    return combined, list(combined.values()), list(failure_map.values())


def ensure_db_connection():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def fetch_existing_ilac(conn, file_name):
    cur = conn.execute("SELECT id FROM ilaclar WHERE file_name = ?", (file_name,))
    row = cur.fetchone()
    return row[0] if row else None


def prepare_counts(conn, records, failures):
    counts = {
        "ilaclar_insert": 0,
        "ilaclar_update": 0,
        "bolumler_delete": 0,
        "bolumler_insert": 0,
        "hatali_insert": 0,
    }

    for record in records:
        file_name = record.get("file_name", "")
        if not file_name:
            continue
        ilac_id = fetch_existing_ilac(conn, file_name)
        if ilac_id is None:
            counts["ilaclar_insert"] += 1
        else:
            counts["ilaclar_update"] += 1

        sections = record.get("sections", []) or []
        counts["bolumler_delete"] += 1
        counts["bolumler_insert"] += len(sections)

    # Failures are appended as individual rows.
    counts["hatali_insert"] = len(failures)
    return counts


def upsert_ilac(conn, record):
    file_name = record.get("file_name", "")
    if not file_name:
        return None

    params = (
        file_name,
        record.get("drug_name", ""),
        record.get("usage_route", ""),
        record.get("active_substance", ""),
        record.get("excipients", ""),
        record.get("warning_text", ""),
        record.get("license_holder", ""),
        record.get("manufacturer", ""),
        record.get("healthcare_professional_info", ""),
    )

    existing_id = fetch_existing_ilac(conn, file_name)
    if existing_id is None:
        cur = conn.execute(
            """
            INSERT INTO ilaclar (
                file_name, drug_name, usage_route, active_substance,
                excipients, warning_text, license_holder, manufacturer,
                healthcare_professional_info
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
        return cur.lastrowid, "insert"

    conn.execute(
        """
        UPDATE ilaclar
        SET drug_name = ?,
            usage_route = ?,
            active_substance = ?,
            excipients = ?,
            warning_text = ?,
            license_holder = ?,
            manufacturer = ?,
            healthcare_professional_info = ?
        WHERE id = ?
        """,
        params[1:] + (existing_id,),
    )
    return existing_id, "update"


def replace_sections(conn, ilac_id, sections):
    conn.execute("DELETE FROM bolumler WHERE ilac_id = ?", (ilac_id,))
    inserted = 0
    for section in sections:
        content = section.get("content", "")
        conn.execute(
            """
            INSERT INTO bolumler (
                ilac_id, bolum_no, section_title, content, content_length
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                ilac_id,
                section.get("bolum_no", 0),
                section.get("section_title", ""),
                content,
                section.get("content_length", len(content)),
            ),
        )
        inserted += 1
    return inserted


def append_failures(conn, failures):
    inserted = 0
    for failure in failures:
        conn.execute(
            """
            INSERT INTO hatali_dosyalar (file_name, reason, text_length)
            VALUES (?, ?, ?)
            """,
            (
                failure.get("file_name", ""),
                failure.get("reason", ""),
                failure.get("text_length", 0),
            ),
        )
        inserted += 1
    return inserted


def run_dry_run(conn, records, failures):
    counts = prepare_counts(conn, records, failures)
    print(json.dumps({"dry_run": True, **counts}, ensure_ascii=False, indent=2))

    if records:
        print()
        print("=== İLK 5 ETKİLENECEK KAYIT ===")
        for record in records[:5]:
            file_name = record.get("file_name", "")
            ilac_id = fetch_existing_ilac(conn, file_name)
            action = "UPDATE" if ilac_id is not None else "INSERT"
            print(f"{file_name} -> ilaclar {action}, bolumler sil+ekle ({len(record.get('sections', []) or [])} bölüm)")

    if failures:
        print()
        print("=== İLK 5 HATALI DOSYA ===")
        for failure in failures[:5]:
            print(f"{failure.get('file_name', '')} -> {failure.get('reason', '')}")


def run_write(conn, records, failures):
    stats = {
        "ilaclar_insert": 0,
        "ilaclar_update": 0,
        "bolumler_delete": 0,
        "bolumler_insert": 0,
        "hatali_insert": 0,
    }

    processed = 0
    for record in records:
        file_name = record.get("file_name", "")
        if not file_name:
            continue

        ilac_id, action = upsert_ilac(conn, record)
        if action == "insert":
            stats["ilaclar_insert"] += 1
        else:
            stats["ilaclar_update"] += 1

        stats["bolumler_delete"] += 1
        stats["bolumler_insert"] += replace_sections(conn, ilac_id, record.get("sections", []) or [])

        processed += 1
        if processed % 50 == 0:
            conn.commit()

    stats["hatali_insert"] += append_failures(conn, failures)
    conn.commit()

    conn.execute("DELETE FROM ilaclar_fts")
    conn.execute("INSERT INTO ilaclar_fts (rowid, drug_name, active_substance) SELECT id, drug_name, active_substance FROM ilaclar")
    conn.commit()

    print(json.dumps({"dry_run": False, **stats}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Adim 4: SQLite DB yazma")
    parser.add_argument("--dry-run", action="store_true", help="Gercek yazma yapmadan sayimlari goster")
    args = parser.parse_args()

    _, records, failures = load_sources()

    conn = ensure_db_connection()
    try:
        if args.dry_run:
            run_dry_run(conn, records, failures)
        else:
            run_write(conn, records, failures)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
