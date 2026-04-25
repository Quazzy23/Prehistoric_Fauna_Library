import os
import csv
import sys
import logging

# [2] ПОДГОТОВКА ПУТЕЙ И КОНФИГА
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import metadata_utils

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LOG_FILE = os.path.join(BASE_DIR, "data", "logs", "init_model_folders.log")

# Логирование: Только в файл
file_handler = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[file_handler]
)

def create_structure():
    INPUT_CSV = os.path.join(BASE_DIR, "data", "exports", "dinosaurs_for_models.csv")
    MODELS_ROOT = os.path.join(BASE_DIR, "models")

    # [3] СТАРТОВЫЙ ОТЧЕТ В ЛОГ (Золотой стандарт)
    logging.info(f"Opening species source: {os.path.abspath(INPUT_CSV)}")

    if not os.path.exists(INPUT_CSV):
        msg_err = f"Input CSV not found: {INPUT_CSV}"
        logging.error(msg_err)
        print(f"[ERROR] {msg_err}")
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

    if total_species == 0:
        print("Total species found: 0")
        return

    # [4] СТАРТ (Консоль)
    print(f"Total species found: {total_species}")
    logging.info(f"--- INITIALIZATION STARTED ---")
    
    created_count = 0

    # [5] ЦИКЛ ОБРАБОТКИ
    for i, row in enumerate(rows, 1):
        # Консольный прогресс-бар
        sys.stdout.write(f"\rProcessing... [{i}/{total_species}]")
        sys.stdout.flush()

        genus = row['genus'].strip().capitalize()
        species = row['species'].strip().lower()
        full_name = f"{genus} {species}" if species not in ["-", "", "null"] else genus

        # Формируем путь для лога через \
        log_path_format = f"models\\{genus}\\{full_name}"
        
        species_abs_path = os.path.join(BASE_DIR, "models", genus, full_name)
        info_file_path = os.path.join(species_abs_path, f"{full_name} info.txt")

        # Лог 1: Начало анализа вида
        logging.info(f"Analyzing: {full_name}")

        try:
            is_new_info = False
            
            # Создаем структуру папок
            for sub in ["sources", "textures"]:
                p = os.path.join(species_abs_path, sub)
                if not os.path.exists(p):
                    os.makedirs(p, exist_ok=True)

            # Создаем info.txt
            if not os.path.exists(info_file_path):
                template = metadata_utils.get_info_template(genus, species)
                with open(info_file_path, 'w', encoding='utf-8') as info_f:
                    info_f.write(template)
                is_new_info = True
                created_count += 1

            # Лог 2: Результат по директории
            msg_dir = "Model directory created on path" if is_new_info else "Model directory verified on path"
            logging.info(f"{msg_dir}: {log_path_format}")

        except Exception as e:
            logging.error(f"CRITICAL ERROR for {full_name}: {e}")

    # [6] ЗАВЕРШЕНИЕ
    sys.stdout.write("\n")
    print(f"New info files initialized: {created_count}")
    
    logging.info("--- INITIALIZATION FINISHED ---")
    logging.info(f"Total new assets initialized: {created_count}")

if __name__ == "__main__":
    create_structure()