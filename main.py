import asyncio
import logging
import signal
import sys
import time
from tabulate import tabulate

from config import config
from core.paper_portfolio import CryptoPortfolio
from core.telegram_notifier import notifier
from modules.crypto_engine import CryptoEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("CryptoBot")

portfolio = CryptoPortfolio(starting_balance=config.STARTING_BALANCE)
crypto_engine = CryptoEngine(portfolio)

is_running = True

def handle_exit(sig, frame):
    global is_running
    print("\n\n[!] Остановка бота... Сохранение портфеля.")
    is_running = False
    portfolio.save()
    sys.exit(0)

signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

def print_dashboard():
    stats = portfolio.get_stats(crypto_engine.last_prices)
    
    dash = [
        ["Метрика", "Значение"],
        ["💵 Свободный кэш (USD)", f"${stats['cash']:.2f}"],
        ["💼 В открытых сделках", f"${stats['invested']:.2f} ({stats['open_count']} поз.)"],
        ["📊 Общий капитал (Equity)", f"${stats['equity']:.2f}"],
        ["📈 Плавающий PnL (момент)", f"{stats['floating_pnl']:+.2f} $"],
        ["🎯 Итоговый PnL портфеля", f"{stats['total_pnl']:+.2f} $ ({stats['total_pnl_pct']:+.2f}%)"],
        ["🏆 Винрейт закрытых сделок", f"{stats['win_rate']:.1f}% ({stats['closed_count']} сделок)"]
    ]
    
    print("\n" + "=" * 65)
    print(f"   🤖 CRYPTO TREND TRADING BOT (OKX / BYBIT) [{config.TIMEFRAME}]")
    print(f"   ⏱  Время: {time.strftime('%Y-%m-%d %H:%M:%S')} | Режим: ТЕСТОВЫЙ (PAPER)")
    print("=" * 65)
    print(tabulate(dash, headers="firstrow", tablefmt="fancy_grid"))
    
    if crypto_engine.last_prices:
        px_info = " | ".join([f"{k}: ${v:.2f}" for k, v in crypto_engine.last_prices.items() if isinstance(k, str)])
        print(f"📈 Курсы: {px_info}")
    print("=" * 65 + "\n")

async def crypto_loop():
    while is_running:
        try:
            await crypto_engine.scan_and_trade()
        except Exception as e:
            logger.error(f"[Crypto Loop Error]: {e}")
        await asyncio.sleep(config.SCAN_INTERVAL)

async def dashboard_and_heartbeat_loop():
    last_heartbeat = time.time()
    while is_running:
        print_dashboard()
        
        # Periodic Telegram digest every HEARTBEAT_INTERVAL seconds
        if time.time() - last_heartbeat >= config.HEARTBEAT_INTERVAL:
            last_heartbeat = time.time()
            stats = portfolio.get_stats(crypto_engine.last_prices)
            pnl_emoji = "🟢" if stats['total_pnl'] >= 0 else "🔴"
            msg = (
                f"📊 <b>[СВОДКА КРИПТО-ПОРТФЕЛЯ]</b>\n\n"
                f"💵 <b>Свободный кэш:</b> <code>${stats['cash']:.2f}</code>\n"
                f"💼 <b>В сделках:</b> <code>${stats['invested']:.2f}</code> ({stats['open_count']} шт.)\n"
                f"📈 <b>Плавающий PnL (в моменте):</b> <code>{stats['floating_pnl']:+.2f}$</code>\n"
                f"💰 <b>Общий капитал:</b> <code>${stats['equity']:.2f}</code>\n"
                f"{pnl_emoji} <b>Итоговый результат:</b> <code>{stats['total_pnl']:+.2f}$</code> (<code>{stats['total_pnl_pct']:+.2f}%</code>)\n"
                f"🏆 <b>Винрейт:</b> <code>{stats['win_rate']:.1f}%</code> (закрыто: {stats['closed_count']})"
            )
            await notifier.send_message(msg, parse_mode="HTML")

        await asyncio.sleep(20)

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
                                pnl_emoji = "🟢" if stats['total_pnl'] >= 0 else "🔴"
                                reply = (
                                    f"📊 <b>[ТЕКУЩИЙ СТАТУС ПОРТФЕЛЯ]</b>\n\n"
                                    f"💵 <b>Свободно:</b> <code>${stats['cash']:.2f}</code>\n"
                                    f"💼 <b>В сделках:</b> <code>${stats['invested']:.2f}</code> ({stats['open_count']} поз.)\n"
                                    f"📈 <b>Плавающий PnL:</b> <code>{stats['floating_pnl']:+.2f}$</code>\n"
                                    f"💰 <b>Капитал (Equity):</b> <code>${stats['equity']:.2f}</code>\n"
                                    f"{pnl_emoji} <b>Итоговый PnL:</b> <code>{stats['total_pnl']:+.2f}$</code> (<code>{stats['total_pnl_pct']:+.2f}%</code>)\n"
                                    f"🏆 <b>Винрейт:</b> <code>{stats['win_rate']:.1f}%</code>"
                                )
                                await notifier.send_message(reply, parse_mode="HTML")

                            elif text in ["/trades", "/positions", "сделки", "позиции"]:
                                stats = portfolio.get_stats(crypto_engine.last_prices)
                                positions = stats.get("positions", [])
                                if not positions:
                                    reply = "ℹ️ <i>Сейчас нет открытых позиций. Бот ждет подтвержденный трендовый сигнал на 15m свечах.</i>"
                                else:
                                    lines = []
                                    for p in positions:
                                        emoji = "🟢" if p["pnl"] >= 0 else "🔴"
                                        lines.append(
                                            f"• <b>{p['symbol']}</b> ({p['side']})\n"
                                            f"   Вход: <code>${p['entry']:.2f}</code> ➔ Сейчас: <code>${p['current']:.2f}</code>\n"
                                            f"   SL: <code>${p['stop_loss']:.2f}</code> | TP: <code>${p['take_profit']:.2f}</code>\n"
                                            f"   PnL: {emoji} <code>{p['pnl']:+.2f}$</code> (<code>{p['pnl_pct']:+.2f}%</code>)"
                                        )
                                    reply = "📋 <b>[ОТКРЫТЫЕ ПОЗИЦИИ (LIVE)]:</b>\n\n" + "\n\n".join(lines)
                                await notifier.send_message(reply, parse_mode="HTML")

                            elif text in ["/history", "история"]:
                                history = [t for t in portfolio.trade_history if "CLOSE" in t.get("action", "")][-5:]
                                if not history:
                                    reply = "ℹ️ <i>История закрытых сделок пока пуста.</i>"
                                else:
                                    lines = []
                                    for h in history:
                                        emoji = "🟢" if h.get("pnl", 0) >= 0 else "🔴"
                                        lines.append(
                                            f"{emoji} <b>{h['symbol']}</b>: {h.get('pnl', 0):+.2f}$ ({h.get('pnl_pct', 0):+.2f}%)\n"
                                            f"   Причина: <code>{h.get('reason', '')}</code>"
                                        )
                                    reply = "📜 <b>[ПОСЛЕДНИЕ ЗАКРЫТЫЕ СДЕЛКИ]:</b>\n\n" + "\n".join(lines)
                                await notifier.send_message(reply, parse_mode="HTML")

                            elif text in ["/help", "помощь"]:
                                reply = (
                                    "ℹ️ <b>Команды бота:</b>\n"
                                    "• /status — сводка баланса и капитала\n"
                                    "• /trades — живые открытые позиции с ценами и PnL\n"
                                    "• /history — история последних закрытых сделок\n"
                                    "• /help — эта справка"
                                )
                                await notifier.send_message(reply, parse_mode="HTML")

        except Exception as e:
            logger.debug(f"Command listener error: {e}")
            await asyncio.sleep(2)

async def main():
    logger.info("Запуск Crypto Algorithmic Trading Bot (15m Trend Breakout)...")
    
    welcome_msg = (
        "🚀 <b>Crypto Trend Trading Bot успешно запущен!</b>\n\n"
        "• 🪙 <b>Инструменты:</b> BTC, ETH, SOL\n"
        "• ⏱ <b>Таймфрейм:</b> 15-минутные свечи (15m)\n"
        "• 📈 <b>Стратегия:</b> Donchian Trend Breakout + EMA 20/50 фильтр\n"
        "• 🛡 <b>Мани-менеджмент:</b> Стоп-лосс -1.5%, Тейк-профит +3.5%\n"
        "• 💵 <b>Виртуальный баланс:</b> $100 (~10,000 руб.)\n\n"
        "💡 <i>Отправьте /status для проверки баланса или /trades для списка позиций.</i>"
    )
    await notifier.send_message(welcome_msg, parse_mode="HTML")
    
    await asyncio.gather(
        crypto_loop(),
        dashboard_and_heartbeat_loop(),
        telegram_command_listener()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
