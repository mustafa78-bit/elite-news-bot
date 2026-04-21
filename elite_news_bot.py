import os
import json
import time
import html
import logging
import requests
import feedparser

# ================= CONFIG (DOĞRUDAN GİRİŞ) =================
SCAN_INTERVAL = 120  # 2 dakikada bir tarar
STATE_FILE = "elite_radar_state.json"

# Paydaş uyumu için token ve ID kodun içine gömüldü
TELEGRAM_BOT_TOKEN = "8735115726:AAHVB0gR_z-Qyzs-ot99ilbDmr_D9tmoIt4"
TELEGRAM_CHAT_ID = "1307136561"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

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
            json.dump({"seen": []}, f, ensure_ascii=False)

def load_state():
    ensure_state()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "seen" not in data or not isinstance(data["seen"], list):
                return {"seen": []}
            return data
    except Exception:
        return {"seen": []}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def send_tg(msg):
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
        if r.status_code != 200:
            logger.error(f"TG Hatası: {r.status_code} - {r.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"TG Bağlantı Hatası: {e}")
        return False

def classify_elite(title):
    t = title.lower()
    # 1) LISTING
    if any(x in t for x in ["listing", "lists", "adds support", "new pair", "listed on"]):
        if any(b in t for b in ["binance", "coinbase", "kraken", "upbit", "okx"]):
            return ("LISTING", 98, "S+", "POZİTİF", "EXCHANGE_RADAR")
    # 2) ABD VERİ / MAKRO
    if any(x in t for x in ["cpi", "fed", "inflation", "pce", "nfp", "interest rate", "powell", "fomc"]):
        return ("USA_DATA", 94, "A+", "VOLATİLİTE", "MACRO_OBSERVER")
    # 3) GÜVENLİK / HACK
    if any(x in t for x in ["hack", "exploit", "stolen", "drain", "attack"]):
        return ("SECURITY", 95, "S", "KRİTİK NEGATİF", "SECURITY_BOT")
    # 4) ETF / KURUMSAL
    if any(x in t for x in ["etf", "blackrock", "fidelity", "spot bitcoin", "grayscale"]):
        return ("INSTITUTIONAL", 90, "A", "POZİTİF", "INSTITUTION_BOT")
    return None

def format_elite_msg(tag, score, quality, effect, bot_type, title, link):
    safe_title = html.escape(title)
    safe_link = html.escape(link, quote=True)
    return (
        f"🚨 <b>{tag} ALARMI</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🤖 Bot: <b>{bot_type}</b>\n"
        f"⭐ Kalite: <b>{quality}</b> | Skor: <b>{score}/100</b>\n"
        f"📈 Etki: <b>{effect}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📰 <b>{safe_title}</b>\n\n"
        f"🔗 <a href='{safe_link}'>Haber Kaynağına Git</a>"
    )

def fetch_google_news(query):
    rss_url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    try:
        r = session.get(rss_url, timeout=15)
        if r.status_code != 200:
            return []
        feed = feedparser.parse(r.content)
        return feed.entries[:10]
    except Exception:
        return []

# ================= CORE LOOP =================
def run_radar():
    logger.info("ELITE RADAR V3 Aktif. Av başlıyor...")

    queries = [
        "Binance Coinbase Kraken OKX Upbit listing crypto",
        "US CPI Fed Inflation PCE FOMC crypto",
        "Bitcoin ETF BlackRock Fidelity Grayscale crypto",
        "crypto hack exploit stolen drain attack",
    ]

    while True:
        try:
            state = load_state()
            seen = set(state.get("seen", []))
            new_items = 0

            for query in queries:
                entries = fetch_google_news(query)
                for e in entries:
                    title = e.get("title", "").strip()
                    link = e.get("link", "").strip()

                    if not title or not link or title in seen:
                        continue

                    info = classify_elite(title)
                    if not info:
                        continue

                    tag, score, quality, effect, bot_type = info
                    msg = format_elite_msg(tag, score, quality, effect, bot_type, title, link)

                    if send_tg(msg):
                        logger.info(f"YAYINLANDI: {tag} | {title[:50]}...")
                        state["seen"].append(title)
                        seen.add(title)
                        new_items += 1

                        if len(state["seen"]) > 300:
                            state["seen"] = state["seen"][-300:]

                        save_state(state)
                        time.sleep(2)

            logger.info(f"Tarama bitti. Yeni: {new_items}. {SCAN_INTERVAL} sn bekleniyor.")
            time.sleep(SCAN_INTERVAL)

        except Exception as e:
            logger.error(f"Döngü Hatası: {e}")
            time.sleep(30)

if __name__ == "__main__":
    run_radar()
