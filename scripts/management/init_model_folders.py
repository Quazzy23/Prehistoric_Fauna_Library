import sys
sys.dont_write_bytecode = True
import os
import csv
import logging

# [1] ПОДГОТОВКА ПУТЕЙ И КОНФИГА
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import metadata_utils

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MODELS_ROOT = os.path.join(BASE_DIR, "models")
LOG_FILE = os.path.join(BASE_DIR, "data", "logs", "init_model_folders.log")

def create_structure():
    print("Starting script: INIT_MODEL_FOLDERS")
    
    INPUT_CSV = os.path.join(BASE_DIR, "data", "exports", "tables", "dinosaurs_for_models.csv")

    # Настройка логирования: Только в файл
    file_handler = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[file_handler]
    )

    logging.info("--- SCRIPT START: INIT_MODEL_FOLDERS ---")
    logging.info("Configuration loaded successfully")
    logging.info(f"Opening species source: {os.path.abspath(INPUT_CSV)}")

    if not os.path.exists(INPUT_CSV):
        err_msg = f"Input CSV not found: {INPUT_CSV}"
        logging.error(err_msg)
        print(f"[ERROR] {err_msg}")
        return

    try:
        with open(INPUT_CSV, 'r', encoding='utf-8-sig') as f:
            rows = list(csv.DictReader(f, delimiter=';'))
    except Exception as e:
        logging.error(f"Failed to read CSV: {e}")
        print(f"[ERROR] Failed to read CSV. Check logs.")
        return

    total_species = len(rows)
    logging.info(f"Detected {total_species} species")
    print(f"Total species to process: {total_species}")

    created_count = 0

    # [2] ЦИКЛ ОБРАБОТКИ
    for i, row in enumerate(rows, 1):
        # Консольный прогресс-бар
        sys.stdout.write(f"\rProcessing... [{i}/{total_species}]")
        sys.stdout.flush()

        genus = row['genus'].strip().capitalize()
        species = row['species'].strip().lower()
        status = row['status'].strip().lower()
        
        full_name = f"{genus} {species}" if species not in ["-", "", "null"] else genus

        # Пути (Классическая структура: models/Genus/Species)
        species_abs_path = os.path.join(MODELS_ROOT, genus, full_name)
        log_path_format = f"models\\{genus}\\{full_name}"
        info_file_path = os.path.join(species_abs_path, f"{full_name} info.txt")

        try:
            # Создание структуры папок
            is_new_dir = False
            for sub in ["sources", "textures", "skeletal"]:
                p = os.path.join(species_abs_path, sub)
                if not os.path.exists(p):
                    os.makedirs(p, exist_ok=True)
                    is_new_dir = True

            # Создание info.txt
            is_new_info = False
            if not os.path.exists(info_file_path):
                template = metadata_utils.get_info_template(genus, species, status)
                with open(info_file_path, 'w', encoding='utf-8') as info_f:
                    info_f.write(template)
                is_new_info = True
                created_count += 1

            # Лаконичный лог по стандарту
            if is_new_dir or is_new_info:
                logging.warning(f"{full_name}: CREATED (Path: {log_path_format})")
            else:
                logging.info(f"{full_name}: OK")

        except Exception as e:
            logging.error(f"CRITICAL ERROR for {full_name}: {e}")

    # [3] ЗАВЕРШЕНИЕ
    sys.stdout.write("\n")
    print("Initialization completed.")
    print(f"New info files initialized: {created_count}")
    
    logging.info("Initialization completed successfully.")
    logging.info("--- SCRIPT END: INIT_MODEL_FOLDERS ---")
    print("Script ended: INIT_MODEL_FOLDERS")

if __name__ == "__main__":
    create_structure()