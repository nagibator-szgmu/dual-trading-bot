import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Config:
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "8652102736:AAGaWF4dTArUqDZPSPYQppRwlYOHVSsrajA")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "5579630526")

    # Tournament Budgets: $100 for each independent strategy
    TREND_BUDGET: float = 100.0
    GRID_BUDGET: float = 100.0
    REVERSION_BUDGET: float = 100.0

    SCAN_INTERVAL: int = 15  # Seconds between scan loops
    HEARTBEAT_INTERVAL: int = 300  # Tournament digest every 5 minutes

    # CS2 Skin Sniper Settings
    CS2_BOT_TOKEN: str = os.getenv("CS2_BOT_TOKEN", "8996410296:AAHNNPjvezKySwYd69C0NFFEr7pUE2BMIS0")
    CS2_BUDGET: float = 200.0
    CS2_SCAN_INTERVAL: int = 60  # seconds between Skinport market catalog checks
    CS2_MIN_DISCOUNT_PCT: float = 20.0  # minimum 20% discount below fair suggested price
    CS2_MIN_PROFIT_PCT: float = 14.0    # minimum 14% net profit after fees
    CS2_MIN_ITEM_PRICE: float = 4.0     # skip 5-cent graffiti
    CS2_MAX_ITEM_PRICE: float = 45.0    # keep diversified portfolio of items

config = Config()
