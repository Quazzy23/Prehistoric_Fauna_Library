import sys
sys.dont_write_bytecode = True
import os
import json
import logging
import time

# [1] ПОДГОТОВКА ПУТЕЙ И КОНФИГА
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MODELS_ROOT = os.path.join(BASE_DIR, "models")
OUTPUT_JSON = os.path.join(BASE_DIR, "data", "exports", "species_catalog.json")
LOG_FILE = os.path.join(BASE_DIR, "data", "logs", "generate_catalog.log")

def get_production_stage(data):
    """Определяет текущую стадию на основе заполненности полей."""
    stages = ["skeletal", "mesh", "texture", "rig"]
    for s in stages:
        author = data.get(s, "")
        approved = data.get(f"{s}_approved_by", "")
        if not author or not approved:
            return s
    return "finished"

def generate_catalog():
    print("Starting script: GENERATE_ARTIST_CATALOG")

    # Настройка логирования
    file_handler = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[file_handler]
    )

    logging.info("--- SCRIPT START: GENERATE_ARTIST_CATALOG ---")
    logging.info(f"Opening models source: {os.path.abspath(MODELS_ROOT)}")

    if not os.path.exists(MODELS_ROOT):
        print("[ERROR] Models directory not found.")
        return

    # [2] СБОР ФАЙЛОВ (Логика под Genus/Species)
    all_info_files = []
    for genus_name in os.listdir(MODELS_ROOT):
        genus_path = os.path.join(MODELS_ROOT, genus_name)
        if not os.path.isdir(genus_path) or genus_name.startswith('.'): continue
        
        for species_folder in os.listdir(genus_path):
            species_path = os.path.join(genus_path, species_folder)
            if not os.path.isdir(species_path): continue
            
            for f in os.listdir(species_path):
                if f.endswith("info.txt"):
                    all_info_files.append(os.path.join(species_path, f))

    total_files = len(all_info_files)
    logging.info(f"Detected {total_files} info files")
    print(f"Total info files found: {total_files}")
    
    catalog = []

    # [3] ЦИКЛ ПАРСИНГА
    for i, file_path in enumerate(all_info_files, 1):
        sys.stdout.write(f"\rScanning... [{i}/{total_files}]")
        time.sleep(0.0001)
        sys.stdout.flush()

        data = {}
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if ":" in line:
                        k, v = line.split(":", 1)
                        data[k.strip().lower()] = v.strip()

            genus = data.get('genus', 'unknown')
            species = data.get('species', 'unknown')
            sci_status = data.get('status', 'empty')
            m_status = data.get('model_status', 'free')
            stage = get_production_stage(data)
            
            claimed_by = data.get('claimed_by', '')
            display_claimed_by = "" if m_status.lower() == 'review' else claimed_by

            catalog.append({
                "genus": genus,
                "species": species,
                "status": sci_status,   # Коротко: статус науки
                "m_status": m_status,   # Коротко: статус модели
                "stage": stage,         # Коротко: стадия
                "user": display_claimed_by # Коротко: кто занял
            })
            logging.info(f"{genus} {species} | {m_status} | {stage}")

        except Exception as e:
            logging.error(f"ERROR parsing {file_path}: {e}")

    # [4] ЗАПИСЬ
    try:
        with open(OUTPUT_JSON, 'w', encoding='utf-8') as jf:
            jf.write("[\n")
            for i, entry in enumerate(catalog):
                # Превращаем один словарь в одну строку
                line = json.dumps(entry, ensure_ascii=False)
                # Добавляем запятую всем, кроме последнего
                comma = "," if i < len(catalog) - 1 else ""
                jf.write(f"    {line}{comma}\n")
            jf.write("]")
        
        sys.stdout.write("\n")
        print("Catalog generation completed.")
        print(f"Data saved to {os.path.abspath(OUTPUT_JSON)}")
        
        logging.info("Catalog generated successfully.")
        logging.info(f"Data saved to {os.path.abspath(OUTPUT_JSON)}") # <--- ДОБАВИЛ ЭТУ СТРОКУ
        logging.info("--- SCRIPT END: GENERATE_ARTIST_CATALOG ---")
        print("Script ended: GENERATE_ARTIST_CATALOG")
        
    except Exception as e:
        print(f"\n[ERROR] Failed to save JSON: {e}")

if __name__ == "__main__":
    generate_catalog()