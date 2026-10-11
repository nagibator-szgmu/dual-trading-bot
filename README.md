---
title: Crypto & CS2 Quant Trading Platform
emoji: 🏆
colorFrom: pink
colorTo: indigo
sdk: gradio
sdk_version: 6.30.0
app_file: app.py
pinned: false
license: mit
---

# 🏆 Crypto & CS2 Quant Trading Platform

Двухмодульная автономная алготрейдинг-платформа 24/7 в облаке Hugging Face Spaces с полным управлением через отдельных Telegram-ботов.

---

## 🏆 Модуль 1: Крипто-Турнир (7 Квант-Стратегий)
* **Telegram-бот:** [@ct331_bot](https://t.me/ct331_bot)
* **Активы:** BTC-USDT, ETH-USDT, SOL-USDT, DOGE-USDT (OKX Live Market Data Feed)
* **Бюджет:** $700 ($100 на каждую изолированную стратегию)
* **Стратегии:**
  1. ⚡ **Trend Follower**: Long-Only Donchian (20) + EMA(50), R:R 1:3.0 (SL -2.0%, TP +6.0%).
  2. 🕸️ **Spot Grid**: Trend-hedged арифметическая сетка (шаг 2.0%, ордер $10).
  3. 🌊 **Mean Reversion**: Long-Only отскоки от перепроданности (RSI < 30 + Bollinger Band 2.0σ).
  4. 💥 **Volatility Squeeze**: ATR сжатие полос Боллинджера в канале Кельтнера с подтверждением объема.
  5. 📐 **Adaptive Grid**: Динамический шаг сетки на основе ATR волатильности (Maker 0.02%).
  6. 🎯 **Flash Dump Hunter**: Ловля каскадных ликвидаций и проливов (RSI < 20, Volume > 300%).
  7. 🚀 **HyperCascade Sniper**: Импульсный памп-чейзер с анти-мартингейл пирамидингом и Breakeven SL (+0.35%).

* **Команды Telegram:**
  * `/status` или `/leaderboard` — балансы, PnL и рейтинг
  * `/trades` — открытые сделки
  * `/export` — выгрузка CSV / JSON баз данных (Admin Only)
  * `/help` — справка

---

## 🔫 Модуль 2: CS2 Skin Paper Sniper
* **Telegram-бот:** [@cs233_bot](https://t.me/cs233_bot)
* **Источник данных:** Skinport Public API (`/v1/items` и `/v1/sales/history`)
* **Бюджет:** $200 виртуальный банк
* **Критерии снайпинга:**
  * Ликвидность: `vol_30d >= 10` (не менее 10 реальных продаж за 30 дней)
  * Оценка: `Fair Price = min(avg_30d, suggested_price)`
  * Дисконт: от 15% к справедливой цене
  * Чистый профит: от 8% после вычета реальной комиссии Skinport 8%
  * Trade Lock: симуляция 7-дневной блокировки инвентаря перед автопродажей по справедливой цене

* **Команды Telegram:**
  * `/status` — баланс и профит инвентаря
  * `/inventory` — список скинов и оставшиеся дни Trade Lock
  * `/scan` — ручной поиск топ-скидок прямо сейчас (Admin Only)
  * `/export` — выгрузка `cs2_trades.csv` (Admin Only)
  * `/help` — справка
