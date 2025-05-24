from openai import OpenAI
import os
import logging
from datetime import datetime


# Настройка логгера
logging.basicConfig(
    level=logging.ERROR,  # Уровень ERROR и выше
    format="%(asctime)s - %(levelname)s - [%(module)s] %(message)s",
    handlers=[
        logging.FileHandler(f"logs/bot_{datetime.now().date()}.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class AIService:
    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv("KEY"),
            base_url=os.getenv("URL"),
            timeout=20.0,
            max_retries=3
        )

    def analyze_comment(self, comment):
        try:
            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "system",
                        "content": "Оцени тональность комментария. Ответь ТОЛЬКО одним словом: 'хороший' или 'плохой'."
                    },
                    {
                        "role": "user",
                        "content": comment
                    }
                ],
                temperature=0.0  # Для минимизации случайности
            )
            return response.choices[0].message.content.strip().lower()
        except Exception as e:
            logger.error(f"Ошибка анализа комментария: {e}", exc_info=True)
            return "нейтральный"