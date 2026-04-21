import os
import re
import json
import time
import html
import logging
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# =========================================================
# ULTRA ELITE BINANCE LISTING RADAR
# Sadece resmi Binance listing duyuruları
# =========================================================

BOT_NAME = "EXCHANGE_RADAR"
STATE_FILE = "binance_listing_state.json"

SCAN_INTERVAL = 180          # saniye
LOOKBACK_HOURS = 36          # sadece son X saat içindeki duyurular
REQUEST_TIMEOUT = 20

BINANCE_LIST_URL = "https://www.binance.com/en/support/announcement/list/48"
BASE_URL = "https://www.binance.com"

TELEGRAM_BOT_TOKEN = "8735115726:AAHVB0gR_z-Qyzs-ot99ilbDmr_D9tmoIt4"
TELEGRAM_CHAT_ID = "1307136561"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("BINANCE_LISTING_RADAR")

session = requests.Session()
session.headers.update(HEADERS)


# =========================================================
# STATE
# =========================================================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"sent_links": [], "sent_titles": []}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {"sent_links": [], "sent_titles": []}
            data.setdefault("sent_links", [])
            data.setdefault("sent_titles", [])
            return data
    except Exception as e:
        logger.warning("State okunamadı, sıfırlanıyor: %s", e)
        return {"sent_links": [], "sent_titles": []}


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("State kaydedilemedi: %s", e)


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram bilgileri eksik. Mesaj basılıyor:\n%s", text)
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }

    try:
        r = session.post(url, data=payload, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error("Telegram gönderim hatası: %s", e)
        return False


# =========================================================
# HELPERS
# =========================================================

def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def now_utc():
    return datetime.now(timezone.utc)


def safe_text(x):
    return html.escape((x or "").strip())


def parse_datetime_from_text(text):
    if not text:
        return None

    text = text.strip()

    patterns = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%b %d, %Y",
        "%B %d, %Y",
    ]

    for fmt in patterns:
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass

    m = re.search(r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}(?::\d{2})?))?", text)
    if m:
        date_part = m.group(1)
        time_part = m.group(2) or "00:00:00"
        if len(time_part) == 5:
            time_part += ":00"
        try:
            dt = datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M:%S")
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    return None


def extract_symbols(title):
    found = re.findall(r"\(([A-Z0-9]{2,15})\)", title or "")
    if found:
        return ", ".join(found[:5])
    return "-"


def is_recent(dt, lookback_hours=LOOKBACK_HOURS):
    if not dt:
        return False
    return dt >= now_utc() - timedelta(hours=lookback_hours)


# =========================================================
# FILTER LOGIC
# =========================================================

BLOCK_KEYWORDS = [
    "price prediction",
    "how high",
    "if listed",
    "analysis",
    "opinion",
    "rumor",
    "speculation",
    "futures will launch",
    "margin will add",
    "earn",
    "buy crypto",
    "convert",
    "vip loan",
    "trading bots services",
    "delist",
    "delisting",
    "maintenance",
    "airdrop",
    "launchpool",
    "launchpad",
    "megadrop",
    "dual investment",
    "simple earn",
    "loan",
    "staking",
    "copy trading",
    "pre-market",
    "perpetual",
    "usdⓈ-margined",
    "usdc perpetual",
    "margin",
    "options",
]

STRONG_LISTING_KEYWORDS = [
    "binance will list",
    "will list",
    "to list",
    "list ",
    "for spot trading",
    "spot trading",
]

SOFT_POSITIVE_KEYWORDS = [
    "seed tag applied",
    "new cryptocurrency listing",
]


def classify_title(title):
    t = norm(title)

    for bad in BLOCK_KEYWORDS:
        if bad in t:
            return {
                "allow": False,
                "score": 0,
                "reason": f"engelli kelime: {bad}"
            }

    score = 0

    if "binance will list" in t:
        score += 70

    if "for spot trading" in t or "spot trading" in t:
        score += 25

    if "seed tag applied" in t:
        score += 4

    if any(k in t for k in STRONG_LISTING_KEYWORDS):
        score += 15

    if any(k in t for k in SOFT_POSITIVE_KEYWORDS):
        score += 5

    allow = score >= 70

    return {
        "allow": allow,
        "score": min(score, 99),
        "reason": "uygun" if allow else "yetersiz listing gücü"
    }


# =========================================================
# BINANCE SCRAPE
# =========================================================

def fetch_html(url):
    r = session.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.text


def parse_list_page():
    html_text = fetch_html(BINANCE_LIST_URL)
    soup = BeautifulSoup(html_text, "html.parser")

    items = []

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        title = a.get_text(" ", strip=True)

        if "/en/support/announcement/detail/" in href and title:
            full_link = urljoin(BASE_URL, href)
            items.append({"title": title, "link": full_link})

    if not items:
        link_matches = re.findall(
            r'(\/en\/support\/announcement\/detail\/[a-zA-Z0-9]+)',
            html_text
        )
        for href in set(link_matches):
            full_link = urljoin(BASE_URL, href)
            items.append({"title": "", "link": full_link})

    seen = set()
    clean = []
    for item in items:
        key = item["link"]
        if key not in seen:
            seen.add(key)
            clean.append(item)

    logger.info("Liste sayfasından %s aday bulundu", len(clean))
    return clean


def parse_detail_page(link, fallback_title=""):
    html_text = fetch_html(link)
    soup = BeautifulSoup(html_text, "html.parser")
    page_text = soup.get_text("\n", strip=True)

    title = fallback_title.strip()
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(" ", strip=True)

    if not title:
        og = soup.find("meta", attrs={"property": "og:title"})
        if og and og.get("content"):
            title = og["content"].strip()

    published = None

    meta_candidates = [
        soup.find("meta", attrs={"property": "article:published_time"}),
        soup.find("meta", attrs={"name": "article:published_time"}),
        soup.find("meta", attrs={"name": "date"}),
        soup.find("meta", attrs={"property": "og:updated_time"}),
    ]
    for m in meta_candidates:
        if m and m.get("content"):
            published = parse_datetime_from_text(m["content"])
            if published:
                break

    if not published:
        m = re.search(
            r"Published on\s+([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}|\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)",
            page_text,
            re.IGNORECASE
        )
        if m:
            published = parse_datetime_from_text(m.group(1))

    if not published:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", page_text)
        if m:
            published = parse_datetime_from_text(m.group(1))

    description = ""
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        description = meta_desc["content"].strip()

    if not description:
        paragraphs = []
        for p in soup.find_all(["p", "article"]):
            txt = p.get_text(" ", strip=True)
            if txt and len(txt) > 40:
                paragraphs.append(txt)
            if len(paragraphs) >= 2:
                break
        description = " ".join(paragraphs)[:400]

    return {
        "title": title or fallback_title or "Untitled",
        "link": link,
        "published": published,
        "description": description.strip()
    }


# =========================================================
# MESSAGE FORMAT
# =========================================================

def build_message(item, score):
    title = safe_text(item["title"])
    link = safe_text(item["link"])
    desc = safe_text(item.get("description", ""))[:300]
    symbols = safe_text(extract_symbols(item["title"]))

    published_local = "-"
    if item.get("published"):
        published_local = item["published"].astimezone(
            timezone(timedelta(hours=3))
        ).strftime("%d.%m.%Y %H:%M:%S")

    msg = (
        "🚨 <b>LISTING ALARMI</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"🤖 Bot: <b>{safe_text(BOT_NAME)}</b>\n"
        f"⭐ Kalite: <b>S+</b> | Skor: <b>{score}/100</b>\n"
        "📈 Etki: <b>POZİTİF</b>\n"
        "🎯 Yorum: <b>GÜÇLÜ TAKİP / HIZLI REAKSİYON</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"🪙 Sembol: <b>{symbols}</b>\n"
        f"📰 <b>{title}</b>\n"
        f"🕒 Tarih: <b>{published_local}</b>\n"
    )

    if desc:
        msg += f"🧠 Özet: {desc}\n"

    msg += (
        "━━━━━━━━━━━━━━━\n"
        f"🔗 <a href=\"{link}\">Resmi Binance duyurusuna git</a>"
    )

    return msg


# =========================================================
# MAIN CHECK
# =========================================================

def process_once():
    state = load_state()
    sent_links = set(state.get("sent_links", []))
    sent_titles = set(norm(x) for x in state.get("sent_titles", []))

    candidates = parse_list_page()
    alerts_sent = 0

    for candidate in candidates[:20]:
        try:
            detail = parse_detail_page(candidate["link"], candidate.get("title", ""))

            if not detail["title"]:
                logger.info("Başlık boş, geçildi: %s", candidate["link"])
                continue

            if candidate["link"] in sent_links:
                logger.info("Zaten gönderilmiş link, geçildi: %s", detail["title"])
                continue

            if norm(detail["title"]) in sent_titles:
                logger.info("Zaten gönderilmiş başlık, geçildi: %s", detail["title"])
                continue

            if not is_recent(detail.get("published"), LOOKBACK_HOURS):
                logger.info("Eski duyuru, geçildi: %s", detail["title"])
                continue

            verdict = classify_title(detail["title"])
            if not verdict["allow"]:
                logger.info("Filtre dışı: %s | neden=%s", detail["title"], verdict["reason"])
                continue

            msg = build_message(detail, verdict["score"])
            ok = send_telegram(msg)

            if ok:
                sent_links.add(candidate["link"])
                sent_titles.add(norm(detail["title"]))
                alerts_sent += 1
                logger.info("ALARM GÖNDERİLDİ: %s", detail["title"])
                time.sleep(2)
            else:
                logger.warning("Mesaj gönderilemedi: %s", detail["title"])

        except Exception as e:
            logger.exception("Aday işlenemedi: %s | hata=%s", candidate, e)

    state["sent_links"] = list(sent_links)[-500:]
    state["sent_titles"] = list(sent_titles)[-500:]
    save_state(state)

    logger.info("Tur tamamlandı | gönderilen alarm: %s", alerts_sent)


def main():
    logger.info("Bot başladı: %s", BOT_NAME)
    logger.info("Kaynak: %s", BINANCE_LIST_URL)
    logger.info("Tarama aralığı: %s sn", SCAN_INTERVAL)
    logger.info("Lookback: %s saat", LOOKBACK_HOURS)

    while True:
        try:
            process_once()
        except Exception as e:
            logger.exception("Ana döngü hatası: %s", e)

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
