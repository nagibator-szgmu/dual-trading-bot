import aiohttp
import asyncio
import logging
import os
from config import config

logger = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self):
        self.token = config.TELEGRAM_BOT_TOKEN
        self.chat_id = config.TELEGRAM_CHAT_ID

    async def auto_discover_chat_id(self) -> bool:
        if self.chat_id:
            return True
        if not self.token:
            return False

        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = data.get("result", [])
                        for update in reversed(results):
                            msg = update.get("message", {})
                            chat = msg.get("chat", {})
                            cid = chat.get("id")
                            if cid:
                                self.chat_id = str(cid)
                                config.TELEGRAM_CHAT_ID = self.chat_id
                                logger.info(f"Automatically discovered Telegram Chat ID: {self.chat_id}")
                                # Persist to .env if possible
                                self._persist_chat_id(self.chat_id)
                                return True
        except Exception as e:
            logger.debug(f"Auto-discover chat_id poll error: {e}")
        return False

    def _persist_chat_id(self, cid: str):
        env_path = ".env"
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                new_lines = []
                for line in lines:
                    if line.startswith("TELEGRAM_CHAT_ID="):
                        new_lines.append(f"TELEGRAM_CHAT_ID={cid}\n")
                    else:
                        new_lines.append(line)
                with open(env_path, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)
            except Exception:
                pass

    async def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        if not self.token:
            print(f"\n[TG ALERT MOCK]:\n{text}\n")
            return False

        if not self.chat_id:
            found = await self.auto_discover_chat_id()
            if not found:
                print(f"\n[TG WAITING FOR /start in @bu7634bot]:\n{text}\n")
                return False

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        return True
                    else:
                        err = await resp.text()
                        logger.warning(f"Telegram API error: {err}")
                        return False
        except Exception as e:
            logger.warning(f"Failed to send Telegram message: {e}")
            return False

notifier = TelegramNotifier()
