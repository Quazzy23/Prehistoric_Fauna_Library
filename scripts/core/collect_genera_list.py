import sys
sys.dont_write_bytecode = True  # Сначала запрещаем
import requests
import os
import re
import time
import logging
import csv
# Добавляем путь к папке scripts, чтобы увидеть config.py
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
sys.dont_write_bytecode = True

# --- ПУТИ И НАСТРОЙКИ ---
WIKI_LIST_URL = config.WIKI_LIST_URL
USER_EMAIL = config.USER_EMAIL

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
# Новый путь для CSV
GENERA_CSV = os.path.join(BASE_DIR, "data", "exports", "genera_list.csv")
CUSTOM_DIR = os.path.join(BASE_DIR, "data", "custom_lists")

# Настройка логов
LOG_DIR = os.path.join(BASE_DIR, "data", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "collect_genera.log")

MISSING_VAL = "-"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    filename=LOG_FILE,
    filemode='w',
    encoding='utf-8'
)

HEADERS = {
    'User-Agent': f'PrehistoricFaunaLibraryCollector/1.0 (mailto:{USER_EMAIL})'
}

def get_all_statuses(text):
    """Находит все возможные статусы в пояснении."""
    text = text.lower().strip()
    if not text:
        return ["valid"]

    found_matches = []

    # 2. Приоритет: Технические ошибки (Preoccupied)
    if "preoccupied" in text:
        found_matches.append("preoccupied")

    # 3. Приоритет: Синонимы
    if "synonym" in text or "now known as" in text:
        found_matches.append("synonym")

    # 4. Приоритет: Nomen nudum
    if "nomen nudum" in text or "nudum" in text:
        found_matches.append("nudum")

    # 5. Приоритет: Dubious / Chimaera
    if "dubium" in text or "doubtful" in text:
        found_matches.append("dubious")
    if "chimaera" in text:
        found_matches.append("chimaera")

    if not found_matches:
        return ["valid"]
        
    return found_matches

def collect_genera():
    logging.info("--- SCRIPT START: COLLECT_GENERA_LIST ---")
    if config:
        logging.info("Configuration loaded successfully")
    else:
        logging.error("Configuration loading failed")
    print("Starting script: COLLECT_GENERA_LIST")

    # Проверка и создание папки custom_lists
    if config.CREATE_CUSTOM_LIST_DIR:
        if not os.path.exists(CUSTOM_DIR):
            try:
                os.makedirs(CUSTOM_DIR, exist_ok=True)
                logging.info(f"Created custom lists directory: {CUSTOM_DIR}")
            except Exception as e:
                logging.error(f"Failed to create directory {CUSTOM_DIR}: {e}")
        else:
            logging.info(f"Custom lists directory already exists: {CUSTOM_DIR}")

    logging.info(f"Connecting to: {WIKI_LIST_URL}")
    
    try:
        response = requests.get(WIKI_LIST_URL, headers=HEADERS, timeout=15)
        response.raise_for_status()
        full_html = response.text
        total_bytes = len(response.content)
        logging.info("Successfully connected to Wikipedia")
    except Exception as e:
        msg = f"Connection error: {e}"
        logging.error(msg)
        print(f"[ERROR] {msg}")
        return

    # Обрезка страницы
    start_pos = full_html.find('id="A"')
    end_pos = full_html.find('id="See_also"')
    content_chunk = full_html[start_pos:end_pos] if start_pos != -1 and end_pos != -1 else full_html

    # Разделение по <li>
    li_blocks = content_chunk.split('<li>')
    # Убираем пустые блоки
    li_blocks = [b for b in li_blocks if b.strip()]
    
    total_blocks = len(li_blocks)
    logging.info(f"Detected {total_blocks} potential entries in A-Z list")
    logging.info(f"Started processing {total_blocks} blocks")
    
    genera_data = [] # Список словарей для CSV
    excluded_log = [] 
    # Списки для детального аудита
    report_synonyms, report_nudums, report_reclassified, report_preoccupied, report_others = [], [], [], [], []
    
    excluded_keywords = {"Contents", "Dinosaur", "List", "Wikipedia", "The", "From", "Category", "File", "Portal", "Special"}
    pattern = r'(?:<i>|\")(?:<a[^>]*>)?([A-Z][a-z]+)'
    
    genera_data = [] # Список словарей для CSV
    excluded_log = [] 

    # Списки для детального аудита
    report_duplicates = [] # Заменили excluded_log на это
    report_synonyms = []
    report_nudums = []
    report_preoccupied = []
    report_others = [] 

    # Счетчики для консоли
    c_dup, c_syn, c_nud, c_pre, c_oth = 0, 0, 0, 0, 0
    
    for block in li_blocks:
        if not block.strip(): continue
            
        # Режем блок при первом же признаке конца строки или начала нового элемента (картинки, списка)
        actual_content = re.split(r'</li>|</ul|<figure|<ul|<h|<div|<p', block, flags=re.IGNORECASE)[0]
        match = re.search(pattern, actual_content)
        
        if match:
            name = match.group(1)
            full_text = re.sub(r'<[^>]*>', '', actual_content)
            full_text = re.sub(r'\[\d+\]', '', full_text).strip()
            text_parts = re.split(r'[-–—]', full_text, maxsplit=1)
            description = text_parts[1].strip() if len(text_parts) > 1 else MISSING_VAL
            
            # Получаем ВСЕ найденные статусы
            potential_statuses = get_all_statuses(description)
            
            # Проверяем уверенность (один раз для всех)
            uncertainty_markers = ["possible", "possibly", "probable", "probably", "likely", "perhaps", "?", "may be"]
            is_uncertain = any(marker in description.lower() for marker in uncertainty_markers)

            # 1. Фильтры (мусор и дубликаты)
            if name in excluded_keywords or len(name) < 2:
                msg = f"{name}: DUPLICATE"
                logging.warning(msg)
                report_duplicates.append(msg)
                c_dup += 1 # Считаем дубликат
                continue

            if any(d['genus'] == name for d in genera_data):
                msg = f"{name}: DUPLICATE"
                logging.warning(msg)
                report_duplicates.append(msg)
                c_dup += 1 # Считаем дубликат
                continue

            # 2. ЛОГИРОВАНИЕ И ОБРАБОТКА СТАТУСОВ
            final_status = "valid"
            if potential_statuses != ["valid"]:
                status_prefix = "POSSIBLE " if is_uncertain else ""
                
                if len(potential_statuses) > 1:
                    for ps in potential_statuses:
                        display_ps = ps.replace("excluded", "reclassified").upper()
                        logging.warning(f"{name}: {status_prefix}{display_ps} ({description[:70]}...)")
                    
                    best_status = potential_statuses[0]
                    final_status = f"possible {best_status}" if is_uncertain else best_status
                    display_final = final_status.replace("excluded", "reclassified").upper()
                    logging.info(f"{name}: DECISION (Chosen: {display_final})")
                
                else:
                    best_status = potential_statuses[0]
                    final_status = f"possible {best_status}" if is_uncertain else best_status
                    display_final = final_status.replace("excluded", "reclassified").upper()
                    logging.warning(f"{name}: {display_final} ({description[:70]}...)")
            else:
                logging.info(f"{name}: OK")

            # 3. Сохранение данных (Только ОДНА запись на род)
            genera_data.append({"genus": name, "status": final_status})
            
            # 4. Наполнение списков аудита и СЧЕТЧИКИ
            log_entry = f"{name}: {final_status.upper()}"
            if "synonym" in final_status: 
                report_synonyms.append(log_entry)
                c_syn += 1
            elif "nudum" in final_status: 
                report_nudums.append(log_entry)
                c_nud += 1
            elif "preoccupied" in final_status: 
                report_preoccupied.append(log_entry)
                c_pre += 1
            elif final_status != "valid": 
                report_others.append(log_entry)
                c_oth += 1
            
            sys.stdout.write(f"\rDiscovering... [{len(genera_data)}]")
            sys.stdout.flush()
            time.sleep(0.0005)

    # Завершение парсинга (просто переходим на новую строку, сохраняя счетчик)
    print() 
    print("Discovery completed.")

    logging.info("Discovery completed.")

    # --- СОХРАНЕНИЕ РЕЗУЛЬТАТОВ ---
    if genera_data:
        os.makedirs(os.path.dirname(GENERA_CSV), exist_ok=True)
        
        try:
            # Сохраняем только таблицу статусов
            with open(GENERA_CSV, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=["genus", "status"], delimiter=';', extrasaction='ignore')
                writer.writeheader()
                writer.writerows(genera_data)
            
            # Статистика
            size_mb = total_bytes / (1024 * 1024)
            size_report = f"Total data downloaded: {size_mb:.2f} MB"
            count_msg = f"Total unique genera found: {len(genera_data)}"
            # Вывод статистики в консоль через табуляцию
            print(f"DUPLICATES: {c_dup}\tSYNONYMS: {c_syn}\tNUDUM: {c_nud}\tPREOCCUPIED: {c_pre}\tDUBIOUS/СHIMAERA: {c_oth}")
            path_msg = f"Status data saved to {os.path.abspath(GENERA_CSV)}"

            print(size_report)
            logging.info(size_report)
            print(count_msg)
            logging.info(count_msg)
            print(path_msg)
            logging.info(path_msg)

        except Exception as e:
            err_msg = f"FILES: ERROR (Save failed: {e})"
            logging.error(err_msg)
            print(f"[ERROR] {err_msg}")
    else:
        msg = "Discovery: ERROR (No names were extracted)"
        logging.error(msg)
        print(f"[ERROR] {msg}")

    print("Script ended: COLLECT_GENERA_LIST")

    # ФИНАЛЬНЫЙ ОТЧЕТ В ЛОГИ
    logging.info("=== FINAL DATA AUDIT REPORT ===")
    
    # Списки варнингов (начинаем нумерацию с 1)
    final_audit_data = [
        ('EXCLUDED / DUPLICATES', excluded_log),
        ('SYNONYMS FOUND', report_synonyms),
        ('NOMINA NUDA FOUND', report_nudums),
        ('PREOCCUPIED NAMES', report_preoccupied),
        ('DUBIOUS / CHIMAERA', report_others)
    ]

    for idx, (title, items) in enumerate(final_audit_data, 1):
        logging.info(f"[{idx}] {title} ({len(items)})")
        for item in items:
            logging.info(item)
    
    logging.info("--- SCRIPT END: COLLECT_GENERA_LIST ---")

if __name__ == "__main__":
    collect_genera()