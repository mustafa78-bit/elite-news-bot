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

# =========================
# CONFIG
# =========================
TELEGRAM_TOKEN = "8735115726:AAHVB0gR_z-Qyzs-ot99ilbDmr_D9tmoIt4"
CHAT_ID = "1307136561"

SCAN_INTERVAL = 120
MIN_SCORE = 75
TELEGRAM_DELAY = 1.2
MAX_WORKERS = 8

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
HEADERS = {"User-Agent": "Mozilla/5.0"}
SCRAPER = cloudscraper.create_scraper()

# =========================
# SOURCES
# =========================
RSS_SOURCES = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "The Block": "https://www.theblock.co/rss.xml",
}

# =========================
# DB
# =========================
def db():
    return sqlite3.connect(DB_FILE)

def init_db():
    conn = db()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

def seen_before(i):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM seen WHERE id=?", (i,))
    r = cur.fetchone()
    conn.close()
    return r

def mark_seen(i):
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO seen VALUES (?)", (i,))
    conn.commit()
    conn.close()

# =========================
# PARSE
# =========================
def parse_rss(source, url):
    try:
        r = SCRAPER.get(url, timeout=10)
        feed = feedparser.parse(r.content)

        out = []
        for e in feed.entries[:10]:
            title = BeautifulSoup(e.title, "html.parser").text
            link = e.link
            out.append((source, title, link))

        return out
    except:
        return []

# =========================
# TELEGRAM
# =========================
def send(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg}
        )
    except:
        pass

# =========================
# MAIN
# =========================
def main():
    init_db()

    while True:
        for source, url in RSS_SOURCES.items():
            items = parse_rss(source, url)

            for s, title, link in items:
                iid = hashlib.md5((title+link).encode()).hexdigest()

                if seen_before(iid):
                    continue

                mark_seen(iid)

                msg = f"🚨 {s}\n\n{title}\n\n{link}"
                send(msg)

        time.sleep(SCAN_INTERVAL)

# =========================
# START
# =========================
if __name__ == "__main__":
    main()
