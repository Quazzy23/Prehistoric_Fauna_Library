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

# [!] УНИФИКАЦИЯ: Все пути теперь привязаны к config
FINAL_CSV = os.path.join(BASE_DIR, config.TABLES_DIR, "final_fauna.csv")
SNAPSHOT_DIR= os.path.join(BASE_DIR, config.SNAPSHOTS_DIR)
DELETED_REGISTRY = os.path.join(BASE_DIR, config.DELETED_REGISTRY)
HISTORY_FILE = os.path.join(BASE_DIR, "project_history.txt")

# Настройка логов (Берем путь строго из config.py)
LOG_FILE = os.path.join(config.LOGS_DIR, "audit_tool.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

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
        logger.error(err); print(f"[ERROR] {err}"); return

    # [2] ОПРЕДЕЛЕНИЕ ИСТОЧНИКОВ
    if config.USE_CUSTOM_LIST:
        source_name = config.CUSTOM_LIST_NAME
    else:
        source_name = "genera_list.csv"
    
    snapshot_path = os.path.join(SNAPSHOT_DIR, f"{source_name.split('.')[0]}.json")
    
    logger.info(f"Opening final source: {os.path.abspath(FINAL_CSV)}")
    logger.info(f"Opening technical snapshot: {os.path.abspath(snapshot_path)}")
    
    old_facts = load_json(snapshot_path)
    is_first_run = not bool(old_facts)
    
    # Загружаем реестр удаленных (всегда приводим к формату списка)
    deleted_registry_raw = load_json(DELETED_REGISTRY)
    if isinstance(deleted_registry_raw, dict):
        deleted_registry = list(deleted_registry_raw.keys())
    else:
        deleted_registry = deleted_registry_raw
    
    # [3] ПОДГОТОВКА ДАННЫХ
    current_facts = {}
    seen_in_this_session = set()
    handled_data_keys_in_this_run = set()
    report = {'NEW': [], 'UPDATED': [], 'LOST': [], 'RESURRECTED': []}

    with open(FINAL_CSV, 'r', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f, delimiter=';'))
    
    total_rows = len(rows)
    logger.info(f"Detected {total_rows} species in {source_name}")
    if not config.BRIEF_CONSOLE: print(f"Total entries to process: {total_rows}")

    # [4] ОСНОВНОЙ ЦИКЛ СРАВНЕНИЯ
    for i, row in enumerate(rows, 1):
        if not config.BRIEF_CONSOLE:
            sys.stdout.write(f"\rAnalyzing... [{i}/{total_rows}]"); sys.stdout.flush()

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
            elif old_d_v is not None:
                clean_msg = f"{data_key}: {old_d_v} -> {data_val}"
                report['UPDATED'].append(clean_msg); logger.warning(f"[UPDATED] {clean_msg}")
            else:
                # NEW или INIT для Рода
                if is_first_run:
                    logger.info(f"[INIT] {data_key}: {data_val}")
                else:
                    # [!] Проверка: если в архиве есть виды этого рода — Род тоже RESURRECTED
                    is_genus_res = any(name.lower().startswith(page.lower() + " ") for name in deleted_registry)
                    
                    clean_msg = f"{data_key}: {data_val}"
                    if is_genus_res:
                        report['RESURRECTED'].append(clean_msg)
                        logger.warning(f"[RESURRECTED] {clean_msg}")
                    else:
                        report['NEW'].append(clean_msg)
                        logger.warning(f"[NEW] {clean_msg}")
            
            seen_in_this_session.add(data_key)
            handled_data_keys_in_this_run.add(data_key)

        # --- Б) ОБРАБОТКА ВИДА (SPECIES) ---
        s_key = f"SPECIES:{g}:{s}"
        s_val = f"{row['status']} | {row['is_type']} | {row['author']} | {row['year']} | {page}"
        current_facts[s_key] = s_val
        seen_in_this_session.add(s_key)
        
        old_s_v = old_facts.get(s_key)
        full_name = f"{g} {s}"

        if old_s_v == s_val:
            logger.info(f"[OK] {s_key}: {s_val}")
        elif old_s_v is not None:
            clean_msg = f"{s_key}: {old_s_v} -> {s_val}"
            report['UPDATED'].append(clean_msg); logger.warning(f"[UPDATED] {clean_msg}")
        else:
            # NEW или RESURRECTED или INIT для Вида
            is_res = False
            if full_name in deleted_registry:
                clean_msg = f"{full_name}: {s_val}"
                report['RESURRECTED'].append(clean_msg); logger.warning(f"[RESURRECTED] {clean_msg}")
                deleted_registry.remove(full_name); is_res = True # [!] Исправлено для списка
            
            if not is_res:
                if is_first_run:
                    logger.info(f"[INIT] {s_key}: {s_val}")
                else:
                    clean_msg = f"{s_key}: {s_val}"
                    report['NEW'].append(clean_msg); logger.warning(f"[NEW] {clean_msg}")

    # [5] ОБРАБОТКА ПРОПАВШИХ (Иерархический вывод в порядке снапшота)
    all_old_keys = list(old_facts.keys()) # Берем список ключей в порядке их записи
    seen_in_this_session = seen_in_this_session # (для контекста)
    
    # Собираем страницы в порядке их появления в старой памяти
    old_pages_ordered = []
    for k in all_old_keys:
        if k not in seen_in_this_session:
            p_name = k.split(":", 1)[1] if k.startswith("DATA:") else old_facts[k].split(" | ")[-1]
            if p_name not in old_pages_ordered:
                old_pages_ordered.append(p_name)

    for page in old_pages_ordered:
        # 1. Сначала DATA страницы (если она пропала)
        d_key = f"DATA:{page}"
        if d_key in all_old_keys and d_key not in seen_in_this_session:
            old_v = old_facts[d_key]
            clean_msg = f"{d_key}: {old_v}"
            report['LOST'].append(clean_msg); logger.warning(f"[LOST] {clean_msg}")

        # 2. Затем все Виды этой страницы (в порядке их записи в снапшоте)
        for k in all_old_keys:
            if k.startswith("SPECIES:") and k not in seen_in_this_session:
                if old_facts[k].split(" | ")[-1] == page:
                    val_parts = old_facts[k].split(" | ")
                    clean_val = " | ".join(val_parts[:-1])
                    clean_msg = f"{k}: {clean_val}"
                    report['LOST'].append(clean_msg); logger.warning(f"[LOST] {clean_msg}")
                    
                    # Запись в реестр
                    p = k.split(":")
                    full_name = f"{p[1]} {p[2]}"
                    if full_name not in deleted_registry: deleted_registry.append(full_name)

    # [6] ЗАПИСЬ В ИСТОРИЮ
    all_changes_count = sum(len(v) for v in report.values())
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode_str = config.RESEARCH_MODE.upper()
    header = f"\n=== SESSION: {ts} | Mode: {mode_str} | Source: {source_name} ===\n"

    if is_first_run:
        with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
            f.write(header + "[INITIALIZED] Baseline established.\n")
    elif all_changes_count > 0:
        with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
            f.write(header)
            for tag, items in report.items():
                for item in items: f.write(f"[{tag}] {item}\n")

    # [7] ЗАВЕРШЕНИЕ И СОХРАНЕНИЕ
    if not config.BRIEF_CONSOLE: print()
    logger.info(f"Audit finished. Total changes: {all_changes_count}")

    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    with open(snapshot_path, 'w', encoding='utf-8') as f:
        json.dump(current_facts, f, indent=2, ensure_ascii=False)
    logger.info(f"Snapshot saved to: {os.path.abspath(snapshot_path)}")

    # Сохранение реестра удаленных (в столбик)
    if deleted_registry:
        with open(DELETED_REGISTRY, 'w', encoding='utf-8') as f:
            # indent=2 превратит список в "столбик"
            json.dump(sorted(list(set(deleted_registry))), f, indent=2, ensure_ascii=False)
        logger.info(f"Deleted registry updated: {os.path.abspath(DELETED_REGISTRY)}")
    elif os.path.exists(DELETED_REGISTRY):
        os.remove(DELETED_REGISTRY)
        logger.info("Deleted registry file removed (empty)")

    # [8] ФИНАЛЬНЫЙ ОТЧЕТ В ЛОГИ
    logger.info("=== FINAL AUDIT REPORT ===")
    final_report_structure = [
        ('NEW DATA FOUND', report['NEW']),
        ('UPDATED SCIENTIFIC DATA', report['UPDATED']),
        ('LOST DATA (MISSING IN SOURCE)', report['LOST']),
        ('RESURRECTED DATA', report['RESURRECTED'])
    ]
    for idx, (title, items) in enumerate(final_report_structure, 1):
        logger.info(f"[{idx}] {title} ({len(items)})")
        for item in items: logger.info(item)

    # [9] ЗАВЕРШЕНИЕ В КОНСОЛИ
    if config.BRIEF_CONSOLE:
        if is_first_run: print("Baseline established")
        elif all_changes_count == 0: print("No changes")
        else: print(f"{all_changes_count} changes recorded")
    else:
        if is_first_run:
            print("Audit completed.")
            print("Baseline established.")
        else:
            print("Audit completed.")
            print(f"Total changes found: {all_changes_count}")
        
        print(f"Snapshot saved to: {os.path.abspath(snapshot_path)}")
        if deleted_registry:
            print(f"Deleted registry updated: {os.path.abspath(DELETED_REGISTRY)}")
        
        if is_first_run or all_changes_count > 0:
            print(f"Project history updated: {os.path.abspath(HISTORY_FILE)}")
        
        print("Script ended: AUDIT_TOOL")

    logger.info("--- SCRIPT END: AUDIT_TOOL ---")

if __name__ == "__main__":
    run_final_audit()