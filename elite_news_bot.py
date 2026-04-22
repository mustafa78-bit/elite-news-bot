import os
import re
import time
import json
import html
import logging
from typing import Dict, List, Tuple, Optional

import requests
import feedparser

# =========================================================
# AYARLAR
# =========================================================
TELEGRAM_BOT_TOKEN = "8735115726:AAHVB0gR_z-Qyzs-ot99ilbDmr_D9tmoIt4"
TELEGRAM_CHAT_ID = "1307136561"

STATE_FILE = "bot_v3_state.json"
SCAN_INTERVAL = 60
REQUEST_TIMEOUT = 15

BINANCE_API_URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"

# Sadece spot listing gücü taşıyan kalıplar
SPOT_REQUIRED = [
    "binance will list",
    "will list",
    "for spot trading",
    "spot trading",
    "trading opens",
    "opens trading",
    "new cryptocurrency listing",
    "binance lists",
    "to list",
]

# Kesin istemediğimiz şeyler
SPOT_FORBIDDEN = [
    "futures",
    "margin",
    "options",
    "perpetual",
    "usdⓈ-margined",
    "usdc perpetual",
    "loan",
    "copy",
    "staking",
    "staked",
    "earn",
    "launchpool",
    "launchpad",
    "megadrop",
    "airdrop",
    "delist",
    "delisting",
    "maintenance",
    "pre-market",
]

ELITE_NEWS_KEYWORDS = [
    "FED", "CPI", "FOMC", "POWELL", "SEC", "ETF",
    "APPROVE", "APPROVAL", "INFLATION", "PCE", "NFP"
]

NEWS_FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
]

MAX_STATE_ITEMS = 800
SEND_DELAY = 2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("ULTRA_ELITE_RADAR")


class UltraEliteBot:
    def __init__(self):
        self.state = self.load_state()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9"
        })

    # =====================================================
    # STATE
    # =====================================================
    def load_state(self) -> Dict[str, List[str]]:
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if not isinstance(data, dict):
                        raise ValueError("State dict değil")
                    data.setdefault("listings", [])
                    data.setdefault("news", [])
                    return data
            except Exception as e:
                logger.warning("State okunamadı, sıfırlanıyor: %s", e)
        return {"listings": [], "news": []}

    def save_state(self) -> None:
        try:
            self.state["listings"] = self.state["listings"][-MAX_STATE_ITEMS:]
            self.state["news"] = self.state["news"][-MAX_STATE_ITEMS:]
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("State kaydedilemedi: %s", e)

    # =====================================================
    # TELEGRAM
    # =====================================================
    def send_telegram(self, text: str) -> bool:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        try:
            r = self.session.post(url, data=payload, timeout=REQUEST_TIMEOUT)
            if r.status_code != 200:
                logger.error("TG Gönderim Hatası: %s", r.text)
                return False
            return True
        except Exception as e:
            logger.error("TG Bağlantı Hatası: %s", e)
            return False

    # =====================================================
    # YARDIMCILAR
    # =====================================================
    def safe(self, value: str) -> str:
        return html.escape((value or "").strip())

    def normalize(self, text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip()).lower()

    def extract_symbols(self, title: str) -> str:
        # Öncelik: (CHIP), (BTC), (ABC123)
        found = re.findall(r"\(([A-Z0-9]{2,15})\)", title or "")
        if found:
            return ", ".join(found[:5])

        # Alternatif: Binance Will List NAME (SYMBOL) tarzı yoksa büyük harf kümeleri dene
        caps = re.findall(r"\b[A-Z0-9]{2,10}\b", title or "")
        blacklist = {
            "BINANCE", "WILL", "LIST", "SPOT", "NEW", "FOR",
            "ETF", "SEC", "FED", "CPI", "FOMC", "USD"
        }
        caps = [x for x in caps if x not in blacklist]
        return ", ".join(caps[:3]) if caps else "-"

    def request_json_with_retry(self, url: str, method: str = "GET", json_payload: Optional[dict] = None) -> Optional[dict]:
        for attempt in range(3):
            try:
                if method == "POST":
                    r = self.session.post(url, json=json_payload, timeout=REQUEST_TIMEOUT)
                else:
                    r = self.session.get(url, timeout=REQUEST_TIMEOUT)

                r.raise_for_status()
                return r.json()
            except Exception as e:
                logger.warning("İstek hatası (%s/3): %s", attempt + 1, e)
                time.sleep(1.5 * (attempt + 1))
        return None

    # =====================================================
    # SKORLAMA
    # =====================================================
    def score_listing(self, title: str) -> Tuple[int, List[str], List[str]]:
        t = self.normalize(title)
        reasons = []
        reject_reasons = []

        score = 0

        for bad in SPOT_FORBIDDEN:
            if bad in t:
                reject_reasons.append(f"forbidden={bad}")

        if reject_reasons:
            return 0, reasons, reject_reasons

        if "binance will list" in t:
            score += 60
            reasons.append("binance will list")

        if "for spot trading" in t:
            score += 25
            reasons.append("for spot trading")

        if "spot trading" in t:
            score += 10
            reasons.append("spot trading")

        if "opens trading" in t or "trading opens" in t:
            score += 10
            reasons.append("trading opens")

        if "new cryptocurrency listing" in t:
            score += 10
            reasons.append("new cryptocurrency listing")

        if "seed tag applied" in t:
            score += 5
            reasons.append("seed tag")

        if "binance lists" in t or "will list" in t or "to list" in t:
            score += 10
            reasons.append("generic list phrase")

        # hiçbir pozitif kalıp yoksa reddet
        if not any(x in t for x in SPOT_REQUIRED):
            reject_reasons.append("spot_required_missing")

        return min(score, 99), reasons, reject_reasons

    def classify_news(self, title: str) -> Tuple[bool, int]:
        upper = (title or "").upper()

        score = 0
        for k in ELITE_NEWS_KEYWORDS:
            if k in upper:
                score += 20

        # Çok kısa / boş clickbait eleği
        if len((title or "").strip()) < 15:
            return False, 0

        # minimum anlamlı eşik
        return score >= 20, min(score, 95)

    # =====================================================
    # BİNANCE SPOT LISTING
    # =====================================================
    def check_binance_spot(self) -> None:
        payload = {
            "catalogId": 48,
            "pageNo": 1,
            "pageSize": 12
        }

        data = self.request_json_with_retry(
            BINANCE_API_URL,
            method="POST",
            json_payload=payload
        )

        if not data:
            logger.error("Binance API boş döndü.")
            return

        articles = (
            data.get("data", {})
                .get("catalogs", [{}])[0]
                .get("articles", [])
        )

        logger.info("Binance aday sayısı: %s", len(articles))

        for art in articles:
            try:
                title = (art.get("title") or "").strip()
                code = str(art.get("code") or "").strip()

                if not title or not code:
                    logger.info("Eksik article alanı, geçildi: %s", art)
                    continue

                link = f"https://www.binance.com/en/support/announcement/{code}"

                if link in self.state["listings"]:
                    continue

                score, reasons, reject_reasons = self.score_listing(title)

                if reject_reasons:
                    logger.info("REJECT LISTING: %s | %s", title, ", ".join(reject_reasons))
                    self.state["listings"].append(link)
                    continue

                if score < 70:
                    logger.info("ZAYIF LISTING: %s | skor=%s | neden=%s", title, score, ", ".join(reasons))
                    self.state["listings"].append(link)
                    continue

                symbols = self.extract_symbols(title)

                quality = "S+" if score >= 90 else "A"
                msg = (
                    "🚨 <b>ELITE SPOT LISTING</b>\n"
                    "━━━━━━━━━━━━━━━\n"
                    f"⭐ Kalite: <b>{quality}</b> | Skor: <b>{score}/100</b>\n"
                    f"🪙 Sembol: <b>{self.safe(symbols)}</b>\n"
                    f"💎 <b>{self.safe(title)}</b>\n"
                    "━━━━━━━━━━━━━━━\n"
                    f"🔗 <a href='{self.safe(link)}'>Duyuruyu Aç</a>"
                )

                if self.send_telegram(msg):
                    self.state["listings"].append(link)
                    logger.info("YENİ SPOT: %s | skor=%s | neden=%s", title, score, ", ".join(reasons))
                    time.sleep(SEND_DELAY)

            except Exception as e:
                logger.error("Binance article işlenemedi: %s", e)

    # =====================================================
    # ELITE HABER RADARI
    # =====================================================
    def check_elite_news(self) -> None:
        for source_name, url in NEWS_FEEDS:
            try:
                feed = feedparser.parse(url)
                entries = getattr(feed, "entries", [])[:8]
                logger.info("%s haber adayı: %s", source_name, len(entries))

                for entry in entries:
                    link = getattr(entry, "link", "").strip()
                    title = getattr(entry, "title", "").strip()

                    if not link or not title:
                        continue

                    if link in self.state["news"]:
                        continue

                    ok, score = self.classify_news(title)

                    if not ok:
                        logger.info("REJECT NEWS: %s | kaynak=%s", title, source_name)
                        self.state["news"].append(link)
                        continue

                    msg = (
                        "⚠️ <b>KRİTİK MAKRO VERİ</b>\n"
                        "━━━━━━━━━━━━━━━\n"
                        f"⭐ Skor: <b>{score}/100</b>\n"
                        f"🏛 Kaynak: <b>{self.safe(source_name)}</b>\n"
                        f"📰 <b>{self.safe(title)}</b>\n"
                        "━━━━━━━━━━━━━━━\n"
                        f"🔗 <a href='{self.safe(link)}'>Detay</a>"
                    )

                    if self.send_telegram(msg):
                        self.state["news"].append(link)
                        logger.info("NEWS ALERT: %s | kaynak=%s | skor=%s", title, source_name, score)
                        time.sleep(SEND_DELAY)

            except Exception as e:
                logger.error("Haber Hatası (%s): %s", source_name, e)

    # =====================================================
    # ANA DÖNGÜ
    # =====================================================
    def run(self) -> None:
        logger.info("Ultra Elite Sistem Aktif...")
        while True:
            try:
                # Öncelik listing tarafında
                self.check_binance_spot()
                self.check_elite_news()
                self.save_state()
                logger.info("Tur tamamlandı, bekleniyor...")
            except Exception as e:
                logger.error("Ana döngü hatası: %s", e)

            time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    bot = UltraEliteBot()
    bot.run()
