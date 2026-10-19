#!/usr/bin/env python3
"""
TİTCK 60 Pilot Dosya Görsel Denetim Paneli v2.9
- İlaç adlarındaki [:32] karakter kesmesi kaldırıldı (NİMBEX vb. tam isimle görünür).
- Bölüm 6 (Sağlık Personeli) görselleri hasta sisteminden tamamen çıkarıldı.
- Sanohaler (Sayfa geçişli 4. adım ve Türkçe 'İ' harfi), Androvium (%2 ve %5 3'lü şema + Yaş/Doz tablosu)
  ve CLINIMIX-2 (1. Sayfa Etkin Madde Tablosu) doğrudan denetlenir.
"""

from __future__ import annotations

import html
import json
import shutil
import webbrowser
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent
KT_DIR = ROOT.parent / "data" / "kt"
PARSED_DIR = ROOT.parent / "data" / "pilot_parsed_60"
AUDIT_DIR = PARSED_DIR / "visual_audit"
IMG_OUT_DIR = AUDIT_DIR / "cropped_figures"


def main():
    if IMG_OUT_DIR.exists():
        shutil.rmtree(IMG_OUT_DIR)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    IMG_OUT_DIR.mkdir(parents=True, exist_ok=True)

    json_files = sorted(PARSED_DIR.glob("*.json"))
    docs = [json.loads(jf.read_text(encoding="utf-8")) for jf in json_files]
    non_golden = [d for d in docs if d["extraction_quality"] != "golden"]

    unique_figures = {}
    total_real_tables = 0

    for d in docs:
        fname = d["pdf_filename"]
        dname = d["primary_drug_name"]
        for c in d["chunks"]:
            total_real_tables += len(c.get("tables", []))
            sec_id = c["section_id"]
            for fig in c.get("figures", []):
                key = (fname, fig["page"], tuple(fig["bbox"]))
                if key not in unique_figures:
                    unique_figures[key] = {
                        "pdf_filename": fname,
                        "drug_name": dname,
                        "page": fig["page"],
                        "bbox": fig["bbox"],
                        "merged_panel_count": fig.get("merged_panel_count", 1),
                        "instruction_text": fig.get("instruction_text", ""),
                        "sections": set(),
                    }
                unique_figures[key]["sections"].add(sec_id)

    cropped_records = []
    grouped_by_pdf = {}
    for key, info in unique_figures.items():
        grouped_by_pdf.setdefault(info["pdf_filename"], []).append(info)

    for fname, fig_list in grouped_by_pdf.items():
        pdf_path = KT_DIR / fname
        if not pdf_path.exists():
            continue
        with pymupdf.open(pdf_path) as pdf_doc:
            for idx, finfo in enumerate(fig_list):
                pno = finfo["page"]
                rect = pymupdf.Rect(finfo["bbox"])
                page = pdf_doc[pno - 1]
                pix = page.get_pixmap(clip=rect, matrix=pymupdf.Matrix(2, 2))
                img_filename = f"{Path(fname).stem}_p{pno}_f{idx}.png"
                pix.save(IMG_OUT_DIR / img_filename)
                finfo["img_rel_path"] = f"cropped_figures/{img_filename}"
                finfo["width"] = round(rect.width, 1)
                finfo["height"] = round(rect.height, 1)
                cropped_records.append(finfo)

    html_parts = [f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<title>ProspektAsistan - Görsel Denetim Paneli v2.9</title>
<style>
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 24px; line-height: 1.5; }}
  h1, h2, h3 {{ color: #f8fafc; margin-top: 0; }}
  .container {{ max-width: 1440px; margin: 0 auto; }}
  .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 28px; }}
  .stat-card {{ background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 18px; }}
  .stat-val {{ font-size: 28px; font-weight: 700; color: #38bdf8; }}
  .stat-label {{ font-size: 13px; color: #94a3b8; margin-top: 4px; }}
  .section-box {{ background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 22px; margin-bottom: 28px; }}
  .badge {{ display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }}
  .badge-green {{ background: #14532d; color: #bbf7d0; }}
  .badge-purple {{ background: #4c1d95; color: #ddd6fe; }}
  .badge-blue {{ background: #1e3a8a; color: #bfdbfe; }}
  .fig-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 18px; }}
  .fig-card {{ background: #0f172a; border: 1px solid #334155; border-radius: 10px; padding: 14px; display: flex; flex-direction: column; justify-content: space-between; }}
  .fig-img-wrap {{ background: #ffffff; border-radius: 6px; padding: 10px; text-align: center; margin-bottom: 10px; min-height: 150px; display: flex; align-items: center; justify-content: center; }}
  .fig-img-wrap img {{ max-width: 100%; max-height: 210px; object-fit: contain; }}
  .instr-box {{ background: #1e293b; border-left: 3px solid #38bdf8; padding: 8px 10px; font-size: 12px; color: #e2e8f0; border-radius: 4px; margin-top: 8px; }}
  .chunk-card {{ background: #0f172a; border-left: 4px solid #38bdf8; border-radius: 8px; padding: 16px; margin-bottom: 16px; }}
  pre {{ background: #020617; padding: 14px; border-radius: 8px; overflow-x: auto; font-size: 13px; color: #cbd5e1; white-space: pre-wrap; margin: 8px 0; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; }}
  th, td {{ border: 1px solid #334155; padding: 8px 10px; text-align: left; }}
  th {{ background: #0f172a; color: #38bdf8; }}
</style>
</head>
<body>
<div class="container">
  <h1>🔬 ProspektAsistan — Görsel Denetim Paneli v2.9 (Sadece Hasta Bölümleri 1..5 & Tam Blok Eşleşme)</h1>
  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-val" style="color:#4ade80;">{len(docs) - len(non_golden)} / {len(docs)}</div>
      <div class="stat-label">5 Bölümü Tam Yakalanan (Golden)</div>
    </div>
    <div class="stat-card">
      <div class="stat-val">{len(unique_figures)}</div>
      <div class="stat-label">Hasta Kullanım Şeması (Bölüm 6 Kesildi!)</div>
    </div>
    <div class="stat-card">
      <div class="stat-val" style="color:#a78bfa;">{total_real_tables}</div>
      <div class="stat-label">Doğrulanmış Gerçek Veri Tablosu</div>
    </div>
    <div class="stat-card">
      <div class="stat-val" style="color:#38bdf8;">{sum(1 for d in docs if d['has_health_staff_section'])}</div>
      <div class="stat-label">Sağlık Personeli Eki İzole Edilen İlaç</div>
    </div>
  </div>

  <div class="section-box">
    <h2>🖼️ 1. Hastaya Gösterilecek Kullanım Şemaları ve Tam Adım Açıklamaları ({len(cropped_records)} Adet)</h2>
    <div class="fig-grid">
"""]

    for finfo in cropped_records:
        secs_str = ", ".join(f"Bölüm {s}" for s in sorted(finfo["sections"]))
        p_cnt = finfo["merged_panel_count"]
        instr = finfo["instruction_text"] or "(Genel bölüm şeması)"
        html_parts.append(f"""
      <div class="fig-card">
        <div>
          <div class="fig-img-wrap">
            <img src="{finfo['img_rel_path']}" alt="Cropped Figure">
          </div>
          <div style="font-size:12px;">
            <b>{html.escape(finfo['drug_name'])}</b><br>
            <span style="color:#94a3b8;">Dosya: {finfo['pdf_filename']} (Sayfa {finfo['page']}) | {finfo['width']}x{finfo['height']} pt</span><br>
            <span class="badge badge-green" style="margin-top:4px;">{secs_str}</span>
            {f'<span class="badge badge-purple">{p_cnt} Yan Yana Görsel Birleştirildi</span>' if p_cnt > 1 else ''}
          </div>
        </div>
        <div class="instr-box">
          <b>Eşleşen Tam Talimat Bloğu:</b><br>{html.escape(instr)}
        </div>
      </div>
        """)

    html_parts.append("""
    </div>
  </div>

  <div class="section-box">
    <h2>📦 2. CLINIMIX-2 (Etkin Madde Tablosu), ANDROVİUM (Yaş/Doz Tablosu) ve SANOHALER Kontrolleri</h2>
    """)

    for d in docs:
        if any(k in d["primary_drug_name"].upper() for k in ("CLINIMIX", "ANDROV", "SANOHALER", "NIMBEX")):
            html_parts.append(f"""
            <div style="background:#020617; padding:12px; border-radius:8px; margin-bottom:12px; border:1px solid #334155;">
              <b>İlaç:</b> {html.escape(d['primary_drug_name'])} |
              <b>Etkin Madde:</b> {html.escape(d['active_substance'])} |
              <b>Sağlık Personeli Eki Kesildi mi?:</b> {d['has_health_staff_section']}
            </div>
            """)
            for c in d["chunks"]:
                if c.get("tables") or c.get("figures") or c["section_id"] == 1:
                    html_parts.append(f"""
      <div class="chunk-card">
        <div style="display:flex; justify-content:space-between; margin-bottom:8px;">
          <span><b>{html.escape(d['primary_drug_name'])}</b> — <code>{c['chunk_id']}</code> (Sayfa {c['start_page']}-{c['end_page']})</span>
          <span class="badge badge-blue">{c['token_count']} Token | Bölüm {c['section_id']}</span>
        </div>
        <pre>{html.escape(c['ground_truth_passage'])}</pre>
                    """)
                    for tbl in c.get("tables", []):
                        html_parts.append("<table>")
                        if tbl.get("headers"):
                            html_parts.append("<tr>" + "".join(f"<th>{html.escape(h)}</th>" for h in tbl["headers"]) + "</tr>")
                        for row in tbl.get("rows", []):
                            html_parts.append("<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>")
                        html_parts.append("</table>")
                    html_parts.append("</div>")

    html_parts.append("</div></div></body></html>")
    report_path = AUDIT_DIR / "index.html"
    report_path.write_text("\n".join(html_parts), encoding="utf-8")

    print("=" * 75)
    print(f"🎨 GÖRSEL DENETİM PANELİ v2.9 OLUŞTURULDU!")
    print(f"   Hasta Kullanım Şeması Sayısı   : {len(unique_figures)}")
    print(f"   Gerçek Veri Tablosu Sayısı     : {total_real_tables}")
    print("=" * 75)
    webbrowser.open(report_path.resolve().as_uri())


if __name__ == "__main__":
    main()