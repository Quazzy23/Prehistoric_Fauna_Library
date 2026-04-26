import sys
sys.dont_write_bytecode = True  # Сначала запрещаем
import csv
import os
import logging
import time
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

# --- ПУТИ И НАСТРОЙКИ ---
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GENERA_LIST_CSV = os.path.join(BASE_DIR, "data", "exports", "tables", "genera_list.csv")
DINO_DATA_CSV = os.path.join(BASE_DIR, "data", "exports", "tables", "dinosaurs_data.csv")
OUTPUT_CSV = os.path.join(BASE_DIR, "data", "exports", "tables", "dinosaurs_validated.csv")

# Настройка логов
LOG_DIR = os.path.join(BASE_DIR, "data", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "validate_status.log")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    filename=LOG_FILE,
    filemode='w',
    encoding='utf-8'
)

def validate_species():
    logging.info("--- SCRIPT START: VALIDATE_SPECIES_STATUS ---")
    if config:
        logging.info("Configuration loaded successfully")
    else:
        logging.error("Configuration loading failed")
    print("Starting script: VALIDATE_SPECIES_STATUS")

    # 1. ЗАГРУЗКА ИСТОЧНИКА ИСТИНЫ (Статусы родов)
    if not os.path.exists(GENERA_LIST_CSV):
        msg = f"File not found: {GENERA_LIST_CSV}"
        logging.error(msg)
        print(f"[ERROR] {msg}")
        return
    
    logging.info(f"Opening Source of Truth file: {GENERA_LIST_CSV}")
    genus_truth = {}
    with open(GENERA_LIST_CSV, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f, delimiter=';')
        for row in reader:
            if row.get('genus') and row.get('status'):
                genus_truth[row['genus'].lower()] = row['status'].lower()
    
    logging.info(f"Source of Truth: Loaded status for {len(genus_truth)} genera")

    # 2. ЗАГРУЗКА ДАННЫХ ДЛЯ ОБРАБОТКИ
    if not os.path.exists(DINO_DATA_CSV):
        msg = f"File not found: {DINO_DATA_CSV}"
        logging.error(msg)
        print(f"[ERROR] {msg}")
        return

    logging.info(f"Opening data file for validation: {DINO_DATA_CSV}")
    with open(DINO_DATA_CSV, 'r', encoding='utf-8-sig') as f:
        species_list = list(csv.DictReader(f, delimiter=';'))

    total = len(species_list)
    logging.info(f"Started validation for {total} species")
    print(f"Total species to validate: {total}")

    validated_data = []
    report_fixed = []
    report_ok = []
    report_not_found = []

    # 3. ОСНОВНОЙ ЦИКЛ СИНХРОНИЗАЦИИ
    for i, row in enumerate(species_list, 1):
        genus_name = row['genus'].strip()
        species_name = row['species'].strip()
        full_name = f"{genus_name} {species_name}"
        
        incoming_status = row['status'].lower()
        true_genus_status = genus_truth.get(genus_name.lower(), 'valid')

        # Получаем веса статусов (если статуса нет в списке, даем вес 4 - valid)
        weight_incoming = config.STATUS_WEIGHTS.get(incoming_status, 4)
        weight_truth = config.STATUS_WEIGHTS.get(true_genus_status, 4)

        # ФИНАЛЬНЫЙ ВЫБОР: Берем статус с наименьшим весом (самый "инвалидный")
        if weight_truth < weight_incoming:
            final_status = true_genus_status
        else:
            final_status = incoming_status

        # ЛОГИРОВАНИЕ
        if final_status == incoming_status:
            logging.info(f"{full_name}: OK (status: {final_status})")
            report_ok.append(f"{full_name}: OK")
        else:
            msg = f"{full_name}: FIX ({incoming_status} -> {final_status})"
            logging.warning(msg)
            report_fixed.append(msg)
            row['status'] = final_status

        validated_data.append(row)

        sys.stdout.write(f"\rValidating... [{i}/{total}]")
        sys.stdout.flush()
        time.sleep(0.0005)

    # 4. ЗАВЕРШЕНИЕ
    print() 
    print("Validation completed.")
    logging.info("Validation completed.")

    # Вывод количества изменений
    fixed_msg = f"Total statuses updated: {len(report_fixed)}"
    print(fixed_msg)
    logging.info(fixed_msg)

    # Сохранение результатов
    if validated_data:
        try:
            fieldnames = validated_data[0].keys()
            with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
                writer.writeheader()
                writer.writerows(validated_data)
            
            msg = f"Data saved to {os.path.abspath(OUTPUT_CSV)}"
            print(msg)
            logging.info(msg)
        except Exception as e:
            err_msg = f"Save failed: {e}"
            print(f"[ERROR] {err_msg}")
            logging.error(err_msg)

    print("Script ended: VALIDATE_SPECIES_STATUS")

    # ФИНАЛЬНЫЙ ОТЧЕТ В ЛОГИ
    logging.info("=== FINAL DATA AUDIT REPORT ===")
    final_audit_data = [
        ('FIXED STATUSES', report_fixed),
        ('GENERA NOT FOUND IN TRUTH', report_not_found)
    ]

    for idx, (title, items) in enumerate(final_audit_data, 1):
        logging.info(f"[{idx}] {title} ({len(items)})")
        for item in items:
            # УБИРАЕМ ОТСТУП: пишем напрямую
            logging.info(item)

    logging.info("--- SCRIPT END: VALIDATE_SPECIES_STATUS ---")

if __name__ == "__main__":
    validate_species()