import re, time, hashlib, sqlite3, logging
from datetime import datetime
import requests, feedparser
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = "8735115726:AAHVB0gR_z-Qyzs-ot99ilbDmr_D9tmoIt4"
CHAT_ID = "1307136561"

SCAN_INTERVAL = 120
MIN_SCORE = 60
OKX_MAX_DAYS = 3

DB_FILE = "elite_listing_news_pro.db"
LOG_FILE = "elite_listing_news_pro.log"

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
    "Pantera": "https://panteracapital.com/feed/",
    "Dragonfly": "https://www.dragonfly.xyz/feed",
}

BLACKLIST = {
    "THE","AND","FOR","WITH","FROM","THIS","THAT","WILL","HAVE","ARE","HAS",
    "USD","USDT","USDC","CEO","SEC","ETF","API","DAO","NEWS","CRYPTO","TOKEN",
    "MARKET","PRICE","GLOBAL","WORLD","FED","CFTC","NASDAQ","IPO","AUM"
}

FAKE_NEWS = re.compile(r"(nasdaq listing|ipo|go public|public listing|stock listing|annual filing)", re.I)

PATTERNS = {
    "OFFICIAL_LISTING": re.compile(r"(will list|to list|new listing|spot trading|trading starts|initial listing)", re.I),
    "FUNDING": re.compile(r"(raises|raised|funding|seed round|series a|strategic round|investment|fundraise)", re.I),
    "VC": re.compile(r"(ventures|capital|labs|fund|partners|a16z|paradigm|multicoin|pantera|dragonfly|polychain|coinbase ventures|binance labs)", re.I),
    "LAUNCH": re.compile(r"(mainnet|launch|launched|tge|token generation event)", re.I),
    "AIRDROP": re.compile(r"(airdrop|claim|token claim)", re.I),
    "HACK": re.compile(r"(hack|hacked|exploit|exploited|drained|attack|breach)", re.I),
    "PARTNERSHIP": re.compile(r"(partner|partnership|integrates|collaboration)", re.I),
}

SCORES = {
    "OFFICIAL_LISTING": 80,
    "FUNDING": 35,
    "VC": 30,
    "LAUNCH": 25,
    "AIRDROP": 20,
    "HACK": 35,
    "PARTNERSHIP": 18,
}

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
    conn.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, created_at INTEGER)")
    conn.commit()
    conn.close()

def is_seen(x):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.execute("SELECT 1 FROM seen WHERE id=?", (x,))
    ok = cur.fetchone() is not None
    conn.close()
    return ok

def mark_seen(x):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT OR IGNORE INTO seen VALUES (?,?)", (x, int(time.time())))
    conn.commit()
    conn.close()

def clean(x):
    return BeautifulSoup(x or "", "html.parser").get_text(" ", strip=True)

def send_tg(text):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": text[:3900],
                "parse_mode": "HTML",
                "disable_web_page_preview": False
            },
            timeout=12
        )
        logging.info(f"Telegram {r.status_code}")
    except Exception as e:
        logging.error(f"Telegram error: {e}")

def extract_symbols(text):
    upper = text.upper()
    candidates = re.findall(r"\b[A-Z][A-Z0-9]{1,9}\b", upper)
    out = []
    for c in candidates:
        if c in BLACKLIST:
            continue
        if len(c) < 3 or len(c) > 8:
            continue
        if c.isdigit():
            continue
        out.append(c)
    return sorted(set(out))[:8]

def score_item(title, summary, source):
    text = f"{title} {summary}"
    score, tags = 0, []

    if source == "OKX_OFFICIAL":
        score += 30

    for tag, rgx in PATTERNS.items():
        if rgx.search(text):
            score += SCORES[tag]
            tags.append(tag)

    return score, tags, extract_symbols(text)

def okx_recent(title):
    m = re.search(r"Published on ([A-Z][a-z]{2} \d{1,2}, \d{4})", title)
    if not m:
        return False
    try:
        dt = datetime.strptime(m.group(1), "%b %d, %Y")
        return (datetime.utcnow() - dt).days <= OKX_MAX_DAYS
    except Exception:
        return False

def handle_item(source, title, link, summary=""):
    title, summary = clean(title), clean(summary)
    if not title:
        return False

    if FAKE_NEWS.search(title):
        return False

    item_id = hashlib.md5(f"{source}|{title}|{link}".encode()).hexdigest()
    if is_seen(item_id):
        return False

    score, tags, coins = score_item(title, summary, source)
    logging.info(f"{source} | score={score} | tags={tags} | title={title[:100]}")

    should_send = score >= MIN_SCORE

    if source == "OKX_OFFICIAL":
        should_send = "OFFICIAL_LISTING" in tags and okx_recent(title)

    if should_send:
        icon = "🚨" if "OFFICIAL_LISTING" in tags else "🧠"
        msg = f"<b>{icon} ELITE LISTING / NEWS PRO</b>\n\n"
        msg += f"<b>Kaynak:</b> {source}\n"
        msg += f"<b>Score:</b> {score}/100\n"
        msg += f"<b>Başlık:</b> {title}\n"

        if tags:
            msg += f"<b>Etiket:</b> {', '.join(tags)}\n"
        if coins:
            msg += f"<b>Yakalanan sembol:</b> {', '.join(coins)}\n"
        if summary:
            msg += f"\n{summary[:500]}\n"
        if link:
            msg += f"\n<a href='{link}'>Detay</a>"

        send_tg(msg)

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

            for e in entries[:20]:
                if handle_item(source, e.get("title",""), e.get("link",""), e.get("summary","")):
                    total += 1
        except Exception as e:
            logging.error(f"RSS error {source}: {e}")
    return total

def scan_okx():
    url = "https://www.okx.com/help/section/announcements-new-listings"
    total = 0

    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        logging.info(f"OKX status={r.status_code} len={len(r.text)}")

        if r.status_code != 200:
            return 0

        soup = BeautifulSoup(r.text, "html.parser")

        for a in soup.find_all("a", href=True):
            title = a.get_text(" ", strip=True)
            href = a["href"]

            if not title or len(title) < 15:
                continue
            if "application" in title.lower() or "delist" in title.lower():
                continue
            if not re.search(r"(will launch|will list|to list).*(spot trading|trading)", title, re.I):
                continue
            if not okx_recent(title):
                continue

            full = href if href.startswith("http") else "https://www.okx.com" + href

            if handle_item("OKX_OFFICIAL", title, full, ""):
                total += 1

    except Exception as e:
        logging.error(f"OKX error: {e}")

    return total

def main():
    init_db()
    logging.info("=== ELITE LISTING NEWS PRO BASLADI ===")
    send_tg("✅ ELITE LISTING NEWS PRO başladı.\nOKX official + RSS + sınırsız VC/funding haber takibi aktif.")

    while True:
        logging.info("Tarama basliyor...")
        rss = scan_rss()
        okx = scan_okx()
        logging.info(f"Tarama bitti | rss={rss} | okx={okx} | {SCAN_INTERVAL} sn bekleniyor...")
        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()
