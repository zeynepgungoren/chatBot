
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser
from io import BytesIO

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader


# ==================================================
# AYARLAR
# ==================================================

# Yalnızca bu web sayfası taranacak.
START_URL = "https://ceng.balikesir.edu.tr/isletmede-mesleki-egitim"

BACKEND_DIR = Path(__file__).resolve().parents[2]

RAW_DIR = BACKEND_DIR / "data" / "raw"
PDF_DIR = RAW_DIR / "pdfs"

OUTPUT_DIR = BACKEND_DIR / "data" / "extracted"
OUTPUT_FILE = OUTPUT_DIR / "documents.jsonl"

REQUEST_DELAY = 1
TIMEOUT = 20
USER_AGENT = "BAUN-Education-Chatbot-Research/0.1"

session = requests.Session()
session.headers.update({
    "User-Agent": USER_AGENT
})

robots_cache = {}
processed_pdfs = set()
records = []
record_keys = set()


# ==================================================
# URL KONTROLÜ
# ==================================================

def normalize_url(url):
    """URL'deki fragment kısmını temizler."""
    if not url:
        return None

    url = urldefrag(url.strip())[0]
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return None

    return url


def can_fetch(url):
    """robots.txt kurallarını kontrol eder."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    if robots_url not in robots_cache:
        try:
            response = session.get(
                robots_url,
                timeout=TIMEOUT
            )

            if response.status_code == 404:
                parser = RobotFileParser()
                parser.parse([])
                robots_cache[robots_url] = parser

            elif response.ok:
                parser = RobotFileParser()
                parser.set_url(robots_url)
                parser.parse(response.text.splitlines())
                robots_cache[robots_url] = parser

            else:
                robots_cache[robots_url] = None

        except requests.RequestException:
            robots_cache[robots_url] = None

    parser = robots_cache[robots_url]

    if parser is None:
        return False

    return parser.can_fetch(USER_AGENT, url)


# ==================================================
# METİN KAYDETME
# ==================================================

def save_record(record):
    """Boş metinleri ve tekrar eden kayıtları kaydetmez."""
    text = record.get("text", "").strip()

    if not text:
        return

    key = (
        record.get("source_url", ""),
        record.get("page"),
        hashlib.sha256(text.encode("utf-8")).hexdigest()
    )

    if key in record_keys:
        return

    record_keys.add(key)
    records.append(record)


# ==================================================
# PDF OKUMA
# ==================================================

def extract_pdf(url, content):
    """PDF dosyasını kaydeder ve sayfa metinlerini çıkarır."""
    if url in processed_pdfs:
        return

    processed_pdfs.add(url)

    if not content.startswith(b"%PDF-"):
        print(f"PDF biçimi doğrulanamadı: {url}")
        return

    try:
        # PDF dosyasını diske kaydet.
        pdf_hash = hashlib.sha256(
            url.encode("utf-8")
        ).hexdigest()

        pdf_path = PDF_DIR / f"{pdf_hash}.pdf"
        pdf_path.write_bytes(content)

        # PDF sayfalarını oku.
        reader = PdfReader(BytesIO(content))

        file_name = Path(
            urlparse(url).path
        ).name or "Universite_Belgesi.pdf"

        extracted_pages = 0

        for page_number, page in enumerate(
            reader.pages,
            start=1
        ):
            page_text = page.extract_text() or ""

            if not page_text.strip():
                continue

            save_record({
                "source_type": "pdf",
                "source_url": url,
                "file_name": file_name,
                "page": page_number,
                "text": page_text
            })

            extracted_pages += 1

        print(
            f"PDF işlendi: {file_name} | "
            f"Metin bulunan sayfa: {extracted_pages}"
        )

        if extracted_pages == 0:
            print(
                "Uyarı: PDF'den metin çıkarılamadı. "
                "Dosya taranmış görüntü olabilir."
            )

    except Exception as error:
        print(f"PDF okunamadı: {url} | Hata: {error}")


def download_pdf(url):
    """PDF bağlantısını indirir ve içeriğini işler."""
    if url in processed_pdfs:
        return

    if not can_fetch(url):
        print(f"PDF robots.txt nedeniyle atlandı: {url}")
        return

    try:
        time.sleep(REQUEST_DELAY)

        response = session.get(
            url,
            timeout=TIMEOUT
        )
        response.raise_for_status()

        if (
            "application/pdf"
            in response.headers.get("Content-Type", "").lower()
            or response.content.startswith(b"%PDF-")
        ):
            extract_pdf(response.url, response.content)
        else:
            print(f"Bağlantı PDF içermiyor: {url}")

    except requests.RequestException as error:
        print(f"PDF indirilemedi: {url} | Hata: {error}")


# ==================================================
# TEK WEB SAYFASINI OKUMA
# ==================================================

def crawl_page(url):
    """Yalnızca belirtilen sayfayı ve doğrudan PDF'lerini işler."""
    if not can_fetch(url):
        print(f"Web sayfasına erişim robots.txt nedeniyle engellendi: {url}")
        return

    try:
        time.sleep(REQUEST_DELAY)

        response = session.get(
            url,
            timeout=TIMEOUT
        )
        response.raise_for_status()

    except requests.RequestException as error:
        print(f"Web sayfası açılamadı: {url} | Hata: {error}")
        return

    content_type = response.headers.get(
        "Content-Type", ""
    ).lower()

    # Başlangıç adresi doğrudan PDF ise onu oku.
    if (
        "application/pdf" in content_type
        or response.content.startswith(b"%PDF-")
    ):
        extract_pdf(response.url, response.content)
        return

    if "html" not in content_type:
        print(f"Adres HTML sayfası değil: {response.url}")
        return

    response.encoding = (
        response.apparent_encoding or response.encoding
    )

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    # Sayfa metnine katkısı olmayan öğeleri çıkar.
    for element in soup.find_all([
        "script",
        "style",
        "noscript",
        "svg"
    ]):
        element.decompose()

    title = (
        soup.title.get_text(" ", strip=True)
        if soup.title
        else response.url
    )

    main_content = (
        soup.find("main")
        or soup.find("article")
        or soup.body
        or soup
    )

    page_text = main_content.get_text(
        separator=" ",
        strip=True
    )

    # Web sayfasının metnini kaydet.
    if page_text:
        save_record({
            "source_type": "web_page",
            "source_url": response.url,
            "file_name": title,
            "page": None,
            "text": page_text
        })

        print(f"Web sayfası okundu: {title}")
    else:
        print("Web sayfasında okunabilir metin bulunamadı.")

    # Yalnızca bu sayfadaki doğrudan PDF bağlantılarını işle.
    for link in soup.find_all("a", href=True):
        href = link["href"].strip()

        if not href or href.startswith(
            ("mailto:", "tel:", "javascript:")
        ):
            continue

        pdf_url = normalize_url(
            urljoin(response.url, href)
        )

        if not pdf_url:
            continue

        # URL'nin uzantısını kontrol et.
        path = urlparse(pdf_url).path.lower()

        if path.endswith(".pdf"):
            print(f"PDF bağlantısı bulundu: {pdf_url}")
            download_pdf(pdf_url)


# ==================================================
# ANA FONKSİYON
# ==================================================

def main():
    RAW_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    PDF_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    # Önceki taramadan kalan kayıtları temizle.
    records.clear()
    record_keys.clear()
    processed_pdfs.clear()

    # Yalnızca tek başlangıç URL'sini işle.
    start_url = normalize_url(START_URL)

    if not start_url:
        print("Başlangıç URL'si geçersiz.")
        return

    print("=" * 50)
    print("BAUN CHATBOT KAYNAK TARAMASI")
    print("=" * 50)
    print(f"Başlangıç sayfası: {start_url}")
    print("Diğer web sayfaları taranmayacak.\n")

    crawl_page(start_url)

    # Web ve PDF metinlerini JSONL dosyasına kaydet.
    with OUTPUT_FILE.open(
        "w",
        encoding="utf-8"
    ) as file:
        for record in records:
            file.write(
                json.dumps(
                    record,
                    ensure_ascii=False
                ) + "\n"
            )

    web_count = sum(
        1 for record in records
        if record["source_type"] == "web_page"
    )

    pdf_page_count = sum(
        1 for record in records
        if record["source_type"] == "pdf"
    )

    print("\n" + "=" * 50)
    print("TARAMA TAMAMLANDI")
    print("=" * 50)
    print(f"Okunan web sayfası: {web_count}")
    print(f"İşlenen PDF URL sayısı: {len(processed_pdfs)}")
    print(f"Kaydedilen PDF sayfa metni: {pdf_page_count}")
    print(f"Toplam kayıt: {len(records)}")
    print(f"Çıktı dosyası: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
