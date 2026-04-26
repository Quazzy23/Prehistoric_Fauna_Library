import sys
sys.dont_write_bytecode = True
import os
import json
import logging
from datetime import datetime

# --- ПУТИ ---
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SNAPSHOT_DIR = os.path.join(BASE_DIR, "data", "exports", "snapshots")
HISTORY_FILE = os.path.join(BASE_DIR, "data", "project_history.txt")
MIGRATIONS_FILE = os.path.join(BASE_DIR, "data", "exports", "known_migrations.json")
LOG_FILE = os.path.join(BASE_DIR, "data", "logs", "audit_tool.log")

# Настройка лога (Золотой Стандарт)
logger = logging.getLogger("audit_tool")
logger.setLevel(logging.INFO)
logger.propagate = False # <--- ЭТА СТРОКА ОСТАНОВИТ ДУБЛИРОВАНИЕ
if logger.hasHandlers(): logger.handlers.clear()
handler = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
logger.addHandler(handler)

def run_audit(current_facts, source_filename):
    """Глубокий аудит данных с полной детализацией и итоговыми списками."""
    print("\nStarting script: AUDIT_TOOL")
    logger.info("--- SCRIPT START: AUDIT_TOOL ---")
    
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    # Убираем .csv из имени (например, genera_list.csv -> genera_list)
    clean_name = source_filename.split('.')[0]
    snapshot_path = os.path.join(SNAPSHOT_DIR, f"{clean_name}.json")
    
    # 1. Загрузка данных
    old_facts = {}
    is_first_run = not os.path.exists(snapshot_path)
    if not is_first_run:
        try:
            with open(snapshot_path, 'r', encoding='utf-8') as f:
                old_facts = json.load(f)
            logger.info(f"Loaded technical snapshot: {os.path.abspath(snapshot_path)}")
        except Exception as e:
            logger.error(f"Failed to load snapshot: {e}")

    known_migrations = {}
    if os.path.exists(MIGRATIONS_FILE):
        try:
            with open(MIGRATIONS_FILE, 'r', encoding='utf-8') as f:
                known_migrations = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load migrations file: {e}")

    # Списки для итогового отчета
    report_data = {
        'NEW': [],
        'UPDATED': [],
        'LOST': [],
        'MIGRATIONS': []
    }
    
    status_msg = f"Analyzing {len(current_facts)} currently extracted facts..."
    print(f"Audit: {status_msg}")
    logger.info(status_msg)

    # 2. СРАВНЕНИЕ (Основной цикл)
    all_keys = sorted(list(set(old_facts.keys()) | set(current_facts.keys())))
    
    for key in all_keys:
        if ":HIST:" in key: continue # Обработка миграций ниже

        old_val = old_facts.get(key)
        new_val = current_facts.get(key)

        if is_first_run:
            logger.info(f"[OK] {key}: {new_val}")
            continue

        if old_val is None and new_val is not None:
            msg = f"[NEW] {key}: {new_val}"
            logger.warning(msg)
            report_data['NEW'].append(msg)
        elif old_val is not None and new_val is None:
            msg = f"[LOST] {key}: {old_val}"
            logger.warning(msg)
            report_data['LOST'].append(msg)
        elif old_val != new_val:
            msg = f"[UPDATED] {key}: {old_val} -> {new_val}"
            logger.warning(msg)
            report_data['UPDATED'].append(msg)
        else:
            logger.info(f"[OK] {key}: {new_val}")

    # 3. ОБРАБОТКА МИГРАЦИЙ
    for key, val in current_facts.items():
        if ":HIST:" in key:
            old_full_name = key.split(":HIST:")[-1]
            new_genus = key.split(":")[0]
            
            if old_full_name not in known_migrations:
                known_migrations[old_full_name] = new_genus
                msg = f"[MIGRATION MAPPED] {old_full_name} -> {new_genus}"
                logger.warning(msg)
                report_data['MIGRATIONS'].append(msg)

    # 4. ЗАПИСЬ В ПРОЕКТНУЮ ИСТОРИЮ
    changes_for_history = report_data['NEW'] + report_data['UPDATED'] + report_data['LOST'] + report_data['MIGRATIONS']
    
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        add_sep = os.path.exists(HISTORY_FILE) and os.path.getsize(HISTORY_FILE) > 0
        header = f"{'\n' if add_sep else ''}=== SESSION: {timestamp} | Source: {source_filename} ===\n"

        with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
            f.write(header)
            if is_first_run:
                f.write(f"[INITIALIZED] Baseline established. Data seeded for {len(current_facts)} facts.\n")
                finish_msg = f"Baseline established for {source_filename} ({len(current_facts)} facts)."
            elif changes_for_history:
                for line in changes_for_history:
                    f.write(f"{line}\n")
                finish_msg = f"Audit finished: {len(changes_for_history)} changes recorded."
            else:
                f.write(f"[NO CHANGES] Data verified. All {len(current_facts)} facts match previous session.\n")
                finish_msg = "Audit finished: No changes detected."

        print(f"Project history updated: {os.path.abspath(HISTORY_FILE)}")
        print(f"Audit: {finish_msg}")
        logger.info(finish_msg)

    except Exception as e:
        logger.error(f"Failed to write history: {e}")

    # 5. ФИНАЛЬНЫЙ АУДИТ-ОТЧЕТ В ЛОГ-ФАЙЛ
    logger.info("=== FINAL AUDIT DATA REPORT ===")
    sections = [
        ('NEW DATA FOUND', report_data['NEW']),
        ('UPDATED SCIENTIFIC DATA', report_data['UPDATED']),
        ('LOST DATA (MISSING IN SOURCE)', report_data['LOST']),
        ('NEW MIGRATION LINKS', report_data['MIGRATIONS'])
    ]
    
    for idx, (title, items) in enumerate(sections, 1):
        logger.info(f"[{idx}] {title} ({len(items)})")
        for item in items:
            logger.info(f"    {item}")

    # 6. СОХРАНЕНИЕ ТЕХНИЧЕСКИХ ФАЙЛОВ
    try:
        with open(snapshot_path, 'w', encoding='utf-8') as f:
            json.dump(current_facts, f, indent=2, ensure_ascii=False)
        logger.info(f"Data saved to {os.path.abspath(snapshot_path)}")

        with open(MIGRATIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(known_migrations, f, indent=2, ensure_ascii=False)
        logger.info(f"Data saved to {os.path.abspath(MIGRATIONS_FILE)}")
    except Exception as e:
        logger.error(f"Failed to save technical files: {e}")

    logger.info("--- SCRIPT END: AUDIT_TOOL ---")
    print("Script ended: AUDIT_TOOL")