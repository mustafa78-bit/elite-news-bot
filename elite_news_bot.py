import os
import re
import json
import time
import html
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests
import feedparser
import schedule

# ================= CONFIG =================
SCAN_EVERY_MINUTES = 5
STATE_FILE = "market_radar_state.json"

TELEGRAM_BOT_TOKEN = os.getenv("8735115726:AAHVB0gR_z-Qyzs-ot99ilbDmr_D9tmoIt4 ")
TELEGRAM_CHAT_ID = os.getenv("1307136561")

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

GOOGLE_NEWS_URL = "https://news.google.com/rss/search"

# ================= LOG =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("elite")

session = requests.Session()
session.headers.update(HEADERS)

# ================= STATE =================
def load_state():
    if not os.path.exists(STATE_FILE):
        return {"seen": {}}
    with open(STATE_FILE, "r") as f:
        return json.load(f)

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def now_ts():
    return int(time.time())

# ================= HELPERS =================
def safe_get(url, params=None):
    try:
        r = session.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r
    except:
        return None

def normalize(text):
    return re.sub(r"\s+", " ", str(text).lower())

def extract_coin(text):
    m = re.search(r"\(([A-Z0-9]{2,10})\)", text)
    return m.group(1) if m else "-"

# ================= TELEGRAM =================
def send(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        })
    except:
        pass

# ================= NEWS =================
def google_news(query):
    r = safe_get(GOOGLE_NEWS_URL, {
        "q": query,
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en"
    })
    if not r:
        return []
    feed = feedparser.parse(r.content)
    return feed.entries[:10]

# ================= CLASSIFY =================
def classify(title):
    t = normalize(title)

    # HACK
    if any(x in t for x in ["hack", "exploit", "breach"]):
        return ("HACK", 92, "A+", "NEGATİF", "TREND")

    # ETF / KURUMSAL
    if any(x in t for x in ["etf", "blackrock", "fidelity"]):
        return ("ETF", 90, "A+", "POZİTİF", "KURUMSAL")

    # SEC
    if "sec" in t:
        return ("SEC", 85, "A", "NEGATİF", "KURUMSAL")

    # MACRO
    if any(x in t for x in ["cpi", "pce", "nfp", "fed"]):
        return ("MACRO", 88, "A", "YÖN TAKİP", "MAKRO")

    return None

# ================= FORMAT =================
def format_msg(tag, score, quality, effect, bot_type, title, link):
    coin = extract_coin(title)

    return (
        f"🧠 <b>ELITE NEWS</b>\n\n"
        f"🤖 Bot: <b>{bot_type}</b>\n"
        f"🏷 Tür: <b>{tag}</b>\n"
        f"🪙 Coin: <b>{coin}</b>\n"
        f"⭐ Kalite: <b>{quality}</b>\n"
        f"📈 Etki: <b>{effect}</b>\n"
        f"🎯 Skor: <b>{score}/100</b>\n"
        f"📰 {html.escape(title)}\n"
        f"🔗 <a href=\"{link}\">Habere Git</a>"
    )

# ================= MAIN =================
def run():
    logger.info("ELITE NEWS başladı...")
    state = load_state()

    while True:
        entries = google_news(
            '(crypto OR bitcoin OR ethereum) (ETF OR hack OR SEC OR CPI OR PCE OR Fed)'
        )

        for e in entries:
            title = e.get("title", "")
            link = e.get("link", "")

            if title in state["seen"]:
                continue

            result = classify(title)
            if not result:
                continue

            tag, score, quality, effect, bot_type = result

            msg = format_msg(tag, score, quality, effect, bot_type, title, link)
            send(msg)

            state["seen"][title] = now_ts()
            save_state(state)

            logger.info(f"Gönderildi: {title[:60]}")
            time.sleep(2)

        logger.info("Tarama bitti, bekleniyor...")
        time.sleep(300)

if __name__ == "__main__":
    run()
