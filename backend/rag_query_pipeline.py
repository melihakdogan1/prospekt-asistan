"""
RAG Query Pipeline
- Query embedding: intfloat/multilingual-e5-large (local)
- Retrieval: ChromaDB (data/chroma_prospekt, collection=prospektus)
- Cross-reference: SQLite (data/prospekt_metadata.db)
- Generation: Gemini API (Google AI Studio key)

Usage:
  set GOOGLE_API_KEY=your_key
  c:/ProspektAsistan/.venv/Scripts/python.exe backend/rag_query_pipeline.py --query "aspirinin yan etkileri"
"""

import argparse
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb
import requests
from sentence_transformers import SentenceTransformer


BASE_DIR = Path(__file__).resolve().parent.parent
SQLITE_PATH = BASE_DIR / "data" / "prospekt_metadata.db"
CHROMA_PATH = BASE_DIR / "data" / "chroma_prospekt"
COLLECTION_NAME = "prospektus"
EMBED_MODEL_NAME = "intfloat/multilingual-e5-large"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"


@dataclass
class RetrievedChunk:
    score: float
    document: str
    metadata: dict[str, Any]

def load_api_key() -> str:
    """Load API key from environment first, then optional .env file."""
    key = os.getenv("GOOGLE_API_KEY", "").strip() or os.getenv("GEMINI_API_KEY", "").strip()
    if key:
        return key

    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return ""

    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k in {"GOOGLE_API_KEY", "GEMINI_API_KEY"} and v:
                return v
    except OSError:
        return ""

    return ""


class ProspektRAG:
    def __init__(self) -> None:
        self.api_key = load_api_key()
        self.embed_model = SentenceTransformer(EMBED_MODEL_NAME)

        self.chroma_client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        self.collection = self.chroma_client.get_collection(name=COLLECTION_NAME)

    def embed_query(self, query: str):
        return self.embed_model.encode([f"query: {query}"], normalize_embeddings=True)

    def retrieve(self, query: str, top_k: int = 8) -> list[RetrievedChunk]:
        query_emb = self.embed_query(query)
        results = self.collection.query(
            query_embeddings=query_emb.tolist(),
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )

        chunks: list[RetrievedChunk] = []
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0]

        for doc, meta, dist in zip(docs, metas, dists):
            score = 1 - float(dist)
            chunks.append(RetrievedChunk(score=score, document=doc, metadata=meta or {}))

        chunks.sort(key=lambda x: x.score, reverse=True)
        return chunks

    def fetch_drug_profiles(self, ilac_ids: list[int]) -> dict[int, dict[str, Any]]:
        if not ilac_ids:
            return {}

        unique_ids = sorted(set(int(i) for i in ilac_ids))
        placeholders = ",".join(["?"] * len(unique_ids))

        conn = sqlite3.connect(SQLITE_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute(
            f"""
            SELECT id, file_name, drug_name, usage_route, active_substance,
                   excipients, warning_text, license_holder, manufacturer,
                   healthcare_professional_info
            FROM ilaclar
            WHERE id IN ({placeholders})
            """,
            tuple(unique_ids),
        )
        rows = cur.fetchall()
        conn.close()

        return {int(r["id"]): dict(r) for r in rows}

    def build_context(self, query: str, chunks: list[RetrievedChunk], max_chunks: int = 6) -> tuple[str, list[dict[str, Any]]]:
        selected = chunks[:max_chunks]
        ilac_ids: list[int] = []
        for c in selected:
            ilac_id = c.metadata.get("ilac_id")
            if ilac_id is not None:
                ilac_ids.append(int(ilac_id))

        profiles = self.fetch_drug_profiles(ilac_ids)

        context_blocks: list[str] = []
        sources: list[dict[str, Any]] = []

        for i, chunk in enumerate(selected, start=1):
            meta = chunk.metadata
            ilac_id = int(meta.get("ilac_id", 0)) if meta.get("ilac_id") is not None else 0
            profile = profiles.get(ilac_id, {})

            source = {
                "rank": i,
                "score": round(chunk.score, 4),
                "drug_name": meta.get("drug_name", ""),
                "bolum_no": meta.get("bolum_no", ""),
                "section_title": meta.get("section_title", ""),
                "file_name": meta.get("file_name", ""),
            }
            sources.append(source)

            block = (
                f"[KAYNAK {i}]\\n"
                f"Skor: {source['score']}\\n"
                f"Ilac: {source['drug_name']}\\n"
                f"Bolum: {source['bolum_no']} - {meta.get('bolum_adi', '')}\\n"
                f"Baslik: {source['section_title']}\\n"
                f"Etken Madde: {profile.get('active_substance', '')}\\n"
                f"Kullanim Yolu: {profile.get('usage_route', '')}\\n"
                f"Uyari Ozet: {profile.get('warning_text', '')}\\n"
                f"Icerik: {chunk.document}\\n"
            )
            context_blocks.append(block)

        prompt_context = "\\n\\n".join(context_blocks)
        return prompt_context, sources

    def ask_gemini(self, query: str, context: str) -> str:
        if not self.api_key:
            return (
                "GOOGLE_API_KEY bulunamadi. Yanit olusturulamadi. "
                "Ama retrieval basarili; kaynaklari gorebilirsiniz."
            )

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
            f"?key={self.api_key}"
        )

        system_prompt = (
            "Sen bir ilac prospektus asistanisin. Sadece verilen kaynaklara dayanarak cevap ver. "
            "Kaynakta olmayan bilgi uydurma. Emin degilsen 'kaynaklarda net bilgi yok' de. "
            "Turkce, net, kisa ve guvenli dil kullan. Tani koyma. "
            "Cevabin sonunda 'Bu bilgi doktor onerisi yerine gecmez.' cumlesini ekle."
        )

        user_prompt = (
            f"Kullanici sorusu: {query}\\n\\n"
            f"Kaynaklar:\\n{context}\\n\\n"
            "Gorev:\\n"
            "1) Soruyu dogrudan cevapla.\\n"
            "2) Ilac adini ve ilgili bolumu belirt.\\n"
            "3) Kritik uyari varsa maddeler halinde yaz."
        )

        def build_payload(prompt_text: str, max_tokens: int) -> dict[str, Any]:
            generation_config: dict[str, Any] = {
                "temperature": 0.2,
                "topP": 0.9,
                "maxOutputTokens": max_tokens,
            }
            # Gemini 2.5 serisinde dusunce tokenlari cevabi erken kesebiliyor.
            if "2.5" in GEMINI_MODEL:
                generation_config["thinkingConfig"] = {"thinkingBudget": 0}

            return {
                "system_instruction": {
                    "parts": [{"text": system_prompt}],
                },
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": prompt_text}],
                    }
                ],
                "generationConfig": generation_config,
            }

        def parse_answer(data: dict[str, Any]) -> tuple[str, str]:
            candidates = data.get("candidates", [])
            if not candidates:
                return "", "NO_CANDIDATE"
            first = candidates[0]
            parts = first.get("content", {}).get("parts", [])
            text_parts = [p.get("text", "") for p in parts if isinstance(p, dict)]
            answer_text = "\n".join(t for t in text_parts if t).strip()
            finish_reason = first.get("finishReason", "")
            return answer_text, finish_reason

        # 1) Normal deneme
        payload = build_payload(user_prompt, max_tokens=900)
        resp = requests.post(url, json=payload, timeout=60)
        if resp.status_code != 200:
            return f"Gemini API hatasi ({resp.status_code}): {resp.text[:500]}"

        data = resp.json()
        answer, finish_reason = parse_answer(data)

        # 2) Cevap cok kisa kaldiysa daha kisa baglam ile tekrar dene
        if finish_reason == "MAX_TOKENS" and len(answer) < 200:
            compact_context = context[:3500]
            retry_prompt = (
                f"Kullanici sorusu: {query}\\n\\n"
                f"Kaynaklar:\\n{compact_context}\\n\\n"
                "Gorev:\\n"
                "- Soruyu 5-8 maddeyle net cevapla.\\n"
                "- Ilac adini ve ilgili bolumu yaz.\\n"
                "- Yan etkileri siklik/ciddiyetle grupla."
            )
            retry_payload = build_payload(retry_prompt, max_tokens=1400)
            retry_resp = requests.post(url, json=retry_payload, timeout=60)
            if retry_resp.status_code == 200:
                retry_data = retry_resp.json()
                retry_answer, _ = parse_answer(retry_data)
                if retry_answer:
                    answer = retry_answer

        return answer or "Gemini bos yanit dondu."

    def ask(self, query: str, top_k: int = 8) -> dict[str, Any]:
        chunks = self.retrieve(query, top_k=top_k)
        context, sources = self.build_context(query, chunks)
        answer = self.ask_gemini(query, context)

        return {
            "query": query,
            "answer": answer,
            "sources": sources,
            "retrieved_count": len(chunks),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Prospekt RAG query pipeline")
    parser.add_argument("--query", required=True, help="Kullanici sorusu")
    parser.add_argument("--top-k", type=int, default=8, help="Retrieval top-k")
    parser.add_argument("--json", action="store_true", help="JSON formatinda yazdir")
    args = parser.parse_args()

    rag = ProspektRAG()
    result = rag.ask(args.query, top_k=args.top_k)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print("=" * 70)
    print("SORU:", result["query"])
    print("=" * 70)
    print(result["answer"])
    print("\nKAYNAKLAR:")
    for s in result["sources"]:
        print(
            f"  #{s['rank']} skor={s['score']} | {s['drug_name']} | "
            f"B{s['bolum_no']} | {s['section_title']}"
        )


if __name__ == "__main__":
    main()
