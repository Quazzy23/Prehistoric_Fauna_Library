import sys
sys.dont_write_bytecode = True
import os
# Добавляем путь к папке scripts, чтобы увидеть config.py
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import csv
import json
import logging
import config
from datetime import datetime

# --- ПУТИ ---
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Умные пути из конфига
FINAL_CSV = os.path.join(BASE_DIR, "data", "exports", config.RESEARCH_MODE, "tables", "final_fauna.csv")
SNAPSHOT_DIR = os.path.join(BASE_DIR, "data", "exports", config.RESEARCH_MODE, "snapshots")
DELETED_REGISTRY = os.path.join(BASE_DIR, config.DELETED_REGISTRY)
MIGRATIONS_FILE = os.path.join(BASE_DIR, config.MIGRATIONS_FILE)

HISTORY_FILE = os.path.join(BASE_DIR, "project_history.txt")
LOG_FILE = os.path.join(BASE_DIR, "data", "logs", "audit_tool.log")

# Настройка лога
logger = logging.getLogger("audit_tool")
logger.setLevel(logging.INFO)
if logger.hasHandlers(): logger.handlers.clear()
handler = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
logger.addHandler(handler)

def load_json(path):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            try: return json.load(f)
            except: return {}
    return {}

def run_final_audit():
    # [1] ИНИЦИАЛИЗАЦИЯ КОНСОЛИ И ЛОГОВ
    if config.BRIEF_CONSOLE:
        print("AUDIT_TOOL...", end=" ", flush=True)
    else:
        print("Starting script: AUDIT_TOOL")
    
    logger.info("--- SCRIPT START: AUDIT_TOOL ---")
    logger.info("Configuration loaded successfully")

    if not os.path.exists(FINAL_CSV):
        err = f"Final CSV not found: {FINAL_CSV}"
        logger.error(err)
        print(f"[ERROR] {err}")
        return

    # [2] ОПРЕДЕЛЕНИЕ ИСТОЧНИКОВ
    if config.USE_CUSTOM_LIST:
        source_name = config.CUSTOM_LIST_NAME
    else:
        source_name = "genera_list.csv"
    
    snapshot_path = os.path.join(SNAPSHOT_DIR, f"{source_name.split('.')[0]}.json")
    
    logger.info(f"Opening final source: {os.path.abspath(FINAL_CSV)}")
    logger.info(f"Opening technical snapshot: {os.path.abspath(snapshot_path)}")
    
    old_facts = load_json(snapshot_path)
    migrations = load_json(MIGRATIONS_FILE)
    deleted_registry = load_json(DELETED_REGISTRY)
    
    # [3] ПОДГОТОВКА ДАННЫХ
    current_facts = {}
    seen_in_this_session = set()
    handled_data_keys_in_this_run = set()
    report = {'NEW': [], 'UPDATED': [], 'LOST': [], 'MIGRATED': [], 'RESURRECTED': []}

    with open(FINAL_CSV, 'r', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f, delimiter=';'))
    
    total_rows = len(rows)
    logger.info(f"Detected {total_rows} species in {source_name}")
    
    if not config.BRIEF_CONSOLE:
        print(f"Total entries to process: {total_rows}")

    # [4] ОСНОВНОЙ ЦИКЛ СРАВНЕНИЯ (Идет по строкам CSV)
    for i, row in enumerate(rows, 1):
        # Прогресс-бар только в полном режиме
        if not config.BRIEF_CONSOLE:
            sys.stdout.write(f"\rAnalyzing... [{i}/{total_rows}]")
            sys.stdout.flush()

        g, s = row['genus'], row['species']
        page = row['source_genus']
        
        # --- А) ОБРАБОТКА ДАННЫХ СТРАНИЦЫ (DATA) ---
        data_key = f"DATA:{page}"
        data_val = f"{row['clade']} | {row['age']} | {row['stage']}"
        current_facts[data_key] = data_val
        
        if data_key not in handled_data_keys_in_this_run:
            old_d_v = old_facts.get(data_key)
            if old_d_v == data_val:
                logger.info(f"[OK] {data_key}: {data_val}")
            elif old_d_v is None:
                msg = f"[NEW] {data_key}: {data_val}"
                report['NEW'].append(msg); logger.warning(msg)
            else:
                msg = f"[UPDATED] {data_key}: {old_d_v} -> {data_val}"
                report['UPDATED'].append(msg); logger.warning(msg)
            
            seen_in_this_session.add(data_key)
            handled_data_keys_in_this_run.add(data_key)

        # --- Б) ОБРАБОТКА ВИДА (SPECIES) ---
        s_key = f"SPECIES:{g}:{s}"
        s_val = f"{row['status']} | {row['is_type']} | {row['author']} | {row['year']}"
        current_facts[s_key] = s_val
        seen_in_this_session.add(s_key)
        
        old_s_v = old_facts.get(s_key)
        full_name = f"{g} {s}"

        if old_s_v == s_val:
            logger.info(f"[OK] {s_key}: {s_val}")
        elif old_s_v is not None:
            msg = f"[UPDATED] {s_key}: {old_s_v} -> {s_val}"
            report['UPDATED'].append(msg); logger.warning(msg)
        else:
            # Проверка на воскрешение или новичка
            is_res = False
            if full_name in deleted_registry:
                msg = f"[RESURRECTED] {full_name}: {s_val}"
                report['RESURRECTED'].append(msg); logger.warning(msg)
                del deleted_registry[full_name]; is_res = True
            
            if not is_res:
                msg = f"[NEW] {s_key}: {s_val}"
                report['NEW'].append(msg); logger.warning(msg)

    # [5] ОБРАБОТКА ПРОПАВШИХ (Те, кто был в памяти, но не в CSV)
    all_old_keys = set(old_facts.keys())
    lost_keys = sorted(list(all_old_keys - seen_in_this_session))

    for l_key in lost_keys:
        old_v = old_facts[l_key]
        is_mig = False
        
        if l_key.startswith("SPECIES:"):
            parts = l_key.split(":")
            old_full_name = f"{parts[1]} {parts[2]}"
            if old_full_name in migrations:
                new_gen = migrations[old_full_name]
                target_k = f"SPECIES:{new_gen}:{parts[2]}"
                if target_k in seen_in_this_session:
                    msg = f"[MIGRATED] {old_full_name} -> {new_gen} {parts[2]}"
                    report['MIGRATED'].append(msg); logger.warning(msg)
                    is_mig = True
        
        if not is_mig:
            msg = f"[LOST] {l_key}: {old_v}"
            report['LOST'].append(msg); logger.warning(msg)
            if l_key.startswith("SPECIES:"):
                p = l_key.split(":")
                deleted_registry[f"{p[1]} {p[2]}"] = {"deleted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    # [6] ЗАПИСЬ В ИСТОРИЮ
    all_changes = report['NEW'] + report['UPDATED'] + report['LOST'] + report['MIGRATED'] + report['RESURRECTED']
    all_changes_count = len(all_changes)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Формируем заголовок с указанием научного режима (в верхнем регистре для наглядности)
    mode_str = config.RESEARCH_MODE.upper()
    header = f"\n=== SESSION: {ts} | Mode: {mode_str} | Source: {source_name} ===\n"

    if not old_facts:
        with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
            f.write(header + "[INITIALIZED] Baseline established.\n")
    elif all_changes:
        with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
            f.write(header)
            for line in sorted(all_changes): f.write(f"{line}\n")

    # 5. ЗАВЕРШЕНИЕ И СОХРАНЕНИЕ ТЕХНИЧЕСКИХ ФАЙЛОВ
    if not config.BRIEF_CONSOLE: print(f"\nAudit completed.")

    # [!] ЛОГ: Сначала пишем итог аудита
    logger.info(f"Audit finished. Total changes: {all_changes_count}")

    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    with open(snapshot_path, 'w', encoding='utf-8') as f:
        json.dump(current_facts, f, indent=2, ensure_ascii=False)
    # [!] ЛОГ: Затем пишем путь сохранения
    logger.info(f"Snapshot saved to: {os.path.abspath(snapshot_path)}")

    if deleted_registry:
        with open(DELETED_REGISTRY, 'w', encoding='utf-8') as f:
            json.dump(deleted_registry, f, indent=2, ensure_ascii=False)
        logger.info(f"Deleted registry updated: {os.path.abspath(DELETED_REGISTRY)}")
    elif os.path.exists(DELETED_REGISTRY):
        os.remove(DELETED_REGISTRY)
        logger.info("Deleted registry file removed (empty)")

    # 6. ФИНАЛЬНЫЙ АУДИТ-ОТЧЕТ В ЛОГ-ФАЙЛ
    logger.info("=== FINAL AUDIT REPORT ===")
    sections = [
        ('NEW', report['NEW']), 
        ('UPDATED', report['UPDATED']), 
        ('LOST', report['LOST']), 
        ('MIGRATIONS', report['MIGRATED']), 
        ('RESURRECTED', report['RESURRECTED'])
    ]
    for title, items in sections:
        logger.info(f"[{title}] ({len(items)})")
        for item in items: logger.info(f"    {item}")

    # 7. ЗАВЕРШЕНИЕ В КОНСОЛИ
    if config.BRIEF_CONSOLE:
        print("No changes" if not all_changes else f"{all_changes_count} changes recorded")
    else:
        print(f"Total changes found: {all_changes_count}")
        # [!] Добавлен вывод путей в консоль перед историей
        print(f"Snapshot saved to: {os.path.abspath(snapshot_path)}")
        if deleted_registry:
            print(f"Deleted registry updated: {os.path.abspath(DELETED_REGISTRY)}")
        print(f"Project history updated: {os.path.abspath(HISTORY_FILE)}")
        print("Script ended: AUDIT_TOOL")

    logger.info("--- SCRIPT END: AUDIT_TOOL ---")

if __name__ == "__main__":
    run_final_audit()