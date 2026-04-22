import os
import re
import time
import json
import html
import logging
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

# =========================================================
# BINANCE LISTING RADAR
# =========================================================

SCAN_INTERVAL = 180
LOOKBACK_HOURS = 36
REQUEST_TIMEOUT = 20
STATE_FILE = "binance_listing_state.json"
BOT_NAME = "EXCHANGE_RADAR"

BINANCE_LIST_URL = "https://www.binance.com/en/support/announcement/list/48"
BASE_URL = "https://www.binance.com"

TELEGRAM_BOT_TOKEN = "8735115726:AAHVB0gR_z-Qyzs-ot99ilbDmr_D9tmoIt4"
TELEGRAM_CHAT_ID = "1307136561"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

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

session = requests.Session()
session.headers.update(HEADERS)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("BINANCE_LISTING_RADAR")


# =========================================================
# STATE
# =========================================================

def ensure_state():
    if not os.path.exists(STATE_FILE):
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"sent_links": [], "sent_titles": []}, f, ensure_ascii=False, indent=2)


def load_state():
    ensure_state()
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
# HELPERS
# =========================================================

def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def now_utc():
    return datetime.now(timezone.utc)


def safe_text(x):
    return html.escape((x or "").strip())


def extract_symbols(title):
    found = re.findall(r"\(([A-Z0-9]{2,15})\)", title or "")
    if found:
        return ", ".join(found[:5])
    return "-"


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


def is_recent(dt, lookback_hours=LOOKBACK_HOURS):
    if not dt:
        return False
    return dt >= now_utc() - timedelta(hours=lookback_hours)


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram bilgileri eksik.")
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
# FILTER
# =========================================================

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
# BINANCE FETCH
# =========================================================

def fetch_html(url):
    r = session.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.text


def parse_list_page():
    html_text = fetch_html(BINANCE_LIST_URL)
    candidates = set()

    patterns = [
        r'/en/support/announcement/detail/([0-9a-fA-F]+)',
        r'"code"\s*:\s*"([0-9a-fA-F]+)"',
    ]

    for pattern in patterns:
        for code in re.findall(pattern, html_text, re.DOTALL):
            candidates.add(f"{BASE_URL}/en/support/announcement/detail/{code}")

    items = [{"title": "", "link": link} for link in sorted(candidates)]

    logger.info("Liste sayfasından %s aday bulundu", len(items))
    return items


def parse_detail_page(link, fallback_title=""):
    html_text = fetch_html(link)
    soup = BeautifulSoup(html_text, "html.parser")
    page_text = soup.get_text("\n", strip=True)

    title = fallback_title.strip() if fallback_title else ""

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
# MESSAGE
# =========================================================

def build_message(item, score):
    title = safe_text(item["title"])
    link = html.escape(item["link"], quote=True)
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

    for candidate in candidates[:30]:
        try:
            detail = parse_detail_page(candidate["link"], candidate.get("title", ""))

            if not detail["title"]:
                logger.info("Başlık boş, geçildi: %s", candidate["link"])
                continue

            if candidate["link"] in sent_links:
                continue

            if norm(detail["title"]) in sent_titles:
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
            time.sleep(30)

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
