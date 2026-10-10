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
    stats = portfolio.get_stats(crypto_engine.last_prices)
    
    dash = [
        ["Модуль", "Свободно (Cash)", "В сделках", "Всего капитал", "PnL ($)"],
        ["🔮 Polymarket", f"${stats['poly_cash']}", f"${stats['poly_invested']}", f"${stats['poly_equity']}", f"{stats['poly_pnl']:+} $"],
        ["🪙 Crypto (OKX)", f"${stats['crypto_cash']}", f"${stats['crypto_invested']}", f"${stats['crypto_equity']}", f"{stats['crypto_pnl']:+} $"],
        ["📊 ВСЕГО", f"${stats['total_cash']}", f"${stats['total_assets']}", f"${stats['total_equity']}", f"{stats['total_pnl']:+} $"]
    ]
    
    print("\n" + "=" * 70)
    print("   🤖 DUAL PAPER TRADING BOT (POLYMARKET + CRYPTO) v1.0")
    print(f"   ⏱  Время: {time.strftime('%Y-%m-%d %H:%M:%S')} | Режим: ТЕСТОВЫЙ (ДЕМО)")
    print("=" * 70)
    print(tabulate(dash, headers="firstrow", tablefmt="fancy_grid"))
    
    if crypto_engine.last_prices:
        px_info = " | ".join([f"{k}: ${v:.2f}" for k, v in crypto_engine.last_prices.items() if isinstance(k, str)])
        print(f"📈 Текущие курсы: {px_info}")
    print("=" * 70 + "\n")

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
            stats = portfolio.get_stats(crypto_engine.last_prices)
            msg = (
                f"📊 <b>[DUAL BOT СВОДКА ПОРТФЕЛЯ]</b>\n\n"
                f"🔮 <b>Polymarket:</b>\n"
                f"• В сделках ({stats['poly_open']} шт): <code>${stats['poly_invested']:.2f}</code>\n"
                f"• Свободно: <code>${stats['poly_cash']:.2f}</code>\n"
                f"• Капитал: <code>${stats['poly_equity']:.2f}</code> (PnL: <code>{stats['poly_pnl']:+.2f}$</code>)\n\n"
                f"🪙 <b>Crypto (OKX live):</b>\n"
                f"• В сделках ({stats['crypto_open']} шт): <code>${stats['crypto_invested']:.2f}</code>\n"
                f"• Плавающий PnL: <code>{stats['crypto_floating_pnl']:+.2f}$</code>\n"
                f"• Свободно: <code>${stats['crypto_cash']:.2f}</code>\n"
                f"• Капитал: <code>${stats['crypto_equity']:.2f}</code>\n\n"
                f"💰 <b>ИТОГО КАПИТАЛ:</b> <code>${stats['total_equity']:.2f}</code> (PnL: <code>{stats['total_pnl']:+.2f}$</code>)\n"
                f"📁 Всего сделок в истории: <code>{stats['total_trades']}</code>"
            )
            await notifier.send_message(msg, parse_mode="HTML")

        await asyncio.sleep(15)

async def telegram_command_listener():
    import aiohttp
    last_update_id = 0
    token = config.TELEGRAM_BOT_TOKEN
    if not token:
        return

    while is_running:
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates?offset={last_update_id + 1}&timeout=10"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for update in data.get("result", []):
                            last_update_id = update["update_id"]
                            msg = update.get("message", {})
                            text = (msg.get("text", "")).strip().lower()
                            chat_id = str(msg.get("chat", {}).get("id", ""))
                            
                            if chat_id != config.TELEGRAM_CHAT_ID:
                                continue

                            if text in ["/status", "/balance", "баланс", "статус"]:
                                stats = portfolio.get_stats(crypto_engine.last_prices)
                                reply = (
                                    f"📊 <b>[ТЕКУЩИЙ СТАТУС ПОРТФЕЛЯ (LIVE)]</b>\n\n"
                                    f"🔮 <b>Polymarket:</b>\n"
                                    f"• В ставках ({stats['poly_open']} позиций): <code>${stats['poly_invested']:.2f}</code>\n"
                                    f"• Свободно кэша: <code>${stats['poly_cash']:.2f}</code>\n"
                                    f"• Капитал: <code>${stats['poly_equity']:.2f}</code> (PnL: <code>{stats['poly_pnl']:+.2f}$</code>)\n\n"
                                    f"🪙 <b>Crypto (OKX Live):</b>\n"
                                    f"• В сделках ({stats['crypto_open']} позиций): <code>${stats['crypto_invested']:.2f}</code>\n"
                                    f"• Плавающий PnL: <code>{stats['crypto_floating_pnl']:+.2f}$</code>\n"
                                    f"• Свободно кэша: <code>${stats['crypto_cash']:.2f}</code>\n"
                                    f"• Капитал: <code>${stats['crypto_equity']:.2f}</code> (PnL: <code>{stats['crypto_pnl']:+.2f}$</code>)\n\n"
                                    f"💰 <b>РЕАЛЬНЫЙ КАПИТАЛ:</b> <code>${stats['total_equity']:.2f}</code> (PnL: <code>{stats['total_pnl']:+.2f}$</code>)\n"
                                    f"📁 Совершено сделок: <code>{stats['total_trades']}</code>"
                                )
                                await notifier.send_message(reply, parse_mode="HTML")

                            elif text in ["/trades", "/positions", "сделки", "позиции"]:
                                stats = portfolio.get_stats(crypto_engine.last_prices)
                                
                                poly_lines = []
                                for pos in list(portfolio.poly_positions.values())[-5:]:
                                    poly_lines.append(f"• <i>{pos['market'][:32]}...</i>\n   Ставка: <code>${pos['cost']:.1f}</code> на <code>{pos['outcome']}</code> (в игре)")

                                crypto_lines = []
                                for c in stats.get("crypto_details", []):
                                    emoji = "🟢" if c["pnl"] >= 0 else "🔴"
                                    crypto_lines.append(
                                        f"• <b>{c['symbol']}</b> ({c['side']})\n"
                                        f"   Вход: <code>${c['entry']:.2f}</code> ➔ Сейчас: <code>${c['current']:.2f}</code>\n"
                                        f"   PnL: {emoji} <code>{c['pnl']:+.2f}$</code> (<code>{c['pnl_pct']:+.2f}%</code>)"
                                    )

                                poly_txt = "\n".join(poly_lines) if poly_lines else "<i>Нет активных ставок</i>"
                                crypto_txt = "\n".join(crypto_lines) if crypto_lines else "<i>Нет открытых позиций</i>"

                                reply = (
                                    f"📋 <b>[ЖИВЫЕ ПОЗИЦИИ В РАБОТЕ (MARK-TO-MARKET)]</b>\n\n"
                                    f"🪙 <b>Crypto (с текущей ценой):\n</b>{crypto_txt}\n\n"
                                    f"🔮 <b>Polymarket (в ожидании финала):\n</b>{poly_txt}"
                                )
                                await notifier.send_message(reply, parse_mode="HTML")

                            elif text in ["/help", "помощь"]:
                                reply = (
                                    "ℹ️ <b>Команды бота:</b>\n"
                                    "• /status или /balance — проверка баланса и капитала\n"
                                    "• /trades — список текущих открытых позиций\n"
                                    "• /help — эта справка"
                                )
                                await notifier.send_message(reply, parse_mode="HTML")

        except Exception as e:
            logger.debug(f"Command listener error: {e}")
            await asyncio.sleep(2)

async def main():
    logger.info("Запуск Dual Trading Bot (Polymarket + Crypto)...")
    
    welcome_msg = (
        "🚀 <b>Dual Trading Bot успешно запущен!</b>\n\n"
        "• 🔮 <b>Модуль Polymarket:</b> Сканирование рынков, поиск спредов и арбитража.\n"
        "• 🪙 <b>Модуль Crypto:</b> Анализ BTC, ETH, SOL по реальным котировкам.\n"
        "• 🛡 <b>Режим:</b> 100% безопасная симуляция (Paper Trading).\n"
        "• 💵 <b>Стартовый виртуальный банк:</b> $200 (~20,000 руб.).\n\n"
        "Все новые сделки и фиксации прибыли будут приходить сюда.\n"
        "💡 <i>Отправьте /status для проверки баланса или /trades для списка позиций.</i>"
    )
    await notifier.send_message(welcome_msg, parse_mode="HTML")
    
    await asyncio.gather(
        polymarket_loop(),
        crypto_loop(),
        dashboard_and_heartbeat_loop(),
        telegram_command_listener()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
