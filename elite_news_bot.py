import os
import re
import json
import time
import html
import logging
from datetime import datetime, timezone

import requests
import feedparser


# ================= CONFIG (KESİN BİLGİLER) =================
SCAN_INTERVAL = 120
STATE_FILE = "elite_radar_state.json"

# Bilgilerin doğrudan işlendi
TELEGRAM_BOT_TOKEN = "8735115726:AAHVB0gR_z-Qyzs-ot99ilbDmr_D9tmoIt4"
TELEGRAM_CHAT_ID = "1307136561"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

GOOGLE_NEWS_QUERIES = [
    "Binance Coinbase Kraken OKX Upbit listing crypto",
    "US CPI Fed inflation PCE FOMC NFP Powell crypto",
    "Bitcoin ETF BlackRock Fidelity Grayscale Chainlink ETF crypto",
    "crypto hack exploit stolen drain attack bridge",
]

REQUEST_TIMEOUT = 15
MAX_SEEN_ITEMS = 500
SEND_DELAY = 2

TRUSTED_SOURCES_HIGH = [
    "reuters", "bloomberg", "the block", "coindesk", 
    "cointelegraph", "decrypt", "federal reserve", 
    "sec", "cme group", "binance", "coinbase", "okx", "kraken"
]

LOW_QUALITY_HINTS = [
    "what to expect", "prediction", "predicts", "opinion", 
    "editorial", "could", "may", "might", "if this happens", 
    "price target", "forecast"
]

OLD_CONTEXT_HINTS = [
    "dec 9-10", "last week", "last month", "2025", "2024"
]

session = requests.Session()
session.headers.update(HEADERS)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("ELITE_RADAR")


# ================= HELPERS =================
def ensure_state():
    if not os.path.exists(STATE_FILE):
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"seen": []}, f, ensure_ascii=False, indent=2)

def load_state():
    ensure_state()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict) or "seen" not in data:
                return {"seen": []}
            return data
    except Exception as e:
        logger.warning(f"State okunamadı: {e}")
        return {"seen": []}

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"State kaydedilemedi: {e}")

def normalize_title(title: str) -> str:
    title = title.lower().strip()
    title = re.sub(r"\s+", " ", title)
    title = re.sub(r"[^\w\s]", "", title)
    return title

def send_tg(msg: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = session.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        logger.error(f"TG Bağlantı Hatası: {e}")
        return False

def source_score(title: str) -> int:
    t = title.lower()
    score = 0
    for s in TRUSTED_SOURCES_HIGH:
        if s in t: score += 8
    for bad in LOW_QUALITY_HINTS:
        if bad in t: score -= 12
    for old in OLD_CONTEXT_HINTS:
        if old in t: score -= 15
    return score

def looks_too_generic_or_old(title: str) -> bool:
    t = title.lower()
    generic_patterns = ["what to expect", "daily roundup", "and more", "weekly recap", "price prediction"]
    if any(x in t for x in generic_patterns): return True
    if any(x in t for x in ["dec ", "2024", "2025"]): return True
    return False

def classify_elite(title: str):
    t = title.lower()
    extra = source_score(title)
    if looks_too_generic_or_old(title): return None

    # 1) SECURITY
    if any(x in t for x in ["hack", "exploit", "stolen", "drain", "attack", "breach"]):
        score = min(99, max(70, 95 + extra))
        return ("SECURITY", score, "S", "KRİTİK NEGATİF", "SECURITY_BOT")

    # 2) LISTING
    if any(x in t for x in ["listing", "lists", "listed on", "adds support", "new pair"]):
        if any(b in t for b in ["binance", "coinbase", "kraken", "upbit", "okx"]):
            score = min(99, max(75, 96 + extra))
            return ("LISTING", score, "S+", "POZİTİF", "EXCHANGE_RADAR")

    # 3) USA DATA / MACRO
    if any(x in t for x in ["cpi", "fed", "inflation", "pce", "nfp", "interest rate", "powell", "fomc"]):
        score = min(97, max(65, 88 + extra))
        quality = "A+" if score >= 92 else "A"
        return ("USA_DATA", score, quality, "VOLATİLİTE", "MACRO_OBSERVER")

    # 4) INSTITUTIONAL / ETF
    if any(x in t for x in ["etf", "blackrock", "fidelity", "spot bitcoin", "grayscale"]):
        score = min(97, max(65, 86 + extra))
        quality = "A" if score >= 84 else "B"
        return ("INSTITUTIONAL", score, quality, "POZİTİF", "INSTITUTION_BOT")

    return None

def get_trade_action(tag: str, score: int, title: str) -> str:
    if tag == "SECURITY": return "ANLIK RİSK / DİKKAT"
    if tag == "LISTING": return "GÜÇLÜ TAKİP / HIZLI REAKSİYON" if score >= 95 else "TAKİP ET"
    if tag == "USA_DATA": return "MAKRO SAATİNİ İZLE"
    return "İZLE"

def format_elite_msg(tag, score, quality, effect, bot_type, title, link):
    safe_title = html.escape(title)
    safe_link = html.escape(link, quote=True)
    action = get_trade_action(tag, score, title)
    return (
        f"🚨 <b>{tag} ALARMI</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🤖 Bot: <b>{bot_type}</b>\n"
        f"⭐ Kalite: <b>{quality}</b> | Skor: <b>{score}/100</b>\n"
        f"📈 Etki: <b>{effect}</b>\n"
        f"🎯 Yorum: <b>{action}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📰 <b>{safe_title}</b>\n\n"
        f"🔗 <a href='{safe_link}'>Haber Kaynağına Git</a>"
    )

def fetch_google_news(query: str):
    rss_url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-US&gl=US&ceid=US:en"
    try:
        r = session.get(rss_url, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200: return []
        return feedparser.parse(r.content).entries[:10]
    except: return []

# ================= CORE LOOP =================
def run_radar():
    logger.info("ELITE RADAR V4 Aktif.")
    while True:
        try:
            state = load_state()
            seen = set(state.get("seen", []))
            new_items = 0
            for query in GOOGLE_NEWS_QUERIES:
                entries = fetch_google_news(query)
                for raw in entries:
                    title = raw.get("title", "").strip()
                    link = raw.get("link", "").strip()
                    if not title or not link: continue
                    title_key = normalize_title(title)
                    if title_key in seen: continue
                    info = classify_elite(title)
                    if not info or info[1] < 84: continue # Sadece A kalite ve üstü
                    
                    tag, score, quality, effect, bot_type = info
                    msg = format_elite_msg(tag, score, quality, effect, bot_type, title, link)
                    if send_tg(msg):
                        state["seen"].append(title_key)
                        seen.add(title_key)
                        new_items += 1
                        if len(state["seen"]) > MAX_SEEN_ITEMS: state["seen"] = state["seen"][-MAX_SEEN_ITEMS:]
                        save_state(state)
                        time.sleep(SEND_DELAY)
            logger.info(f"Tarama bitti. Yeni: {new_items}.")
            time.sleep(SCAN_INTERVAL)
        except Exception as e:
            logger.error(f"Hata: {e}")
            time.sleep(30)

if __name__ == "__main__":
    run_radar()
