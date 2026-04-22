import os
import re
import time
import json
import html
import logging
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

import requests
import feedparser

# =========================================================
# AYARLAR
# =========================================================
TELEGRAM_BOT_TOKEN = "8735115726:AAHVB0gR_z-Qyzs-ot99ilbDmr_D9tmoIt4"
TELEGRAM_CHAT_ID = "1307136561"

STATE_FILE = "binance_listing_rss_state.json"
SCAN_INTERVAL = 180
REQUEST_TIMEOUT = 20

# Kaç saatlik haberler değerlendirilsin
LOOKBACK_HOURS = 24

# İlk açılışta mevcut adayları state'e ekle, mesaj atma
WARM_START = True

# Sadece Binance support listing benzeri sonuçları hedefliyoruz
SEARCH_QUERY = (
    'site:binance.com/en/support/announcement '
    '("Binance Will List" OR "New Cryptocurrency Listing" OR '
    '"for Spot Trading" OR "Spot Trading Begins" OR "Binance Lists")'
)

GOOGLE_NEWS_RSS = (
    "https://news.google.com/rss/search?"
    "q={query}&hl=en-US&gl=US&ceid=US:en"
)

# Pozitif kalıplar
LISTING_REQUIRED = [
    "binance will list",
    "will list",
    "for spot trading",
    "spot trading begins",
    "opens trading for",
    "new cryptocurrency listing",
    "binance lists",
]

# Engellenecek kalıplar
LISTING_FORBIDDEN = [
    "futures",
    "perpetual",
    "margin",
    "options",
    "loan",
    "staking",
    "launchpool",
    "launchpad",
    "megadrop",
    "airdrop",
    "delist",
    "delisting",
    "maintenance",
    "pre-market",
    "copy trading",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("BINANCE_LISTING_RSS")

session = requests.Session()
session.headers.update(HEADERS)


# =========================================================
# STATE
# =========================================================
def ensure_state():
    if not os.path.exists(STATE_FILE):
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "sent_links": [],
                    "sent_titles": [],
                    "initialized": False
                },
                f,
                ensure_ascii=False,
                indent=2
            )


def load_state():
    ensure_state()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("State dict değil")
            data.setdefault("sent_links", [])
            data.setdefault("sent_titles", [])
            data.setdefault("initialized", False)
            return data
    except Exception as e:
        logger.warning("State okunamadı, sıfırlanıyor: %s", e)
        return {
            "sent_links": [],
            "sent_titles": [],
            "initialized": False
        }


def save_state(state):
    try:
        state["sent_links"] = state["sent_links"][-1000:]
        state["sent_titles"] = state["sent_titles"][-1000:]
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("State kaydedilemedi: %s", e)


# =========================================================
# HELPERS
# =========================================================
def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def safe_text(s: str) -> str:
    return html.escape((s or "").strip())


def now_utc():
    return datetime.now(timezone.utc)


def extract_symbols(title: str) -> str:
    found = re.findall(r"\(([A-Z0-9]{2,15})\)", title or "")
    if found:
        return ", ".join(found[:5])
    return "-"


def parse_entry_time(entry):
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        except Exception:
            pass

    text_candidates = [
        getattr(entry, "published", None),
        getattr(entry, "updated", None),
    ]

    patterns = [
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]

    for text in text_candidates:
        if not text:
            continue
        for fmt in patterns:
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            except Exception:
                pass

    return None


def is_recent(dt, lookback_hours=LOOKBACK_HOURS):
    if not dt:
        return False
    return dt >= now_utc() - timedelta(hours=lookback_hours)


# =========================================================
# TELEGRAM
# =========================================================
def send_telegram(text: str) -> bool:
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
def classify_listing(title: str):
    t = norm(title)

    for bad in LISTING_FORBIDDEN:
        if bad in t:
            return {
                "allow": False,
                "score": 0,
                "reason": f"forbidden={bad}"
            }

    score = 0
    reasons = []

    if "binance will list" in t:
        score += 70
        reasons.append("binance will list")

    if "for spot trading" in t:
        score += 20
        reasons.append("for spot trading")

    if "spot trading begins" in t or "opens trading for" in t:
        score += 15
        reasons.append("trading start phrase")

    if "new cryptocurrency listing" in t:
        score += 15
        reasons.append("new cryptocurrency listing")

    if "binance lists" in t or "will list" in t:
        score += 10
        reasons.append("generic list phrase")

    if "seed tag applied" in t:
        score += 5
        reasons.append("seed tag")

    if not any(x in t for x in LISTING_REQUIRED):
        return {
            "allow": False,
            "score": 0,
            "reason": "required_phrase_missing"
        }

    return {
        "allow": score >= 70,
        "score": min(score, 99),
        "reason": ", ".join(reasons) if reasons else "weak"
    }


# =========================================================
# FETCH
# =========================================================
def fetch_candidates():
    rss_url = GOOGLE_NEWS_RSS.format(query=quote(SEARCH_QUERY))
    r = session.get(rss_url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()

    feed = feedparser.parse(r.content)
    items = []

    for entry in feed.entries:
        title = (getattr(entry, "title", "") or "").strip()
        link = (getattr(entry, "link", "") or "").strip()
        published = parse_entry_time(entry)
        summary = (getattr(entry, "summary", "") or "").strip()

        if not title or not link:
            continue

        items.append({
            "title": title,
            "link": link,
            "published": published,
            "description": summary,
        })

    logger.info("RSS aday sayısı: %s", len(items))
    return items


# =========================================================
# MESSAGE
# =========================================================
def build_message(item, score: int) -> str:
    title = safe_text(item["title"])
    link = html.escape(item["link"], quote=True)
    desc = safe_text(item.get("description", ""))[:250]
    symbols = safe_text(extract_symbols(item["title"]))

    published_local = "-"
    if item.get("published"):
        published_local = item["published"].astimezone(
            timezone(timedelta(hours=3))
        ).strftime("%d.%m.%Y %H:%M:%S")

    msg = (
        "🚨 <b>ELITE SPOT LISTING</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"⭐ Skor: <b>{score}/100</b>\n"
        f"🪙 Sembol: <b>{symbols}</b>\n"
        f"📰 <b>{title}</b>\n"
        f"🕒 Tarih: <b>{published_local}</b>\n"
    )

    if desc:
        msg += f"🧠 Özet: {desc}\n"

    msg += (
        "━━━━━━━━━━━━━━━\n"
        f"🔗 <a href=\"{link}\">Habere Git</a>"
    )

    return msg


# =========================================================
# MAIN
# =========================================================
def process_once():
    state = load_state()
    sent_links = set(state.get("sent_links", []))
    sent_titles = set(norm(x) for x in state.get("sent_titles", []))
    initialized = state.get("initialized", False)

    candidates = fetch_candidates()
    alerts_sent = 0
    seeded = 0

    for item in candidates[:20]:
        try:
            title = item["title"]
            link = item["link"]

            if not title or not link:
                continue

            if link in sent_links or norm(title) in sent_titles:
                continue

            # Eski haberleri hiç gönderme
            if not is_recent(item.get("published"), LOOKBACK_HOURS):
                logger.info("ESKİ GEÇİLDİ: %s", title)
                sent_links.add(link)
                sent_titles.add(norm(title))
                continue

            verdict = classify_listing(title)
            if not verdict["allow"]:
                logger.info("REJECT: %s | neden=%s", title, verdict["reason"])
                sent_links.add(link)
                sent_titles.add(norm(title))
                continue

            # İlk açılışta mevcut adayları sadece state'e yaz, mesaj atma
            if WARM_START and not initialized:
                logger.info("WARM START SEED: %s", title)
                sent_links.add(link)
                sent_titles.add(norm(title))
                seeded += 1
                continue

            msg = build_message(item, verdict["score"])
            ok = send_telegram(msg)

            if ok:
                sent_links.add(link)
                sent_titles.add(norm(title))
                alerts_sent += 1
                logger.info("ALARM GÖNDERİLDİ: %s", title)
                time.sleep(2)

        except Exception as e:
            logger.exception("Aday işlenemedi: %s | hata=%s", item, e)

    state["sent_links"] = list(sent_links)
    state["sent_titles"] = list(sent_titles)
    state["initialized"] = True
    save_state(state)

    logger.info("Tur tamamlandı | gönderilen alarm: %s | seed: %s", alerts_sent, seeded)


def main():
    logger.info("Bot başladı: Binance Listing RSS Radar")
    logger.info("Tarama aralığı: %s sn", SCAN_INTERVAL)
    logger.info("Lookback: %s saat", LOOKBACK_HOURS)
    logger.info("Warm start: %s", WARM_START)

    while True:
        try:
            process_once()
        except Exception as e:
            logger.exception("Ana döngü hatası: %s", e)
            time.sleep(30)

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
