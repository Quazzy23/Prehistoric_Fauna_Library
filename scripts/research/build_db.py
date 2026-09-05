import sys
sys.dont_write_bytecode = True

import sqlite3
import csv
import os
import re
import logging
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

# --- ПУТИ И НАСТРОЙКИ ---
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_ROOT = os.path.join(BASE_DIR, config.TABLES_DIR)

INPUT_CSV = os.path.join(DATA_ROOT, "final_fauna.csv")
GEO_CSV = os.path.join(DATA_ROOT, "geochronology_ref.csv")
CLASSIFICATION_CSV = os.path.join(DATA_ROOT, "taxonomic_tree.csv")

DB_DIR = os.path.join(BASE_DIR, "database")
DB_FILE = os.path.join(DB_DIR, config.DB_NAME)

SQL_DIR = os.path.join(DB_DIR, "sql")
SQL_FILE = os.path.join(SQL_DIR, "queries.sql")
T_SQL = os.path.join(BASE_DIR, "templates", "queries_template.sql")

LOG_FILE = os.path.join(config.LOGS_DIR, "build_db.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

MISSING_VAL = "-"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    filename=LOG_FILE,
    filemode='w',
    encoding='utf-8'
)

def build_database():
    logging.info("--- SCRIPT START: BUILD_DB ---")
    if config:
        logging.info("Configuration loaded successfully")
    else:
        logging.error("Configuration loading failed")
    
    if config.BRIEF_CONSOLE:
        print("BUILD_DB...", end=" ", flush=True)
    else:
        print("Starting script: BUILD_DB")

    # 1. ПРЕДВАРИТЕЛЬНЫЙ ПОДСЧЕТ И ЗАГРУЗКА ДАННЫХ
    data_geo, data_taxo, data_species = [], [], []
    
    try:
        # Геохронология
        if os.path.exists(GEO_CSV):
            logging.info(f"Opening geochronology source: {GEO_CSV}")
            with open(GEO_CSV, 'r', encoding='utf-8-sig') as f:
                data_geo = list(csv.DictReader(f, delimiter=';'))
                logging.info(f"Detected {len(data_geo)} geo units")
        
        # Таксономия
        if os.path.exists(CLASSIFICATION_CSV):
            logging.info(f"Opening taxonomy source: {CLASSIFICATION_CSV}")
            with open(CLASSIFICATION_CSV, 'r', encoding='utf-8-sig') as f:
                data_taxo = list(csv.reader(f, delimiter=';'))
                logging.info(f"Detected {len(data_taxo)-1} taxonomy branches")

        # Виды (с сортировкой)
        if os.path.exists(INPUT_CSV):
            logging.info(f"Opening species source: {INPUT_CSV}")
            with open(INPUT_CSV, 'r', encoding='utf-8-sig') as f:
                raw_species = list(csv.DictReader(f, delimiter=';'))
                data_species = sorted(raw_species, key=lambda x: (
                    x['genus'].lower(),
                    0 if x.get('is_type') == 'True' else 1,
                    int(x['year']) if x.get('year') and str(x['year']).isdigit() else 9999,
                    x['species'].lower()
                ))
                logging.info(f"Detected {len(data_species)} species records (sorted scientifically)")
    except Exception as e:
        msg = f"Failed to read source CSV files: {e}"
        print(f"[ERROR] {msg}")
        logging.error(msg)
        return

    n_species = len(data_species)
    n_geo = len(data_geo)
    n_taxo = len(data_taxo) - 1 if len(data_taxo) > 0 else 0
    total_items = n_species + n_geo + n_taxo

    if not config.BRIEF_CONSOLE:
        print(f"Total species/geo units/taxa branches: {n_species}/{n_geo}/{n_taxo}")
    logging.info(f"Summary total to import: {total_items} items")

    # 2. ПОДГОТОВКА БД И ТАБЛИЦ
    if not os.path.exists(DB_DIR): 
        os.makedirs(DB_DIR, exist_ok=True)
        logging.info(f"Created directory: {DB_DIR}")

    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        logging.info(f"Connected to SQLite: {DB_FILE}")
        
        # [!] 3. ТОЧЕЧНАЯ ОЧИСТКА ТОЛЬКО ТАБЛИЦ ТЕКУЩЕГО РЕЖИМА
        # Удаляем ТОЛЬКО таблицы текущей группы (например, dinosaurs и dinosaurs_taxonomy),
        # не трогая таблицы других животных (ichthyosaurs, pterosaurs и др.)
        logging.info(f"Refreshing tables for mode [{config.RESEARCH_MODE}]...")
        
        mode_tables = [config.TABLE_SPECIES, config.TABLE_TAXONOMY]
        for t_name in mode_tables:
            cursor.execute(f"DROP TABLE IF EXISTS [{t_name}]")
            try:
                cursor.execute("DELETE FROM sqlite_sequence WHERE name = ?", (t_name,))
            except:
                pass
            logging.info(f"Dropped existing mode table: {t_name}")
            
        conn.commit()
        
        # Создаем таблицу видов текущего режима
        logging.info(f"Creating table: {config.TABLE_SPECIES}")
        cursor.execute(f"""
            CREATE TABLE [{config.TABLE_SPECIES}] (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                genus TEXT, species TEXT, is_type BOOLEAN, is_extant BOOLEAN, status TEXT,
                clade TEXT, stage TEXT, age_ma TEXT, author TEXT, year INTEGER
            )""")

        # Таблица геохронологии (общая для всех, пересоздаем безопасно)
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS [{config.TABLE_GEOLOGY}] (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                eon TEXT, era TEXT, period TEXT, epoch TEXT, stage TEXT, start_ma REAL, uncertainty REAL
            )""")
        # Очищаем старые гео-записи, чтобы не дублировать
        cursor.execute(f"DELETE FROM [{config.TABLE_GEOLOGY}]")
        try:
            cursor.execute("DELETE FROM sqlite_sequence WHERE name = ?", (config.TABLE_GEOLOGY,))
        except:
            pass

        # Таблица таксономии текущего режима
        if len(data_taxo) > 0:
            logging.info(f"Creating table: {config.TABLE_TAXONOMY}")
            cursor.execute(f"CREATE TABLE [{config.TABLE_TAXONOMY}] (clade TEXT PRIMARY KEY, path TEXT)")
        
        conn.commit()
    except Exception as e:
        msg = f"Database structure creation failed: {e}"
        print(f"[ERROR] {msg}")
        logging.error(msg)
        return

    # 4. ИМПОРТ ДАННЫХ
    current_progress = 0
    errors = []

    # А) Геохронология
    logging.info("Starting Geochronology import...")
    try:
        for row in data_geo:
            start_ma = float(row['start_ma']) if row['start_ma'] != MISSING_VAL else None
            uncertainty = float(row['uncertainty']) if row['uncertainty'] != MISSING_VAL else None
            cursor.execute(f"""
                INSERT INTO [{config.TABLE_GEOLOGY}] 
                (eon, era, period, epoch, stage, start_ma, uncertainty) 
                VALUES (?,?,?,?,?,?,?)""",
                (row['eon'], row['era'], row['period'], row['epoch'], row['stage'], start_ma, uncertainty))
            current_progress += 1
            if not config.BRIEF_CONSOLE:
                sys.stdout.write(f"\rImporting... [{current_progress}/{total_items}]")
                sys.stdout.flush()
        logging.info(f"Geochronology table: OK (Imported {n_geo} units)")
    except Exception as e: 
        errors.append(f"Geochronology import failed: {e}")

    # Б) Таксономия
    logging.info("Starting Taxonomy import...")
    try:
        if len(data_taxo) > 1:
            for row in data_taxo[1:]:
                clade_name = row[0]
                ancestors = row[2:]
                
                unique_chain = []
                for node in ancestors:
                    if node and node != clade_name and node not in unique_chain:
                        unique_chain.append(node)
                
                hierarchy_path = "|" + "|".join(unique_chain) + "|" + clade_name + "|"
                
                cursor.execute(f"INSERT OR REPLACE INTO [{config.TABLE_TAXONOMY}] (clade, path) VALUES (?, ?)",
                             (clade_name, hierarchy_path))
                
                current_progress += 1
                if not config.BRIEF_CONSOLE:
                    sys.stdout.write(f"\rImporting... [{current_progress}/{total_items}]")
                    sys.stdout.flush()
        logging.info(f"Taxonomy table: OK (Imported {n_taxo} branches)")
    except Exception as e: 
        errors.append(f"Taxonomy import failed: {e}")

    # В) Виды
    logging.info("Starting Species import...")
    try:
        for row in data_species:
            is_type_val = row.get('is_type', 'False')
            is_extant_val = row.get('is_extant', 'False') 
            
            raw_y = row.get('year', '')
            clean_year = re.sub(r'\D', '', raw_y) if raw_y else None
            year_val = int(clean_year) if clean_year else None

            cursor.execute(f"""
                INSERT INTO [{config.TABLE_SPECIES}] 
                (genus, species, is_type, is_extant, status, clade, stage, age_ma, author, year) 
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (row['genus'], row['species'], is_type_val, is_extant_val, row['status'], 
                 row['clade'], row['stage'], row['age'], row['author'], year_val))
            
            current_progress += 1
            if not config.BRIEF_CONSOLE:
                sys.stdout.write(f"\rImporting... [{current_progress}/{total_items}]")
                sys.stdout.flush()
                time.sleep(0.0001)
        logging.info(f"Species table: OK (Imported {n_species} records)")
    except Exception as e: 
        errors.append(f"Species import failed: {e}")

    conn.commit()
    conn.close()

    if not config.BRIEF_CONSOLE:
        print()
    if not errors:
        if not config.BRIEF_CONSOLE:
            print("Import completed.")
        logging.info("Import completed successfully.")
    else:
        for err in errors:
            print(f"[ERROR] {err}")
            logging.error(err)

    # 5. SQL ФАЙЛ (из шаблона)
    if os.path.exists(T_SQL):
        try:
            if not os.path.exists(SQL_DIR): os.makedirs(SQL_DIR, exist_ok=True)
            with open(T_SQL, "r", encoding="utf-8") as f:
                sql_template = f.read()
            
            sql_content = sql_template.format(table_species=config.TABLE_SPECIES)
            with open(SQL_FILE, 'w', encoding='utf-8') as f: 
                f.write(sql_content)
            logging.info(f"SQL queries template created from file.")
        except Exception as e:
            logging.error(f"SQL template failed: {e}")

    logging.info(f"Database saved to {DB_FILE}")
    
    if config.BRIEF_CONSOLE:
        print(f"Database saved ({total_items} records)")
    else:
        print(f"Database saved to {DB_FILE}")
        print(f"SQL queries saved to {SQL_FILE}")
        print("Script ended: BUILD_DB")
    logging.info(f"--- SCRIPT END: BUILD_DB ---")

if __name__ == "__main__":
    build_database()