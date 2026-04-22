import os
import sys
import logging
import metadata_utils

# [1] ПУТИ И КОНФИГ
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LOG_FILE = os.path.join(BASE_DIR, "data", "logs", "upgrade_info.log")

# Настройка логирования: СТРОГО в файл
file_handler = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[file_handler]
)

def upgrade_files():
    MODELS_ROOT = os.path.join(BASE_DIR, "models")
    if not os.path.exists(MODELS_ROOT):
        print("[ERROR] Models directory not found.")
        return

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
    if total_files == 0:
        print("Total files found: 0")
        return

    # [3] СТАРТОВЫЙ ОТЧЕТ (КОНСОЛЬ + ЛОГ)
    msg_start = f"Total files found: {total_files}"
    print(msg_start)
    logging.info("--- UPGRADE SESSION STARTED ---")
    logging.info(msg_start)
    
    upgraded_count = 0

    # [4] ЦИКЛ ОБРАБОТКИ
    for i, target in enumerate(all_info_files, 1):
        sys.stdout.write(f"\rProcessing... [{i}/{total_files}]")
        sys.stdout.flush()

        target_file = target['path']
        
        # Считываем текущее содержимое целиком для сравнения
        try:
            with open(target_file, 'r', encoding='utf-8') as f:
                old_raw_content = f.read()
        except Exception as e:
            logging.error(f"Failed to read {target_file}: {e}")
            continue

        # Разбираем данные из старого контента
        old_data = {}
        for line in old_raw_content.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                old_data[k.strip().lower()] = v.strip()

        # Подготовка данных для нового шаблона
        parts = target['folder'].split()
        genus_val = parts[0]
        species_val = " ".join(parts[1:]) if len(parts) > 1 else "-"

        def clean_v(key):
            val = old_data.get(key, "")
            return "" if val in ["-", "None", None, ""] else val

        mesh_val = clean_v('mesh') if clean_v('mesh') else clean_v('model')
        status_val = clean_v('status') if clean_v('status') else "free"

        new_content = metadata_utils.get_info_template(genus_val, species_val, data=old_data)

        # СРАВНЕНИЕ: НУЖНО ЛИ ОБНОВЛЕНИЕ?
        if old_raw_content.strip() == new_content.strip():
            logging.info(f"SKIPPED: {genus_val} {species_val} (Already up to date)")
            continue

        # Если контент отличается — записываем
        try:
            with open(target_file, 'w', encoding='utf-8') as f:
                f.write(new_content)
            logging.info(f"UPGRADED: {genus_val} | {species_val}")
            upgraded_count += 1
        except Exception as e:
            logging.error(f"Failed to write {target_file}: {e}")

    # [6] ФИНАЛЬНЫЙ ВЫВОД
    sys.stdout.write("\n") 
    print(f"Info files updated: {upgraded_count}")
    
    logging.info("--- UPGRADE SESSION FINISHED ---")
    logging.info(f"Total files successfully updated: {upgraded_count}")
    
    msg_end = f"Upgrade report saved to {os.path.abspath(LOG_FILE)}"
    print(msg_end)
    logging.info(msg_end)

if __name__ == "__main__":
    upgrade_files()