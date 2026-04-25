import sys
sys.dont_write_bytecode = True  # Сначала запрещаем
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

# Настройка лога
logger = logging.getLogger("audit_tool")
logger.setLevel(logging.INFO)
if logger.hasHandlers(): logger.handlers.clear()
handler = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
logger.addHandler(handler)

def run_audit(current_facts, source_filename):
    """Сравнивает данные и ведет летопись. Логи синхронизированы с консолью."""
    print("Starting script: AUDIT_TOOL")
    logger.info(f"=== AUDIT SESSION START: {source_filename} ===")
    
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    snapshot_path = os.path.join(SNAPSHOT_DIR, f"{source_filename}.json")
    
    # 1. Загружаем прошлое
    old_facts = {}
    is_first_run = not os.path.exists(snapshot_path)
    if is_first_run:
        msg = f"No previous snapshot found for {source_filename}. Initializing baseline."
        logger.info(msg)
    else:
        try:
            with open(snapshot_path, 'r', encoding='utf-8') as f:
                old_facts = json.load(f)
            logger.info(f"Loaded previous snapshot with {len(old_facts)} facts.")
        except Exception as e:
            logger.error(f"Failed to load snapshot: {e}")

    # 2. Загружаем базу миграций
    known_migrations = {}
    if os.path.exists(MIGRATIONS_FILE):
        try:
            with open(MIGRATIONS_FILE, 'r', encoding='utf-8') as f:
                known_migrations = json.load(f)
            logger.info(f"Loaded migration registry: {len(known_migrations)} entries.")
        except Exception as e:
            logger.error(f"Failed to load migrations file: {e}")

    changes = []
    new_migrations_found = 0
    
    # Вывод процесса
    status_msg = f"Analyzing {len(current_facts)} currently extracted facts..."
    print(f"Audit: {status_msg}")
    logger.info(status_msg)

    # 3. СРАВНЕНИЕ (Только если это НЕ первый запуск)
    if not is_first_run:
        all_keys = set(old_facts.keys()) | set(current_facts.keys())
        for key in sorted(all_keys):
            if ":HIST:" in key: continue # Обрабатываем отдельно

            old_val = old_facts.get(key)
            new_val = current_facts.get(key)

            if old_val is None and new_val is not None:
                changes.append(f"[NEW] {key}: {new_val}")
            elif old_val is not None and new_val is None:
                changes.append(f"[LOST] {key}: {old_val}")
            elif old_val != new_val:
                changes.append(f"[UPDATED] {key}: {old_val} -> {new_val}")

    # 4. ОБРАБОТКА МИГРАЦИЙ (Historical Notes)
    for key, val in current_facts.items():
        if ":HIST:" in key:
            old_full_name = key.split(":HIST:")[-1]
            new_genus = key.split(":")[0]
            
            if old_full_name not in known_migrations:
                known_migrations[old_full_name] = new_genus
                if not is_first_run:
                    msg = f"[MIGRATION MAPPED] {old_full_name} -> {new_genus}"
                    changes.append(msg)
                    new_migrations_found += 1
                    logger.info(f"Detected new migration: {old_full_name}")

    # 5. ЗАПИСЬ В ПРОЕКТНУЮ ИСТОРИЮ (Летопись)
    try:
        # Сессия записывается ВСЕГДА, чтобы подтвердить факт проверки данных
        add_separator = os.path.exists(HISTORY_FILE) and os.path.getsize(HISTORY_FILE) > 0
        prefix = "\n" if add_separator else ""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = f"{prefix}=== SESSION: {timestamp} | Source: {source_filename} ===\n"

        with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
            f.write(header)
            
            if is_first_run:
                # Случай 1: Самый первый запуск (Базовая линия)
                content_line = f"[INITIALIZED] Baseline established. Data seeded for {len(current_facts)} facts.\n"
                finish_msg = f"Baseline established for {source_filename} ({len(current_facts)} facts)."
            elif changes:
                # Случай 2: Найдены изменения
                for line in changes:
                    f.write(f"{line}\n")
                content_line = "" # Изменения уже записаны выше в цикле
                finish_msg = f"Audit finished: {len(changes)} changes recorded."
            else:
                # Случай 3: Изменений нет (Данные проверены и актуальны)
                content_line = f"[NO CHANGES] Data verified. All {len(current_facts)} facts match the previous session.\n"
                finish_msg = "Audit finished: No changes detected (Data is up to date)."

            if content_line:
                f.write(content_line)
        
        # Вывод информации о сохранении истории
        history_path_msg = f"Project history updated: {os.path.abspath(HISTORY_FILE)}"
        print(history_path_msg)
        logger.info(history_path_msg)
        
        print(f"Audit: {finish_msg}")
        logger.info(finish_msg)
        if changes:
            for c in changes: logger.info(f"  Change: {c}")

    except Exception as e:
        logger.error(f"Failed to write to project_history.txt: {e}")

    # 6. СОХРАНЕНИЕ ТЕХНИЧЕСКОЙ ПАМЯТИ
    try:
        with open(snapshot_path, 'w', encoding='utf-8') as f:
            json.dump(current_facts, f, indent=2, ensure_ascii=False)
        
        # --- СТАНДАРТНЫЙ ВЫВОД ДЛЯ СНАПШОТА ---
        snap_path_msg = f"Technical snapshot saved: {os.path.abspath(snapshot_path)}"
        print(snap_path_msg)
        logger.info(snap_path_msg)

        with open(MIGRATIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(known_migrations, f, indent=2, ensure_ascii=False)
        
        # --- СТАНДАРТНЫЙ ВЫВОД ДЛЯ МИГРАЦИЙ ---
        mig_path_msg = f"Migration registry updated: {os.path.abspath(MIGRATIONS_FILE)}"
        print(mig_path_msg)
        logger.info(mig_path_msg)

    except Exception as e:
        logger.error(f"Failed to save technical files: {e}")

    print("Script ended: AUDIT_TOOL")
    logger.info(f"=== AUDIT SESSION END ===")

if __name__ == "__main__":
    print("AUDIT TOOL: This script is unable to run independently.")
    print("Usage: audit_tool.py is automatically called by fetch_dino_details.py. To run it, run fetch_dino_details.py.")