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
INPUT_CSV = os.path.join(BASE_DIR, config.TABLES_DIR, "production_list.csv")

# Реестры (динамически строятся из путей в config.py)
CATALOG_PATH = os.path.join(BASE_DIR, config.MASTER_CATALOG)
MIGRATIONS_FILE = os.path.join(BASE_DIR, config.MIGRATIONS_FILE)
TEMPLATE_PATH = os.path.join(BASE_DIR, "templates", "info_template.txt")

# Настройка логов (Берем путь строго из config.py)
LOG_FILE = os.path.join(config.LOGS_DIR, "init_catalog.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

def init_catalog():
    print("Starting script: INIT_CATALOG")
    import re

    # [0] ЧТЕНИЕ ШАБЛОНА ДЛЯ ОПРЕДЕЛЕНИЯ СТРУКТУРЫ
    if not os.path.exists(TEMPLATE_PATH):
        print(f"[ERROR] Template not found: {TEMPLATE_PATH}"); return
    with open(TEMPLATE_PATH, 'r', encoding='utf-8') as tf:
        template_content = tf.read()

    # Парсим ключи и дефолты из шаблона {key|default}
    fields = re.findall(r'\{(.*?)\}', template_content)
    schema_defaults = {}
    for f in fields:
        if '|' in f:
            k, d = f.split('|', 1)
            if k != "notes": schema_defaults[k] = d
        else:
            if f != "notes": schema_defaults[f] = ""

    # Технические поля для внутренней логики PFL
    INTERNAL_FIELDS = {"stage": "skeletal", "is_approved": False, "s_author": ""}

    # [1] НАСТРОЙКА ЛОГИРОВАНИЯ
    file_handler = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S', handlers=[file_handler])
    logging.info("--- SCRIPT START: INIT_CATALOG ---")
    logging.info(f"Opening species source: {os.path.abspath(INPUT_CSV)}")
    
    if not os.path.exists(INPUT_CSV):
        err = f"Input CSV not found: {INPUT_CSV}"
        logging.error(err); print(f"[ERROR] {err}"); return

    # Загрузка данных
    with open(INPUT_CSV, 'r', encoding='utf-8-sig') as f:
        csv_species = list(csv.DictReader(f, delimiter=';'))
    
    old_catalog_data = []
    if os.path.exists(CATALOG_PATH):
        with open(CATALOG_PATH, 'r', encoding='utf-8') as f: old_catalog_data = json.load(f)

    migrations = {}
    if os.path.exists(MIGRATIONS_FILE):
        with open(MIGRATIONS_FILE, 'r', encoding='utf-8') as f: migrations = json.load(f)

    catalog_dict = {f"{item['genus']} {item['species']}".lower(): item for item in old_catalog_data}
    new_catalog = []
    total_csv = len(csv_species)
    logging.info(f"Detected {total_csv} species in CSV")
    print(f"Total species to process: {total_csv}")

    # [2] ЦИКЛ СИНХРОНИЗАЦИИ
    for i, row in enumerate(csv_species, 1):
        sys.stdout.write(f"\rSyncing... [{i}/{total_csv}]"); sys.stdout.flush()

        genus, species = row['genus'].strip(), row['species'].strip()
        sci_status = row['status'].strip().lower()
        full_name_lower = f"{genus} {species}".lower()

        entry = catalog_dict.get(full_name_lower)
        is_migration = False

        if not entry:
            # Поиск в миграциях
            for old_full_name, new_genus_target in migrations.items():
                if new_genus_target.lower() == genus.lower() and old_full_name.lower() in catalog_dict:
                    entry = catalog_dict[old_full_name]
                    entry['genus'], entry['species'] = genus, species
                    logging.warning(f"[MIGRATION] {old_full_name} -> {full_name_lower}")
                    is_migration = True
                    break

        if not entry:
            # Новый вид
            entry = {"genus": genus, "species": species}
            logging.info(f"{genus} {species} | free | skeletal")
        elif not is_migration:
            # Обычный вид
            logging.info(f"{genus} {species} | {entry.get('m_status', 'free')} | {entry.get('stage', 'skeletal')}")

        # [!] СИНХРОНИЗАЦИЯ СХЕМЫ ПО ШАБЛОНУ (Strict Schema Sync)
        entry['status'] = sci_status
        
        # 1. Собираем список всех разрешенных ключей (Шаблон + Технические)
        allowed_keys = set(schema_defaults.keys()) | set(INTERNAL_FIELDS.keys()) | {"genus", "species", "status"}
        
        # 2. Создаем чистую копию записи только с разрешенными полями
        # Это автоматически удалит те поля, которых больше нет в шаблоне
        clean_entry = {k: v for k, v in entry.items() if k in allowed_keys}
        
        # 3. Добавляем недостающие поля из шаблона и заполняем дефолты
        for key, default in schema_defaults.items():
            if key not in clean_entry or clean_entry[key] in [None, "", "-"]:
                clean_entry[key] = default
        
        # 4. Добавляем недостающие технические поля
        for key, default in INTERNAL_FIELDS.items():
            if key not in clean_entry:
                clean_entry[key] = default

        new_catalog.append(clean_entry)

    # [3] ЗАПИСЬ JSON
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
        logging.info(f"Catalog synced. Total entries: {len(new_catalog)}")
        logging.info(f"Data saved to {os.path.abspath(CATALOG_PATH)}")
        logging.info("--- SCRIPT END: INIT_CATALOG ---")
        print("Script ended: INIT_CATALOG")
    except Exception as e:
        logging.error(f"Failed to save JSON: {e}")
        print(f"\n[ERROR] Failed to save JSON: {e}")

if __name__ == "__main__":
    init_catalog()