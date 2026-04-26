import sys
sys.dont_write_bytecode = True  # Сначала запрещаем
import os
import logging
import metadata_utils
import time

# [1] ПОДГОТОВКА ПУТЕЙ И КОНФИГА
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MODELS_ROOT = os.path.join(BASE_DIR, "models")
LOG_FILE = os.path.join(BASE_DIR, "data", "logs", "upgrade_info.log")

def upgrade_files():
    # КОНСОЛЬ: СТАРТ
    print("Starting script: UPGRADE_INFO_FILES")

    # Настройка логирования: Только в файл, без миллисекунд
    file_handler = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[file_handler]
    )

    logging.info("--- SCRIPT START: UPGRADE_INFO_FILES ---")
    logging.info("Configuration loaded successfully")

    if not os.path.exists(MODELS_ROOT):
        err_msg = f"Models directory not found at {MODELS_ROOT}"
        logging.error(err_msg)
        print(f"[ERROR] {err_msg}")
        return

    logging.info(f"Opening models source: {os.path.abspath(MODELS_ROOT)}")

    # [2] ПРЕДВАРИТЕЛЬНЫЙ СБОР ФАЙЛОВ
    all_info_files = []
    for genus_name in os.listdir(MODELS_ROOT):
        genus_path = os.path.join(MODELS_ROOT, genus_name)
        if not os.path.isdir(genus_path) or genus_name.startswith('.'): continue
        
        for species_folder in os.listdir(genus_path):
            species_path = os.path.join(genus_path, species_folder)
            if not os.path.isdir(species_path): continue
            
            for f in os.listdir(species_path):
                if f.endswith("info.txt"):
                    all_info_files.append({
                        'path': os.path.join(species_path, f),
                        'genus': genus_name,
                        'folder': species_folder
                    })

    total_files = len(all_info_files)
    logging.info(f"Detected {total_files} info files")

    if total_files == 0:
        print("Total info files found: 0")
        logging.info("Nothing to upgrade.")
        return

    print(f"Total info files found: {total_files}")
    
    upgraded_count = 0

    # [3] ЦИКЛ ОБРАБОТКИ
    for i, target in enumerate(all_info_files, 1):
        # Консольный прогресс-бар
        sys.stdout.write(f"\rProcessing... [{i}/{total_files}]")
        time.sleep(0.0001)
        sys.stdout.flush()

        target_file = target['path']
        
        # Считываем текущее содержимое
        try:
            with open(target_file, 'r', encoding='utf-8') as f:
                old_raw_content = f.read()
        except Exception as e:
            logging.error(f"ERROR reading {target_file}: {e}")
            continue

        # Разбираем данные
        old_data = {}
        for line in old_raw_content.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                old_data[k.strip().lower()] = v.strip()

        parts = target['folder'].split()
        genus_val = parts[0]
        species_val = " ".join(parts[1:]) if len(parts) > 1 else "-"

        # Генерируем новый контент через централизованный модуль
        new_content = metadata_utils.get_info_template(genus_val, species_val, data=old_data)

        # Лаконичный лог по стандарту
        if old_raw_content.strip() == new_content.strip():
            logging.info(f"{genus_val} {species_val}: OK")
            continue

        # Если контент отличается — записываем
        try:
            with open(target_file, 'w', encoding='utf-8') as f:
                f.write(new_content)
            logging.warning(f"{genus_val} {species_val}: UPGRADED (Metadata schema updated)")
            upgraded_count += 1
        except Exception as e:
            logging.error(f"ERROR writing {target_file}: {e}")

    # [4] ЗАВЕРШЕНИЕ (Консоль + Лог)
    sys.stdout.write("\n") 
    print("Upgrade completed.")
    print(f"Info files updated: {upgraded_count}")
    
    logging.info("Upgrade completed successfully.")
    logging.info(f"Total files successfully updated: {upgraded_count}")
    logging.info("--- SCRIPT END: UPGRADE_INFO_FILES ---")
    print("Script ended: UPGRADE_INFO_FILES")

if __name__ == "__main__":
    upgrade_files()