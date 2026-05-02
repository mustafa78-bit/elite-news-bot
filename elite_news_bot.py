import re
import time
import hashlib
import sqlite3
import logging
import requests
import feedparser
from bs4 import BeautifulSoup

# =====================
# TELEGRAM
# =====================
TELEGRAM_TOKEN = "8735115726:AAHVB0gR_z-Qyzs-ot99ilbDmr_D9tmoIt4"
CHAT_ID = "1307136561"

# =====================
# CONFIG
# =====================
SCAN_INTERVAL = 120
MIN_SCORE = 35
DB_FILE = "listing_news_radar.db"
LOG_FILE = "listing_news_radar.log"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "en-US,en;q=0.9",
}

RSS_SOURCES = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "The Block": "https://www.theblock.co/rss.xml",
    "Blockworks": "https://blockworks.co/feed",
    "Decrypt": "https://decrypt.co/feed",
    "CryptoSlate": "https://cryptoslate.com/feed/",
    "a16z Crypto": "https://a16zcrypto.com/feed/",
    "Paradigm": "https://www.paradigm.xyz/feed.xml",
    "Multicoin": "https://multicoin.capital/feed/",
}

PATTERNS = {
    "OFFICIAL_LISTING": re.compile(r"(will list|to list|new listing|spot trading|trading starts|listed on|adds support for|initial listing)", re.I),
    "LISTING": re.compile(r"(listing|listed|goes live|available on|launches trading|starts trading|exchange listing)", re.I),
    "FUNDING": re.compile(r"(raises|raised|funding|seed round|series a|strategic round|investment)", re.I),
    "VC": re.compile(r"(a16z|paradigm|multicoin|pantera|dragonfly|polychain|coinbase ventures|binance labs)", re.I),
    "LAUNCH": re.compile(r"(mainnet|launch|launched|token generation event|tge)", re.I),
    "AIRDROP": re.compile(r"(airdrop|claim|token claim)", re.I),
    "HACK": re.compile(r"(hack|hacked|exploit|exploited|drained|attack|breach)", re.I),
    "ETF_MACRO": re.compile(r"(etf|sec|fed|rate cut|inflation|cpi|pce)", re.I),
    "PARTNERSHIP": re.compile(r"(partner|partnership|integrates|collaboration)", re.I),
}

SCORES = {
    "OFFICIAL_LISTING": 45,
    "LISTING": 35,
    "FUNDING": 25,
    "VC": 25,
    "LAUNCH": 22,
    "AIRDROP": 18,
    "HACK": 30,
    "ETF_MACRO": 18,
    "PARTNERSHIP": 14,
}

COINS = [
    "BTC","ETH","SOL","BNB","XRP","ADA","AVAX","LINK","TON","SUI","APT","ARB","OP",
    "INJ","SEI","TIA","PYTH","JUP","KAS","FET","RNDR","NEAR","STRK","TAO","ZEC",
    "MOVE","CKB","ENA","ONDO","PENDLE","WLD","MANTA","JTO","AAVE","EIGEN","CHIP",
    "GRASS","RLUSD","HYPE","DOGE","PEPE","WIF"
]

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger("").addHandler(console)


def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen (
            id TEXT PRIMARY KEY,
            created_at INTEGER
        )
    """)
    conn.commit()
    conn.close()


def is_seen(item_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.execute("SELECT 1 FROM seen WHERE id=?", (item_id,))
    result = cur.fetchone()
    conn.close()
    return result is not None


def mark_seen(item_id):
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "INSERT OR IGNORE INTO seen VALUES (?, ?)",
        (item_id, int(time.time()))
    )
    conn.commit()
    conn.close()


def clean_text(x):
    return BeautifulSoup(x or "", "html.parser").get_text(" ", strip=True)


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": text[:3900],
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        r = requests.post(url, json=payload, timeout=12)
        logging.info(f"Telegram status={r.status_code} text={r.text[:120]}")
    except Exception as e:
        logging.error(f"Telegram error: {e}")


def calc_score(title, summary, source):
    text = f"{title} {summary}"
    score = 0
    tags = []

    if source in ["OKX_OFFICIAL", "BYBIT_OFFICIAL"]:
        score += 25

    for tag, regex in PATTERNS.items():
        if regex.search(text):
            score += SCORES.get(tag, 0)
            tags.append(tag)

    coins = []
    upper = text.upper()

    for coin in COINS:
        if re.search(rf"(?<![A-Z0-9]){re.escape(coin)}(?![A-Z0-9])", upper):
            coins.append(coin)

    return score, tags, coins


def handle_item(source, title, link, summary=""):
    title = clean_text(title)
    summary = clean_text(summary)
    link = link or ""

    if not title:
        return False

    item_id = hashlib.md5(f"{source}|{title}|{link}".encode()).hexdigest()

    if is_seen(item_id):
        return False

    score, tags, coins = calc_score(title, summary, source)

    logging.info(f"{source} | score={score} | tags={tags} | title={title[:100]}")

    if score >= MIN_SCORE:
        emoji = "🚨" if "OFFICIAL_LISTING" in tags or "LISTING" in tags else "🧠"

        msg = f"<b>{emoji} ELITE LISTING / NEWS RADAR</b>\n\n"
        msg += f"<b>Kaynak:</b> {source}\n"
        msg += f"<b>Score:</b> {score}/100\n"
        msg += f"<b>Başlık:</b> {title}\n"

        if tags:
            msg += f"<b>Etiket:</b> {', '.join(tags)}\n"

        if coins:
            msg += f"<b>Coin:</b> {', '.join(coins)}\n"

        if summary:
            msg += f"\n{summary[:500]}\n"

        if link:
            msg += f"\n<a href='{link}'>Detay</a>"

        send_telegram(msg)

    mark_seen(item_id)
    return True


def scan_rss():
    total = 0

    for source, url in RSS_SOURCES.items():
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            feed = feedparser.parse(r.content)
            entries = feed.entries or []

            logging.info(f"RSS {source} status={r.status_code} entries={len(entries)}")

            for entry in entries[:20]:
                title = entry.get("title", "")
                link = entry.get("link", "")
                summary = entry.get("summary", "")
                if handle_item(source, title, link, summary):
                    total += 1

        except Exception as e:
            logging.error(f"RSS error {source}: {e}")

    return total


def scan_okx_official():
    url = "https://www.okx.com/help/section/announcements-new-listings"
    total = 0

    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        logging.info(f"OKX official status={r.status_code} len={len(r.text)}")

        if r.status_code != 200:
            return 0

        soup = BeautifulSoup(r.text, "html.parser")
        links = soup.find_all("a", href=True)

        for a in links:
            title = a.get_text(" ", strip=True)
            href = a.get("href", "")

            if not title or len(title) < 8:
                continue

            if not re.search(r"(list|listing|trading|launch|new|listed)", title, re.I):
                continue

            full_link = href if href.startswith("http") else "https://www.okx.com" + href

            if handle_item("OKX_OFFICIAL", title, full_link, ""):
                total += 1

        logging.info(f"OKX candidates={total}")

    except Exception as e:
        logging.error(f"OKX official error: {e}")

    return total


def scan_bybit_official():
    url = "https://api.bybit.com/v5/announcements/index?locale=en-US&limit=10"
    total = 0

    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        logging.info(f"Bybit official status={r.status_code} text={r.text[:100]}")

        if r.status_code != 200:
            return 0

        data = r.json()
        items = data.get("result", {}).get("list", []) or []

        for item in items:
            title = item.get("title", "")
            link = item.get("url", "")
            desc = item.get("description", "")

            if handle_item("BYBIT_OFFICIAL", title, link, desc):
                total += 1

    except Exception as e:
        logging.error(f"Bybit official error: {e}")

    return total


def main():
    init_db()

    logging.info("=== ELITE LISTING NEWS RADAR BASLADI ===")
    send_telegram("✅ ELITE LISTING NEWS RADAR başladı.\nRSS + OKX official + Bybit official deneniyor.")

    while True:
        logging.info("Tarama basliyor...")

        rss_count = scan_rss()
        okx_count = scan_okx_official()
        bybit_count = scan_bybit_official()

        logging.info(
            f"Tarama bitti | rss_seen={rss_count} | okx_seen={okx_count} | bybit_seen={bybit_count} | {SCAN_INTERVAL} sn bekleniyor..."
        )

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
