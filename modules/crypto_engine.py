import aiohttp
import asyncio
import logging
from typing import List, Dict, Any, Optional
from config import config
from core.paper_portfolio import PaperPortfolio
from core.telegram_notifier import notifier

logger = logging.getLogger(__name__)

class CryptoEngine:
    def __init__(self, portfolio: PaperPortfolio):
        self.portfolio = portfolio
        self.symbols = config.CRYPTO_SYMBOLS
        self.base_url = "https://www.okx.com/api/v5/market/candles"
        self.last_prices: Dict[str, float] = {}

    async def fetch_candles(self, symbol: str, bar: str = "1m", limit: int = 25) -> List[Dict[str, float]]:
        params = {"instId": symbol, "bar": bar, "limit": str(limit)}
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.base_url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        rows = data.get("data", [])
                        # OKX returns newest first, reverse so oldest is first
                        candles = []
                        for r in reversed(rows):
                            candles.append({
                                "ts": int(r[0]),
                                "open": float(r[1]),
                                "high": float(r[2]),
                                "low": float(r[3]),
                                "close": float(r[4]),
                                "vol": float(r[5])
                            })
                        return candles
        except Exception as e:
            logger.warning(f"[Crypto] Error fetching candles for {symbol}: {e}")
        return []

    def calculate_indicators(self, candles: List[Dict[str, float]]) -> Optional[Dict[str, float]]:
        if len(candles) < 20:
            return None
        closes = [c["close"] for c in candles]
        
        # Simple moving average / EMA approx
        sma_fast = sum(closes[-7:]) / 7
        sma_slow = sum(closes[-20:]) / 20
        
        # Donchian channel (highest high & lowest low over last 15 candles except current)
        highest_prev = max(c["high"] for c in candles[-16:-1])
        lowest_prev = min(c["low"] for c in candles[-16:-1])
        
        current_close = closes[-1]
        self.last_prices[candles[-1]["ts"]] = current_close
        
        return {
            "close": current_close,
            "sma_fast": sma_fast,
            "sma_slow": sma_slow,
            "highest_prev": highest_prev,
            "lowest_prev": lowest_prev
        }

    async def check_open_positions(self, symbol: str, current_price: float):
        # Scan current open positions for this symbol
        to_close = []
        for trade_id, pos in list(self.portfolio.crypto_positions.items()):
            if pos["symbol"] != symbol:
                continue

            side = pos["side"]
            entry = pos["entry_price"]
            sl = pos["stop_loss"]
            tp = pos["take_profit"]

            if side == "BUY":
                if current_price <= sl:
                    to_close.append((trade_id, current_price, "STOP_LOSS (Защита капитала)"))
                elif current_price >= tp:
                    to_close.append((trade_id, current_price, "TAKE_PROFIT (Фиксация прибыли)"))
            elif side == "SELL":
                if current_price >= sl:
                    to_close.append((trade_id, current_price, "STOP_LOSS (Защита капитала)"))
                elif current_price <= tp:
                    to_close.append((trade_id, current_price, "TAKE_PROFIT (Фиксация прибыли)"))

        for trade_id, exit_px, reason in to_close:
            closed = self.portfolio.close_crypto_trade(trade_id, exit_px, reason)
            if closed:
                pnl = closed["pnl"]
                emoji = "🟢" if pnl >= 0 else "🔴"
                msg = (
                    f"{emoji} *[CRYPTO TRADE CLOSED]*\n"
                    f"🪙 *Пара:* `{closed['symbol']}` ({closed['side']})\n"
                    f"🚪 *Причина:* `{reason}`\n"
                    f"🎯 *Вход:* `${closed['entry']:.2f}` ➔ *Выход:* `${closed['exit']:.2f}`\n"
                    f"💵 *PnL:* `+{pnl:.2f}$` ({closed['pnl_pct']:+.2f}%)\n"
                    f"💰 *Новый баланс Crypto:* `${self.portfolio.crypto_balance:.2f}`"
                )
                await notifier.send_message(msg)
                logger.info(f"[Crypto] Closed {trade_id}: PnL = {pnl:.2f}")

    async def scan_and_trade(self):
        for symbol in self.symbols:
            candles = await self.fetch_candles(symbol)
            if not candles:
                continue

            ind = self.calculate_indicators(candles)
            if not ind:
                continue

            current_price = ind["close"]
            self.last_prices[symbol] = current_price

            # 1. Manage active trades first
            await self.check_open_positions(symbol, current_price)

            # 2. Check if we already have an open position in this symbol
            has_position = any(p["symbol"] == symbol for p in self.portfolio.crypto_positions.values())
            if has_position:
                continue

            # Check if we have free balance
            trade_size = 10.0  # $10 per trade (10% of $100)
            if self.portfolio.crypto_balance < trade_size:
                continue

            # 3. Strategy rules: Donchian Volatility Breakout + Trend confirmation
            signal = None
            stop_loss = None
            take_profit = None

            # Bullish Breakout: price breaks above 15-min high AND short SMA > long SMA
            if current_price > ind["highest_prev"] and ind["sma_fast"] > ind["sma_slow"]:
                signal = "BUY"
                stop_loss = round(current_price * 0.985, 2)   # -1.5% SL
                take_profit = round(current_price * 1.035, 2)  # +3.5% TP (R:R > 1:2)

            # Bearish Breakout: price breaks below 15-min low AND short SMA < long SMA
            elif current_price < ind["lowest_prev"] and ind["sma_fast"] < ind["sma_slow"]:
                signal = "SELL"
                stop_loss = round(current_price * 1.015, 2)   # +1.5% SL
                take_profit = round(current_price * 0.965, 2)  # -3.5% TP

            if signal:
                trade_id = self.portfolio.record_crypto_trade(
                    symbol=symbol,
                    side=signal,
                    price=current_price,
                    amount_usdc=trade_size,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    strategy="Donchian Volatility Breakout"
                )
                if trade_id:
                    side_emoji = "🚀 ЛОНГ" if signal == "BUY" else "🔻 ШОРТ"
                    alert = (
                        f"⚡ *[CRYPTO DEMO TRADE]*\n"
                        f"🪙 *Пара:* `{symbol}` ({side_emoji})\n"
                        f"📈 *Стратегия:* `Donchian Breakout + Trend`\n"
                        f"💵 *Цена входа:* `${current_price:.2f}`\n"
                        f"🛡 *Стоп-лосс (риск):* `${stop_loss:.2f}` (-1.5%)\n"
                        f"🎯 *Тейк-профит (цель):* `${take_profit:.2f}` (+3.5%)\n"
                        f"💰 *Размер позиции:* `${trade_size:.2f}` | Баланс: `${self.portfolio.crypto_balance:.2f}`"
                    )
                    await notifier.send_message(alert)
                    logger.info(f"[Crypto] Opened paper trade on {symbol} ({signal}) at {current_price}")
