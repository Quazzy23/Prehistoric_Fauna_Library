import sys
sys.dont_write_bytecode = True  # Сначала запрещаем
import os
import subprocess
import logging
from datetime import datetime

# 2. ОПРЕДЕЛЯЕМ ПУТИ ДЛЯ ЛОГОВ (Чтобы настроить их ДО импорта конфига)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "data", "logs"))
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "main_pipeline.log")

# 3. ИНИЦИАЛИЗИРУЕМ ЛОГИРОВАНИЕ НЕМЕДЛЕННО
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    filename=LOG_FILE,
    filemode='w',
    encoding='utf-8'
)

# 4. БЕЗОПАСНЫЙ ИМПОРТ CONFIG
logging.info("--- SCRIPT START: MAIN_PIPELINE ---")
try:
    import config
    logging.info("Configuration loaded successfully")
except ImportError:
    msg = "CRITICAL ERROR: config.py not found in /scripts/ folder! Pipeline cannot start."
    logging.critical(msg)
    print(f"\n[ERROR] {msg}")
    sys.exit(1) # Выходим с кодом ошибки

# 5. НАСТРОЙКА ПУТЕЙ И ПАЙПЛАЙНА
SCRIPTS_DIR = os.path.join(BASE_DIR, "core")

PIPELINE = [
    "collect_genera_list.py",      # 1. Список родов
    "fetch_geochronology.py",      # 2. Шкала ICS
    "fetch_dino_details.py",       # 3. Парсинг Википедии
    "validate_species_status.py",  # 4. Валидация статусов
    "sync_dino_stages.py",         # 5. Синхронизация времени
    "audit_tool.py",               # 6. ФИНАЛЬНЫЙ ИНСПЕКТОР (Новое место)
    "build_database.py"            # 7. Заливка в SQLite
]

def run_script(script_name):
    """Запускает скрипт и транслирует его вывод в консоль в реальном времени."""
    script_path = os.path.join(SCRIPTS_DIR, script_name)
    
    if not os.path.exists(script_path):
        msg = f"Script not found: {script_name}"
        logging.error(msg)
        print(f"[ERROR] {msg}")
        return False

    logging.info(f">>> Executing: {script_name}")
    
    # Запускаем скрипт как отдельный процесс
    # Мы не перехватываем stdout, чтобы он шел напрямую в текущую консоль
    # Это позволит сохранить все \r и цвета.
    result = subprocess.call([sys.executable, script_path])
    
    if result == 0:
        logging.info(f"<<< Successfully finished: {script_name}")
        return True
    else:
        logging.error(f"!!! Failed: {script_name} (Exit code: {result})")
        return False

def main():
    logging.info("--- SCRIPT START: MAIN_PIPELINE (v4.0) ---")
    print("Starting script: MAIN_PIPELINE")
    if not config.BRIEF_CONSOLE:
        print()

    start_time = datetime.now()
    success_count = 0

    for script in PIPELINE:
        # Запускаем очередной скрипт из цепочки
        if run_script(script):
            success_count += 1
            # Разделитель строк нужен только в полном режиме
            if not config.BRIEF_CONSOLE:
                print()
        else:
            print(f"\n[CRITICAL ERROR] Pipeline stopped at {script}")
            logging.critical(f"Pipeline stopped at {script}")
            break

    # Итоговое время
    duration = datetime.now() - start_time
    
    print("Script ended: MAIN_PIPELINE")
    
    # Финальные логи
    summary = f"Pipeline execution finished. Duration: {duration}. Scripts successful: {success_count}/{len(PIPELINE)}"
    logging.info(summary)
    logging.info("--- SCRIPT END: MAIN_PIPELINE ---")

if __name__ == "__main__":
    main()