import aiohttp
import asyncio
import logging
import html
from typing import List, Dict, Any, Optional
from config import config
from core.paper_portfolio import CryptoPortfolio
from core.telegram_notifier import notifier

logger = logging.getLogger(__name__)

class CryptoEngine:
    def __init__(self, portfolio: CryptoPortfolio):
        self.portfolio = portfolio
        self.symbols = config.SYMBOLS
        self.base_url = "https://www.okx.com/api/v5/market/candles"
        self.last_prices: Dict[str, float] = {}

    async def fetch_candles(self, symbol: str, bar: str = config.TIMEFRAME, limit: int = 40) -> List[Dict[str, float]]:
        params = {"instId": symbol, "bar": bar, "limit": str(limit)}
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.base_url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        rows = data.get("data", [])
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

    def calculate_indicators(self, symbol: str, candles: List[Dict[str, float]]) -> Optional[Dict[str, float]]:
        if len(candles) < 25:
            return None
            
        current_tick = candles[-1]["close"]
        self.last_prices[symbol] = current_tick

        # Only evaluate signals on CONFIRMED completed bars (excluding bar [-1] which is forming)
        closed_bars = candles[:-1]
        closes = [c["close"] for c in closed_bars]
        
        # Exponential Moving Averages approximation
        ema_fast = sum(closes[-10:]) / 10
        ema_slow = sum(closes[-25:]) / 25
        
        # Donchian channel: 20 completed bars before the last closed bar
        highest_prev = max(c["high"] for c in closed_bars[-21:-1])
        lowest_prev = min(c["low"] for c in closed_bars[-21:-1])
        confirmed_close = closes[-1]
        
        return {
            "current_price": current_tick,
            "confirmed_close": confirmed_close,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "highest_prev": highest_prev,
            "lowest_prev": lowest_prev
        }

    async def check_open_positions(self, symbol: str, current_price: float):
        to_close = []
        for trade_id, pos in list(self.portfolio.positions.items()):
            if pos["symbol"] != symbol:
                continue

            side = pos["side"]
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
            closed = self.portfolio.close_position(trade_id, exit_px, reason)
            if closed:
                pnl = closed["pnl"]
                emoji = "🟢" if pnl >= 0 else "🔴"
                pnl_str = f"+{pnl:.2f}$" if pnl >= 0 else f"{pnl:.2f}$"
                msg = (
                    f"{emoji} <b>[СДЕЛКА ЗАКРЫТА]</b>\n"
                    f"🪙 <b>Пара:</b> <code>{closed['symbol']}</code> ({closed['side']})\n"
                    f"🚪 <b>Причина:</b> <code>{reason}</code>\n"
                    f"🎯 <b>Вход:</b> <code>${closed['entry']:.2f}</code> ➔ <b>Выход:</b> <code>${closed['exit']:.2f}</code>\n"
                    f"💵 <b>Результат (PnL):</b> <code>{pnl_str}</code> (<code>{closed['pnl_pct']:+.2f}%</code>)\n"
                    f"💰 <b>Баланс наличных:</b> <code>${self.portfolio.cash_balance:.2f}</code>"
                )
                await notifier.send_message(msg, parse_mode="HTML")
                logger.info(f"[Crypto] Closed {trade_id}: PnL = {pnl:.2f}")

    async def scan_and_trade(self):
        for symbol in self.symbols:
            candles = await self.fetch_candles(symbol)
            if not candles:
                continue

            ind = self.calculate_indicators(symbol, candles)
            if not ind:
                continue

            current_price = ind["current_price"]
            confirmed_close = ind["confirmed_close"]

            # 1. Manage active trades first
            await self.check_open_positions(symbol, current_price)

            # 2. Check position limit and existing positions for this symbol
            if len(self.portfolio.positions) >= config.MAX_OPEN_POSITIONS:
                continue

            has_position = any(p["symbol"] == symbol for p in self.portfolio.positions.values())
            if has_position:
                continue

            # 3. Check cash balance
            trade_size = config.POSITION_SIZE_USD
            if self.portfolio.cash_balance < trade_size:
                continue

            # 4. Strategy rules: Donchian 15m Trend-Following Breakout
            signal = None
            stop_loss = None
            take_profit = None

            # Bullish Breakout: Confirmed close above 20-period high AND EMA fast > EMA slow
            if confirmed_close > ind["highest_prev"] and ind["ema_fast"] > ind["ema_slow"]:
                signal = "BUY"
                stop_loss = round(current_price * (1.0 - config.STOP_LOSS_PCT), 2)
                take_profit = round(current_price * (1.0 + config.TAKE_PROFIT_PCT), 2)

            # Bearish Breakout: Confirmed close below 20-period low AND EMA fast < EMA slow
            elif confirmed_close < ind["lowest_prev"] and ind["ema_fast"] < ind["ema_slow"]:
                signal = "SELL"
                stop_loss = round(current_price * (1.0 + config.STOP_LOSS_PCT), 2)
                take_profit = round(current_price * (1.0 - config.TAKE_PROFIT_PCT), 2)

            if signal:
                trade_id = self.portfolio.open_position(
                    symbol=symbol,
                    side=signal,
                    price=current_price,
                    amount_usd=trade_size,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    strategy=f"15m Donchian Trend Breakout"
                )
                if trade_id:
                    side_emoji = "🚀 ЛОНГ" if signal == "BUY" else "🔻 ШОРТ"
                    alert = (
                        f"⚡ <b>[НОВАЯ КРИПТО-СДЕЛКА]</b>\n"
                        f"🪙 <b>Пара:</b> <code>{symbol}</code> ({side_emoji})\n"
                        f"📈 <b>Стратегия:</b> <code>15m Donchian Trend Breakout</code>\n"
                        f"💵 <b>Цена входа:</b> <code>${current_price:.2f}</code>\n"
                        f"🛡 <b>Стоп-лосс (риск):</b> <code>${stop_loss:.2f}</code> (-{config.STOP_LOSS_PCT*100:.1f}%)\n"
                        f"🎯 <b>Тейк-профит (цель):</b> <code>${take_profit:.2f}</code> (+{config.TAKE_PROFIT_PCT*100:.1f}%)\n"
                        f"💰 <b>Объем сделки:</b> <code>${trade_size:.2f}</code> | Свободно кэша: <code>${self.portfolio.cash_balance:.2f}</code>"
                    )
                    await notifier.send_message(alert, parse_mode="HTML")
                    logger.info(f"[Crypto] Opened position on {symbol} ({signal}) at {current_price}")
