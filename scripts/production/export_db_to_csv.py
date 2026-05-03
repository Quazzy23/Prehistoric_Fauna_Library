import sys
sys.dont_write_bytecode = True
import sqlite3
import csv
import os
import logging
import time

# [1] ПОДГОТОВКА ПУТЕЙ И КОНФИГА
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

# --- ГЛОБАЛЬНЫЕ НАСТРОЙКИ И ПУТИ ---
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Путь к базе данных
DB_PATH = os.path.join(BASE_DIR, "database", config.DB_NAME)

# [!] УНИФИКАЦИЯ: Используем TABLES_DIR из конфига
DATA_ROOT  = os.path.join(BASE_DIR, config.TABLES_DIR)
OUTPUT_CSV = os.path.join(DATA_ROOT, "production_list.csv")

# Настройки логов
LOG_FILE = os.path.join(config.LOGS_DIR, "export_db_to_csv.log")

# Настройки фильтрации для тестов
USE_FILTER = False  
FILTER_PATH = os.path.join(BASE_DIR, config.CUSTOM_LISTS_DIR, "export_filter.txt")

def run_export():
    """Экспорт научных данных из базы в таблицу производства."""
    print("Starting script: EXPORT_DB_TO_CSV")

    # Инициализация папки логов
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    file_handler = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[file_handler]
    )

    logging.info("--- SCRIPT START: EXPORT_DB_TO_CSV ---")
    logging.info("Configuration loaded successfully")
    logging.info(f"Opening database source: {os.path.abspath(DB_PATH)}")
    
    if not os.path.exists(DB_PATH):
        err_msg = f"Database not found at {DB_PATH}"
        logging.error(err_msg)
        print(f"[ERROR] {err_msg}")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # [2] ЛОГИКА ФИЛЬТРАЦИИ
        if USE_FILTER and os.path.exists(FILTER_PATH):
            with open(FILTER_PATH, 'r', encoding='utf-8') as f:
                genera = [line.strip().capitalize() for line in f if line.strip()]
            
            if genera:
                placeholders = ', '.join(['?'] * len(genera))
                query = f"SELECT genus, species, status FROM {config.TABLE_SPECIES} WHERE genus IN ({placeholders}) ORDER BY genus, species"
                cursor.execute(query, genera)
                logging.info(f"Filter active: loading {len(genera)} genera from {os.path.basename(FILTER_PATH)}")
            else:
                cursor.execute(f"SELECT genus, species, status FROM {config.TABLE_SPECIES} ORDER BY genus, species")
                logging.info("Filter file is empty. Loading full database.")
        else:
            cursor.execute(f"SELECT genus, species, status FROM {config.TABLE_SPECIES} ORDER BY genus, species")
            logging.info("Filter disabled or file missing. Loading full database.")
        
        rows = cursor.fetchall()
        total_rows = len(rows)
        logging.info(f"Detected {total_rows} species records")
        
        # [3] ЗАПИСЬ CSV
        os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
        with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerow(['genus', 'species', 'status']) # Заголовок
            
            for i, row in enumerate(rows, 1):
                writer.writerow(row)
                sys.stdout.write(f"\rExporting... [{i}/{total_rows}]")
                sys.stdout.flush()
                time.sleep(0.0001)
        
        print() 
            
        # [4] ЗАВЕРШЕНИЕ
        logging.info("Export completed successfully.")
        logging.info(f"Data saved to {os.path.abspath(OUTPUT_CSV)}")
        logging.info("--- SCRIPT END: EXPORT_DB_TO_CSV ---")

        print(f"Total species exported: {total_rows}")
        print(f"Data saved to {os.path.abspath(OUTPUT_CSV)}")
        print("Script ended: EXPORT_DB_TO_CSV")

        conn.close()
        
    except Exception as e:
        error_msg = f"Database error during export: {e}"
        logging.error(error_msg)
        print(f"[ERROR] {error_msg}")

if __name__ == "__main__":
    run_export()