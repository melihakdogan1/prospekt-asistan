"""
TİTCK KT ve KÜB PDF İndirici (Arşiv ve Tam URL Desteği)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

import requests
from requests.adapters import HTTPAdapter

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "titck_clean_metadata.json"
DATA_DIR = ROOT.parent / "data"
FAILURES = ROOT / "failed_downloads.json"
RETRY_STATUSES = {429, 500, 502, 503, 504}

LOCAL = threading.local()
SESSIONS: list[requests.Session] = []
SESSIONS_LOCK = threading.Lock()


@dataclass(frozen=True, order=True)
class Task:
    kind: str
    url: str
    filename: str

    @property
    def target(self) -> Path:
        folder = "kt2" if self.kind == "KT" else "kub2"
        return DATA_DIR / folder / self.filename


class RateLimiter:
    def __init__(self, requests_per_second: float) -> None:
        self.interval = 1.0 / requests_per_second
        self.next_start = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next_start)
            self.next_start = start + self.interval
        if start > now:
            time.sleep(start - now)


def sanitize_filename(name: str) -> str:
    decoded = unquote(name).strip()
    clean = re.sub(r'[\\/*?:"<>|]', "_", decoded)
    return clean if clean.lower().endswith(".pdf") else f"{clean}.pdf"


def load_tasks(source: Path) -> list[Task]:
    with source.open(encoding="utf-8") as file:
        records = json.load(file)

    tasks: set[Task] = set()
    for r in records:
        kt_url = r.get("kt_url")
        kt_pdf = r.get("kt_pdf")
        if kt_url and kt_pdf:
            tasks.add(Task("KT", kt_url, sanitize_filename(kt_pdf)))

        kub_url = r.get("kub_url")
        kub_pdf = r.get("kub_pdf")
        if kub_url and kub_pdf:
            tasks.add(Task("KÜB", kub_url, sanitize_filename(kub_pdf)))

    return sorted(tasks)


def valid_pdf(path: Path) -> bool:
    try:
        size = path.stat().st_size
        if size < 256:
            return False
        with path.open("rb") as file:
            if not file.read(5).startswith(b"%PDF"):
                return False
            file.seek(max(0, size - 32768))
            return b"%%EOF" in file.read()
    except OSError:
        return False


def get_session() -> requests.Session:
    session = getattr(LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://titck.gov.tr/kubkt",
            "Accept": "application/pdf, application/octet-stream;q=0.9, */*;q=0.1",
        })
        session.mount("https://", HTTPAdapter(pool_connections=5, pool_maxsize=5))
        LOCAL.session = session
        with SESSIONS_LOCK:
            SESSIONS.append(session)
    return session


def retry_delay(header: str | None, attempt: int) -> float:
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            try:
                date = parsedate_to_datetime(header)
                if date.tzinfo is not None:
                    return max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return min(30.0, 1.5 ** attempt)


def download(task: Task, limiter: RateLimiter, attempts: int) -> tuple[bool, str]:
    target = task.target
    if valid_pdf(target):
        return True, "zaten mevcut"

    target.parent.mkdir(parents=True, exist_ok=True)
    session = get_session()
    last_error = "Bilinmeyen hata"

    for attempt in range(1, attempts + 1):
        temporary: Path | None = None
        retry_after: str | None = None
        should_retry = True

        try:
            limiter.wait()
            with session.get(task.url, stream=True, timeout=(10, 45)) as response:
                if response.status_code != 200:
                    last_error = f"HTTP {response.status_code}"
                    retry_after = response.headers.get("Retry-After")
                    should_retry = response.status_code in RETRY_STATUSES
                else:
                    with tempfile.NamedTemporaryFile(
                        mode="wb",
                        dir=target.parent,
                        prefix=f".{target.name}.",
                        suffix=".tmp",
                        delete=False,
                    ) as output:
                        temporary = Path(output.name)
                        for chunk in response.iter_content(chunk_size=128 * 1024):
                            if chunk:
                                output.write(chunk)

                    content_length = response.headers.get("Content-Length")
                    if content_length and temporary.stat().st_size != int(content_length):
                        raise ValueError("Content-Length uyuşmazlığı")

                    if not valid_pdf(temporary):
                        raise ValueError("Geçersiz PDF formatı")

                    os.replace(temporary, target)
                    return True, "indirildi"

        except (requests.RequestException, OSError, ValueError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink(missing_ok=True)

        if not should_retry or attempt == attempts:
            break
        time.sleep(retry_delay(retry_after, attempt))

    return False, last_error


def write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary = Path(file.name)
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="TİTCK PDF İndirici (Arşiv Destekli)")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--rps", type=float, default=8.0)
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args()

    tasks = load_tasks(SOURCE)
    pending = [task for task in tasks if not valid_pdf(task.target)]
    skipped = len(tasks) - len(pending)

    print("=" * 65)
    print("🚀 GÜNCEL ARŞİV İNDİRME HATTI BAŞLATILDI")
    print(f"Toplam Tekil Görev : {len(tasks)}")
    print(f"Mevcut (Doğrulanan): {skipped}")
    print(f"İndirilecek Kalan  : {len(pending)}")
    print(f"Worker / Hız (RPS): {args.workers} worker | {args.rps} req/sec")
    print("=" * 65)

    if not pending:
        print("Tüm dosyalar zaten eksiksiz mevcut!")
        return 0

    limiter = RateLimiter(args.rps)
    failures: dict[Task, str] = {}
    completed = 0
    started = time.monotonic()
    work = iter(pending)

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            active = {}

            def submit_next() -> bool:
                try:
                    task = next(work)
                except StopIteration:
                    return False
                active[executor.submit(download, task, limiter, args.attempts)] = task
                return True

            for _ in range(min(len(pending), args.workers * 2)):
                submit_next()

            while active:
                done, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in done:
                    task = active.pop(future)
                    try:
                        ok, detail = future.result()
                    except Exception as exc:
                        ok, detail = False, f"Beklenmeyen: {type(exc).__name__}: {exc}"

                    completed += 1
                    if not ok:
                        failures[task] = detail

                    submit_next()

                if completed % 25 == 0 or completed == len(pending):
                    elapsed = max(time.monotonic() - started, 0.001)
                    speed = completed / elapsed
                    remaining_sec = (len(pending) - completed) / speed if speed > 0 else 0
                    print(
                        f"\rİlerleme: {completed}/{len(pending)} (%{completed*100/len(pending):.1f}) | "
                        f"Hata: {len(failures)} | Hız: {speed:.1f} dosya/sn | "
                        f"Kalan: ~{remaining_sec/60:.1f} dk",
                        end="",
                        flush=True,
                    )

                if completed % 200 == 0 and failures:
                    write_json_atomic(
                        FAILURES,
                        [
                            {"type": t.kind, "filename": t.filename, "url": t.url, "error": err}
                            for t, err in sorted(failures.items())
                        ],
                    )

    finally:
        for session in SESSIONS:
            session.close()

        if failures:
            write_json_atomic(
                FAILURES,
                [
                    {"type": t.kind, "filename": t.filename, "url": t.url, "error": err}
                    for t, err in sorted(failures.items())
                ],
            )

    elapsed_total = (time.monotonic() - started) / 60
    print("\n" + "=" * 65)
    print("✅ İNDİRME TAMAMLANDI")
    print(f"Toplam Süre       : {elapsed_total:.2f} dakika")
    print(f"Başarılı İndirilen : {completed - len(failures)}")
    print(f"Hatalı Dosyalar   : {len(failures)}")
    print("=" * 65)

    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nİşlem durduruldu.")
        raise SystemExit(130)