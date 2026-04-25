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

# =========================
# HTTP
# =========================
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, text/html,*/*",
    "Accept-Language": "en-US,en;q=0.9,tr;q=0.8",
}

SCRAPER = cloudscraper.create_scraper()

# =========================
# SOURCES
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

STATIC_SYMBOLS = {
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "LINK",
    "TON", "SUI", "APT", "ARB", "OP", "INJ", "SEI", "TIA", "PYTH",
    "JUP", "KAS", "FET", "RNDR", "NEAR", "STRK", "TAO", "ZEC",
    "MOVE", "ASTER", "CKB", "ENA", "ONDO", "PENDLE", "WLD",
    "MANTA", "JTO", "LDO", "MKR", "AAVE", "EIGEN", "JASMY"
}

VALID_SYMBOLS = set(STATIC_SYMBOLS)
COIN_REGEX = re.compile(r"\$?\b[A-Z]{2,12}\b")

PATTERNS = {
    "Listing": re.compile(r"\b(list|lists|listed|listing|will list|spot trading|trading starts)\b", re.I),
    "Funding": re.compile(r"\b(raise|raises|raised|funding|seed round|series a|strategic round|investment)\b", re.I),
    "VC Backing": re.compile(r"\b(a16z|paradigm|multicoin|pantera|dragonfly|polychain|binance labs|coinbase ventures|framework|electric capital)\b", re.I),
    "Launch/TGE": re.compile(r"\b(launch|launched|mainnet|airdrop|tge|token generation event)\b", re.I),
    "ETF/Macro": re.compile(r"\b(etf|sec|approval|approved|fed|rate cut|inflation|cpi|pce)\b", re.I),
    "Hack/Exploit": re.compile(r"\b(hack|hacked|exploit|exploited|drained|attack|breach)\b", re.I),
    "Partnership": re.compile(r"\b(partnership|partners with|integrates with|collaboration)\b", re.I),
}

BAD_PATTERNS = {
    "Sponsored": re.compile(r"\b(sponsored|advertisement|partner content)\b", re.I),
    "Low Quality": re.compile(r"\b(giveaway|quiz|learn and earn|ama|maintenance|price analysis|opinion)\b", re.I),
}

PATTERN_SCORE = {
    "Listing": 28,
    "Funding": 22,
    "VC Backing": 22,
    "Launch/TGE": 18,
    "ETF/Macro": 14,
    "Hack/Exploit": 24,
    "Partnership": 12,
}

SOURCE_BONUS = {
    "a16z Crypto": 22,
    "Paradigm": 22,
    "Pantera": 18,
    "Multicoin": 20,
    "Dragonfly": 18,
    "CoinDesk": 8,
    "The Block": 8,
    "Blockworks": 7,
    "Cointelegraph": 6,
    "Binance": 40,
    "OKX": 32,
    "Bybit": 30,
}

BAD_PENALTY = {
    "Sponsored": -45,
    "Low Quality": -30,
}

# =========================
# DB
# =========================
def db():
    return sqlite3.connect(DB_FILE, timeout=20, check_same_thread=False)

def init_db():
    with db_lock:
        conn = db()
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS seen (
                id TEXT PRIMARY KEY,
                source TEXT,
                created_at INTEGER
            )
        """)

        cur.execute("CREATE INDEX IF NOT EXISTS idx_seen_created_at ON seen(created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_seen_source ON seen(source)")

        conn.commit()
        conn.close()

# =========================
# (DEVAM SENİN KODUN AYNI)
# =========================

# =========================
# START
# =========================
if __name__ == "__main__":
    main()
