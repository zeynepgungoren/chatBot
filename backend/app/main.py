
import json
import re
from pathlib import Path
from collections import Counter

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


# ==================================================
# FASTAPI UYGULAMASI
# ==================================================

app = FastAPI(
    title="BAUN Eğitim Chatbot API",
    description=(
        "Balıkesir Üniversitesi web sayfası ve "
        "PDF belgelerine dayalı soru-cevap sistemi."
    ),
    version="1.0.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================================================
# DOSYA YOLLARI
# ==================================================

BACKEND_DIR = Path(__file__).resolve().parents[1]

DOCUMENTS_FILE = (
    BACKEND_DIR
    / "data"
    / "extracted"
    / "documents.jsonl"
)


# ==================================================
# VERİ MODELLERİ
# ==================================================

class ChatRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Chatbot'a sorulacak soru",
    )


class Source(BaseModel):
    title: str
    url: str
    page: int | None = None
    source_type: str


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]


# ==================================================
# METİN İŞLEME
# ==================================================

def tokenize(text: str) -> list[str]:
    """
    Türkçe metinleri basitçe kelimelere ayırır.
    Noktalama işaretlerini kaldırır.
    """
    text = text.casefold()
    return re.findall(r"\w+", text, flags=re.UNICODE)


def split_into_chunks(
    text: str,
    chunk_size: int = 180,
    overlap: int = 35,
) -> list[str]:
    """
    Uzun metinleri daha küçük parçalara ayırır.
    Böylece ilgili bilgi daha kolay bulunabilir.
    """
    words = text.split()

    if not words:
        return []

    chunks = []
    step = max(1, chunk_size - overlap)

    for start in range(0, len(words), step):
        chunk = " ".join(words[start:start + chunk_size])

        if chunk:
            chunks.append(chunk)

        if start + chunk_size >= len(words):
            break

    return chunks


# ==================================================
# BELGELERİ YÜKLEME
# ==================================================

def load_documents() -> list[dict]:
    """
    documents.jsonl dosyasındaki web ve PDF
    kayıtlarını okur.
    """
    documents = []

    if not DOCUMENTS_FILE.exists():
        return documents

    try:
        with DOCUMENTS_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:
            for line in file:
                if not line.strip():
                    continue

                try:
                    document = json.loads(line)
                except json.JSONDecodeError:
                    continue

                text = document.get("text", "").strip()

                if not text:
                    continue

                # Her belgeyi daha küçük metin parçalarına ayır.
                chunks = split_into_chunks(text)

                for chunk in chunks:
                    documents.append({
                        "text": chunk,
                        "source_url": document.get(
                            "source_url", ""
                        ),
                        "file_name": document.get(
                            "file_name",
                            "Üniversite kaynağı",
                        ),
                        "page": document.get("page"),
                        "source_type": document.get(
                            "source_type",
                            "web_page",
                        ),
                    })

    except OSError as error:
        print(f"Belge dosyası okunamadı: {error}")

    return documents


# ==================================================
# KAYNAKLARDA ARAMA
# ==================================================

def search_documents(
    question: str,
    limit: int = 5,
) -> list[dict]:
    """
    Soru kelimeleri ile kaynak metinlerini karşılaştırır.
    En fazla eşleşme bulunan parçaları döndürür.
    """
    documents = load_documents()

    if not documents:
        return []

    question_words = set(tokenize(question))

    # Tek harfli kelimeler genellikle faydalı değildir.
    question_words = {
        word for word in question_words
        if len(word) > 1
    }

    if not question_words:
        return []

    results = []

    for document in documents:
        text = document["text"]
        document_words = tokenize(text)

        if not document_words:
            continue

        word_counts = Counter(document_words)

        # Eşleşen kelimelerin sayısı ve metindeki sıklığı.
        matched_words = question_words.intersection(
            document_words
        )

        if not matched_words:
            continue

        score = sum(
            1 + min(word_counts[word], 3) * 0.1
            for word in matched_words
        )

        # Sorudaki kelimelerin ne kadarının eşleştiği.
        coverage = len(matched_words) / len(question_words)

        # Çok kısa eşleşmelerin üst sıralara çıkmasını önle.
        score = score * coverage

        results.append({
            **document,
            "score": score,
            "coverage": coverage,
        })

    results.sort(
        key=lambda item: item["score"],
        reverse=True,
    )

    # Aynı kaynaktan çok sayıda benzer parçayı tekrarlama.
    selected = []
    seen = set()

    for result in results:
        key = (
            result["source_url"],
            result["page"],
            result["text"][:150],
        )

        if key in seen:
            continue

        seen.add(key)
        selected.append(result)

        if len(selected) >= limit:
            break

    return selected


# ==================================================
# YANIT OLUŞTURMA
# ==================================================

def create_answer(
    question: str,
    results: list[dict],
) -> str:
    """
    Bulunan kaynak parçalarından kaynaklı bir yanıt hazırlar.
    Bu sürüm LLM kullanmaz; kaynak metinlerini sunar.
    """
    if not results:
        return (
            "Bu soruyla ilgili taranmış kaynaklarda "
            "yeterli bilgi bulamadım. Sorunuzu farklı "
            "kelimelerle ifade edebilir veya kaynak "
            "sayfasındaki bilgileri kontrol edebilirsiniz."
        )

    answer_parts = [
        "Kaynaklarda sorunuzla ilişkili bulunan bilgiler:\n"
    ]

    for index, result in enumerate(results, start=1):
        answer_parts.append(
            f"{index}. {result['text']}"
        )

    answer_parts.append(
        "\nNot: Bu yanıt, kaynak metinlerinde bulunan "
        "kelime eşleşmelerine dayanır. Bilgilerin "
        "sorunuzu tam olarak cevaplayıp cevaplamadığını "
        "kaynaklardan kontrol edin."
    )

    return "\n\n".join(answer_parts)


def create_sources(results: list[dict]) -> list[Source]:
    """Yanıtta kullanılan kaynakları listeler."""
    sources = []
    seen = set()

    for result in results:
        url = result.get("source_url", "")

        key = (
            url,
            result.get("page"),
        )

        if not url or key in seen:
            continue

        seen.add(key)

        sources.append(
            Source(
                title=result.get(
                    "file_name",
                    "Üniversite kaynağı",
                ),
                url=url,
                page=result.get("page"),
                source_type=result.get(
                    "source_type",
                    "web_page",
                ),
            )
        )

    return sources


# ==================================================
# API UÇ NOKTALARI
# ==================================================

@app.get("/")
def home():
    return {
        "message": "BAUN Chatbot API çalışıyor.",
        "docs": "/docs",
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "documents_file_exists": DOCUMENTS_FILE.exists(),
        "documents_file": str(DOCUMENTS_FILE),
    }


@app.get("/documents/count")
def documents_count():
    documents = load_documents()

    return {
        "count": len(documents),
        "documents_file": str(DOCUMENTS_FILE),
        "file_exists": DOCUMENTS_FILE.exists(),
    }


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    question = request.question.strip()

    if not question:
        return ChatResponse(
            answer="Lütfen bir soru yazın.",
            sources=[],
        )

    results = search_documents(
        question=question,
        limit=5,
    )

    answer = create_answer(
        question=question,
        results=results,
    )

    sources = create_sources(results)

    return ChatResponse(
        answer=answer,
        sources=sources,
    )
