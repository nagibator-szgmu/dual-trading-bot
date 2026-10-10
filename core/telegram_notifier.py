import aiohttp
import logging
from config import config

logger = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self):
        self.token = config.TELEGRAM_BOT_TOKEN
        self.chat_id = config.TELEGRAM_CHAT_ID
        self.api_url = f"https://api.telegram.org/bot{self.token}/sendMessage" if self.token else None

    async def send_message(self, text: str, parse_mode: str = "Markdown"):
        if not self.api_url or not self.chat_id:
            # Fallback: log to console
            print(f"\n[TG ALERT MOCK]:\n{text}\n")
            return False

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.api_url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
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
