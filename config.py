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

    SYMBOLS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT"]

config = Config()
