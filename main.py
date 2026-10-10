import asyncio
import logging
import signal
import sys
import time
import aiohttp
from tabulate import tabulate

from config import config
from core.multi_portfolio import TournamentManager
from core.telegram_notifier import notifier
from strategies.trend_strategy import TrendStrategy
from strategies.grid_strategy import GridStrategy
from strategies.reversion_strategy import MeanReversionStrategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("TournamentBot")

tournament = TournamentManager(
    trend_budget=config.TREND_BUDGET,
    grid_budget=config.GRID_BUDGET,
    reversion_budget=config.REVERSION_BUDGET
)

strat_trend = TrendStrategy(tournament.strat_trend)
strat_grid = GridStrategy(tournament.strat_grid)
strat_reversion = MeanReversionStrategy(tournament.strat_reversion)

live_prices = {}
is_running = True

def handle_exit(sig, frame):
    global is_running
    print("\n\n[!] Остановка турнира... Сохранение портфелей.")
    is_running = False
    tournament.save()
    sys.exit(0)

signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

async def fetch_candles(symbol: str, bar: str = "15m", limit: int = 40):
    url = "https://www.okx.com/api/v5/market/candles"
    params = {"instId": symbol, "bar": bar, "limit": str(limit)}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
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
        logger.warning(f"Error fetching candles for {symbol}: {e}")
    return []

def print_dashboard():
    lb = tournament.get_leaderboard(live_prices)
    summary = tournament.get_summary(live_prices)

    table = [
        ["Место", "Стратегия", "Капитал (Eq)", "Кэш", "В сделках", "PnL ($ / %)", "Винрейт"]
    ]
    medals = ["🥇", "🥈", "🥉"]
    for i, s in enumerate(lb):
        m = medals[i] if i < 3 else f"{i+1}"
        table.append([
            f"{m} {i+1}",
            s["name"],
            f"${s['equity']:.2f}",
            f"${s['cash']:.2f}",
            f"${s['invested']:.2f} ({s['open_count']})",
            f"{s['total_pnl']:+.2f}$ ({s['total_pnl_pct']:+.2f}%)",
            f"{s['win_rate']:.1f}% ({s['closed_count']} сд.)"
        ])

    print("\n" + "=" * 85)
    print("   🏆 ТУРНИР АЛГОРИТМОВ (3 СТРАТЕГИИ ПО $100 КАЖДАЯ) [15m]")
    print(f"   ⏱  Время: {time.strftime('%Y-%m-%d %H:%M:%S')} | Общий капитал: ${summary['total_equity']:.2f} (PnL: {summary['total_pnl']:+.2f}$)")
    print("=" * 85)
    print(tabulate(table, headers="firstrow", tablefmt="fancy_grid"))

    if live_prices:
        px_info = " | ".join([f"{k}: ${v:.2f}" for k, v in live_prices.items()])
        print(f"📈 Текущие курсы: {px_info}")
    print("=" * 85 + "\n")

async def market_loop():
    while is_running:
        for symbol in config.SYMBOLS:
            candles = await fetch_candles(symbol)
            if not candles:
                continue

            current_price = candles[-1]["close"]
            live_prices[symbol] = current_price

            # Run all 3 strategies concurrently on this tick
            await asyncio.gather(
                strat_trend.update(symbol, current_price, candles),
                strat_grid.update(symbol, current_price, candles),
                strat_reversion.update(symbol, current_price, candles)
            )

        tournament.save()
        await asyncio.sleep(config.SCAN_INTERVAL)

async def dashboard_and_heartbeat_loop():
    last_heartbeat = time.time()
    while is_running:
        print_dashboard()

        # Periodic Telegram digest
        if time.time() - last_heartbeat >= config.HEARTBEAT_INTERVAL:
            last_heartbeat = time.time()
            lb = tournament.get_leaderboard(live_prices)
            summary = tournament.get_summary(live_prices)
            pnl_emoji = "🟢" if summary['total_pnl'] >= 0 else "🔴"

            lines = []
            medals = ["🥇", "🥈", "🥉"]
            for i, s in enumerate(lb):
                lines.append(
                    f"{medals[i]} <b>{s['name']}</b>\n"
                    f"   Капитал: <code>${s['equity']:.2f}</code> | PnL: <code>{s['total_pnl']:+.2f}$</code> (<code>{s['total_pnl_pct']:+.2f}%</code>)\n"
                    f"   Кэш: <code>${s['cash']:.2f}</code> | В сделках: <code>${s['invested']:.2f}</code> | Винрейт: <code>{s['win_rate']:.1f}%</code>"
                )

            board_txt = "\n\n".join(lines)
            msg = (
                f"🏆 <b>[ТУРНИР АЛГОРИТМОВ — ТЕКУЩИЙ ЛИДЕРБОРД]</b>\n\n"
                f"{board_txt}\n\n"
                f"💰 <b>ОБЩИЙ БАНК (3 стратегии):</b> <code>${summary['total_equity']:.2f}</code> ({summary['total_pnl']:+.2f}$)\n"
                f"💡 <i>Отправьте /trades для просмотра позиций или /status для сводки.</i>"
            )
            await notifier.send_message(msg, parse_mode="HTML")

        await asyncio.sleep(20)

async def telegram_command_listener():
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

                            if text in ["/status", "/balance", "/leaderboard", "баланс", "статус", "лидерборд"]:
                                lb = tournament.get_leaderboard(live_prices)
                                summary = tournament.get_summary(live_prices)
                                medals = ["🥇", "🥈", "🥉"]
                                lines = []
                                for i, s in enumerate(lb):
                                    lines.append(
                                        f"{medals[i]} <b>{s['name']}:</b>\n"
                                        f"   Капитал: <code>${s['equity']:.2f}</code> | PnL: <code>{s['total_pnl']:+.2f}$</code>\n"
                                        f"   Сделок в работе: <code>{s['open_count']}</code> | Винрейт: <code>{s['win_rate']:.1f}%</code>"
                                    )
                                reply = (
                                    f"🏆 <b>[ТУРНИР АЛГОРИТМОВ — РЕЙТИНГ]</b>\n\n"
                                    + "\n\n".join(lines) +
                                    f"\n\n💰 <b>Всего капитал (3 банка):</b> <code>${summary['total_equity']:.2f}</code> (PnL: <code>{summary['total_pnl']:+.2f}$</code>)"
                                )
                                await notifier.send_message(reply, parse_mode="HTML")

                            elif text in ["/trades", "/positions", "сделки", "позиции"]:
                                lb = tournament.get_leaderboard(live_prices)
                                sections = []
                                for s in lb:
                                    pos = s["positions"]
                                    if not pos:
                                        sections.append(f"<b>{s['name']}:</b> <i>нет открытых сделок</i>")
                                    else:
                                        p_lines = []
                                        for p in pos:
                                            emoji = "🟢" if p["pnl"] >= 0 else "🔴"
                                            p_lines.append(
                                                f"  • <b>{p['symbol']}</b> ({p['side']}): вход ${p['entry']:.2f} ➔ сейчас ${p['current']:.2f} "
                                                f"[{emoji} <code>{p['pnl']:+.2f}$</code>]"
                                            )
                                        sections.append(f"<b>{s['name']}:</b>\n" + "\n".join(p_lines))

                                reply = "📋 <b>[АКТИВНЫЕ ПОЗИЦИИ ПО СТРАТЕГИЯМ]:</b>\n\n" + "\n\n".join(sections)
                                await notifier.send_message(reply, parse_mode="HTML")

                            elif text in ["/help", "помощь"]:
                                reply = (
                                    "ℹ️ <b>Команды турнира:</b>\n"
                                    "• /leaderboard или /status — лидерборд и рейтинг трех стратегий\n"
                                    "• /trades — детальный список открытых сделок каждой стратегии\n"
                                    "• /help — эта справка"
                                )
                                await notifier.send_message(reply, parse_mode="HTML")

        except Exception as e:
            logger.debug(f"Command listener error: {e}")
            await asyncio.sleep(2)

async def main():
    logger.info("Запуск ТУРНИРА АЛГОРИТМОВ (3 стратегии параллельно)...")

    welcome_msg = (
        "🏆 <b>ТУРНИР 3 АЛГОРИТМОВ УСПЕШНО СТАРТОВАЛ!</b>\n\n"
        "Каждой стратегии выделен независимый демо-банк в <b>$100 (~10,000 руб.)</b>:\n\n"
        "1. 🚀 <b>Трендовый пробой (15m):</b> вход по сильному импульсу с жестким SL -1.5% и TP +3.5%\n"
        "2. 🧺 <b>Спотовая Сетка (Grid):</b> сбор микроприбыли (+1.0%) на колебаниях во флэте\n"
        "3. 🧲 <b>Возврат к средней (RSI):</b> покупка на резких просадках (RSI < 30) с целью к средней\n\n"
        "💰 <b>Общий банк турнира:</b> $300.00 (~30,000 руб.)\n"
        "📊 <i>Отправьте /status для лидерборда или /trades для списка сделок!</i>"
    )
    await notifier.send_message(welcome_msg, parse_mode="HTML")

    await asyncio.gather(
        market_loop(),
        dashboard_and_heartbeat_loop(),
        telegram_command_listener()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
