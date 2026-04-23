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
# SABİT AYARLAR
# =========================================================
TELEGRAM_BOT_TOKEN = "8763528906:AAHcCr2WfM6YUQpdBHiO_RldzHDPXOdxTsg"
TELEGRAM_CHAT_ID = "1307136561"

STATE_FILE = "mega_radar_state.json"
SCAN_INTERVAL = 180
REQUEST_TIMEOUT = 20

# Binance Listing Radar
LOOKBACK_HOURS = 24
LISTING_WARM_START = True

SEARCH_QUERY = (
    'site:binance.com/en/support/announcement '
    '("Binance Will List" OR "New Cryptocurrency Listing" OR '
    '"for Spot Trading" OR "Spot Trading Begins" OR "Binance Lists")'
)

GOOGLE_NEWS_RSS = (
    "https://news.google.com/rss/search?"
    "q={query}&hl=en-US&gl=US&ceid=US:en"
)

LISTING_REQUIRED = [
    "binance will list",
    "will list",
    "for spot trading",
    "spot trading begins",
    "opens trading for",
    "new cryptocurrency listing",
    "binance lists",
]

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

# Alpha / Funding / VC Radar
RSS_FEEDS = [
    "https://www.theblock.co/rss.xml",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://cryptoslate.com/feed/",
    "https://news.bitcoin.com/feed/",
    "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml",
]

TIER_S_VCS = [
    "a16z", "paradigm", "binance labs", "polychain",
    "multicoin", "dragonfly", "sequoia", "pantera"
]

TIER_A_VCS = [
    "coinbase ventures", "hashed", "animoca",
    "electric capital", "consensys"
]

ALPHA_SCORE_THRESHOLD = 45

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
logger = logging.getLogger("MEGA_RADAR")

session = requests.Session()
session.headers.update(HEADERS)


# =========================================================
# STATE
# =========================================================
def default_state():
    return {
        "listing_initialized": False,
        "listing_sent_links": [],
        "listing_sent_titles": [],
        "alpha_seen_ids": []
    }


def ensure_state():
    if not os.path.exists(STATE_FILE):
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(default_state(), f, ensure_ascii=False, indent=2)


def load_state():
    ensure_state()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError("State dict değil")

        base = default_state()
        base.update(data)

        if not isinstance(base.get("listing_sent_links"), list):
            base["listing_sent_links"] = []

        if not isinstance(base.get("listing_sent_titles"), list):
            base["listing_sent_titles"] = []

        if not isinstance(base.get("alpha_seen_ids"), list):
            base["alpha_seen_ids"] = []

        if not isinstance(base.get("listing_initialized"), bool):
            base["listing_initialized"] = False

        return base

    except Exception as e:
        logger.warning("State okunamadı, sıfırlanıyor: %s", e)
        return default_state()


def save_state(state):
    try:
        state["listing_sent_links"] = state.get("listing_sent_links", [])[-2000:]
        state["listing_sent_titles"] = state.get("listing_sent_titles", [])[-2000:]
        state["alpha_seen_ids"] = state.get("alpha_seen_ids", [])[-5000:]

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


def safe_markdown_text(s: str) -> str:
    s = (s or "").strip()
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    for ch in escape_chars:
        s = s.replace(ch, f"\\{ch}")
    return s


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
def send_telegram_html(text: str) -> bool:
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
        logger.error("Telegram HTML gönderim hatası: %s", e)
        return False


def send_telegram_markdown(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": True
    }

    try:
        r = session.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error("Telegram Markdown gönderim hatası: %s", e)
        return False


# =========================================================
# BINANCE LISTING
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


def fetch_listing_candidates():
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

    logger.info("Listing RSS aday sayısı: %s", len(items))
    return items


def build_listing_message(item, score: int) -> str:
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


def process_listing_once(state):
    sent_links = set(state.get("listing_sent_links", []))
    sent_titles = set(norm(x) for x in state.get("listing_sent_titles", []))
    initialized = state.get("listing_initialized", False)

    candidates = fetch_listing_candidates()
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

            if not is_recent(item.get("published"), LOOKBACK_HOURS):
                logger.info("LISTING ESKİ GEÇİLDİ: %s", title)
                sent_links.add(link)
                sent_titles.add(norm(title))
                continue

            verdict = classify_listing(title)
            if not verdict["allow"]:
                logger.info("LISTING REJECT: %s | neden=%s", title, verdict["reason"])
                sent_links.add(link)
                sent_titles.add(norm(title))
                continue

            if LISTING_WARM_START and not initialized:
                logger.info("LISTING WARM START SEED: %s", title)
                sent_links.add(link)
                sent_titles.add(norm(title))
                seeded += 1
                continue

            msg = build_listing_message(item, verdict["score"])
            ok = send_telegram_html(msg)

            if ok:
                sent_links.add(link)
                sent_titles.add(norm(title))
                alerts_sent += 1
                logger.info("LISTING ALARM GÖNDERİLDİ: %s", title)
                time.sleep(2)

        except Exception as e:
            logger.exception("Listing adayı işlenemedi: %s | hata=%s", item, e)

    state["listing_sent_links"] = list(sent_links)
    state["listing_sent_titles"] = list(sent_titles)
    state["listing_initialized"] = True

    logger.info("Listing turu tamamlandı | alarm: %s | seed: %s", alerts_sent, seeded)
    return alerts_sent


# =========================================================
# ALPHA / FUNDING / VC RADAR
# =========================================================
def calculate_alpha_score(title, body):
    text = f"{title} {body}".lower()
    score = 20

    for vc in TIER_S_VCS:
        if vc in text:
            score += 25

    for vc in TIER_A_VCS:
        if vc in text:
            score += 12

    funding_keywords = [
        "funding",
        "raised",
        "investment",
        "round",
        "seed",
        "series a",
        "series b",
        "$",
        "million",
        "m raised"
    ]

    for kw in funding_keywords:
        if kw in text:
            score += 8

    if any(x in text for x in ["10m", "20m", "50m", "100m", "million"]):
        score += 15

    return min(score, 100)


def fetch_alpha_news():
    news = []

    for url in RSS_FEEDS:
        try:
            r = session.get(url, timeout=12)
            r.raise_for_status()

            feed = feedparser.parse(r.text)
            for entry in feed.entries[:6]:
                news_id = entry.get("id") or entry.get("link")
                title = (entry.get("title") or "").strip()
                body = (entry.get("summary") or entry.get("description") or "").strip()
                link = (entry.get("link") or "").strip()

                if not news_id or not title:
                    continue

                news.append({
                    "id": news_id,
                    "title": title,
                    "body": body,
                    "link": link,
                })

        except Exception as e:
            logger.warning("Alpha RSS hatası (%s): %s", url, e)

    logger.info("Alpha RSS toplam haber sayısı: %s", len(news))
    return news


def build_alpha_message(title, score, link):
    safe_title = safe_markdown_text(title)
    safe_link = safe_markdown_text(link)
    now_text = safe_markdown_text(datetime.now().strftime("%H:%M:%S"))

    emoji = "💎" if score >= 75 else "🔥" if score >= 55 else "📈"

    msg = (
        f"{emoji} *ALPHA ENGINE*\n\n"
        f"🪙 *Project:* {safe_title}\n"
        f"📊 *Score:* {score}/100\n"
        f"🔗 *Link:* {safe_link}\n\n"
        f"🕒 {now_text}"
    )
    return msg


def process_alpha_once(state):
    seen_news_ids = set(state.get("alpha_seen_ids", []))
    sent_count = 0

    all_news = fetch_alpha_news()

    for item in all_news:
        try:
            news_id = item.get("id")
            title = item.get("title", "")
            body = item.get("body", "")
            link = item.get("link", "")

            if not news_id or news_id in seen_news_ids:
                continue

            score = calculate_alpha_score(title, body)

            if score >= ALPHA_SCORE_THRESHOLD:
                logger.info("ALPHA sinyal yakalandı | score=%s | title=%s", score, title[:80])
                msg = build_alpha_message(title, score, link)
                ok = send_telegram_markdown(msg)

                if ok:
                    sent_count += 1
                    time.sleep(2)

            seen_news_ids.add(news_id)

        except Exception as e:
            logger.exception("Alpha haber işlenemedi: %s | hata=%s", item, e)

    state["alpha_seen_ids"] = list(seen_news_ids)
    logger.info("Alpha turu tamamlandı | alarm: %s", sent_count)
    return sent_count


# =========================================================
# ANA DÖNGÜ
# =========================================================
def process_once():
    state = load_state()

    listing_count = process_listing_once(state)
    alpha_count = process_alpha_once(state)

    save_state(state)

    logger.info(
        "GENEL TUR BİTTİ | listing_alarm=%s | alpha_alarm=%s",
        listing_count, alpha_count
    )


def main():
    logger.info("Bot başladı: MEGA RADAR")
    logger.info("Tarama aralığı: %s sn", SCAN_INTERVAL)
    logger.info("Lookback: %s saat", LOOKBACK_HOURS)
    logger.info("Listing warm start: %s", LISTING_WARM_START)
    logger.info("Alpha eşik: %s", ALPHA_SCORE_THRESHOLD)

    while True:
        try:
            process_once()
        except Exception as e:
            logger.exception("Ana döngü hatası: %s", e)
            time.sleep(30)

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
