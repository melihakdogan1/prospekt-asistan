#!/usr/bin/env python3
"""
TİTCK KT Pilot Parser & Chunker v2.4 (60 Dosyalık Tabakalı Örneklem + 2-Pass OCR Onarıcı)
Yenilikler:
1. SANDACE 'saklaması' ('n' harfi unutulmuş) ve DUOBAK 'igin kullandtr' başlık yakalayıcı.
2. 2-Pass OCR Self-Corrector: Şablon ve morfoloji tabanlı otomatik kelime onarımı
   ('Bu Ham' -> 'Bu ilacı', 'saklanmast' -> 'saklanması', 'kuılınmadan' -> 'kullanmadan', 'k4ilerde' -> 'kişilerde').
3. Bölüm 4 sonundaki 'Yan etkilerin raporlanması' (TÜFAM) bloğunu tek satır değil, 5. Bölüme kadar tamamen keser.
4. 'Yardımcı Madde(ler)' varyasyonları ve 'Ramipril          2,5 mg' / 'Çok yaygın          :' tab boşluğu temizliği.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pymupdf

ROOT = Path(__file__).resolve().parent
META_FILE = ROOT / "titck_clean_metadata.json"
PROFIL_FILE = ROOT / "kt_profil_raporu.jsonl"
KT_DIR = ROOT.parent / "data" / "kt"
OUT_DIR = ROOT.parent / "data" / "pilot_parsed_60"


def normalize(text: str) -> str:
    text = text.replace("\u00ad", "").replace("\u200b", "").replace("\u200c", "")
    text = unicodedata.normalize("NFKD", text.casefold().replace("ı", "i"))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip()


def compile_tr(pattern_tr: str) -> re.Pattern:
    tr_map = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    return re.compile(pattern_tr.translate(tr_map), re.IGNORECASE)


CANONICAL_TITLES = {
    1: "Nedir ve ne için kullanılır?",
    2: "Kullanmadan önce dikkat edilmesi gerekenler",
    3: "Nasıl kullanılır?",
    4: "Olası yan etkiler nelerdir?",
    5: "Saklanması",
    6: "Aşağıdaki bilgiler bu ilacı uygulayacak sağlık personeli içindir",
}

SECTION_TOKEN_BUDGETS: Dict[int, Tuple[int, int]] = {
    1: (320, 430),
    2: (360, 450),
    3: (360, 450),
    4: (340, 430),
    5: (300, 420),
    6: (360, 450),
}

# [GÜNCELLEME 1]: SANDACE ('saklaması'), DUOBAK ('igin kullandtr') ve BOSELİX ('vc ne için') dahil tam toleranslı desenler
SECTION_REGEXES = {
    1: compile_tr(
        r"^(?:[1-6Ilit]\s*[.)\-]?\s*)?.{0,200}?nedir\s+(?:ve|vc|ye)\s+ne\s+i[cçg]in\s+kullan[i1ltruüdh]{2,7}[\s:?]*$"
    ),
    2: compile_tr(
        r"^(?:[1-6Ilit]\s*[.)\-]?\s*)?.{0,200}?ku[l1i]{1,3}[ae]?nmadan\s+[oö]nce\s+dikkat\s+[ec]dilmesi\s+[gçc]?erekenler[\s:?]*$"
    ),
    3: compile_tr(
        r"^(?:[1-6Ilit]\s*[.)\-]?\s*)?.{0,200}?nas[i1lduü]{1,3}\s+kullan[i1ltruüdh]{2,7}[\s:?]*$"
    ),
    4: compile_tr(
        r"^(?:[1-6Ilit]\s*[.)\-]?\s*)?(?:olas[i1l]\s+)?yan\s+etkiler[i]?(?:\s+nelerdir)?[\s:?]*$"
    ),
    5: compile_tr(
        r"^(?:[1-6Ilit]\s*[.)\-]?\s*)?.{0,200}?sakla(?:n|nı)?mas[i1rlt]?[\s:?]*$"
    ),
    6: compile_tr(
        r"^(?:aşağıdaki\s+bilgiler|bu\s+bölümdeki\s+bilgiler|bilgiler).{0,120}?(?:sağlık\s+personeli|doktorlar|hekimler).{0,80}?içindir"
    ),
}

RE_PAGE_NUM = re.compile(r"^\s*(?:sayfa\s*)?(\d{1,4})(?:\s*/\s*(\d{1,4}))?\s*$")
RE_EBYS = compile_tr(
    r"belge\s+(?:doğrulama\s+kodu|takip\s+adresi)|elektronik\s+imza|güvenli\s+elektronik|5070\s+sayılı|https?://www\.turkiye\.gov\.tr"
)
RE_ROUTE = compile_tr(
    r"(?:ağız|oral|damar|kas\s+içi|eklem|deri|cilt|göz|kulak|burun|rektal|vajinal|inhalasyon|solunum|dil\s+altı|subkutan|intramüsküler|intravenöz)"
    r".{0,80}?"
    r"(?:uygulanır|kullanılır|alınır|enjekte\s+edilir|infüze\s+edilir|damlatılır|sürülür|yapıştırılarak|püskürtülür|inhale\s+edilir|yoluyla|yolu\s+ile)"
)
RE_STERIL_LINE = compile_tr(r"^(?:steril|apiröjen|steril\s+süspansiyon|steril\s+çözelti|steril\s+toz)$")
RE_APPROVAL = compile_tr(r"bu\s+kullanma\s+talimatı\s*(.{0,60}?)\s*tarihinde\s+onaylanmıştır")
RE_DATE = re.compile(r"\b\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{2,4}\b")

# Bölüm 4 sonundaki raporlama bloğu başlangıcı
RE_REPORTING_START = compile_tr(
    r"^yan\s+etkilerin\s+raporlanmas[i1nr]|^eğer\s+bu\s+kullanma\s+talimatında\s+(?:bahsi\s+geçmeyen|değinilmeyen)\s+herhangi\s+bir\s+yan\s+etki"
)
# Bölüm 4 içindeki standart sıklık tanım satırları ("Çok yaygın : 10 hastanın en az 1'inde...")
RE_FREQ_DEFINITION = compile_tr(
    r"^(?:çok\s+yaygın|yaygın|yaygın\s+olmayan|seyrek|çok\s+seyrek|bilinmeyen|bilinmiyor)\s*:\s*(?:10|100|1\.?000|10\.?000|eldeki\s+verilerden)"
)

RE_BULLET_START = re.compile(r"^[\s•●▪◦▪▸►\uf0b7\-–—*]+")
RE_SUPER_UNICODE = re.compile(r"[¹²³⁴⁵⁶⁷⁸⁹⁰]+")

# --- [GÜNCELLEME 2]: 2-PASS DETERMINİSTİK OCR ONARICI (Şablon + Kelime İçi Harf Onarımı) ---
OCR_PHRASE_FIXES = [
    (re.compile(r"\bBu\s+Ham\s+kullanmaya", re.IGNORECASE), "Bu ilacı kullanmaya"),
    (re.compile(r"\bTurn\s+ilaclar\s+gibi\b", re.IGNORECASE), "Tüm ilaçlar gibi"),
    (re.compile(r"\bGÜFAM\)"), "(TÜFAM)"),
    (re.compile(r"\bk4ilerde\b", re.IGNORECASE), "kişilerde"),
    (re.compile(r"\bgOnilen\b", re.IGNORECASE), "görülen"),
    (re.compile(r"\ba§agidaki\b|\bapgidaki\b", re.IGNORECASE), "aşağıdaki"),
    (re.compile(r"\bku[ıi1]l?nmadan\b|\bkullqnmaılan\b", re.IGNORECASE), "kullanmadan"),
    (re.compile(r"\bdikkat\s+cdilmesi\b|\bdikkat\s+eılilmesi\b", re.IGNORECASE), "dikkat edilmesi"),
    (re.compile(r"\bçrekenler\b", re.IGNORECASE), "gerekenler"),
    (re.compile(r"\bkullan[uü]r\?", re.IGNORECASE), "kullanılır?"),
    (re.compile(r"\bkullanıIır\b"), "kullanılır"),
    (re.compile(r"\bkullandtr\?|\bkullandir\?", re.IGNORECASE), "kullanılır?"),
    (re.compile(r"\bsaklanmas[rt]\b", re.IGNORECASE), "saklanması"),
    (re.compile(r"\bolast\s+yan\b", re.IGNORECASE), "Olası yan"),
    (re.compile(r"\bknrşılaşırsanız\b|\bkarsdasirsantz\b|\bkarşılaşffSanlz\b", re.IGNORECASE), "karşılaşırsanız"),
    (re.compile(r"\bi\|acın\b|\biIacın\b"), "ilacın"),
]


def repair_ocr_line(text: str) -> Tuple[str, int]:
    """
    2 Aşamalı (2-Pass) OCR ve Boşluk Onarıcı:
    Pass 1: Tab boşluklarını, iki nokta (:) öncesi/sonrası kaymaları ve kelime içi |, ] bozulmalarını düzeltir.
    Pass 2: Bilinen TİTCK şablon ve OCR harf kırılmalarını onarır.
    Dönüş: (onarılmış_metin, yapılan_onarım_sayısı)
    """
    fixes = 0
    orig = text

    # Pass 1: Tab boşluklarını ve iki nokta üst üste hizalamalarını normalize et ("Çok yaygın     : 10" -> "Çok yaygın: 10")
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+:\s+", ": ", text)
    text = re.sub(r"\s+([,.;?!])", r"\1", text)

    # Kelime ortasında OCR'ın 'l' veya 'ı' yerine bastığı '|' karakterini düzelt (ör: 'aza|madır' -> 'azalmadır', 'karsılaşırsan|z' -> 'karşılaşırsanız')
    def fix_pipe(m: re.Match) -> str:
        nonlocal fixes
        fixes += 1
        left, right = m.group(1), m.group(2)
        if right in "zZ":
            return f"{left}ı{right.lower()}"
        return f"{left}l{right}"

    text = re.sub(r"([a-zA-ZçğıöşüÇĞİÖŞÜ])\|([a-zA-ZçğıöşüÇĞİÖŞÜ])", fix_pipe, text)

    # Kelime ortasında 'l' yerine basılan ']' karakterini düzelt (ör: 'bu]unan' -> 'bulunan', 'bi]dirim' -> 'bildirim')
    text, n_br = re.subn(r"([a-zA-ZçğıöşüÇĞİÖŞÜ])\]([a-zA-ZçğıöşüÇĞİÖŞÜ])", r"\1l\2", text)
    fixes += n_br

    # Pass 2: TİTCK şablon ve morfolojik OCR sözlüğü
    for pattern, replacement in OCR_PHRASE_FIXES:
        text, n_sub = pattern.subn(replacement, text)
        fixes += n_sub

    return text.strip(), fixes


class TokenCounter:
    def __init__(self):
        self.tokenizer = None
        self.mode = "calibrated_turkish_subword"
        try:
            from transformers import AutoTokenizer
            self.tokenizer = AutoTokenizer.from_pretrained(
                "intfloat/multilingual-e5-base", local_files_only=True
            )
            self.mode = "huggingface_multilingual_e5"
        except Exception:
            pass

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self.tokenizer is not None:
            return len(self.tokenizer.encode(text, add_special_tokens=False))
        tokens = 0
        for w in text.split():
            clean_w = re.sub(r"[^\w]", "", w)
            punct = len(w) - len(clean_w)
            if len(clean_w) <= 4:
                tokens += 1 + punct
            else:
                tokens += 1 + int((len(clean_w) - 3) / 3.6) + punct
        return tokens


TOKEN_COUNTER = TokenCounter()


@dataclass
class TableData:
    page: int
    headers: List[str]
    rows: List[List[str]]
    bbox: List[float]


@dataclass
class FigureRef:
    page: int
    bbox: List[float]


@dataclass
class SectionChunk:
    chunk_id: str
    parent_block_id: str
    section_id: int
    canonical_section_title: str
    printed_section_heading: str
    heading_hierarchy: List[str] = field(default_factory=list)
    ground_truth_passage: str = ""
    retrieval_text: str = ""
    has_visual_figure: bool = False
    figures: List[FigureRef] = field(default_factory=list)
    boxed_warnings: List[str] = field(default_factory=list)
    start_page: int = 1
    end_page: int = 1
    token_count: int = 0
    has_overlap_from_previous: bool = False
    tables: List[TableData] = field(default_factory=list)


@dataclass
class DrugDocument:
    drug_id: str
    primary_drug_name: str
    alternative_names: List[str]
    pdf_extracted_drug_title: Optional[str]
    active_substance: str
    excipients: Optional[str]
    administration_route: Optional[str]
    marketing_authorisation_holder: Optional[str]
    manufacturer: Optional[str]
    portal_approval_date: Optional[str]
    pdf_approval_date: Optional[str]
    has_black_triangle: bool
    has_health_staff_section: bool
    ocr_repairs_applied: int
    extraction_quality: str
    sections_captured: List[int]
    pdf_filename: str
    pdf_sha256: str
    source_url: str
    total_pages: int
    chunks: List[SectionChunk] = field(default_factory=list)


def clean_bullet_prefix(text: str) -> str:
    return RE_BULLET_START.sub("", text).strip()


def extract_clean_lines(page: pymupdf.Page, page_num: int) -> Tuple[List[dict], int]:
    data = page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
    raw_lines = []
    page_h = page.rect.height
    total_repairs = 0

    for bi, block in enumerate(data["blocks"]):
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            spans = line["spans"]
            if not spans:
                continue

            max_size = max(s["size"] for s in spans)
            parts = []
            has_bold = False

            for s in spans:
                stext = s["text"]
                if not stext.strip():
                    parts.append(" ")
                    continue
                font_lower = s["font"].casefold()
                if "bold" in font_lower or "black" in font_lower or (s["flags"] & 16):
                    has_bold = True

                # Üst indis (superscript) dipnot rakamlarını tamamen sil
                if stext.strip().isdigit() and s["size"] < max_size * 0.78 and (s["flags"] & 1 or s["size"] < 8.0):
                    continue

                stext = RE_SUPER_UNICODE.sub("", stext)
                parts.append(stext)

            txt = "".join(parts).strip()
            if not txt:
                continue

            # 2-Pass OCR ve Tab Boşluğu Onarımını çalıştır
            txt, rep_count = repair_ocr_line(txt)
            total_repairs += rep_count

            norm = normalize(txt)
            bbox = list(line["bbox"])

            if RE_PAGE_NUM.fullmatch(norm) and (bbox[1] > 0.86 * page_h or bbox[3] < 0.09 * page_h):
                continue
            if RE_EBYS.search(norm):
                continue

            raw_lines.append({
                "text": txt,
                "norm": norm,
                "bbox": bbox,
                "page": page_num,
                "block": bi,
                "size": max_size,
                "is_bold": has_bold,
            })

    # Ayrı düşmüş madde işaretlerini veya numara başlıklarını (1., I., •, ▪) yanındaki satırla birleştir
    removed = set()
    for i, marker in enumerate(raw_lines):
        if not re.fullmatch(r"(?:[1-9Ilit]\s*[.)\-]?|[•●▪◦\uf0b7\-–—])", marker["norm"]):
            continue
        candidates = []
        for j, line in enumerate(raw_lines):
            if i == j or j in removed:
                continue
            gap = line["bbox"][0] - marker["bbox"][2]
            # SANDACE gibi nokta ile metin arasında geniş tab boşluğu olanları da yakalamak için 95 pt'ye kadar izin ver
            if -5 <= gap <= 95 and abs(line["bbox"][3] - marker["bbox"][3]) <= max(4.5, marker["size"] * 0.45):
                candidates.append((abs(gap), j))
        if candidates:
            _, j = min(candidates)
            target = raw_lines[j]
            target["text"] = f"{marker['text']} {target['text']}"
            target["norm"] = normalize(target["text"])
            target["is_bold"] = target["is_bold"] or marker["is_bold"]
            target["bbox"] = [
                min(marker["bbox"][0], target["bbox"][0]),
                min(marker["bbox"][1], target["bbox"][1]),
                max(marker["bbox"][2], target["bbox"][2]),
                max(marker["bbox"][3], target["bbox"][3]),
            ]
            removed.add(i)

    cleaned = [l for i, l in enumerate(raw_lines) if i not in removed]
    return sorted(cleaned, key=lambda x: (x["bbox"][1], x["bbox"][0])), total_repairs


def extract_page_figures(page: pymupdf.Page, page_num: int) -> List[FigureRef]:
    figs = []
    try:
        for im in page.get_image_info():
            rect = pymupdf.Rect(im["bbox"]) & page.rect
            if rect.is_empty or rect.width < 45 or rect.height < 45:
                continue
            if (rect.get_area() / max(page.rect.get_area(), 1)) > 0.80:
                continue
            figs.append(FigureRef(page=page_num, bbox=[round(v, 2) for v in tuple(rect)]))
    except Exception:
        pass
    return figs


def extract_page_tables(page: pymupdf.Page, page_num: int) -> List[TableData]:
    tables_out = []
    try:
        drawings = page.get_drawings()
        if len(drawings) > 600:
            return []
        detected = page.find_tables(paths=drawings)
        for t in detected.tables:
            if (t.col_count >= 2 or t.row_count >= 2) and not (t.col_count == 1 and t.row_count == 1):
                extracted = t.extract()
                if not extracted:
                    continue
                cleaned_rows = [
                    [re.sub(r"\s+", " ", (cell or "")).strip() for cell in row]
                    for row in extracted
                ]
                headers = cleaned_rows[0] if cleaned_rows else []
                rows = cleaned_rows[1:] if len(cleaned_rows) > 1 else []
                tables_out.append(
                    TableData(
                        page=page_num,
                        headers=headers,
                        rows=rows,
                        bbox=[round(v, 2) for v in t.bbox],
                    )
                )
    except Exception:
        pass
    return tables_out


def match_main_section(line_norm: str, is_bold: bool) -> Optional[int]:
    if len(line_norm) > 220:
        return None
    if "bakiniz" in line_norm or line_norm.startswith("("):
        return None
    for sec_id, pattern in SECTION_REGEXES.items():
        if pattern.search(line_norm):
            has_num_prefix = bool(re.match(r"^[1-6Ilit]\s*[.)\-]?", line_norm))
            if has_num_prefix or is_bold or len(line_norm) < 105 or sec_id == 6:
                return sec_id
    return None


def is_subheading(line: dict) -> bool:
    txt = line["text"].strip()
    norm = line["norm"]
    if not line["is_bold"]:
        return False
    if len(txt) < 3 or len(txt) > 140:
        return False
    if RE_BULLET_START.match(txt):
        return False
    # Yan etkilerdeki sıklık tanım satırlarını ('Çok yaygın: 10 hastanın...') alt başlık sanma
    if RE_FREQ_DEFINITION.search(norm):
        return False
    if txt.endswith(".") and not any(k in norm for k in ("kullanmayiniz", "dikkatli kullaniniz", "uyarilar")):
        return False
    return True


def parse_single_kt(pdf_path: Path, meta_record: dict) -> DrugDocument:
    raw_bytes = pdf_path.read_bytes()
    sha256_hex = hashlib.sha256(raw_bytes).hexdigest()

    all_lines: List[dict] = []
    all_figures: List[FigureRef] = []
    all_tables: List[TableData] = []
    doc_repairs = 0

    with pymupdf.open(pdf_path) as doc:
        total_pages = doc.page_count
        for pi in range(total_pages):
            pn = pi + 1
            page = doc[pi]
            p_lines, p_rep = extract_clean_lines(page, pn)
            all_lines.extend(p_lines)
            doc_repairs += p_rep
            all_figures.extend(extract_page_figures(page, pn))
            all_tables.extend(extract_page_tables(page, pn))

    full_norm_text = " ".join(l["norm"] for l in all_lines)
    full_raw_text = "\n".join(l["text"] for l in all_lines)

    # 1. Künye Bilgileri Çıkarımı (Yardımcı Madde(ler) ve Tab Boşluklarına Tam Uyumlu)
    first_page_lines = [l for l in all_lines if l["page"] == 1]
    extracted_title = None
    route_str = None
    active_str = meta_record.get("active_ingredient") or ""
    excipients_str = None

    for idx, l in enumerate(first_page_lines):
        if "kullanma talimat" in l["norm"] and extracted_title is None:
            title_parts = []
            for nxt in first_page_lines[idx + 1 : idx + 4]:
                if (
                    RE_ROUTE.search(nxt["norm"])
                    or RE_STERIL_LINE.match(nxt["norm"])
                    or "etkin madde" in nxt["norm"]
                    or "yardimci madde" in nxt["norm"]
                ):
                    break
                title_parts.append(nxt["text"])
            if title_parts:
                extracted_title = " ".join(title_parts)

        if route_str is None and RE_ROUTE.search(l["norm"]) and len(l["text"]) < 130:
            route_str = clean_bullet_prefix(l["text"])

        if re.search(r"^\W*etkin\s+madde", l["norm"]):
            cleaned = clean_bullet_prefix(l["text"])
            active_str = re.sub(r"^etkin\s+madde(?:\(ler\)|ler)?\s*:?\s*", "", cleaned, flags=re.IGNORECASE).strip()

        if re.search(r"^\W*yardimci\s+madde", l["norm"]):
            cleaned = clean_bullet_prefix(l["text"])
            exc_parts = [
                re.sub(r"^yard[ıi]mc[ıi]\s+madde(?:\(ler\)|ler)?\s*:?\s*", "", cleaned, flags=re.IGNORECASE).strip()
            ]
            for nxt in first_page_lines[idx + 1 : idx + 5]:
                if (
                    "bu ilaci kullanmaya" in nxt["norm"]
                    or "bu kullanma talimatinda" in nxt["norm"]
                    or "ek izlemeye" in nxt["norm"]
                ):
                    break
                exc_parts.append(nxt["text"])
            excipients_str = " ".join(exc_parts).strip()

    holder_str, maker_str, pdf_app_date = None, None, None
    for idx, l in enumerate(all_lines):
        if "ruhsat sahibi" in l["norm"] and holder_str is None:
            nxt_txt = all_lines[idx + 1]["text"] if idx + 1 < len(all_lines) else ""
            holder_str = f"{l['text']} {nxt_txt}".strip()
        if ("uretim yeri" in l["norm"] or "uretici" in l["norm"]) and maker_str is None:
            nxt_txt = all_lines[idx + 1]["text"] if idx + 1 < len(all_lines) else ""
            maker_str = f"{l['text']} {nxt_txt}".strip()

    m_app = RE_APPROVAL.search(full_norm_text)
    if m_app:
        d_match = RE_DATE.search(m_app.group(1))
        if d_match:
            pdf_app_date = d_match.group(0)

    # BOSELİX gibi ters üçgenin vektör çizim olduğu durumlarda 'ek izlemeye tabidir' metni %100 yakalar
    has_triangle = ("ek izlemeye tabidir" in full_norm_text) or ("▼" in full_raw_text) or ("\u25bc" in full_raw_text)

    # 2. İçindekiler (TOC) Fihristini ve 1. Sayfa Standart Kutusunu Atla
    toc_end_idx = -1
    for idx, l in enumerate(all_lines):
        if l["page"] <= 2 and "basliklari yer almaktadir" in l["norm"]:
            toc_end_idx = idx
            break

    if toc_end_idx == -1:
        for idx, l in enumerate(all_lines):
            if l["page"] == 1 and match_main_section(l["norm"], l["is_bold"]) == 5:
                toc_end_idx = idx
                break

    body_lines = all_lines[toc_end_idx + 1 :] if toc_end_idx >= 0 else all_lines

    # 3. Gövdeyi Ana Bölümlere (1..6) Ayır ve TÜFAM Raporlama Bloğunu Tamamen Kes
    sections_raw: Dict[int, dict] = {}
    current_sec_id: Optional[int] = None
    skip_until_idx = -1
    in_reporting_boilerplate = False

    for i, l in enumerate(body_lines):
        if i <= skip_until_idx:
            continue

        # 1. sayfadaki standart uyarı kutusu ve ek izleme cümlelerini gövdeye karıştırma
        if l["page"] == 1 and (
            "bu kullanma talimatini dikkatlice" in l["norm"]
            or "bu ilac kisisel olarak sizin icin" in l["norm"]
            or "yuksek veya dusuk doz kullanmayiniz" in l["norm"]
            or "ek izlemeye tabidir" in l["norm"]
            or "bolumun sonuna bakabilirsiniz" in l["norm"]
        ):
            continue

        combined_norm = l["norm"]
        combined_text = l["text"]
        matched_sec = match_main_section(combined_norm, l["is_bold"])

        if matched_sec is None:
            for span_len in (2, 3):
                if i + span_len <= len(body_lines):
                    lookahead = body_lines[i : i + span_len]
                    if all(x["page"] == l["page"] for x in lookahead):
                        test_norm = " ".join(x["norm"] for x in lookahead)
                        any_bold = any(x["is_bold"] for x in lookahead)
                        m_sec = match_main_section(test_norm, any_bold)
                        if m_sec is not None:
                            matched_sec = m_sec
                            combined_text = " ".join(x["text"] for x in lookahead)
                            skip_until_idx = i + span_len - 1
                            break

        if matched_sec is not None:
            if matched_sec not in sections_raw:
                current_sec_id = matched_sec
                in_reporting_boilerplate = False  # Yeni bölüm başladığında TÜFAM kilidini aç
                sections_raw[current_sec_id] = {
                    "printed_heading": combined_text,
                    "lines": [],
                }
                continue

        if current_sec_id is not None:
            # [GÜNCELLEME 3]: Bölüm 4'te 'Yan etkilerin raporlanması' başladığı an 5. Bölüme kadar tamamını kes!
            if current_sec_id == 4:
                if RE_REPORTING_START.search(l["norm"]):
                    in_reporting_boilerplate = True
                if in_reporting_boilerplate:
                    continue

            if current_sec_id in (5, 6) and (
                l["norm"].startswith("ruhsat sahibi")
                or l["norm"].startswith("uretim yeri")
                or l["norm"].startswith("lisans sahibi")
                or "onaylanmistir" in l["norm"]
            ):
                continue
            sections_raw[current_sec_id]["lines"].append(l)

    # 4. Alt Başlık Paketleme (Sub-block Packing)
    chunks: List[SectionChunk] = []
    chunk_counter = 0

    for sec_id in sorted(sections_raw.keys()):
        sec_info = sections_raw[sec_id]
        sec_lines = sec_info["lines"]
        if not sec_lines:
            continue

        canonical_title = CANONICAL_TITLES[sec_id]
        printed_heading = sec_info["printed_heading"]
        target_budget, max_ceiling = SECTION_TOKEN_BUDGETS.get(sec_id, (350, 440))

        sub_blocks = []
        curr_sub_title = "Genel Bilgiler"
        curr_sub_lines = []

        for l in sec_lines:
            if is_subheading(l):
                if curr_sub_lines:
                    sub_blocks.append((curr_sub_title, curr_sub_lines))
                curr_sub_title = l["text"].rstrip(":")
                curr_sub_lines = []
            else:
                curr_sub_lines.append(l)

        if curr_sub_lines:
            sub_blocks.append((curr_sub_title, curr_sub_lines))

        base_prefix_sample = (
            f"[İlaç: {meta_record['name']} | "
            f"Etkin Madde: {active_str or 'Belirtilmemiş'} | "
            f"Bölüm: {sec_id}. {canonical_title}]"
        )
        base_prefix_tokens = TOKEN_COUNTER.count(base_prefix_sample) + 15

        def emit_chunk(sub_titles: List[str], markdown_lines: List[str], raw_line_objs: List[dict], is_overlap: bool, p_id: str):
            nonlocal chunk_counter
            if not markdown_lines or not raw_line_objs:
                return
            chunk_counter += 1
            cid = f"{sha256_hex[:8]}_s{sec_id}_c{chunk_counter}"
            gt_passage = "\n".join(markdown_lines)

            s_page = min(x["page"] for x in raw_line_objs)
            e_page = max(x["page"] for x in raw_line_objs)
            y_min = min(x["bbox"][1] for x in raw_line_objs if x["page"] == s_page)
            y_max = max(x["bbox"][3] for x in raw_line_objs if x["page"] == e_page)

            bound_figs = [
                fig for fig in all_figures
                if s_page <= fig.page <= e_page
                and (fig.page != s_page or fig.bbox[3] >= y_min - 40)
                and (fig.page != e_page or fig.bbox[1] <= y_max + 80)
            ]
            bound_tables = [tbl for tbl in all_tables if s_page <= tbl.page <= e_page]
            boxed = [
                x["text"] for x in raw_line_objs
                if x["is_bold"] and ("dikkat" in x["norm"] or "uyari" in x["norm"] or "kullanmayiniz" in x["norm"])
            ]

            clean_hierarchy = [t for t in sub_titles if t != "Genel Bilgiler"]
            locative_prefix = (
                f"[İlaç: {meta_record['name']} | "
                f"Etkin Madde: {active_str or 'Belirtilmemiş'} | "
                f"Bölüm: {sec_id}. {canonical_title}"
                + (f" | Alt Başlıklar: {' / '.join(clean_hierarchy[:3])}" if clean_hierarchy else "")
                + "]"
            )
            ret_text = f"{locative_prefix}\n\n{gt_passage}"

            chunks.append(
                SectionChunk(
                    chunk_id=cid,
                    parent_block_id=p_id,
                    section_id=sec_id,
                    canonical_section_title=canonical_title,
                    printed_section_heading=printed_heading,
                    heading_hierarchy=clean_hierarchy,
                    ground_truth_passage=gt_passage,
                    retrieval_text=ret_text,
                    has_visual_figure=bool(bound_figs),
                    figures=bound_figs,
                    boxed_warnings=boxed[:3],
                    start_page=s_page,
                    end_page=e_page,
                    token_count=TOKEN_COUNTER.count(ret_text),
                    has_overlap_from_previous=is_overlap,
                    tables=bound_tables,
                )
            )

        packed_titles: List[str] = []
        packed_md: List[str] = []
        packed_objs: List[dict] = []
        pack_group_idx = 0

        for sub_title, b_lines in sub_blocks:
            block_md = [f"### {sub_title}"]
            for l in b_lines:
                txt = l["text"]
                if RE_BULLET_START.match(txt):
                    block_md.append(f"- {clean_bullet_prefix(txt)}")
                else:
                    block_md.append(txt)

            block_str = "\n".join(block_md)
            block_tokens = TOKEN_COUNTER.count(block_str)

            if base_prefix_tokens + block_tokens > max_ceiling:
                if packed_md:
                    emit_chunk(packed_titles, packed_md, packed_objs, False, f"{sha256_hex[:8]}_s{sec_id}_p{pack_group_idx}")
                    pack_group_idx += 1
                    packed_titles, packed_md, packed_objs = [], [], []

                w_md: List[str] = []
                w_objs: List[dict] = []
                is_cont = False
                p_id = f"{sha256_hex[:8]}_s{sec_id}_p{pack_group_idx}"
                pack_group_idx += 1

                for m_line, l_obj in zip(block_md[1:], b_lines):
                    header_str = f"### {sub_title}" + (" (Devamı)" if is_cont else "")
                    cand_str = "\n".join([header_str] + w_md + [m_line])
                    if base_prefix_tokens + TOKEN_COUNTER.count(cand_str) > target_budget and w_md:
                        emit_chunk([sub_title], [header_str] + w_md, w_objs, is_cont, p_id)
                        w_md = w_md[-2:] + [m_line]
                        w_objs = w_objs[-2:] + [l_obj]
                        is_cont = True
                    else:
                        w_md.append(m_line)
                        w_objs.append(l_obj)

                if w_md:
                    header_str = f"### {sub_title}" + (" (Devamı)" if is_cont else "")
                    emit_chunk([sub_title], [header_str] + w_md, w_objs, is_cont, p_id)
                continue

            combined_md_str = "\n".join(packed_md + block_md)
            if packed_md and (base_prefix_tokens + TOKEN_COUNTER.count(combined_md_str) > target_budget):
                emit_chunk(packed_titles, packed_md, packed_objs, False, f"{sha256_hex[:8]}_s{sec_id}_p{pack_group_idx}")
                pack_group_idx += 1
                packed_titles = [sub_title]
                packed_md = block_md
                packed_objs = list(b_lines)
            else:
                packed_titles.append(sub_title)
                packed_md.extend(block_md)
                packed_objs.extend(b_lines)

        if packed_md:
            emit_chunk(packed_titles, packed_md, packed_objs, False, f"{sha256_hex[:8]}_s{sec_id}_p{pack_group_idx}")

    captured_secs = sorted(sections_raw.keys())
    has_1_to_5 = all(s in captured_secs for s in (1, 2, 3, 4, 5))
    quality = "golden" if has_1_to_5 else ("partial_review" if captured_secs else "ocr_or_unstructured")

    return DrugDocument(
        drug_id=str(meta_record.get("id", pdf_path.stem)),
        primary_drug_name=meta_record["name"],
        alternative_names=meta_record.get("alternative_names", []),
        pdf_extracted_drug_title=extracted_title,
        active_substance=active_str,
        excipients=excipients_str,
        administration_route=route_str,
        marketing_authorisation_holder=holder_str,
        manufacturer=maker_str,
        portal_approval_date=meta_record.get("kt_date"),
        pdf_approval_date=pdf_app_date,
        has_black_triangle=has_triangle,
        has_health_staff_section=(6 in captured_secs),
        ocr_repairs_applied=doc_repairs,
        extraction_quality=quality,
        sections_captured=captured_secs,
        pdf_filename=pdf_path.name,
        pdf_sha256=sha256_hex,
        source_url=meta_record.get("kt_url", ""),
        total_pages=total_pages,
        chunks=chunks,
    )


def select_stratified_60(profile_rows: List[dict], valid_filenames: set) -> List[Tuple[str, str]]:
    buckets = {
        "1_golden_standard": [],
        "2_repaired_missing_heading": [],
        "3_with_tables": [],
        "4_two_column_layout": [],
        "5_figures_and_staff": [],
        "6_edge_case_headers": [],
    }
    quotas = {
        "1_golden_standard": 15,
        "2_repaired_missing_heading": 10,
        "3_with_tables": 10,
        "4_two_column_layout": 10,
        "5_figures_and_staff": 10,
        "6_edge_case_headers": 5,
    }
    chosen = set()

    if "00b436cc28510.pdf" in valid_filenames:
        buckets["2_repaired_missing_heading"].append("00b436cc28510.pdf")
        chosen.add("00b436cc28510.pdf")

    for r in profile_rows:
        fname = r["filename"]
        if fname not in valid_filenames or fname in chosen:
            continue
        if r.get("ocr_candidate_pages"):
            continue

        body_secs = r.get("body_sections_found", [])
        has_tables = any(bool(p.get("table_candidates")) for p in r.get("pages", []))
        has_cols = any(bool(p.get("column_or_table_candidate")) for p in r.get("pages", []))
        has_imgs = any(p.get("image_count", 0) > 0 for p in r.get("pages", []))
        has_staff = bool(r.get("health_staff_heading_pages"))
        doc_type = r.get("document_type")

        if 1 <= len(body_secs) < 5 and len(buckets["2_repaired_missing_heading"]) < quotas["2_repaired_missing_heading"]:
            buckets["2_repaired_missing_heading"].append(fname)
            chosen.add(fname)
        elif has_tables and len(buckets["3_with_tables"]) < quotas["3_with_tables"]:
            buckets["3_with_tables"].append(fname)
            chosen.add(fname)
        elif has_imgs and has_staff and len(buckets["5_figures_and_staff"]) < quotas["5_figures_and_staff"]:
            buckets["5_figures_and_staff"].append(fname)
            chosen.add(fname)
        elif has_cols and len(buckets["4_two_column_layout"]) < quotas["4_two_column_layout"]:
            buckets["4_two_column_layout"].append(fname)
            chosen.add(fname)
        elif doc_type in ("mixed", "unknown") and len(body_secs) == 5 and len(buckets["6_edge_case_headers"]) < quotas["6_edge_case_headers"]:
            buckets["6_edge_case_headers"].append(fname)
            chosen.add(fname)
        elif len(body_secs) == 5 and doc_type == "KT" and len(buckets["1_golden_standard"]) < quotas["1_golden_standard"]:
            buckets["1_golden_standard"].append(fname)
            chosen.add(fname)

        if len(chosen) >= 60:
            break

    result = []
    for b_name, flist in buckets.items():
        for fn in flist:
            result.append((fn, b_name))
    return result


def main():
    with open(META_FILE, "r", encoding="utf-8") as f:
        meta_list = json.load(f)
    meta_by_pdf = {r["kt_pdf"]: r for r in meta_list if r.get("kt_pdf")}

    profile_rows = []
    with open(PROFIL_FILE, "r", encoding="utf-8") as f:
        for line in f:
            profile_rows.append(json.loads(line))

    sample_60 = select_stratified_60(profile_rows, set(meta_by_pdf.keys()))
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 75)
    print(f"🧪 60 DOSYALIK PİLOT PARSER v2.4 (2-PASS OCR ONARICI + ALT BAŞLIK PAKETLEME)")
    print("=" * 75)

    started = time.perf_counter()
    bucket_stats = Counter()
    bucket_golden = Counter()
    sec_avg_tokens = Counter()
    sec_chunk_counts = Counter()
    total_chunks = 0
    total_ocr_repairs = 0
    repaired_examples = []

    for fname, bucket in sample_60:
        pdf_path = KT_DIR / fname
        meta_rec = meta_by_pdf[fname]
        parsed_doc = parse_single_kt(pdf_path, meta_rec)

        bucket_stats[bucket] += 1
        total_ocr_repairs += parsed_doc.ocr_repairs_applied

        if parsed_doc.extraction_quality == "golden":
            bucket_golden[bucket] += 1
            if bucket in ("2_repaired_missing_heading", "4_two_column_layout"):
                repaired_examples.append(
                    (fname, parsed_doc.primary_drug_name, parsed_doc.sections_captured, parsed_doc.ocr_repairs_applied)
                )

        total_chunks += len(parsed_doc.chunks)
        for c in parsed_doc.chunks:
            sec_avg_tokens[c.section_id] += c.token_count
            sec_chunk_counts[c.section_id] += 1

        out_file = OUT_DIR / f"{pdf_path.stem}.json"
        with open(out_file, "w", encoding="utf-8") as out_f:
            json.dump(asdict(parsed_doc), out_f, ensure_ascii=False, indent=2)

    elapsed = time.perf_counter() - started
    print(f"\n✅ PİLOT PARSER v2.4 TAMAMLANDI ({elapsed:.2f} saniye)")
    print("-" * 75)
    print(f"{'KATEGORİ':<32} | {'SEÇİLEN':<8} | {'5 BÖLÜM TAM (GOLDEN)':<22}")
    print("-" * 75)
    for b in sorted(bucket_stats.keys()):
        print(f"{b:<32} | {bucket_stats[b]:<8} | {bucket_golden[b]}/{bucket_stats[b]}")
    print("-" * 75)
    print("Bölümlere Göre Ortalama Chunk Token Dağılımı:")
    for sid in sorted(sec_chunk_counts.keys()):
        avg_t = sec_avg_tokens[sid] / max(sec_chunk_counts[sid], 1)
        print(f"  Bölüm {sid} ({CANONICAL_TITLES[sid][:32]:<32}): {sec_chunk_counts[sid]:>4} chunk | Ort: {avg_t:.0f} token")
    print("-" * 75)
    print(f"Toplam Üretilen Atomik Chunk          : {total_chunks} (Dosya başı ort: {total_chunks/max(len(sample_60),1):.1f})")
    print(f"2-Pass Onarıcı İle Düzeltilen Satır   : {total_ocr_repairs}")
    print("=" * 75)

    if repaired_examples:
        print("\n🔧 Onarılan Zorlu Dosyalar (DUOBAK, SANDACE, BOSELİX vb.):")
        for fn, dname, secs, reps in repaired_examples:
            print(f"  - {fn} ({dname[:30]:<30}) -> Bölümler: {secs} | OCR Onarımı: {reps} adet")


if __name__ == "__main__":
    main()