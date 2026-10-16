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

Двухмодульная автономная алготрейдинг-платформа 24/7 в облаке Hugging Face Spaces с полным управлением через отдельных Telegram-ботов и автоматической персистентной синхронизацией состояния в Hugging Face Dataset (`Hhhej/trading-bot-state`).

---

## 🏆 Модуль 1: Крипто-Турнир (Топ-4 Квант-Стратегии)
* **Telegram-бот:** [@ct331_bot](https://t.me/ct331_bot)
* **Активы:** BTC-USDT, ETH-USDT, SOL-USDT, DOGE-USDT (OKX Live Market Data Feed)
* **Бюджет:** $800.00 ($200.00 на каждую изолированную стратегию)
* **Рабочий лот:** $40–$50 (риск 0.4%–0.5% от банка)
* **Стратегии:**
  1. 📊 **POC Pullback Sniper**: Отскок от 24h Point-of-Control горизонтального профиля объема (лот $45, SL -1.8%, Trailing BE).
  2. 🎯 **Flash Dump Hunter**: Ловля каскадных ликвидаций и проливов с длинным нижним фитилём (лот $45, SL -2.0%, TP +2.8%–+3.2%).
  3. 📐 **Adaptive Grid**: Адаптивная сетка с динамическим шагом 1.2% при низкой волатильности (ATR < 0.35%) и до 2.4%–4.8% при всплесках (шаг $25, до 3 уровней).
  4. 💥 **Volatility Squeeze**: Разжатие полос Боллинджера внутри канала Кельтнера с подтверждением объёма (лот $45, SL -1.8%, TP +5.5%, R:R 1:3).

* **Команды Telegram:**
  * `/status` или `/leaderboard` — балансы, PnL и рейтинг
  * `/trades` — открытые сделки
  * `/export` — выгрузка CSV / JSON баз данных (Admin Only)
  * `/help` — справка

---

## 🔫 Модуль 2: CS2 Skin Paper Sniper
* **Telegram-бот:** [@cs233_bot](https://t.me/cs233_bot)
* **Источник данных:** Skinport Public API (`/v1/items` и `/v1/sales/history`)
* **Бюджет:** $200 виртуальный банк (загрузка депозита поднята до 85%)
* **Инвентарь:** Лимит поднят с 6 до 15 слотов, максимум 2 закупки за один скан
* **Персистентность:** Авто-синхронизация `cs2_portfolio_state.json` с датасетом `Hhhej/trading-bot-state` для защиты 7-дневного Trade Lock от сбросов
* **Критерии снайпинга:**
  * Ликвидность: `vol_30d >= 15` (не менее 15 реальных продаж за 30 дней)
  * Оценка: `Fair Price = min(avg_30d, suggested_price)`
  * Дисконт: от 15% к справедливой цене
  * Чистый профит: от 8% после вычета реальной комиссии Skinport 8%
  * Trade Lock: симуляция 7-дневной блокировки инвентаря перед автопродажей по динамической рыночной цене

* **Команды Telegram:**
  * `/status` — баланс и профит инвентаря
  * `/inventory` — список скинов и оставшиеся дни Trade Lock
  * `/scan` — ручной поиск топ-скидок прямо сейчас (Admin Only)
  * `/export` — выгрузка `cs2_trades.csv` (Admin Only)
  * `/help` — справка
