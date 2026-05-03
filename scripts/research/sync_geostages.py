import sys
sys.dont_write_bytecode = True  # Сначала запрещаем
import csv
import os
import re
import time
import logging
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

# --- ПУТИ К ФАЙЛАМ ---
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
# [!] УНИФИКАЦИЯ: Используем TABLES_DIR из конфига
DATA_ROOT = os.path.join(BASE_DIR, config.TABLES_DIR)

GEO_CSV = os.path.join(DATA_ROOT, "geochronology_ref.csv")
SOURCE_CSV = os.path.join(DATA_ROOT, "validated_fauna.csv")
FINAL_CSV = os.path.join(DATA_ROOT, "final_fauna.csv")

# Настройка логов
LOG_FILE = os.path.join(config.LOGS_DIR, "sync_geostages.log")
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

def get_geological_stages():
    """Загружает ярусы, вычисляет границы и обрабатывает NULL."""
    if not os.path.exists(GEO_CSV): 
        logging.error(f"Geochronology file not found: {GEO_CSV}")
        return []
    
    logging.info(f"Opening geochronology reference: {GEO_CSV}")
    with open(GEO_CSV, 'r', encoding='utf-8-sig') as f:
        raw_data = list(csv.DictReader(f, delimiter=';'))
    
    logging.info(f"Geochronology: Loaded {len(raw_data)} units")
    
    stages = []
    for i in range(len(raw_data)):
        row = raw_data[i]
        name = row['stage'] if row['stage'] != "-" else row['period']
        if not name or name == "-": continue
        
        try:
            older = round(float(row['start_ma'] or 0), 4)
            younger = round(float(raw_data[i-1]['start_ma']), 4) if i > 0 else 0.0
            err_raw = str(row.get('uncertainty', '0')).upper()
            error = round(float(err_raw), 4) if err_raw not in ["NULL", "-", "", "NONE"] else 0.0
            
            stages.append({'name': name, 'older': older, 'younger': younger, 'error': error})
        except: continue
    return stages

def sync_stages():
    logging.info("--- SCRIPT START: SYNC_GEOSTAGES ---")
    if config.BRIEF_CONSOLE:
        print("SYNC_GEOSTAGES...", end=" ", flush=True)
    else:
        print("Starting script: SYNC_GEOSTAGES")

    stages_ref = get_geological_stages()
    if not stages_ref:
        print("[ERROR] No geochronology data loaded! Check logs.")
        return

    if not os.path.exists(SOURCE_CSV):
        msg = f"Validated data file not found: {SOURCE_CSV}"
        logging.error(msg)
        print(f"[ERROR] {msg}")
        return

    logging.info(f"Opening validated data for syncing: {SOURCE_CSV}")
    with open(SOURCE_CSV, 'r', encoding='utf-8-sig') as f:
        reader = list(csv.DictReader(f, delimiter=';'))
    
    total = len(reader)
    logging.info(f"Started synchronization for {total} species")
    if not config.BRIEF_CONSOLE:
        print(f"Total species to process: {total}")

    final_data = []
    report_fixed = []
    report_ok = []
    report_skipped = []
    report_out_of_bounds = []

    for i, row in enumerate(reader, 1):
        full_name = f"{row['genus']} {row['species']}"
        age_str = row['age']
        current_stage = row['stage']

        # 1. Пропускаем без даты
        if not age_str or age_str in [MISSING_VAL, "Unknown"]:
            msg = f"{full_name}: SKIP (No age data)"
            logging.warning(msg)
            report_skipped.append(full_name)
            final_data.append(row)
            continue

        # 2. Парсим цифры
        ages = re.findall(r'(\d+\.?\d*)', age_str.replace(',', ''))
        if not ages:
            msg = f"{full_name}: SKIP (Parse error in age: {age_str})"
            logging.warning(msg)
            report_skipped.append(msg)
            final_data.append(row)
            continue

        d_start = round(float(ages[0]), 4)
        d_end = round(float(ages[1]), 4) if len(ages) > 1 else d_start
        d_max, d_min = (max(d_start, d_end), min(d_start, d_end)) if len(ages) > 1 else (d_start, d_start)

        # 3. Поиск пересечений
        matches = []
        for st in stages_ref:
            overlap_start = max(d_min, st['younger'])
            overlap_end = min(d_max, st['older'])
            intersection = round(overlap_end - overlap_start, 4)

            if d_max == d_min: # Точка
                # Мы делаем старую границу исключающей (<), чтобы точка 66.0 
                # попадала в Maastrichtian [66.0 - 72.1), а не в Danian [61.6 - 66.0)
                if (st['younger'] - st['error']) <= d_max < (st['older'] + st['error']):
                    matches.append({'name': st['name'], 'marginal': False})
            elif intersection > 0:
                is_marginal = intersection < st['error']
                matches.append({'name': st['name'], 'marginal': is_marginal})

        # 4. Фильтрация маргинальных зацепов
        final_selection = [m['name'] for m in matches if not m['marginal']]
        if not final_selection and matches:
            final_selection = [m['name'] for m in matches]

        if not final_selection:
            # Ищем границы для лога
            max_limit = max(st['older'] for st in stages_ref)
            min_limit = min(st['younger'] for st in stages_ref)
            
            # ЭТО ОШИБКА (ERROR): данные выходят за пределы шкалы
            msg = f"{full_name}: ERROR (Out of bounds: {age_str}) [{d_max}-{d_min} Ma] is outside scale [{max_limit}-{min_limit} Ma]"
            logging.error(msg)
            report_out_of_bounds.append(f"{full_name} ({age_str})")
            final_data.append(row)
            continue

        # Собираем диапазон ярусов
        res_names = []
        for st in reversed(stages_ref):
            if st['name'] in final_selection and st['name'] not in res_names:
                res_names.append(st['name'])
        
        new_stage = res_names[0] if len(res_names) == 1 else f"{res_names[0]}-{res_names[-1]}"

        # Собираем границы выбранного диапазона времени для обоснования в логе
        matched_stages = [st for st in stages_ref if st['name'] in final_selection]
        # Границы "цели": самый старый из ярусов и самый молодой
        ref_older = max(st['older'] for st in matched_stages)
        ref_younger = min(st['younger'] for st in matched_stages)
        # Максимальная погрешность в этом диапазоне (для справки)
        ref_err = max(st['error'] for st in matched_stages)
        
        # Формируем строку обоснования: [Вид Ma] vs [Ярус Ma ± погрешность]
        age_justification = f"[{d_max}-{d_min} Ma] vs [{ref_older}-{ref_younger} Ma ±{ref_err}]"

        # 5. Сверка и Логирование
        clean_current = current_stage.replace('-', ' ').lower()
        clean_new = new_stage.replace('-', ' ').lower()

        if clean_new == clean_current:
            # СТАТУС OK: только в лог (INFO)
            log_msg = f"{full_name}: OK ({new_stage}) {age_justification}"
            logging.info(log_msg)
            report_ok.append(log_msg)
        else:
            # СТАТУС FIX: предупреждение в лог (WARNING)
            log_msg = f"{full_name}: FIX ({current_stage} -> {new_stage}) {age_justification}"
            logging.warning(log_msg)
            report_fixed.append(log_msg)
            row['stage'] = new_stage
        
        final_data.append(row)
        
        # Счетчик в консоли
        if not config.BRIEF_CONSOLE:
            sys.stdout.write(f"\rSyncing... [{i}/{total}]")
            sys.stdout.flush()
        time.sleep(0.0005) # Задержка для красоты прогресса

    # 6. ЗАВЕРШЕНИЕ
    if not config.BRIEF_CONSOLE:
        print()
        print("Synchronization completed.")
    logging.info("Synchronization completed.")

    # Статистика
    logging.info(f"Total species processed: {total}")
    logging.info(f"Stages updated: {len(report_fixed)}")
    
    if config.BRIEF_CONSOLE:
        print(f"{len(report_fixed)} stages synchronized")
    else:
        print(f"Total species processed: {total}")
        print(f"Stages updated: {len(report_fixed)}")
    
    if len(report_out_of_bounds) > 0:
        msg_err = f"Errors (OUT OF BOUNDS): {len(report_out_of_bounds)}"
        print(msg_err)
        logging.info(msg_err)

    # Сохранение
    if final_data:
        try:
            # [!] Финальный набор колонок для Аудита и Базы
            fieldnames = ["genus", "species", "status", "is_type", "clade", "stage", "age", "author", "year", "source_genus"]
            
            with open(FINAL_CSV, 'w', newline='', encoding='utf-8-sig') as f:
                # Любое отклонение от fieldnames остановит конвейер
                writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
                writer.writeheader()
                writer.writerows(final_data)
            
            path_msg = f"Final list saved to {os.path.abspath(FINAL_CSV)}"
            if not config.BRIEF_CONSOLE:
                print(path_msg)
            logging.info(path_msg)
        except Exception as e:
            err_msg = f"Save failed: {e}"
            print(f"[ERROR] {err_msg}")
            logging.error(err_msg)

    # Вывод критических ошибок в консоль
    if report_out_of_bounds:
        print("SUSPICIOUS CASES (Check logs for details):")
        for err in report_out_of_bounds:
            print(err.split(')')[0] + ')') 

    if not config.BRIEF_CONSOLE:
        print("Script ended: SYNC_GEOSTAGES")

    # ФИНАЛЬНЫЙ ОТЧЕТ В ЛОГИ
    logging.info("=== FINAL DATA AUDIT REPORT ===")
    
    audit_lists = [
        ('STAGES UPDATED (FIX)', report_fixed),
        ('STAGES MATCHING (OK)', [f"Total matches: {len(report_ok)}"]),
        ('SKIPPED (NO AGE DATA)', report_skipped),
        ('ERRORS (OUT OF BOUNDS)', report_out_of_bounds)
    ]

    for idx, (title, items) in enumerate(audit_lists, 1):
        logging.info(f"[{idx}] {title} ({len(items)})")
        for item in items:
            logging.info(item)

    logging.info("--- SCRIPT END: SYNC_GEOSTAGES ---")

if __name__ == "__main__":
    sync_stages()