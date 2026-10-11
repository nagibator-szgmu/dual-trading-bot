import logging
import os
from typing import Optional
from config import config

logger = logging.getLogger(__name__)

class CS2TelegramNotifier:
    def __init__(self):
        self.token = config.CS2_BOT_TOKEN
        self.default_chat_id = config.TELEGRAM_CHAT_ID

    async def send_message(self, text: str, chat_id: Optional[str] = None, parse_mode: str = "HTML") -> bool:
        target_chat = chat_id or self.default_chat_id
        if not self.token or not target_chat:
            logger.warning("CS2 Telegram token or chat_id is missing.")
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": target_chat,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": False
        }

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        return True
                    else:
                        err = await resp.text()
                        logger.error(f"Failed to send CS2 Telegram message: {err}")
                        return False
        except Exception as e:
            logger.error(f"Error sending CS2 Telegram message: {e}")
            return False

    async def send_document(self, file_path: str, chat_id: Optional[str] = None, caption: str = "") -> bool:
        target_chat = chat_id or self.default_chat_id
        if not self.token or not target_chat or not os.path.exists(file_path):
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendDocument"
        try:
            import aiohttp
            data = aiohttp.FormData()
            data.add_field("chat_id", str(target_chat))
            if caption:
                data.add_field("caption", caption)
                data.add_field("parse_mode", "HTML")
            data.add_field(
                "document",
                open(file_path, "rb"),
                filename=os.path.basename(file_path)
            )
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=data, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    return resp.status == 200
        except Exception as e:
            logger.error(f"Error sending CS2 document: {e}")
            return False

cs2_notifier = CS2TelegramNotifier()
