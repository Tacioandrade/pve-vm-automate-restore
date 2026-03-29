import requests
import logging
from tenacity import retry, wait_exponential, stop_after_attempt
from pbs_restore.exceptions import TelegramError

logger = logging.getLogger(__name__)

@retry(
    wait=wait_exponential(multiplier=1, min=4, max=10),
    stop=stop_after_attempt(3),
    reraise=True
)
def send_telegram_message(token, chat_id, message):

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Falha ao enviar mensagem no Telegram: {e}")
        if e.response is not None:
            logger.debug(f"Resposta da API do Telegram: {e.response.text}")
        raise TelegramError(f"Falha ao enviar mensagem via Telegram: {e}")
