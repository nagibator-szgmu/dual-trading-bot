import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Config:
    # Telegram settings
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "8652102736:AAGaWF4dTArUqDZPSPYQppRwlYOHVSsrajA")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "5579630526")

    # Mode
    PAPER_TRADING: bool = True  # Always True for safe simulation

    # Starting budget for simulation ($100 ~= 10,000 RUB)
    STARTING_BALANCE: float = float(os.getenv("CRYPTO_BALANCE", "100.0"))

    # Scanning intervals
    SCAN_INTERVAL: int = int(os.getenv("SCAN_INTERVAL", "15"))  # Scan every 15s
    HEARTBEAT_INTERVAL: int = int(os.getenv("HEARTBEAT_INTERVAL", "300"))  # Digest every 5 min

    # Timeframe: 15m filters out 90% of noise while providing 2-4 quality signals a day
    TIMEFRAME: str = "15m"

    # Risk management
    RISK_PER_TRADE_USD: float = 1.50   # 1.5% max risk on $100 bankroll
    POSITION_SIZE_USD: float = 15.00   # $15 position size per trade
    STOP_LOSS_PCT: float = 0.015       # -1.5% Stop Loss
    TAKE_PROFIT_PCT: float = 0.035     # +3.5% Take Profit (Reward-to-Risk > 1:2.3)
    MAX_OPEN_POSITIONS: int = 3        # Max 3 concurrent positions (BTC, ETH, SOL)

    # Monitored liquid pairs
    SYMBOLS = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]

config = Config()
