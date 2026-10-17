#!/usr/bin/env python3
"""
TİTCK KT/KÜB PDF Profiler v2.1.0
Layout-aware, regex-tolerant, deep metadata & structural analysis.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import time
import unicodedata
import pymupdf

VERSION = '2.1.0'

# [DÜZELTME 1]: Rakamdan sonra nokta/parantez zorunluluğu kaldırıldı (ALIPZA vb. '2 ALIPZA' biçimleri için)
SECTION_PATTERNS = {
    1: re.compile(r'^1\s*[.)\-]?\s*.{0,250}?nedir\s+ve\s+ne\s+icin\s+kullanilir'),
    2: re.compile(r'^2\s*[.)\-]?\s*.{0,250}?kullanmadan\s+once\s+dikkat'),
    3: re.compile(r'^3\s*[.)\-]?\s*.{0,250}?nasil\s+kullanilir'),
    4: re.compile(r'^4\s*[.)\-]?\s*(?:olasi\s+)?yan\s+etkiler'),
    5: re.compile(r'^5\s*[.)\-]?\s*.{0,250}?saklanmasi'),
}

PAGE_NUMBER = re.compile(r'^\s*(?:sayfa\s*)?(\d{1,4})(?:\s*/\s*(\d{1,4}))?\s*$')

# [DÜZELTME 2]: Hekimler, doktorlar ve alternatif kalıpları kapsayan genişletilmiş sağlık personeli regexi
STAFF = re.compile(r'(?:asagidaki\s+bilgiler|bu\s+bolumdeki\s+bilgiler|bilgiler).{0,120}?(?:saglik\s+personeli|doktorlar|hekimler).{0,80}?icindir')

APPROVAL = re.compile(r'bu\s+kullanma\s+talimati\s*(.{0,100}?)\s*tarihinde\s+onaylanmistir')
DATE = re.compile(r'\b\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{2,4}\b')
ROUTE = re.compile(r'(?:agiz|oral|damar|kas|deri|goz|kulak|burun|rektal|vajinal|inhalasyon|solunum).{0,70}?(?:uygulanir|kullanilir|alinir|yoluyla|yolu ile)')
OWNER = re.compile(r'ruhsat\s+sahibi')
MAKER = re.compile(r'uretim\s+yeri|uretici')
EBYS = re.compile(r'belge\s+(?:dogrulama\s+kodu|takip\s+adresi)|elektronik\s+imza|guvenli\s+elektronik')


def normalize(text: str) -> str:
    text = text.replace('\u00ad', '').replace('\u200b', '').replace('\u200c', '')
    text = unicodedata.normalize('NFKD', text.casefold().replace('ı', 'i'))
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', text).strip()


def header_match(compact: str) -> tuple[str, str] | None:
    exact = {'kullanmatalimati': 'KT', 'kisaurunbilgisi': 'KUB', 'kisaurunbilgileri': 'KUB'}
    if compact in exact:
        return exact[compact], 'exact_normalized'
    
    # [DÜZELTME 3]: Regex tabanlı esnek font bozulma toleransı
    if re.search(r'kullanma.*tal[i1!l]mat', compact):
        return 'KT', 'suspected_font_encoding'
    if re.search(r'kisa.*urun.*b[i1!l]lg', compact):
        return 'KUB', 'suspected_font_encoding'
        
    return None


def quality_flags(text: str) -> tuple[float, list[str], list[str]]:
    replacement = text.count('\ufffd')
    private = sum(0xE000 <= ord(c) <= 0xF8FF for c in text)
    ocr, review = [], []
    
    if replacement / max(len(text), 1) >= 0.01 and re.search(r'\w\ufffd\w', text):
        ocr.append('replacement_characters_inside_words')
    elif replacement:
        review.append('replacement_glyphs_review')
        
    if private:
        review.append('private_use_glyphs_may_be_bullets_or_symbols')
        
    if len(re.findall(r'[øØÕùú÷]', text)) >= 3:
        ocr.append('suspected_font_encoding_corruption')
        
    return (replacement + private) / max(len(text), 1), ocr, review


def union_area(rectangles: list[tuple[float, float, float, float]]) -> float:
    if not rectangles:
        return 0.0
    xs = sorted({x for r in rectangles for x in (r[0], r[2])})
    area = 0.0
    for x0, x1 in zip(xs, xs[1:]):
        ys = sorted((r[1], r[3]) for r in rectangles if r[0] < x1 and r[2] > x0)
        covered = 0.0
        if ys:
            lo, hi = ys[0]
            for a, b in ys[1:]:
                if a > hi:
                    covered += hi - lo
                    lo, hi = a, b
                else:
                    hi = max(hi, b)
            covered += hi - lo
        area += (x1 - x0) * covered
    return area


def lines_from_page(page: pymupdf.Page) -> list[dict]:
    data = page.get_text('dict', flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
    lines = []
    for bi, block in enumerate(data['blocks']):
        if block['type'] != 0:
            continue
        for line in block['lines']:
            spans = line['spans']
            txt = ''.join(s['text'] for s in spans).strip()
            if txt:
                lines.append({
                    'text': txt,
                    'norm': normalize(txt),
                    'bbox': list(line['bbox']),
                    'block': bi,
                    'size': max(s['size'] for s in spans),
                    'ocr_font': any('glyphless' in s['font'].casefold() for s in spans)
                })

    removed = set()
    for i, marker in enumerate(lines):
        if not re.fullmatch(r'(?:[1-9]\s*[.)\-]?|[•●▪◦\-–—])', marker['norm']):
            continue
        candidates = []
        for j, line in enumerate(lines):
            if i == j or j in removed:
                continue
            gap = line['bbox'][0] - marker['bbox'][2]
            if 0 <= gap <= 50 and abs(line['bbox'][3] - marker['bbox'][3]) <= max(3, marker['size'] * 0.3):
                candidates.append((gap, j))
        if candidates:
            _, j = min(candidates)
            line = lines[j]
            line['text'] = marker['text'] + ' ' + line['text']
            line['norm'] = normalize(line['text'])
            line['bbox'] = [
                min(marker['bbox'][0], line['bbox'][0]),
                min(marker['bbox'][1], line['bbox'][1]),
                max(marker['bbox'][2], line['bbox'][2]),
                max(marker['bbox'][3], line['bbox'][3])
            ]
            removed.add(i)

    return sorted((l for i, l in enumerate(lines) if i not in removed), key=lambda x: (x['bbox'][1], x['bbox'][0]))


def evidence(line: dict, page_number: int) -> dict:
    return {
        'page': page_number,
        'text': line['text'][:400],
        'bbox': [round(x, 2) for x in line['bbox']]
    }


def section_hits(lines: list[dict], page_number: int) -> list[dict]:
    intro = next((i for i, l in enumerate(lines) if 'bu kullanma talimatinda' in l['norm']), None)
    end = next((i for i, l in enumerate(lines) if 'basliklari yer almaktadir' in l['norm']), None)
    hits = []
    
    for i, line in enumerate(lines):
        # [DÜZELTME]: Satır başındaki rakam kontrolü esnetildi
        if not re.match(r'^[1-5]\s*[.)\-]?', line['norm']):
            continue
        text = line['norm']
        for k in range(3):
            if k:
                if i + k >= len(lines):
                    break
                nxt, prev = lines[i + k], lines[i + k - 1]
                if nxt['block'] != line['block']:
                    if abs(nxt['bbox'][0] - line['bbox'][0]) > 25:
                        break
                    if nxt['bbox'][1] - prev['bbox'][3] > max(15, line['size'] * 1.5):
                        break
                if re.match(r'^\d+\s*[.)\-]', nxt['norm']):
                    break
                text += ' ' + nxt['norm']
            
            found = next((n for n, p in SECTION_PATTERNS.items() if p.search(text)), None)
            if found:
                hit = evidence(line, page_number)
                hit.update(
                    section=found,
                    normalized_match=text[:400],
                    zone='toc' if intro is not None and end is not None and intro < i < end else 'body_candidate'
                )
                hits.append(hit)
                break
    return hits


def ordered_chain(hits: list[dict]) -> list[dict]:
    chosen = []
    needed = 1
    for hit in hits:
        if hit['section'] == needed:
            chosen.append(hit)
            needed += 1
    return chosen if needed == 6 else []


def profile_pdf(job: tuple[str, dict]) -> dict:
    filename, options = job
    path = Path(filename)
    started = time.perf_counter()
    result = {
        'path': str(path),
        'filename': path.name,
        'status': 'ok',
        'error': None,
        'pages': [],
        'page_errors': [],
        'header_candidates': [],
        'heading_candidates': []
    }

    try:
        stat = path.stat()
        result.update(size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)
        
        if options.get('hash'):
            with path.open('rb') as f:
                h = hashlib.sha256()
                for chunk in iter(lambda: f.read(1024 * 1024), b''):
                    h.update(chunk)
            result['sha256'] = h.hexdigest()

        with pymupdf.open(path) as doc:
            if not doc.is_pdf:
                raise ValueError('File is not a PDF')
            if doc.needs_pass:
                result.update(status='encrypted', error='Password required')
                return result
            
            result['page_count'] = doc.page_count
            if not doc.page_count:
                raise ValueError('PDF has no pages')

            texts, first_lines = [], []
            owner_pages, maker_pages, staff_pages = [], [], []

            for pi in range(doc.page_count):
                pn = pi + 1
                try:
                    page = doc[pi]
                    lines = lines_from_page(page)
                    text = '\n'.join(l['text'] for l in lines)
                    norm = normalize(text)
                    texts.append(text)
                    
                    if pi == 0:
                        first_lines = lines

                    result['heading_candidates'] += section_hits(lines, pn)

                    for l in lines:
                        match = header_match(re.sub(r'\s+', '', l['norm']))
                        if match:
                            ev = evidence(l, pn)
                            ev.update(
                                kind=match[0],
                                match_mode=match[1],
                                centered_candidate=abs((l['bbox'][0] + l['bbox'][2]) / 2 - page.rect.width / 2) < page.rect.width * 0.08
                            )
                            result['header_candidates'].append(ev)

                    if pi < 2:
                        for i, l in enumerate(lines[:20]):
                            for count in (2, 3):
                                subset = lines[i:i + count]
                                if len(subset) != count:
                                    continue
                                compact = ''.join(re.sub(r'\s+', '', x['norm']) for x in subset)
                                match = header_match(compact)
                                if match:
                                    ev = evidence(l, pn)
                                    ev.update(kind=match[0], match_mode=match[1], split_lines=count)
                                    result['header_candidates'].append(ev)

                    rects, image_count, image_error = [], 0, None
                    try:
                        for im in page.get_image_info():
                            rect = pymupdf.Rect(im['bbox']) & page.rect
                            if not rect.is_empty:
                                rects.append(tuple(rect))
                                image_count += 1
                    except Exception as exc:
                        image_error = str(exc)

                    coverage = union_area(rects) / max(page.rect.get_area(), 1)
                    meaningful = sum(c.isalnum() for c in text)
                    bad_ratio, ocr_reasons, review = quality_flags(text)
                    sparse = meaningful < options['min_chars']

                    if sparse and coverage >= 0.5:
                        ocr_reasons.append('sparse_text_with_large_raster')
                    if sparse and not ocr_reasons:
                        review.append('sparse_page_may_be_blank_graphic_or_scan')
                    if coverage >= 0.8 and not sparse:
                        review.append('full_page_raster_with_text_layer_quality_unknown')
                    if any(l['ocr_font'] for l in lines):
                        review.append('ocr_font_detected_quality_unknown')

                    left = [l for l in lines if l['bbox'][2] < page.rect.width * 0.49 and len(l['text']) > 8]
                    right = [l for l in lines if l['bbox'][0] > page.rect.width * 0.51 and len(l['text']) > 8]
                    pairs = sum(any(abs(a['bbox'][1] - b['bbox'][1]) < max(a['size'], b['size']) for b in right) for a in left)

                    numbers, ebys, bullets = [], [], Counter()
                    for l in lines:
                        m = PAGE_NUMBER.fullmatch(l['norm'])
                        if m and (l['bbox'][1] > 0.88 * page.rect.height or l['bbox'][3] < 0.08 * page.rect.height):
                            cx = (l['bbox'][0] + l['bbox'][2]) / 2 / page.rect.width
                            numbers.append({
                                **evidence(l, pn),
                                'style': 'n/total' if m[2] else 'n',
                                'position': 'left' if cx < 0.35 else 'right' if cx > 0.65 else 'center',
                                'matches_physical_page': int(m[1]) == pn,
                                'matches_page_count': int(m[2]) == doc.page_count if m[2] else None
                            })
                        if EBYS.search(l['norm']):
                            ebys.append(evidence(l, pn))
                        m_b = re.match(r'^\s*([•●▪◦\-–—])\s*', l['text'])
                        if m_b:
                            bullets[m_b[1]] += 1

                    drawings_count = None
                    tables = None
                    layout_errors = []
                    drawings = None
                    table_state = 'not_requested'

                    if options.get('deep') or options.get('tables'):
                        try:
                            drawings = page.get_drawings()
                            drawings_count = len(drawings)
                        except Exception as exc:
                            layout_errors.append('drawings: ' + str(exc))

                    # [DÜZELTME 4]: Akıllı Tablo Tespiti (Uyarı kutusu gibi 1x1'lik kutuları eleyen filtre)
                    if options.get('tables'):
                        limit = options.get('max_table_paths', 600)  # Sınır 600'e yükseltildi
                        if drawings is None:
                            table_state = 'error'
                            layout_errors.append('tables skipped: drawings unavailable')
                        elif limit and drawings_count > limit:
                            table_state = 'skipped_complex_graphics'
                            review.append('table_detection_skipped_complex_graphics')
                        else:
                            try:
                                detected = page.find_tables()
                                valid_tables = []
                                for t in detected.tables:
                                    # Sadece en az 2 sütun veya en az 2 satır içeren gerçek tabloları al
                                    if t.col_count >= 2 or t.row_count >= 2:
                                        # 1x1 tek hücreli uyarı kutularını yok say
                                        if not (t.col_count == 1 and t.row_count == 1):
                                            valid_tables.append({
                                                'bbox': [round(v, 2) for v in t.bbox],
                                                'rows': t.row_count,
                                                'columns': t.col_count
                                            })
                                tables = valid_tables
                                table_state = 'detected_candidates' if tables else 'no_candidates'
                            except Exception as exc:
                                table_state = 'error'
                                layout_errors.append('tables: ' + str(exc))

                    if OWNER.search(norm):
                        owner_pages.append(pn)
                    if MAKER.search(norm):
                        maker_pages.append(pn)
                    if STAFF.search(norm):
                        staff_pages.append(pn)

                    result['pages'].append({
                        'page': pn,
                        'width': round(page.rect.width, 2),
                        'height': round(page.rect.height, 2),
                        'rotation': page.rotation,
                        'char_count': len(text),
                        'alnum_count': meaningful,
                        'bad_character_ratio': round(bad_ratio, 4),
                        'image_count': image_count,
                        'image_error': image_error,
                        'image_bbox_coverage': round(coverage, 4),
                        'ocr_candidate_reasons': ocr_reasons,
                        'review_reasons': review,
                        'column_or_table_candidate': pairs >= 4,
                        'aligned_column_rows': pairs,
                        'page_number_candidates': numbers,
                        'ebys_mentions': ebys,
                        'bullet_counts': dict(bullets),
                        'drawing_path_count': drawings_count,
                        'table_candidates': tables,
                        'table_detection_state': table_state,
                        'layout_errors': layout_errors
                    })

                except Exception as exc:
                    result['page_errors'].append({'page': pn, 'error': str(exc)})

            joined = '\n'.join(texts)
            normalized = normalize(joined)
            kinds = {h['kind'] for h in result['header_candidates']}
            
            result['document_type'] = 'mixed' if len(kinds) > 1 else next(iter(kinds), 'unknown')
            result['has_kt_header_first_page'] = any(
                h['kind'] == 'KT' and h['page'] == 1 for h in result['header_candidates']
            )
            
            toc = [h for h in result['heading_candidates'] if h['zone'] == 'toc']
            body = [h for h in result['heading_candidates'] if h['zone'] == 'body_candidate']
            
            result['toc_sections_found'] = sorted({h['section'] for h in toc})
            result['body_sections_found'] = sorted({h['section'] for h in body})
            result['body_ordered_chain'] = ordered_chain(body)
            result['kt_five_body_headings_candidate'] = result['document_type'] == 'KT' and bool(result['body_ordered_chain'])
            result['ocr_candidate_pages'] = [p['page'] for p in result['pages'] if p['ocr_candidate_reasons']]
            result['review_pages'] = [p['page'] for p in result['pages'] if p['review_reasons'] or p['layout_errors'] or p['image_error']]
            result['owner_mentions_pages'] = owner_pages
            result['manufacturer_mentions_pages'] = maker_pages
            result['health_staff_heading_pages'] = staff_pages
            result['additional_monitoring_text'] = 'ek izlemeye tabidir' in normalized
            result['triangle_glyph_in_text'] = '▼' in joined or '▾' in joined or '\u25bc' in joined

            result['approval_mentions'] = []
            for m in APPROVAL.finditer(normalized):
                value = m[1].strip()
                date = DATE.search(value)
                result['approval_mentions'].append({
                    'value': value,
                    'state': 'numeric_date_candidate' if date else 'blank' if not re.search(r'[a-z0-9]', value) else 'other_text_review'
                })

            # [DÜZELTME 5]: Çok satırlı ilaç isimlerini yakalayan akıllı birleştirici
            result['first_page_opening_candidates'] = {}
            header = next((h for h in result['header_candidates'] if h['kind'] == 'KT' and h['page'] == 1), None)
            if header:
                after = [l for l in first_lines if l['bbox'][1] > header['bbox'][3] - 0.5]
                name_lines = []
                for l in after:
                    if ROUTE.search(l['norm']) or re.search(r'^\W*etkin\s+madde', l['norm']) or 'kullanmaya baslamadan once' in l['norm']:
                        break
                    if len(l['text']) > 2 and not EBYS.search(l['norm']) and not PAGE_NUMBER.fullmatch(l['norm']):
                        name_lines.append(l)
                    if len(name_lines) >= 2:
                        break
                
                if name_lines:
                    combined_text = ' '.join(l['text'] for l in name_lines)
                    first_bbox = name_lines[0]['bbox']
                    last_bbox = name_lines[-1]['bbox']
                    merged_bbox = [min(first_bbox[0], last_bbox[0]), min(first_bbox[1], last_bbox[1]),
                                   max(first_bbox[2], last_bbox[2]), max(first_bbox[3], last_bbox[3])]
                    result['first_page_opening_candidates']['drug_name_line'] = {
                        'page': 1, 'text': combined_text[:400], 'bbox': [round(x, 2) for x in merged_bbox]
                    }
                    result['first_page_opening_candidates']['header_to_name_gap_pt'] = round(first_bbox[1] - header['bbox'][3], 2)

            result['first_page_opening_candidates'].update(
                route_lines=[evidence(l, 1) for l in first_lines if ROUTE.search(l['norm'])][:5],
                active_ingredient_lines=[evidence(l, 1) for l in first_lines if re.search(r'^\W*etkin\s+madde', l['norm'])][:3],
                excipient_lines=[evidence(l, 1) for l in first_lines if re.search(r'^\W*yardimci\s+madde', l['norm'])][:3]
            )

            if result['page_errors']:
                result['status'] = 'partial'

    except Exception as exc:
        result.update(status='error', error=f'{type(exc).__name__}: {exc}')
    finally:
        result['elapsed_seconds'] = round(time.perf_counter() - started, 4)
        result['profile_version'] = VERSION
        result['options'] = options

    return result


def bounded_results(jobs: list[tuple[str, dict]], workers: int):
    if workers == 1:
        yield from map(profile_pdf, jobs)
        return

    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn')) as pool:
        iterator = iter(jobs)
        pending = set()
        for _ in range(workers * 2):
            job = next(iterator, None)
            if job is None:
                break
            pending.add(pool.submit(profile_pdf, job))

        while pending:
            completed, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                yield future.result()
                job = next(iterator, None)
                if job is not None:
                    pending.add(pool.submit(profile_pdf, job))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='PDF directory')
    parser.add_argument('--output', type=Path, required=True, help='JSONL path')
    parser.add_argument('--workers', type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument('--deep', action='store_true', help='Count vector drawing paths')
    parser.add_argument('--tables', action='store_true', help='Run smart table detector')
    parser.add_argument('--max-table-paths', type=int, default=600, help='Max vector paths before skip')
    parser.add_argument('--hash', action='store_true', help='Compute SHA256')
    parser.add_argument('--resume', action='store_true', help='Resume previous run')
    parser.add_argument('--limit', type=int, help='Limit files for testing')
    parser.add_argument('--min-chars', type=int, default=150)
    args = parser.parse_args()

    if not args.input.is_dir():
        parser.error('Input directory does not exist')

    files = sorted(p.resolve() for p in args.input.rglob('*') if p.is_file() and p.suffix.lower() == '.pdf')
    if args.limit:
        files = files[:args.limit]
    if not files:
        parser.error('No PDF files found')

    options = {
        'deep': args.deep,
        'tables': args.tables,
        'hash': args.hash,
        'min_chars': args.min_chars,
        'max_table_paths': args.max_table_paths
    }

    cached = {}
    if args.resume:
        for source in (args.output, args.output.with_name(args.output.name + '.partial')):
            if not source.exists():
                continue
            with source.open(encoding='utf-8') as previous:
                for line in previous:
                    try:
                        r = json.loads(line)
                        if r.get('status') == 'ok' and r.get('profile_version') == VERSION and r.get('options') == options:
                            cached[r['path']] = r
                    except (ValueError, KeyError):
                        pass

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_name(args.output.name + '.partial')

    summary = {
        'profile_version': VERSION,
        'pymupdf_version': pymupdf.VersionBind,
        'options': options,
        'started_utc': datetime.now(timezone.utc).isoformat(),
        'total_files': len(files),
        'status_counts': Counter(),
        'document_type_counts': Counter(),
        'pages': 0,
        'kt_header_first_page_files': 0,
        'kt_five_body_headings_candidate_files': 0,
        'ocr_candidate_files': 0,
        'ocr_candidate_pages': 0,
        'review_files': 0,
        'additional_monitoring_text_files': 0,
        'health_staff_heading_files': 0,
        'table_candidate_files': 0,
        'table_detection_skipped_pages': 0,
        'cache_hits': 0,
        'analyzed_files': 0
    }

    started = time.perf_counter()
    jobs = []
    seen = 0

    def record(r, out):
        nonlocal seen
        out.write(json.dumps(r, ensure_ascii=False, separators=(',', ':')) + '\n')
        out.flush()
        seen += 1
        summary['status_counts'][r['status']] += 1
        summary['document_type_counts'][r.get('document_type', 'unknown')] += 1
        summary['pages'] += len(r['pages'])
        for key, field in [
            ('kt_header_first_page_files', 'has_kt_header_first_page'),
            ('kt_five_body_headings_candidate_files', 'kt_five_body_headings_candidate'),
            ('ocr_candidate_files', 'ocr_candidate_pages'),
            ('review_files', 'review_pages'),
            ('additional_monitoring_text_files', 'additional_monitoring_text'),
            ('health_staff_heading_files', 'health_staff_heading_pages')
        ]:
            summary[key] += bool(r.get(field))

        summary['ocr_candidate_pages'] += len(r.get('ocr_candidate_pages', []))
        summary['table_candidate_files'] += any(bool(p.get('table_candidates')) for p in r['pages'])
        summary['table_detection_skipped_pages'] += sum(p.get('table_detection_state') == 'skipped_complex_graphics' for p in r['pages'])

        if seen % 100 == 0 or seen == len(files):
            print(f'{seen}/{len(files)} | süre: {time.perf_counter()-started:.1f}s', flush=True)

    with temp.open('w', encoding='utf-8') as out:
        for path in files:
            r = cached.get(str(path))
            stat = path.stat()
            if r and (r['size_bytes'], r['mtime_ns']) == (stat.st_size, stat.st_mtime_ns):
                record(r, out)
                summary['cache_hits'] += 1
            else:
                jobs.append((str(path), options))

        for r in bounded_results(jobs, args.workers):
            record(r, out)
            summary['analyzed_files'] += 1

    temp.replace(args.output)
    summary['elapsed_seconds'] = round(time.perf_counter() - started, 4)
    summary['files_per_second'] = round(summary['analyzed_files'] / max(summary['elapsed_seconds'], 0.0001), 2)
    
    summary_path = args.output.with_suffix('.summary.json')
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"\nİşlem Tamamlandı! Özet Rapor: {summary_path.resolve()}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()