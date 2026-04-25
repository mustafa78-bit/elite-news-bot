import os
import re
import time
import html
import queue
import hashlib
import sqlite3
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests
import feedparser
import cloudscraper
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# =========================
# CONFIG
# =========================
TELEGRAM_TOKEN = "8735115726:AAHVB0gR_z-Qyzs-ot99ilbDmr_D9tmoIt4"
CHAT_ID = "1307136561"

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "120"))
MIN_SCORE = int(os.getenv("MIN_SCORE", "75"))
TELEGRAM_DELAY = float(os.getenv("TELEGRAM_DELAY", "1.2"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "8"))

DB_FILE = "elite_radar_v5.db"
LOG_FILE = "elite_radar_v5.log"
MAX_MSG_LEN = 3500

send_queue = queue.Queue()
db_lock = threading.Lock()

# =========================
# LOGGING
# =========================
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger("").addHandler(console)

SCRAPER = cloudscraper.create_scraper()

# =========================
# SOURCES & PATTERNS (Senin Ayarların)
# =========================
RSS_SOURCES = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "The Block": "https://www.theblock.co/rss.xml",
    "Blockworks": "https://blockworks.co/feed",
    "Decrypt": "https://decrypt.co/feed",
    "CryptoSlate": "https://cryptoslate.com/feed/",
    "a16z Crypto": "https://a16zcrypto.com/feed/",
    "Paradigm": "https://www.paradigm.xyz/feed.xml",
    "Pantera": "https://panteracapital.com/feed/",
    "Multicoin": "https://multicoin.capital/feed/",
    "Dragonfly": "https://www.dragonfly.xyz/feed",
}

STATIC_SYMBOLS = {"BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "LINK", "TON", "SUI", "APT", "ARB", "OP", "INJ", "SEI", "TIA", "PYTH", "JUP", "KAS", "FET", "RNDR", "NEAR", "STRK", "TAO", "ZEC", "MOVE", "ASTER", "CKB", "ENA", "ONDO", "PENDLE", "WLD", "MANTA", "JTO", "LDO", "MKR", "AAVE", "EIGEN", "JASMY"}
PATTERNS = {
    "Listing": re.compile(r"\b(list|lists|listed|listing|will list|spot trading|trading starts)\b", re.I),
    "Funding": re.compile(r"\b(raise|raises|raised|funding|seed round|series a|strategic round|investment)\b", re.I),
    "VC Backing": re.compile(r"\b(a16z|paradigm|multicoin|pantera|dragonfly|polychain|binance labs|coinbase ventures|framework|electric capital)\b", re.I),
    "Launch/TGE": re.compile(r"\b(launch|launched|mainnet|airdrop|tge|token generation event)\b", re.I),
    "ETF/Macro": re.compile(r"\b(etf|sec|approval|approved|fed|rate cut|inflation|cpi|pce)\b", re.I),
    "Hack/Exploit": re.compile(r"\b(hack|hacked|exploit|exploited|drained|attack|breach)\b", re.I),
    "Partnership": re.compile(r"\b(partnership|partners with|integrates with|collaboration)\b", re.I),
}
PATTERN_SCORE = {"Listing": 28, "Funding": 22, "VC Backing": 22, "Launch/TGE": 18, "ETF/Macro": 14, "Hack/Exploit": 24, "Partnership": 12}
SOURCE_BONUS = {"a16z Crypto": 22, "Paradigm": 22, "Pantera": 18, "Multicoin": 20, "Dragonfly": 18, "CoinDesk": 8, "The Block": 8, "Blockworks": 7, "Cointelegraph": 6}

# =========================
# CORE FUNCTIONS
# =========================

def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, created_at INTEGER)")
        conn.commit()
        conn.close()

def is_seen(news_id):
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM seen WHERE id = ?", (news_id,))
        res = cur.fetchone()
        conn.close()
        return res is not None

def mark_as_seen(news_id):
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO seen (id, created_at) VALUES (?, ?)", (news_id, int(time.time())))
        conn.commit()
        conn.close()

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
        time.sleep(TELEGRAM_DELAY)
    except Exception as e:
        logging.error(f"Telegram error: {e}")

def telegram_worker():
    while True:
        msg = send_queue.get()
        if msg is None: break
        send_telegram_msg(msg)
        send_queue.task_done()

def process_feed(name, url):
    try:
        resp = SCRAPER.get(url, timeout=15)
        feed = feedparser.parse(resp.content)
        for entry in feed.entries:
            link = entry.link
            news_id = hashlib.md5(link.encode()).hexdigest()
            
            if is_seen(news_id): continue
            
            title = entry.title
            summary = entry.get("summary", "")
            content = f"{title} {summary}".upper()
            
            score = SOURCE_BONUS.get(name, 0)
            matches = []
            
            for p_name, p_regex in PATTERNS.items():
                if p_regex.search(content):
                    score += PATTERN_SCORE.get(p_name, 0)
                    matches.append(p_name)
            
            found_coins = [coin for coin in STATIC_SYMBOLS if f" {coin} " in f" {content} "]
            
            if score >= MIN_SCORE:
                msg = f"<b>🚀 {name} | Score: {score}</b>\n\n"
                msg += f"📌 {title}\n\n"
                msg += f"🏷 Tags: {', '.join(matches)}\n"
                if found_coins: msg += f"💰 Coins: {', '.join(found_coins)}\n"
                msg += f"\n🔗 <a href='{link}'>Haber Detayi</a>"
                
                send_queue.put(msg)
            
            mark_as_seen(news_id)
    except Exception as e:
        logging.error(f"Error processing {name}: {e}")

def main():
    init_db()
    
    # Mesaj gönderici thread başlat
    threading.Thread(target=telegram_worker, daemon=True).start()
    
    logging.info("--- ELITE RADAR V5 BASLATILDI ---")
    
    while True:
        logging.info("Tarama basliyor...")
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(process_feed, name, url) for name, url in RSS_SOURCES.items()]
            for future in as_completed(futures):
                future.result()
        
        logging.info(f"Tarama bitti. {SCAN_INTERVAL} saniye bekleniyor...")
        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()
