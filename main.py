import asyncio
import logging
import signal
import sys
import time
from tabulate import tabulate

from config import config
from core.paper_portfolio import PaperPortfolio
from core.telegram_notifier import notifier
from modules.polymarket_engine import PolymarketEngine
from modules.crypto_engine import CryptoEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("DualBot")

portfolio = PaperPortfolio(
    polymarket_balance=config.POLYMARKET_STARTING_BALANCE,
    crypto_balance=config.CRYPTO_STARTING_BALANCE
)

poly_engine = PolymarketEngine(portfolio)
crypto_engine = CryptoEngine(portfolio)

is_running = True

def handle_exit(sig, frame):
    global is_running
    print("\n\n[!] Остановка бота... Сохранение состояния портфеля.")
    is_running = False
    portfolio.save()
    sys.exit(0)

signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

def print_dashboard():
    stats = portfolio.get_stats()
    
    dash = [
        ["Модуль", "Баланс (USDC)", "Открыто сделок", "PnL ($)"],
        ["🔮 Polymarket", f"${stats['poly_balance']}", f"{stats['poly_open']}", f"{stats['poly_pnl']:+} $"],
        ["🪙 Crypto (OKX)", f"${stats['crypto_balance']}", f"{stats['crypto_open']}", f"{stats['crypto_pnl']:+} $"],
        ["📊 ВСЕГО", f"${stats['total_balance']}", f"{stats['poly_open'] + stats['crypto_open']}", f"{stats['total_pnl']:+} $"]
    ]
    
    print("\n" + "=" * 65)
    print("   🤖 DUAL PAPER TRADING BOT (POLYMARKET + CRYPTO) v1.0")
    print(f"   ⏱  Время: {time.strftime('%Y-%m-%d %H:%M:%S')} | Режим: ТЕСТОВЫЙ (ДЕМО)")
    print("=" * 65)
    print(tabulate(dash, headers="firstrow", tablefmt="fancy_grid"))
    
    if crypto_engine.last_prices:
        px_info = " | ".join([f"{k}: ${v:.2f}" for k, v in crypto_engine.last_prices.items() if isinstance(k, str)])
        print(f"📈 Текущие курсы: {px_info}")
    print("=" * 65 + "\n")

async def polymarket_loop():
    while is_running:
        try:
            await poly_engine.scan_and_trade()
        except Exception as e:
            logger.error(f"[Polymarket Loop Error]: {e}")
        await asyncio.sleep(config.POLYMARKET_INTERVAL)

async def crypto_loop():
    while is_running:
        try:
            await crypto_engine.scan_and_trade()
        except Exception as e:
            logger.error(f"[Crypto Loop Error]: {e}")
        await asyncio.sleep(config.CRYPTO_INTERVAL)

async def dashboard_and_heartbeat_loop():
    last_heartbeat = time.time()
    while is_running:
        print_dashboard()
        
        # Periodic Telegram digest every HEARTBEAT_INTERVAL seconds
        if time.time() - last_heartbeat >= config.HEARTBEAT_INTERVAL:
            last_heartbeat = time.time()
            stats = portfolio.get_stats()
            msg = (
                f"📊 *[DUAL BOT СВОДКА ЗА ПЕРИОД]*\n"
                f"🔮 *Polymarket:* `${stats['poly_balance']:.2f}` (PnL: `{stats['poly_pnl']:+.2f}$`)\n"
                f"🪙 *Crypto:* `${stats['crypto_balance']:.2f}` (PnL: `{stats['crypto_pnl']:+.2f}$`)\n"
                f"💰 *Общий баланс:* `${stats['total_balance']:.2f}` (PnL: `{stats['total_pnl']:+.2f}$`)\n"
                f"📁 *Всего закрыто сделок:* `{stats['total_trades']}`"
            )
            await notifier.send_message(msg)

        await asyncio.sleep(15)

async def main():
    logger.info("Запуск Dual Trading Bot (Polymarket + Crypto)...")
    
    welcome_msg = (
        "🚀 *Dual Trading Bot успешно запущен!*\n\n"
        "• 🔮 *Модуль Polymarket:* Сканирование рынков, поиск спредов и арбитража.\n"
        "• 🪙 *Модуль Crypto:* Анализ BTC, ETH, SOL по реальным котировкам.\n"
        "• 🛡 *Режим:* 100% безопасная симуляция (Paper Trading).\n"
        "• 💵 *Стартовый виртуальный банк:* $200 (~20,000 руб.).\n\n"
        "Все новые сделки и фиксации прибыли будут приходить сюда."
    )
    await notifier.send_message(welcome_msg)
    
    await asyncio.gather(
        polymarket_loop(),
        crypto_loop(),
        dashboard_and_heartbeat_loop()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
