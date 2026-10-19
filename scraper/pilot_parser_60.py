#!/usr/bin/env python3
"""
TİTCK KT Pilot Parser & Chunker v2.9 (Hybrid Dictionary + Levenshtein + Strict Schema Gatekeeper)
Yenilikler:
1. Bölüm 6 (Sağlık Personeli) ve Makas Sembolü (✂---) Kesimi: Hastaya yönelik olmayan Bölüm 6 metinleri,
   tabloları ve görselleri (CLINIMIX-2, NİMBEX vb.) hasta RAG sistemine kesinlikle dahil edilmez.
2. 1. Sayfa Etkin Madde Tablosu (CLINIMIX-2) ve Çok Satırlı Etkin Madde (NİMBEX) Yakalayıcı.
3. CP1252 Türkçe Font Kodlama Onarıcısı ('3. Đçine çekilmesi (Đnhale edilmesi)' -> 'İçine çekilmesi').
4. Sabit '3 satır' yerine Tam Adım/Talimat Bloğu Eşleştirici (Sayfa geçişli görseller dahil).
5. Kanonik TİTCK Başlık/Alt Başlık Sözlüğü + Levenshtein Mesafesi + Katı İnsan Müdahalesi Bayrağı.
"""

from __future__ import annotations

import difflib
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

TOKENIZER_MODEL_NAME = "BAAI/bge-m3"

# CP1252 / Legacy PDF Font Kodlama Haritası ('Đnhale' -> 'İnhale')
LEGACY_TR_FONT_MAP = str.maketrans({
    "Đ": "İ",
    "đ": "i",
    "Þ": "Ş",
    "þ": "ş",
    "Ð": "Ğ",
    "ð": "ğ",
})


def fix_legacy_encoding(text: str) -> str:
    return text.translate(LEGACY_TR_FONT_MAP)


def normalize(text: str) -> str:
    text = fix_legacy_encoding(text)
    text = text.replace("\u00ad", "").replace("\u200b", "").replace("\u200c", "")
    text = unicodedata.normalize("NFKD", text.casefold().replace("ı", "i"))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip()


def compile_tr(pattern_tr: str) -> re.Pattern:
    tr_map = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    return re.compile(pattern_tr.translate(tr_map), re.IGNORECASE)


# Hasta Prospektüsü Kanonik Ana Bölümleri (Bölüm 6 hasta sisteminden tamamen izole edildi)
CANONICAL_TITLES = {
    1: "Nedir ve ne için kullanılır?",
    2: "Kullanmadan önce dikkat edilmesi gerekenler",
    3: "Nasıl kullanılır?",
    4: "Olası yan etkiler nelerdir?",
    5: "Saklanması",
}

SECTION_TOKEN_BUDGETS: Dict[int, Tuple[int, int]] = {
    1: (320, 430),
    2: (360, 450),
    3: (360, 450),
    4: (340, 430),
    5: (300, 420),
}

# ============================================================================
# HİBRİT BAŞLIK & ALT BAŞLIK SÖZLÜĞÜ (Levenshtein + Şablon Eşleştirme İçin)
# ============================================================================
MAIN_SECTION_TEMPLATES = {
    1: "nedir ve ne icin kullanilir",
    2: "kullanmadan once dikkat edilmesi gerekenler",
    3: "nasil kullanilir",
    4: "olasi yan etkiler nelerdir",
    5: "saklanmasi",
}

CANONICAL_SUBHEADING_TEMPLATES = [
    "asagidaki durumlarda kullanmayiniz",
    "asagidaki durumlarda dikkatli kullaniniz",
    "yiyecek ve icecek ile kullanilmasi",
    "hamilelik",
    "gebelik",
    "emzirme",
    "arac ve makine kullanimi",
    "iceriginde bulunan bazi yardimci maddeler hakkinda onemli bilgiler",
    "diger ilaclar ile birlikte kullanimi",
    "diger ilaclarla birlikte kullanimi",
    "uygun kullanim ve doz uygulama sikligi icin talimatlar",
    "uygulama yolu ve metodu",
    "degisik yas gruplari",
    "cocuklarda kullanimi",
    "cocuklarda kullanim",
    "yaslilarda kullanimi",
    "yaslilarda kullanim",
    "ozel kullanim durumlari",
    "bobrek yetmezligi",
    "karaciger yetmezligi",
    "bobrek karaciger yetmezligi",
    "kullanmaniz gerekenden daha fazla kullandiysaniz",
    "kullanmayi unutursaniz",
    "tedavi sonlandirildiginda olusabilecek etkiler",
    "tedavi sonlandirildigindaki olusabilecek etkiler",
    "cok yaygin",
    "yaygin",
    "yaygin olmayan",
    "seyrek",
    "cok seyrek",
    "bilinmiyor",
    "bilinmeyen",
    "cok yaygin gorulen yan etkiler",
    "yaygin gorulen yan etkiler",
    "yaygin olmayan yan etkiler",
    "seyrek gorulen yan etkiler",
    "cok seyrek gorulen yan etkiler",
]

# Makas çizgisi (✂---) veya Sağlık Personeli bölümü başlangıcı (Hasta prospektüsü kesim noktası)
RE_HEALTH_STAFF_CUTOFF = compile_tr(
    r"^(?:aşağıdaki\s+bilgiler|bu\s+bölümdeki\s+bilgiler|bilgiler).{0,120}?(?:sağlık\s+personeli|doktorlar|hekimler).{0,80}?içindir"
)
RE_SCISSORS_LINE = re.compile(r"[\u2700-\u2704✂]+|[-\s]{12,}")

# Yedek Regex (Ağır OCR bozulmaları için Levenshtein ile birlikte hibrit çalışır)
SECTION_REGEXES = {
    1: compile_tr(
        r"^(?:[1-6Ilit]\s*[.,)\-]?\s*)?.{0,55}?nedir\s+(?:ve|vc|ye)\s+ne\s+i[cçg]in\s+kullan[i1ltruüdh]{2,8}[\s:?]*$"
    ),
    2: compile_tr(
        r"^(?:[1-6Ilit]\s*[.,)\-]?\s*)?.{0,55}?ku[l1i]{1,3}[ae]?nmadan\s+[oö]nce\s+dikkat\s+[ec]dilmesi\s+[gçc]?e?rekenler[\s:?]*$"
    ),
    3: compile_tr(
        r"^(?:[1-6Ilit]\s*[.,)\-]?\s*)?.{0,55}?nas[i1lduütr]{1,4}\s+kullan[i1ltruüdh]{2,8}[\s:?]*$"
    ),
    4: compile_tr(
        r"^(?:[1-6Ilit]\s*[.,)\-]?\s*)?(?:olas[i1l]\s+)?yan\s+etkiler[i]?(?:\s+nelerdir)?[\s:?]*$"
    ),
    5: compile_tr(
        r"^(?:[1-6Ilit]\s*[.,)\-]?\s*)?.{0,55}?sakla(?:n|nı)?mas[i1rlt]?[\s:?]*$"
    ),
}

RE_NUMBERED_STEP_SUBHEADING = re.compile(
    r"^[1-9]\s*[.)]\s*[A-ZÇĞİÖŞÜ][a-zA-ZçğıöşüÇĞİÖŞÜ\s()'’-]{3,70}$"
)

RE_PAGE_NUM = re.compile(r"^\s*(?:sayfa\s*)?(\d{1,4})(?:\s*/\s*(\d{1,4}))?\s*$")
RE_EBYS = compile_tr(
    r"belge\s+(?:doğrulama\s+kodu|takip\s+adresi)|elektronik\s+imza|güvenli\s+elektronik|5070\s+sayılı|https?://www\.turkiye\.gov\.tr"
)
RE_ROUTE = compile_tr(
    r"(?:ağız|oral|damar|kas\s+içi|eklem|deri|cilt|göz|kulak|burun|rektal|vajinal|inhalasyon|solunum|dil\s+altı|subkutan|intramüsküler|intravenöz|kuru\s+saça|saç\s+derisine|haricen)"
    r".{0,80}?"
    r"(?:uygulanır|kullanılır|alınır|enjekte\s+edilir|infüze\s+edilir|damlatılır|sürülür|yapıştırılarak|püskürtülür|inhale\s+edilir|yoluyla|yolu\s+ile)"
)
RE_STERIL_LINE = compile_tr(r"^(?:steril|apiröjen|steril\s+süspansiyon|steril\s+çözelti|steril\s+toz)$")
RE_APPROVAL = compile_tr(r"bu\s+kullanma\s+talimatı\s*(.{0,60}?)\s*tarihinde\s+onaylanmıştır")
RE_DATE = re.compile(r"\b\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{2,4}\b")

RE_REPORTING_START = compile_tr(
    r"^yan\s+etkilerin\s+raporlanmas[i1nr]|^eğer\s+bu\s+kullanma\s+talimatında\s+(?:bahsi\s+geçmeyen|değinilmeyen)\s+herhangi\s+bir\s+yan\s+etki"
)
RE_FOOTER_START = compile_tr(
    r"^(?:ruhsat\s+sahibi|üretim\s+yeri|üretici|lisans\s+sahibi)\s*:?|^bu\s+kullanma\s+talimatı.{0,40}?onaylanmıştır"
)
RE_FREQ_DEFINITION = compile_tr(
    r"^(?:çok\s+yaygın|yaygın|yaygın\s+olmayan|seyrek|çok\s+seyrek|bilinmeyen|bilinmiyor)\s*:\s*(?:10|100|1\.?000|10\.?000|eldeki\s+verilerden)"
)

RE_BULLET_START = re.compile(r"^[\s•●▪◦▪▸►\uf0b7o\-–—*]+(?=\s)")
RE_SUPER_UNICODE = re.compile(r"[¹²³⁴⁵⁶⁷⁸⁹⁰]+")
RE_LINE_NOISE = re.compile(r"^[_\-—–=~*.\s✂\u2700-\u2704]{4,}$")
RE_DIRTY_OCR_SIGNAL = re.compile(
    r"[a-zA-ZçğıöşüÇĞİÖŞÜ][|\]\[§][a-zA-ZçğıöşüÇĞİÖŞÜ]|GÜFAM|saklanmas[rt]\b|kuılınmadan|çrekenler|mğ3|k4ilerde|Bu\s+Ham\s+kullanmaya"
)


class TokenCounter:
    def __init__(self, model_name: str = TOKENIZER_MODEL_NAME):
        self.tokenizer = None
        self.mode = "calibrated_xlm_roberta_subword"
        for candidate in (model_name, "intfloat/multilingual-e5-base", "xlm-roberta-base"):
            try:
                from transformers import AutoTokenizer
                self.tokenizer = AutoTokenizer.from_pretrained(candidate, local_files_only=True)
                self.mode = f"hf:{candidate}"
                break
            except Exception:
                continue

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self.tokenizer is not None:
            return len(self.tokenizer.encode(text, add_special_tokens=False))
        tokens = 0
        for w in text.split():
            clean_w = re.sub(r"[^\w]", "", w)
            punct = len(w) - len(clean_w)
            tokens += (1 + punct) if len(clean_w) <= 4 else (1 + int((len(clean_w) - 3) / 3.6) + punct)
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
    merged_panel_count: int = 1
    instruction_text: str = ""


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
    needs_visual_ocr: bool
    human_intervention_required: bool
    missing_mandatory_fields: List[str]
    extraction_quality: str
    sections_captured: List[int]
    pdf_filename: str
    pdf_sha256: str
    source_url: str
    total_pages: int
    chunks: List[SectionChunk] = field(default_factory=list)


def clean_bullet_prefix(text: str) -> str:
    return re.sub(r"^[\s•●▪◦▪▸►\uf0b7o\-–—*]+", "", text).strip()


def levenshtein_similarity(a: str, b: str) -> float:
    """İki normalize metin arasındaki [0.0 - 1.0] Levenshtein dizi benzerliğini hesaplar."""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def strip_drug_prefix(line_norm: str, drug_tokens: List[str]) -> str:
    """Başlık adayının başındaki rakamı ve ilaç ismini (en fazla 50 karakter) soyutlar."""
    s = re.sub(r"^[1-6Ilit]\s*[.,)\-]?\s*", "", line_norm).strip()
    # İlaç adının ilk kelimesi satır başında geçiyorsa kesme işaretinden ('in, 'un, 'i) sonrasını al
    if "'" in s or "’" in s:
        parts = re.split(r"['’]\s*[a-z]{1,4}\s+", s, maxsplit=1)
        if len(parts) == 2 and len(parts[0]) <= 50:
            return parts[1].strip()
    for dt in drug_tokens:
        if len(dt) >= 3 and dt in s[:50]:
            idx = s.find(dt)
            after = s[idx + len(dt) :].strip()
            after = re.sub(r"^['’]?\s*[a-z]{0,3}\s+", "", after).strip()
            if len(after) >= 8:
                s = after
    return re.sub(r"[:?]+$", "", s).strip()


def match_main_section_hybrid(line_norm: str, is_bold: bool, drug_tokens: List[str]) -> Optional[int]:
    """
    Hibrit Ana Başlık Sınıflandırıcı:
    1. Geometrik Kalkan (False Positive = 0): Çok uzunsa (>160 kar.) veya '(bakınız...)' referansıysa reddet.
    2. Kanonik Sözlük + Levenshtein Benzerliği (>= 0.82) VEYA Regex doğrulaması.
    """
    if len(line_norm) > 160 or len(line_norm) < 8:
        return None
    if "bakiniz" in line_norm or line_norm.startswith("("):
        return None

    has_num_prefix = bool(re.match(r"^[1-6Ilit]\s*[.,)\-]?", line_norm))

    # 1. Regex kontrolü
    for sec_id, pattern in SECTION_REGEXES.items():
        if pattern.search(line_norm):
            if has_num_prefix or is_bold or len(line_norm) < 95:
                return sec_id

    # 2. İlaç adını soyutlayıp Kanonik Sözlük ile Levenshtein mesafesi kontrolü
    if has_num_prefix or is_bold:
        stripped = strip_drug_prefix(line_norm, drug_tokens)
        for sec_id, template in MAIN_SECTION_TEMPLATES.items():
            sim = levenshtein_similarity(stripped, template)
            # Son kısmın (suffix) şablonla eşleşmesi (ilaç adı kesme işareti olmadan yazıldıysa: 'ANDROVİUM nasıl kullanılır')
            if sim >= 0.83 or (len(stripped) <= 75 and stripped.endswith(template)):
                return sec_id
            if len(stripped) > len(template) and len(stripped) <= 80:
                tail = stripped[-len(template) - 3 :]
                if levenshtein_similarity(tail, template) >= 0.84:
                    return sec_id

    return None


def is_subheading_hybrid(line: dict, drug_tokens: List[str]) -> bool:
    """
    Hibrit Alt Başlık Sınıflandırıcı:
    - Küçük harfle başlayan veya cümle ortası altı çizili uyarı ifadelerini ('bu durumda size...') kesinlikle reddeder.
    - Kanonik TİTCK Alt Başlık Sözlüğü ile Levenshtein benzerliği (>= 0.84) hesaplar.
    - Numaralı kullanım adımlarını ('1. Cihaz kapağının açılması', '3. İçine çekilmesi') yakalar.
    """
    txt = line["text"].strip()
    norm = line["norm"]

    if len(txt) < 3 or len(txt) > 115:
        return False
    if RE_BULLET_START.match(txt) or txt.startswith("-") or txt.startswith("•"):
        return False
    if RE_FREQ_DEFINITION.search(norm):
        return False
    # Küçük harfle başlayan hiçbir satır alt başlık olamaz (CLINIMIX altı çizili cümle devamı koruması)
    first_alpha = next((c for c in txt if c.isalpha()), None)
    if first_alpha and first_alpha.islower():
        return False

    # Numaralı kullanım adımları (Örn: '1. Cihaz kapağının açılması', '3. İçine çekilmesi (İnhale edilmesi)')
    if RE_NUMBERED_STEP_SUBHEADING.match(txt) and not txt.endswith("."):
        return True

    # İlaç adını (<= 50 karakter) soyutlayıp Kanonik Alt Başlık Sözlüğü ile Levenshtein karşılaştırması yap
    stripped = strip_drug_prefix(norm, drug_tokens)
    for tmpl in CANONICAL_SUBHEADING_TEMPLATES:
        if stripped == tmpl or levenshtein_similarity(stripped, tmpl) >= 0.85:
            return True
        if len(stripped) <= 85 and stripped.endswith(tmpl):
            return True

    # Sözlük dışı özel alt başlıklar (ör. organ sistemleri: 'Göz hastalıkları', 'Böbrek hastalıkları:')
    if line["is_bold"] and len(txt) <= 75:
        if txt.endswith(".") and not any(k in norm for k in ("kullanmayiniz", "dikkatli kullaniniz")):
            return False
        # İçinde cümle fiili geçen uzun ifadeleri alt başlık yapma
        if any(v in norm for v in ("olabilir", "verilecektir", "kesilmelidir", "gerekmektedir", "önerilir")):
            return False
        return True

    return False


def extract_clean_lines(page: pymupdf.Page, page_num: int) -> List[dict]:
    data = page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
    raw_lines = []
    page_h = page.rect.height

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
                stext = fix_legacy_encoding(s["text"])
                if not stext.strip():
                    parts.append(" ")
                    continue
                font_lower = s["font"].casefold()
                if "bold" in font_lower or "black" in font_lower or (s["flags"] & 16):
                    has_bold = True

                if stext.strip().isdigit() and s["size"] < max_size * 0.78 and (s["flags"] & 1 or s["size"] < 8.0):
                    continue

                stext = RE_SUPER_UNICODE.sub("", stext)
                parts.append(stext)

            txt = re.sub(r"[ \t]{2,}", " ", "".join(parts)).strip()
            txt = re.sub(r"\s+:\s+", ": ", txt)
            txt = re.sub(r"[_\-—–✂\u2700-\u2704]{3,}\s*$", "", txt).strip()
            if not txt or RE_LINE_NOISE.fullmatch(txt):
                continue

            norm = normalize(txt)
            bbox = list(line["bbox"])

            if RE_PAGE_NUM.fullmatch(norm) and ("/" in norm or bbox[1] > 0.82 * page_h or bbox[3] < 0.10 * page_h):
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

    removed = set()
    for i, marker in enumerate(raw_lines):
        if not re.fullmatch(r"(?:[1-9Ilit]\s*[.,)\-]?|[•●▪◦\uf0b7\-–—])", marker["norm"]):
            continue
        candidates = []
        for j, line in enumerate(raw_lines):
            if i == j or j in removed:
                continue
            gap = line["bbox"][0] - marker["bbox"][2]
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
    return sorted(cleaned, key=lambda x: (x["bbox"][1], x["bbox"][0]))


def extract_and_merge_page_figures(
    page: pymupdf.Page,
    page_num: int,
    page_lines: List[dict],
    prev_page_lines: List[dict],
    drug_tokens: List[str],
) -> List[FigureRef]:
    """
    1. Sahte sayfa taramalarını ve QR kodları eler.
    2. Yan yana (2'li veya Androvium gibi 3'lü) çizimleri tek bir birleşik çerçevede (union_bbox) birleştirir.
    3. Sabit '3 satır' yerine, bir önceki görselden veya alt başlıktan başlayan TAM TALİMAT BLOĞUNU
       (önceki sayfadan sarkan açıklamalar dahil!) görsele bağlar.
    """
    raw_rects: List[pymupdf.Rect] = []
    page_w, page_h = page.rect.width, page.rect.height
    page_area = max(page.rect.get_area(), 1.0)

    try:
        for im in page.get_image_info():
            rect = pymupdf.Rect(im["bbox"]) & page.rect
            if rect.is_empty or rect.width < 38 or rect.height < 38:
                continue

            if (rect.get_area() / page_area) > 0.30 or rect.width > page_w * 0.72 or rect.height > page_h * 0.58:
                continue

            aspect_ratio = rect.width / max(rect.height, 1.0)
            is_square_ish = 0.80 <= aspect_ratio <= 1.20
            in_corner = (rect.y0 > page_h * 0.76) or (rect.y1 < page_h * 0.15) or (rect.x0 > page_w * 0.62)
            if is_square_ish and rect.width < 115 and in_corner:
                continue

            overlapping_lines = []
            for l in page_lines:
                l_rect = pymupdf.Rect(l["bbox"])
                inter = rect & l_rect
                if not inter.is_empty and (inter.get_area() / max(l_rect.get_area(), 1.0)) > 0.50:
                    overlapping_lines.append(l["text"])

            total_overlap_chars = sum(len(t) for t in overlapping_lines)
            if len(overlapping_lines) > 2 or total_overlap_chars > 45:
                continue

            if not any(abs(rect.x0 - r.x0) < 6 and abs(rect.y0 - r.y0) < 6 and abs(rect.x1 - r.x1) < 6 for r in raw_rects):
                raw_rects.append(rect)
    except Exception:
        return []

    if not raw_rects:
        return []

    raw_rects.sort(key=lambda r: (r.y0, r.x0))
    merged_groups: List[Tuple[pymupdf.Rect, int]] = []

    for r in raw_rects:
        merged = False
        for idx, (m_rect, m_cnt) in enumerate(merged_groups):
            y_overlap = min(m_rect.y1, r.y1) - max(m_rect.y0, r.y0)
            min_h = min(m_rect.height, r.height)
            horiz_gap = max(r.x0 - m_rect.x1, m_rect.x0 - r.x1)

            vert_gap = max(r.y0 - m_rect.y1, m_rect.y0 - r.y1)
            lines_between = [
                l for l in page_lines
                if l["bbox"][1] >= min(m_rect.y1, r.y1) - 4 and l["bbox"][3] <= max(m_rect.y0, r.y0) + 4
            ]

            if (y_overlap > min_h * 0.40 and horiz_gap < 120) or (0 <= vert_gap < 24 and not lines_between):
                u_rect = (m_rect | r) + (-4, -4, 4, 4)
                u_rect = u_rect & page.rect
                merged_groups[idx] = (u_rect, m_cnt + 1)
                merged = True
                break
        if not merged:
            padded = (r + (-3, -3, 3, 3)) & page.rect
            merged_groups.append((padded, 1))

    figures_out: List[FigureRef] = []
    prev_fig_bottom = 0.0

    for u_rect, panel_cnt in merged_groups:
        # 1. Görselin solunda/sağında aynı hizada açıklama var mı?
        side_lines = [
            l["text"] for l in page_lines
            if (l["bbox"][2] <= u_rect.x0 + 12 or l["bbox"][0] >= u_rect.x1 - 12)
            and l["bbox"][3] >= u_rect.y0 - 12 and l["bbox"][1] <= u_rect.y1 + 12
        ]
        if side_lines:
            instr = " ".join(side_lines)
        else:
            # 2. Üstteki TAM AÇIKLAMA BLOĞUNU al (önceki görselin altından bu görselin üstüne kadar)
            above_objs = [
                l for l in page_lines
                if prev_fig_bottom - 5 <= l["bbox"][3] <= u_rect.y0 + 14 and len(l["text"]) > 2
            ]
            # İçinde yeni bir alt başlık / adım başlığı varsa, en son adım başlığından itibaren tamamını al!
            start_idx = 0
            for idx_a, l_obj in enumerate(above_objs):
                if is_subheading_hybrid(l_obj, drug_tokens) or match_main_section_hybrid(l_obj["norm"], l_obj["is_bold"], drug_tokens):
                    start_idx = idx_a
            selected_above = [x["text"] for x in above_objs[start_idx:]]

            # 3. [YENİ SAYFA KONTROLÜ]: Eğer görsel sayfanın üst kısmındaysa ve bu sayfada üstünde yazı yoksa,
            # bir önceki sayfanın (prev_page_lines) sonundaki adım/açıklama bloğunu çek! (Sanohaler 4. adım & Androvium)
            if not selected_above and prev_page_lines and u_rect.y0 < page_h * 0.38:
                tail_candidates = prev_page_lines[-8:]
                sub_start = max(0, len(tail_candidates) - 4)
                for idx_p, p_obj in enumerate(tail_candidates):
                    if is_subheading_hybrid(p_obj, drug_tokens):
                        sub_start = idx_p
                selected_above = [x["text"] for x in tail_candidates[sub_start:]]

            # 4. Üstte de yoksa (Sitagem tedavi kürü şeması gibi), hemen altındaki ilk açıklama cümlesini al
            if not selected_above:
                below_objs = [
                    l["text"] for l in page_lines
                    if u_rect.y1 - 5 <= l["bbox"][1] <= u_rect.y1 + 55 and len(l["text"]) > 5
                ]
                selected_above = below_objs[:2]

            instr = " ".join(selected_above)

        prev_fig_bottom = u_rect.y1
        figures_out.append(
            FigureRef(
                page=page_num,
                bbox=[round(v, 2) for v in tuple(u_rect)],
                merged_panel_count=panel_cnt,
                instruction_text=instr.strip(),
            )
        )

    return figures_out


def is_real_data_table(cleaned_rows: List[List[str]]) -> Tuple[bool, List[List[str]]]:
    if not cleaned_rows:
        return False, []

    num_cols = max(len(r) for r in cleaned_rows)
    active_col_indices = [
        c_idx for c_idx in range(num_cols)
        if any(c_idx < len(r) and bool(r[c_idx].strip()) for r in cleaned_rows)
    ]
    if len(active_col_indices) < 2:
        return False, []

    pruned_rows = []
    for r in cleaned_rows:
        new_r = [r[c_idx].strip() if c_idx < len(r) else "" for c_idx in active_col_indices]
        if any(new_r):
            pruned_rows.append(new_r)

    if len(pruned_rows) < 2:
        return False, []

    multi_col_rows = sum(1 for r in pruned_rows if sum(1 for cell in r if cell) >= 2)
    if multi_col_rows < 2:
        return False, []

    all_text_norm = normalize(" ".join(cell for r in pruned_rows for cell in r if cell))
    if (
        "kullanmaya baslamadan once" in all_text_norm
        or "bu kullanma talimatini saklayiniz" in all_text_norm
        or "dikkat edilmesi gerekenler" in all_text_norm
        or "asagidaki durumlarda kullanmayiniz" in all_text_norm
        or "asagidaki durumlarda dikkatli kullaniniz" in all_text_norm
        or "diger ilaclar ile birlikte kullanimi" in all_text_norm
        or "olusabilecek etkiler" in all_text_norm
    ):
        return False, []

    non_empty_cells = [cell for r in pruned_rows for cell in r if cell]
    max_cell_len = max((len(c) for c in non_empty_cells), default=0)
    if max_cell_len > 280:
        return False, []

    return True, pruned_rows


def extract_tables_and_warning_boxes(
    page: pymupdf.Page, page_num: int, page_lines: List[dict]
) -> Tuple[List[TableData], List[Tuple[int, List[float], str]]]:
    """
    [KRİTİK DÜZELTME]: Sayfada vektörel çizim (Örn: Androvium 4. sayfadaki saç/el çizimleri) olduğu için
    len(drawings) > 600 olduğunda tablo aramayı iptal etmek yerine, çizimleri süzüp sadece tablo
    ızgara çizgilerini (width >= 18 veya height >= 12) find_tables'a verir!
    """
    tables_out: List[TableData] = []
    warning_boxes: List[Tuple[int, List[float], str]] = []
    box_rects: List[pymupdf.Rect] = []

    try:
        raw_drawings = page.get_drawings()
        # Vektörel illüstrasyonların minik kıvrımlarını ele, sadece tablo/çerçeve çizgilerini tut
        if len(raw_drawings) > 450:
            grid_drawings = [
                d for d in raw_drawings
                if d.get("rect") and (d["rect"].width >= 18 or d["rect"].height >= 12)
            ]
        else:
            grid_drawings = raw_drawings

        for d in grid_drawings:
            r = d.get("rect")
            if not r:
                continue
            rect = pymupdf.Rect(r)
            if rect.width >= 180 and 28 <= rect.height <= 420:
                has_fill = d.get("fill") is not None and d.get("fill") != (1.0, 1.0, 1.0)
                has_border = d.get("color") is not None and (d.get("width") or 0) >= 0.5
                if has_fill or has_border:
                    box_rects.append(rect)

        if len(grid_drawings) <= 800:
            detected = page.find_tables(paths=grid_drawings)
            for t in detected.tables:
                t_rect = pymupdf.Rect(t.bbox)
                if t.col_count == 1 and t.row_count == 1:
                    if t_rect.width >= 180 and 28 <= t_rect.height <= 420:
                        box_rects.append(t_rect)
                    continue

                if t.col_count >= 2 and t.row_count >= 2:
                    extracted = t.extract()
                    if not extracted:
                        continue
                    cleaned_rows = [
                        [fix_legacy_encoding(re.sub(r"\s+", " ", (cell or "")).strip()) for cell in row]
                        for row in extracted
                    ]
                    is_valid, pruned_rows = is_real_data_table(cleaned_rows)
                    if not is_valid:
                        continue

                    headers = pruned_rows[0]
                    rows = pruned_rows[1:]
                    tables_out.append(
                        TableData(
                            page=page_num,
                            headers=headers,
                            rows=rows,
                            bbox=[round(v, 2) for v in t.bbox],
                        )
                    )

        dedup_rects: List[pymupdf.Rect] = []
        for br in box_rects:
            if not any(abs(br.x0 - ex.x0) < 15 and abs(br.y0 - ex.y0) < 15 for ex in dedup_rects):
                dedup_rects.append(br)

        for br in dedup_rects:
            inside_lines = [
                l["text"] for l in page_lines
                if l["bbox"][0] >= br.x0 - 8 and l["bbox"][2] <= br.x1 + 8
                and l["bbox"][1] >= br.y0 - 6 and l["bbox"][3] <= br.y1 + 6
            ]
            if not inside_lines:
                continue
            box_text = " ".join(inside_lines).strip()
            box_norm = normalize(box_text)
            if "kullanmaya baslamadan once" in box_norm or "bu kullanma talimatini saklayiniz" in box_norm:
                continue
            if len(box_text) >= 20:
                warning_boxes.append((page_num, [round(v, 2) for v in tuple(br)], box_text))

    except Exception:
        pass

    return tables_out, warning_boxes


def parse_single_kt(pdf_path: Path, meta_record: dict) -> DrugDocument:
    raw_bytes = pdf_path.read_bytes()
    sha256_hex = hashlib.sha256(raw_bytes).hexdigest()

    drug_tokens = [
        normalize(w) for w in re.split(r"[\s/(),%-]+", meta_record.get("name", "")) if len(w) >= 3
    ]

    all_lines: List[dict] = []
    all_figures: List[FigureRef] = []
    all_tables: List[TableData] = []
    all_warning_boxes: List[Tuple[int, List[float], str]] = []
    has_health_staff = False

    with pymupdf.open(pdf_path) as doc:
        total_pages = doc.page_count
        prev_p_lines: List[dict] = []
        staff_cutoff_reached = False

        for pi in range(total_pages):
            if staff_cutoff_reached:
                break
            pn = pi + 1
            page = doc[pi]
            p_lines = extract_clean_lines(page, pn)

            # [KRİTİK KURAL]: Sayfada 'SAĞLIK PERSONELİ İÇİNDİR' başlığı varsa, tam o satırda belgeyi kes!
            kept_page_lines = []
            cutoff_y = page.rect.height + 100.0
            for l in p_lines:
                if RE_HEALTH_STAFF_CUTOFF.search(l["norm"]):
                    has_health_staff = True
                    staff_cutoff_reached = True
                    cutoff_y = l["bbox"][1]
                    break
                kept_page_lines.append(l)

            all_lines.extend(kept_page_lines)

            # Görselleri ve tabloları da yalnızca kesim noktasının (cutoff_y) üstündeyse al!
            p_figs = extract_and_merge_page_figures(page, pn, kept_page_lines, prev_p_lines, drug_tokens)
            all_figures.extend([f for f in p_figs if f.bbox[1] < cutoff_y - 5])

            p_tables, p_wboxes = extract_tables_and_warning_boxes(page, pn, kept_page_lines)
            all_tables.extend([t for t in p_tables if t.bbox[1] < cutoff_y - 5])
            all_warning_boxes.extend([wb for wb in p_wboxes if wb[1][1] < cutoff_y - 5])

            prev_p_lines = kept_page_lines

    full_norm_text = " ".join(l["norm"] for l in all_lines)
    full_raw_text = "\n".join(l["text"] for l in all_lines)
    needs_ocr = len(RE_DIRTY_OCR_SIGNAL.findall(full_raw_text)) >= 2

    # 1. Künye Bilgileri (CLINIMIX-2 Tablolu Etkin Madde ve NİMBEX Çok Satırlı Etkin Madde Desteği)
    first_page_lines = [l for l in all_lines if l["page"] == 1]
    extracted_title = None
    route_str = None
    active_str = meta_record.get("active_ingredient") or ""
    excipients_str = None
    preamble_active_tables: List[TableData] = []

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
            act_y0 = l["bbox"][1]
            act_y1 = 9999.0
            cleaned = clean_bullet_prefix(l["text"])
            act_parts = [re.sub(r"^etkin\s+madde(?:\(ler\)|ler)?\s*:?\s*", "", cleaned, flags=re.IGNORECASE).strip()]
            for nxt in first_page_lines[idx + 1 :]:
                if (
                    re.search(r"^\W*yardimci\s+madde", nxt["norm"])
                    or "bu ilaci kullanmaya" in nxt["norm"]
                    or "bu kullanma talimatini" in nxt["norm"]
                    or "bu kullanma talimatinda" in nxt["norm"]
                ):
                    act_y1 = nxt["bbox"][1]
                    break
                act_parts.append(nxt["text"])

            # 1. sayfada Etkin Madde ile Yardımcı Maddeler arasında tablo var mı? (CLINIMIX-2)
            for tbl in all_tables:
                if tbl.page == 1 and tbl.bbox[1] >= act_y0 - 10 and tbl.bbox[3] <= act_y1 + 15:
                    preamble_active_tables.append(tbl)

            if preamble_active_tables:
                tbl0 = preamble_active_tables[0]
                row_summaries = [
                    f"{r[0]} ({', '.join(r[1:])})" for r in tbl0.rows if r and r[0]
                ]
                active_str = "; ".join(row_summaries[:12])
            else:
                joined_act = " ".join(p for p in act_parts if p).strip()
                if joined_act:
                    active_str = joined_act

        if re.search(r"^\W*yardimci\s+madde", l["norm"]):
            cleaned = clean_bullet_prefix(l["text"])
            exc_parts = [
                re.sub(r"^yard[ıi]mc[ıi]\s+madde(?:\(ler\)|ler)?\s*:?\s*", "", cleaned, flags=re.IGNORECASE).strip()
            ]
            for nxt in first_page_lines[idx + 1 : idx + 6]:
                if (
                    "bu ilaci kullanmaya" in nxt["norm"]
                    or "bu kullanma talimatini" in nxt["norm"]
                    or "bu kullanma talimatinda" in nxt["norm"]
                    or "ek izlemeye" in nxt["norm"]
                ):
                    break
                exc_parts.append(nxt["text"])
            excipients_str = " ".join(p for p in exc_parts if p).strip()

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

    has_triangle = ("ek izlemeye tabidir" in full_norm_text) or ("▼" in full_raw_text) or ("\u25bc" in full_raw_text)

    # 2. İçindekiler (TOC) Fihristini İzole Et
    toc_end_idx = -1
    for idx, l in enumerate(all_lines):
        if l["page"] <= 2 and "basliklari yer almaktadir" in l["norm"]:
            toc_end_idx = idx
            break

    if toc_end_idx == -1:
        for idx, l in enumerate(all_lines):
            if l["page"] == 1 and match_main_section_hybrid(l["norm"], l["is_bold"], drug_tokens) == 5:
                toc_end_idx = idx
                break

    body_lines = all_lines[toc_end_idx + 1 :] if toc_end_idx >= 0 else all_lines

    # 3. Gövdeyi 1..5 Ana Bölümlere Ayır
    sections_raw: Dict[int, dict] = {}
    current_sec_id: Optional[int] = None
    skip_until_idx = -1
    in_reporting_boilerplate = False
    in_footer_boilerplate = False

    for i, l in enumerate(body_lines):
        if i <= skip_until_idx:
            continue

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
        matched_sec = match_main_section_hybrid(combined_norm, l["is_bold"], drug_tokens)

        if matched_sec is None:
            for span_len in (2, 3):
                if i + span_len <= len(body_lines):
                    lookahead = body_lines[i : i + span_len]
                    if all(x["page"] == l["page"] for x in lookahead):
                        test_norm = " ".join(x["norm"] for x in lookahead)
                        any_bold = any(x["is_bold"] for x in lookahead)
                        m_sec = match_main_section_hybrid(test_norm, any_bold, drug_tokens)
                        if m_sec is not None:
                            matched_sec = m_sec
                            combined_text = " ".join(x["text"] for x in lookahead)
                            skip_until_idx = i + span_len - 1
                            break

        if matched_sec is not None:
            if matched_sec not in sections_raw:
                current_sec_id = matched_sec
                in_reporting_boilerplate = False
                in_footer_boilerplate = False
                sections_raw[current_sec_id] = {
                    "printed_heading": combined_text,
                    "heading_page": l["page"],
                    "heading_y0": l["bbox"][1],
                    "lines": [],
                }
                continue

        if current_sec_id is not None:
            if current_sec_id == 4:
                if RE_REPORTING_START.search(l["norm"]):
                    in_reporting_boilerplate = True
                if in_reporting_boilerplate:
                    continue

            if current_sec_id == 5:
                if RE_FOOTER_START.search(l["norm"]):
                    in_footer_boilerplate = True
                if in_footer_boilerplate:
                    continue

            sections_raw[current_sec_id]["lines"].append(l)

    # 4. Alt Başlık Paketleme + Bölüm Sınırlarına Göre Kesin Görsel/Tablo Bağlama
    chunks: List[SectionChunk] = []
    chunk_counter = 0
    claimed_figures: set = set()
    claimed_tables: set = set()

    # CLINIMIX-2 gibi 1. sayfada Etkin Madde tablosu varsa, tekrar bağlanmaması için işaretle
    for ptbl in preamble_active_tables:
        claimed_tables.add((ptbl.page, tuple(ptbl.bbox)))

    sorted_sec_ids = sorted(sections_raw.keys())

    for s_idx, sec_id in enumerate(sorted_sec_ids):
        sec_info = sections_raw[sec_id]
        sec_lines = sec_info["lines"]
        if not sec_lines:
            continue

        canonical_title = CANONICAL_TITLES[sec_id]
        printed_heading = sec_info["printed_heading"]
        target_budget, max_ceiling = SECTION_TOKEN_BUDGETS.get(sec_id, (350, 440))

        # Bu ana bölümün kapsadığı kesin (sayfa, y) aralığı (Androvium %2'de sayfa sonundaki 3 kafa şemasının kaçmaması için!)
        sec_start_page = sec_info["heading_page"]
        sec_start_y = sec_info["heading_y0"]
        if s_idx + 1 < len(sorted_sec_ids):
            nxt_sec = sections_raw[sorted_sec_ids[s_idx + 1]]
            sec_end_page = nxt_sec["heading_page"]
            sec_end_y = nxt_sec["heading_y0"]
        else:
            sec_end_page = total_pages
            sec_end_y = 9999.0

        sub_blocks = []
        curr_sub_title = "Genel Bilgiler"
        curr_sub_lines = []

        for l in sec_lines:
            if is_subheading_hybrid(l, drug_tokens):
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
            f"Etkin Madde: {active_str[:120] or 'Belirtilmemiş'} | "
            f"Bölüm: {sec_id}. {canonical_title}]"
        )
        base_prefix_tokens = TOKEN_COUNTER.count(base_prefix_sample) + 15
        sec_chunk_indices: List[int] = []

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

            bound_figs = []
            if sec_id in (1, 2, 3):
                for fig in all_figures:
                    fig_key = (fig.page, tuple(fig.bbox))
                    if fig_key in claimed_figures:
                        continue
                    if s_page <= fig.page <= e_page:
                        if (fig.page != s_page or fig.bbox[3] >= y_min - 25) and (
                            fig.page != e_page or fig.bbox[1] <= y_max + 160
                        ):
                            bound_figs.append(fig)
                            claimed_figures.add(fig_key)

            bound_tables = []
            # Bölüm 1'in ilk chunk'ına 1. sayfadaki Etkin Madde tablosunu (CLINIMIX-2) bağla!
            if sec_id == 1 and not sec_chunk_indices and preamble_active_tables:
                bound_tables.extend(preamble_active_tables)

            for tbl in all_tables:
                tbl_key = (tbl.page, tuple(tbl.bbox))
                if tbl_key in claimed_tables:
                    continue
                if s_page <= tbl.page <= e_page:
                    if (tbl.page != s_page or tbl.bbox[3] >= y_min - 30) and (
                        tbl.page != e_page or tbl.bbox[1] <= y_max + 140
                    ):
                        bound_tables.append(tbl)
                        claimed_tables.add(tbl_key)

            boxed = [
                wb_text for (wb_page, wb_bbox, wb_text) in all_warning_boxes
                if s_page <= wb_page <= e_page
                and (wb_page != s_page or wb_bbox[3] >= y_min - 20)
                and (wb_page != e_page or wb_bbox[1] <= y_max + 20)
            ]
            for x in raw_line_objs:
                if x["is_bold"] and any(
                    k in x["norm"] for k in ("dikkat", "uyari", "kullanmayiniz", "asiri duyarlilik", "derhal")
                ):
                    if x["text"] not in boxed:
                        boxed.append(x["text"])

            clean_hierarchy = [t for t in sub_titles if t != "Genel Bilgiler"]
            locative_prefix = (
                f"[İlaç: {meta_record['name']} | "
                f"Etkin Madde: {active_str[:120] or 'Belirtilmemiş'} | "
                f"Bölüm: {sec_id}. {canonical_title}"
                + (f" | Alt Başlıklar: {' / '.join(clean_hierarchy[:3])}" if clean_hierarchy else "")
                + "]"
            )
            ret_text = f"{locative_prefix}\n\n{gt_passage}"

            sec_chunk_indices.append(len(chunks))
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
                    boxed_warnings=boxed[:4],
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
                if RE_BULLET_START.match(txt) or txt.startswith("•"):
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

        # Bölümün en sonunda (yeni sayfaya sarktığı için) açıkta kalan görsel/tablo varsa bu bölümün son chunk'ına bağla!
        if sec_chunk_indices:
            last_c = chunks[sec_chunk_indices[-1]]
            if sec_id in (1, 2, 3):
                for fig in all_figures:
                    fig_key = (fig.page, tuple(fig.bbox))
                    if fig_key in claimed_figures:
                        continue
                    in_sec_span = (
                        (sec_start_page < fig.page < sec_end_page)
                        or (fig.page == sec_start_page and fig.bbox[1] >= sec_start_y)
                        or (fig.page == sec_end_page and fig.bbox[3] <= sec_end_y)
                    )
                    if in_sec_span:
                        last_c.figures.append(fig)
                        last_c.has_visual_figure = True
                        claimed_figures.add(fig_key)

            for tbl in all_tables:
                tbl_key = (tbl.page, tuple(tbl.bbox))
                if tbl_key in claimed_tables:
                    continue
                in_sec_span = (
                    (sec_start_page < tbl.page < sec_end_page)
                    or (tbl.page == sec_start_page and tbl.bbox[1] >= sec_start_y)
                    or (tbl.page == sec_end_page and tbl.bbox[3] <= sec_end_y)
                )
                if in_sec_span:
                    last_c.tables.append(tbl)
                    claimed_tables.add(tbl_key)

    # ========================================================================
    # 5. KATI ŞEMA DENETÇİSİ (STRICT GATEKEEPER - İNSAN MÜDAHALESİ KURALI)
    # ========================================================================
    captured_secs = sorted(sections_raw.keys())
    missing_mandatory: List[str] = []

    for req_sec in (1, 2, 3, 4, 5):
        if req_sec not in captured_secs:
            missing_mandatory.append(f"Ana Bölüm {req_sec}: {CANONICAL_TITLES[req_sec]}")

    if not active_str or not active_str.strip():
        missing_mandatory.append("Künye: Etkin Madde")

    all_subheadings_norm = " | ".join(
        normalize(h) for c in chunks for h in c.heading_hierarchy
    )
    if "dikkatli" not in all_subheadings_norm and "kullanmayiniz" not in all_subheadings_norm:
        missing_mandatory.append("Bölüm 2 Alt Başlıkları (KULLANMAYINIZ / DİKKATLİ KULLANINIZ)")
    if "uygun kullanim" not in all_subheadings_norm and "uygulama yolu" not in all_subheadings_norm:
        missing_mandatory.append("Bölüm 3 Alt Başlıkları (Uygun kullanım / Uygulama yolu)")

    human_intervention = len(missing_mandatory) > 0
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
        has_health_staff_section=has_health_staff,
        needs_visual_ocr=needs_ocr,
        human_intervention_required=human_intervention,
        missing_mandatory_fields=missing_mandatory,
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

    print("=" * 80)
    print(f"🧪 60 DOSYALIK PİLOT PARSER v2.9 (HİBRİT SÖZLÜK + BÖLÜM 6 İZOLASYONU) | {TOKEN_COUNTER.mode}")
    print("=" * 80)

    started = time.perf_counter()
    bucket_stats = Counter()
    bucket_golden = Counter()
    total_chunks = 0
    total_figures = 0
    merged_panel_figs = 0
    total_tables = 0
    staff_isolated_count = 0
    human_review_count = 0

    for fname, bucket in sample_60:
        pdf_path = KT_DIR / fname
        meta_rec = meta_by_pdf[fname]
        parsed_doc = parse_single_kt(pdf_path, meta_rec)

        bucket_stats[bucket] += 1
        if parsed_doc.extraction_quality == "golden":
            bucket_golden[bucket] += 1
        if parsed_doc.has_health_staff_section:
            staff_isolated_count += 1
        if parsed_doc.human_intervention_required:
            human_review_count += 1

        total_chunks += len(parsed_doc.chunks)
        for c in parsed_doc.chunks:
            total_figures += len(c.figures)
            merged_panel_figs += sum(1 for f in c.figures if f.merged_panel_count > 1)
            total_tables += len(c.tables)

        out_file = OUT_DIR / f"{pdf_path.stem}.json"
        with open(out_file, "w", encoding="utf-8") as out_f:
            json.dump(asdict(parsed_doc), out_f, ensure_ascii=False, indent=2)

    elapsed = time.perf_counter() - started
    print(f"\n✅ PİLOT PARSER v2.9 TAMAMLANDI ({elapsed:.2f} saniye)")
    print("-" * 80)
    print(f"{'KATEGORİ':<32} | {'SEÇİLEN':<8} | {'5 BÖLÜM TAM (GOLDEN)':<22}")
    print("-" * 80)
    for b in sorted(bucket_stats.keys()):
        print(f"{b:<32} | {bucket_stats[b]:<8} | {bucket_golden[b]}/{bucket_stats[b]}")
    print("-" * 80)
    print(f"Toplam Üretilen Hasta Chunk'ı (Bölüm 1..5)     : {total_chunks}")
    print(f"Bölüm 6 (Sağlık Personeli) Kesilip İzole Edilen: {staff_isolated_count} dosya")
    print(f"Hasta Kullanım Şeması (Bölüm 6 Hariç Net)      : {total_figures} (Çoklu Birleşen: {merged_panel_figs})")
    print(f"Doğrulanmış Veri Tablosu (Etkin Madde + Doz)   : {total_tables}")
    print(f"Katı Şema Denetimi (İnsan Müdahalesi Bayrağı)  : {human_review_count} dosya")
    print("=" * 80)


if __name__ == "__main__":
    main()