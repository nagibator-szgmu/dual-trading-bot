import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Config:
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "8951644743:AAH2X1JfkNl8HTnJPZdIc8yls_SiA-U7UW4")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "5579630526")

    # Tournament Budgets: $100 for each independent strategy
    TREND_BUDGET: float = 100.0
    GRID_BUDGET: float = 100.0
    REVERSION_BUDGET: float = 100.0

    SYMBOLS: list = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT")
    TIMEFRAME: str = "15m"

    SCAN_INTERVAL: int = 15  # Seconds between scan loops
    HEARTBEAT_INTERVAL: int = 300  # Tournament digest every 5 minutes

    # CS2 Skin Sniper Settings
    CS2_BOT_TOKEN: str = os.getenv("CS2_BOT_TOKEN", "8667647842:AAF0Ui-aolcAy6XCwo7fnyqwQsvafPF6DNo")
    CS2_BUDGET: float = 200.0
    CS2_SCAN_INTERVAL: int = 300  # 5 minutes, matching Skinport CDN cache and avoiding 429 rate limit
    CS2_MIN_DISCOUNT_PCT: float = 15.0  # minimum 15% discount below real average sales price
    CS2_MIN_PROFIT_PCT: float = 8.0     # minimum 8% net profit after real 8% fees
    CS2_MIN_ITEM_PRICE: float = 4.0     # skip 5-cent graffiti
    CS2_MAX_ITEM_PRICE: float = 45.0    # keep diversified portfolio of items
    CS2_MIN_SALES_LAST_30D: int = 10    # strict liquidity filter: at least 10 real sales in last 30 days
    CS2_FEE_PCT: float = 0.08           # Skinport real standard seller fee (8%)

config = Config()
