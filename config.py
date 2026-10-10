import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Config:
    # Telegram settings
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Mode
    PAPER_TRADING: bool = True  # Always True for safe testing

    # Starting budgets for simulation ($100 each ~= 10,000 RUB total)
    POLYMARKET_STARTING_BALANCE: float = float(os.getenv("POLYMARKET_BALANCE", "100.0"))
    CRYPTO_STARTING_BALANCE: float = float(os.getenv("CRYPTO_BALANCE", "100.0"))

    # Scanning intervals (in seconds)
    POLYMARKET_INTERVAL: int = int(os.getenv("POLYMARKET_INTERVAL", "15"))
    CRYPTO_INTERVAL: int = int(os.getenv("CRYPTO_INTERVAL", "10"))
    HEARTBEAT_INTERVAL: int = int(os.getenv("HEARTBEAT_INTERVAL", "300"))  # Digest every 5 min

    # Risk management
    MAX_RISK_PER_CRYPTO_TRADE: float = 1.50   # 1.5% of $100
    MAX_BET_PER_POLYMARKET_TRADE: float = 5.00  # $5 per market (5% of $100)

    # Monitored crypto pairs
    CRYPTO_SYMBOLS = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]

config = Config()
