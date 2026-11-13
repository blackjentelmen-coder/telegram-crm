import asyncio
import logging
from datetime import datetime

# Настройка простого логирования для отслеживания фоновых задач
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

class BackgroundTaskManager:
    def __init__(self):
        # Словарь для отслеживания активных фоновых задач (например, рассылок)
        self.active_tasks = {}

    async def simulate_heavy_telegram_task(self, task_id: int, account_phone: str, action_type: str):
        """
        Имитация тяжелой асинхронной задачи (рассылка, парсинг),
        которая не блокирует основной потоковый сервер FastAPI.
        """
        logging.info(f"Задача #{task_id} [{action_type}] для аккаунта {account_phone} запущена.")
        
        try:
            # Имитируем сетевые задержки или запросы к Telegram API
            for i in range(3):
                await asyncio.sleep(2)
                logging.info(f"Задача #{task_id} ({account_phone}): выполнение шаг {i+1}/3...")
            
            logging.info(f"Задача #{task_id} [{action_type}] для аккаунта {account_phone} успешно завершена.")
            return {"task_id": task_id, "status": "completed", "timestamp": str(datetime.now())}
        
        except asyncio.CancelledError:
            logging.warning(f"Задача #{task_id} для аккаунта {account_phone} была отменена.")
            raise
        except Exception as e:
            logging.error(f"Ошибка в задаче #{task_id}: {str(e)}")
            return {"task_id": task_id, "status": "failed", "error": str(e)}

    def run_task_in_background(self, task_id: int, account_phone: str, action_type: str):
        """
        Метод для запуска задачи в фоновом режиме через asyncio.create_task
        """
        loop = asyncio.get_event_loop()
        task = loop.create_task(
            self.simulate_heavy_telegram_task(task_id, account_phone, action_type)
        )
        self.active_tasks[task_id] = task
        return task_id

# Глобальный менеджер задач
task_manager = BackgroundTaskManager()
