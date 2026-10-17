from __future__ import annotations

import argparse
import html
import json
import os
import re
import tempfile
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

PAGE_URL = "https://titck.gov.tr/kubkt"
DATA_URL = "https://titck.gov.tr/getkubktviewdatatable"

# JS içindeki olası tüm token tanımlarını yakalayan esnek desenler
TOKEN_PATTERNS = [
    re.compile(r"""["']?_token["']?\s*[:=]\s*["']([^"']+)["']"""),
    re.compile(r"""<meta\s+name=["']csrf-token["']\s+content=["']([^"']+)["']"""),
    re.compile(r"""name=["']_token["']\s+value=["']([^"']+)["']"""),
]


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }
    )

    retry = Retry(
        total=5,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(("GET", "POST")),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def get_token(session: requests.Session) -> str:
    response = session.get(PAGE_URL, timeout=(10, 30))
    response.raise_for_status()

    for pattern in TOKEN_PATTERNS:
        match = pattern.search(response.text)
        if match:
            # Grup 1 token değeridir
            return html.unescape(match.group(1))

    raise RuntimeError(
        f"Sayfa açıldı ({response.status_code}), ancak CSRF token bulunamadı."
    )


def make_payload(start: int, length: int, draw: int, token: str) -> dict[str, str]:
    payload = {
        "_token": token,
        "draw": str(draw),
        "start": str(start),
        "length": str(length),
        "search[value]": "",
        "search[regex]": "false",
        "order[0][column]": "4",
        "order[0][dir]": "desc",
    }

    columns = (
        "name",
        "element",
        "firmName",
        "confirmationDateKub",
        "confirmationDateKt",
        "documentPathKub",
        "documentPathKt",
    )

    for index, name in enumerate(columns):
        payload[f"columns[{index}][data]"] = name
        payload[f"columns[{index}][name]"] = ""
        payload[f"columns[{index}][searchable]"] = "true"
        payload[f"columns[{index}][orderable]"] = (
            "false" if index >= 5 else "true"
        )
        payload[f"columns[{index}][search][value]"] = ""
        payload[f"columns[{index}][search][regex]"] = "false"

    return payload


def fetch_page(
    session: requests.Session,
    start: int,
    length: int,
    draw: int,
    token: str,
) -> tuple[dict, str]:
    payload = make_payload(start, length, draw, token)
    headers = {
        "Referer": PAGE_URL,
        "Origin": "https://titck.gov.tr",
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRF-TOKEN": token,
    }

    response = session.post(
        DATA_URL, data=payload, headers=headers, timeout=(10, 60)
    )

    if response.status_code == 419:
        # Token süresi dolduysa yenile
        token = get_token(session)
        payload["_token"] = token
        headers["X-CSRF-TOKEN"] = token
        response = session.post(
            DATA_URL, data=payload, headers=headers, timeout=(10, 60)
        )

    if response.status_code != 200:
        raise RuntimeError(
            f"POST başarısız: HTTP {response.status_code}; "
            f"start={start}; yanıt={response.text[:200]!r}"
        )

    result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError(f"Beklenmeyen JSON biçimi; start={start}")

    return result, token


def save_atomic(output: Path, records: list[dict]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary_path = Path(file.name)
            json.dump(records, file, ensure_ascii=False, indent=2)
            file.write("\n")

        os.replace(temporary_path, output)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TİTCK KÜB/KT ham metadata indirici"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "titck_raw_metadata.json",
    )
    parser.add_argument(
        "--page-size", type=int, default=100
    )  # Sitenin standart değeri
    args = parser.parse_args()

    records: list[dict] = []
    expected_total: int | None = None
    draw = 1

    with create_session() as session:
        print("[1/3] Oturum başlatılıyor ve CSRF Token alınıyor...")
        token = get_token(session)
        print(f"Token başarıyla alındı: {token[:6]}***")

        print("[2/3] Veri çekme döngüsü başladı...")
        while expected_total is None or len(records) < expected_total:
            result, token = fetch_page(
                session=session,
                start=len(records),
                length=args.page_size,
                draw=draw,
                token=token,
            )

            total = int(result.get("recordsTotal", 0))
            batch = result.get("data", [])

            if expected_total is None:
                expected_total = total
                print(f"Sunucudaki toplam kayıt sayısı: {expected_total}")

            if not batch:
                print("\nDaha fazla kayıt dönmedi, döngü sonlandırılıyor.")
                break

            records.extend(batch)
            print(
                f"\rİlerleme: {len(records)} / {expected_total} (%{len(records)*100/expected_total:.1f})",
                end="",
                flush=True,
            )

            draw += 1
            time.sleep(0.15)  # Sunucuyu boğmamak için kısa bekleme

    print(f"\n[3/3] Alınan toplam {len(records)} kayıt diske kaydediliyor...")
    save_atomic(args.output, records)
    print(f"İşlem tamamlandı: {args.output.resolve()}")


if __name__ == "__main__":
    main()