import sys
sys.dont_write_bytecode = True
import os
import csv
import json
import logging
import time

# [1] ПОДГОТОВКА ПУТЕЙ И КОНФИГА
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

# [1] ПОДГОТОВКА ПУТЕЙ
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Входная таблица из папки текущего режима
INPUT_CSV = os.path.join(BASE_DIR, "data", "exports", config.RESEARCH_MODE, "tables", "production_list.csv")

# Реестры (динамически строятся из путей в config.py)
CATALOG_PATH = os.path.join(BASE_DIR, config.MASTER_CATALOG)
MIGRATIONS_FILE = os.path.join(BASE_DIR, config.MIGRATIONS_FILE)
LOG_FILE = os.path.join(BASE_DIR, "data", "logs", "init_catalog.log")

def init_catalog():
    print("Starting script: INIT_CATALOG")
    
    # Настройка логирования (Твой Золотой Стандарт)
    file_handler = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[file_handler]
    )

    logging.info("--- SCRIPT START: INIT_CATALOG ---")
    logging.info(f"Opening species source: {os.path.abspath(INPUT_CSV)}")
    
    if not os.path.exists(INPUT_CSV):
        err = f"Input CSV not found: {INPUT_CSV}"
        logging.error(err)
        print(f"[ERROR] {err}")
        return

    # Загрузка научных данных
    with open(INPUT_CSV, 'r', encoding='utf-8-sig') as f:
        csv_species = list(csv.DictReader(f, delimiter=';'))
    
    # Загрузка текущего производства (Единственный источник истины)
    old_catalog_data = []
    if os.path.exists(CATALOG_PATH):
        with open(CATALOG_PATH, 'r', encoding='utf-8') as f:
            old_catalog_data = json.load(f)

    migrations = {}
    if os.path.exists(MIGRATIONS_FILE):
        with open(MIGRATIONS_FILE, 'r', encoding='utf-8') as f:
            migrations = json.load(f)

    # Словарь старого каталога для сохранения прогресса художников
    catalog_dict = {f"{item['genus']} {item['species']}".lower(): item for item in old_catalog_data}
    
    new_catalog = []
    total_csv = len(csv_species)
    logging.info(f"Detected {total_csv} species in CSV")
    print(f"Total species to process: {total_csv}")

    # [2] ЦИКЛ СИНХРОНИЗАЦИИ (Сверху вниз)
    for i, row in enumerate(csv_species, 1):
        # Твой прогресс-бар
        sys.stdout.write(f"\rSyncing... [{i}/{total_csv}]")
        time.sleep(0.0001)
        sys.stdout.flush()

        genus = row['genus'].strip()
        species = row['species'].strip()
        sci_status = row['status'].strip().lower()
        full_name_lower = f"{genus} {species}".lower()

        # 1. Если вид уже есть в каталоге — обновляем науку, сохраняем производство
        if full_name_lower in catalog_dict:
            entry = catalog_dict[full_name_lower]
            entry['status'] = sci_status 
            new_catalog.append(entry)
            logging.info(f"{genus} {species} | {entry['m_status']} | {entry['stage']}")
        
        else:
            # 2. Если вида нет — проверяем миграции (не переехал ли он)
            old_name_found = None
            for old_full_name, new_genus_target in migrations.items():
                if new_genus_target.lower() == genus.lower():
                    if old_full_name.lower() in catalog_dict:
                        old_name_found = old_full_name.lower()
                        break
            
            if old_name_found:
                entry = catalog_dict[old_name_found]                    
                entry['genus'] = genus
                entry['species'] = species
                entry['status'] = sci_status
                new_catalog.append(entry)
                logging.info(f"{genus} {species} | {entry['m_status']} | {entry['stage']}")
            else:
                # 3. Абсолютно новый вид для системы
                logging.info(f"{genus} {species} | free | skeletal")
                entry = {
                    "genus": genus, "species": species, "status": sci_status,
                    "m_status": "free", "stage": "skeletal", "is_approved": False,
                    "s_author": "", "user": ""
                }
                new_catalog.append(entry)
                logging.info(f"{genus} {species} | free | skeletal")

    # [3] ЗАПИСЬ JSON (Строго одна строка — один динозавр)
    try:
        with open(CATALOG_PATH, 'w', encoding='utf-8') as jf:
            jf.write("[\n")
            for i, entry in enumerate(new_catalog):
                line = json.dumps(entry, ensure_ascii=False)
                comma = "," if i < len(new_catalog) - 1 else ""
                jf.write(f"    {line}{comma}\n")
            jf.write("]")
        
        sys.stdout.write("\n")
        print("Catalog syncing completed.")
        print(f"Data saved to {os.path.abspath(CATALOG_PATH)}")
        
        logging.info("Catalog generated successfully.")
        logging.info(f"Data saved to {os.path.abspath(CATALOG_PATH)}")
        logging.info("--- SCRIPT END: INIT_CATALOG ---")
        print("Script ended: INIT_CATALOG")

    except Exception as e:
        logging.error(f"Failed to save JSON: {e}")
        print(f"\n[ERROR] Failed to save JSON: {e}")

if __name__ == "__main__":
    init_catalog()