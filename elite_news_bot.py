import os
import re
import json
import time
import html
import logging
import requests
import feedparser

# ================= CONFIG =================
SCAN_EVERY_MINUTES = 5
STATE_FILE = "market_radar_state.json"

# Değerleri direkt tırnak içine aldım, boşlukları temizledim
TELEGRAM_BOT_TOKEN = "8735115726:AAHVB0gR_z-Qyzs-ot99ilbDmr_D9tmoIt4"
TELEGRAM_CHAT_ID = "1307136561"

HEADERS = {"User-Agent": "Mozilla/5.0"}
GOOGLE_NEWS_URL = "https://news.google.com/rss/search"

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("elite")

session = requests.Session()
session.headers.update(HEADERS)

# ================= STATE MANAGEMENT =================
def load_state():
    if not os.path.exists(STATE_FILE):
        return {"seen": {}}
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {"seen": {}}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

# ================= CORE FUNCTIONS =================
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        # Eğer Telegram hata dönerse logda görelim
        if r.status_code != 200:
            logger.error(f"Telegram Hatası: {r.status_code} - {r.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"Bağlantı Hatası: {e}")
        return False

def classify(title):
    t = title.lower()
    if any(x in t for x in ["hack", "exploit", "breach", "drain"]):
        return ("HACK", 92, "A+", "NEGATİF", "TREND")
    if any(x in t for x in ["etf", "blackrock", "fidelity", "grayscale"]):
        return ("ETF", 90, "A+", "POZİTİF", "KURUMSAL")
    if "sec" in t:
        return ("SEC", 85, "A", "NEGATİF", "KURUMSAL")
    if any(x in t for x in ["cpi", "fed", "interest rate", "inflation"]):
        return ("MACRO", 88, "A", "YÖN TAKİP", "MAKRO")
    return None

def format_msg(tag, score, quality, effect, bot_type, title, link):
    return (
        f"🧠 <b>ELITE NEWS</b>\n\n"
        f"🤖 Bot: <b>{bot_type}</b>\n"
        f"🏷 Tür: <b>{tag}</b>\n"
        f"⭐ Kalite: <b>{quality}</b>\n"
        f"📈 Etki: <b>{effect}</b>\n"
        f"🎯 Skor: <b>{score}/100</b>\n\n"
        f"📰 {html.escape(title)}\n\n"
        f"🔗 <a href=\"{link}\">Habere Git</a>"
    )

# ================= MAIN LOOP =================
def run():
    logger.info("ELITE NEWS RADAR Başlatıldı...")
    state = load_state()

    while True:
        try:
            # RSS üzerinden haberleri çek
            params = {
                "q": "(crypto OR bitcoin OR ethereum) (ETF OR hack OR SEC OR Fed)",
                "hl": "en-US", "gl": "US", "ceid": "US:en"
            }
            r = session.get(GOOGLE_NEWS_URL, params=params, timeout=15)
            if r.status_code != 200:
                logger.warning("Google News'e ulaşılamadı.")
                time.sleep(60)
                continue

            feed = feedparser.parse(r.content)
            
            for e in feed.entries[:15]:
                title = e.get("title", "")
                link = e.get("link", "")

                if title in state["seen"]:
                    continue

                result = classify(title)
                if result:
                    tag, score, quality, effect, bot_type = result
                    msg = format_msg(tag, score, quality, effect, bot_type, title, link)
                    
                    if send_telegram(msg):
                        logger.info(f"BAŞARILI: {title[:50]}...")
                        state["seen"][title] = int(time.time())
                        save_state(state)
                    time.sleep(2) # Telegram spam filtresine takılmamak için

            logger.info(f"Tarama bitti. {SCAN_EVERY_MINUTES} dk bekleniyor...")
            time.sleep(SCAN_EVERY_MINUTES * 60)

        except Exception as e:
            logger.error(f"Ana döngüde hata: {e}")
            time.sleep(30)

if __name__ == "__main__":
    run()
