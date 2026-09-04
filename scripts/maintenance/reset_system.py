import sys
import os
import shutil
import sqlite3

# [0] СИСТЕМНЫЕ НАСТРОЙКИ
sys.dont_write_bytecode = True

# Подтягиваем конфиг
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.append(os.path.join(BASE_DIR, "scripts"))
import config

# --- ПУТИ ---
STORAGE_DIR = os.path.join(BASE_DIR, config.STORAGE_BASE_NAME)
DB_DIR = os.path.join(BASE_DIR, "database")
DB_FILE = os.path.join(DB_DIR, config.DB_NAME)
MODELS_ROOT = os.path.join(BASE_DIR, "models")
LOGS_DIR = config.LOGS_DIR

def is_junction(path):
    """Проверяет, является ли путь Junction (через атрибуты Windows)."""
    if not os.path.exists(path): 
        return False
    try:
        return bool(os.lstat(path).st_file_attributes & 1024)
    except:
        return False

def get_rel_path(path):
    """
    Возвращает путь в Windows-стиле относительно корня.
    Если путь на другом диске (C:\ vs D:\), возвращает аккуратный абсолютный путь.
    """
    try:
        return os.path.relpath(path, BASE_DIR).replace('/', '\\')
    except ValueError:
        # Защита от сбоя при путях на разных дисках в Windows
        return os.path.abspath(path).replace('/', '\\')

def silent_delete(path):
    """Удаляет объект и выводит статус: удалено или уже пусто."""
    rel = get_rel_path(path)
    if not os.path.exists(path):
        print(f"already empty: {rel}")
        return False
    try:
        # Если это Junction-ссылка - не удаляем через rmtree (защита диска E)
        if is_junction(path):
            return False 
        
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        print(f"removed: {rel}")
        return True
    except Exception as e:
        print(f"error: {rel} ({e})")
        return False

def clean_logs():
    """
    Безопасно очищает все .log файлы в AppData.
    Саму папку сохраняем, чтобы не ломать вкладку воркспейса VS Code.
    """
    if not os.path.exists(LOGS_DIR):
        print(f"already empty: logs\\")
        return

    log_files = [f for f in os.listdir(LOGS_DIR) if f.endswith('.log')]
    if not log_files:
        print(f"already empty: logs\\")
        return

    for f in log_files:
        p = os.path.join(LOGS_DIR, f)
        try:
            os.remove(p)
            print(f"removed: logs\\{f}")
        except Exception as e:
            print(f"error: logs\\{f} ({e})")

def smart_models_wipe(target_mode='all'):
    """Очистка моделей на диске E с сохранением Junction-ссылки."""
    print()
    if not os.path.exists(MODELS_ROOT):
        print(f"already empty: models")
        return

    if target_mode == 'all':
        items = os.listdir(MODELS_ROOT)
        if items:
            for item in items:
                silent_delete(os.path.join(MODELS_ROOT, item))
        else:
            print("already empty: models contents")
        
        if is_junction(MODELS_ROOT):
            print(f"keeped: models (Directory Junction preserved)")
        else:
            silent_delete(MODELS_ROOT)
            
    else:
        mode_path = os.path.join(MODELS_ROOT, target_mode)
        silent_delete(mode_path)

        if os.path.exists(MODELS_ROOT) and not os.listdir(MODELS_ROOT):
            if is_junction(MODELS_ROOT):
                print(f"keeped: models (Directory Junction preserved)")
            else:
                silent_delete(MODELS_ROOT)

def clean_db_tables(target_mode):
    """Удаляет таблицы режима из базы данных."""
    if not os.path.exists(DB_FILE):
        print(f"already empty: database\\")
        return

    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        target_tables = [target_mode, f"{target_mode}_taxonomy"]
        
        for t_name in target_tables:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t_name,))
            if cursor.fetchone():
                cursor.execute(f"DROP TABLE IF EXISTS [{t_name}]")
                print(f"removed: DB table '{t_name}'")
            else:
                print(f"already empty: DB table '{t_name}'")
        
        conn.commit()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        remaining = [r[0] for r in cursor.fetchall()]
        conn.close()

        if not remaining or (len(remaining) == 1 and remaining[0] == config.TABLE_GEOLOGY):
            silent_delete(DB_DIR)
            
    except Exception as e:
        print(f"error: database access ({e})")

def run_research_cleanup():
    target = get_target_menu("RESEARCH CLEANUP")
    if not target: return
    
    print(f"\nConfirm RESEARCH cleanup: {target}? (y/n): ", end="")
    if input().lower() != 'y': return
    print() 

    if target == 'all':
        # Глобально: удаляем все хранилища (master и sandbox), базу и логи
        silent_delete(STORAGE_DIR)
        silent_delete(DB_DIR)
        clean_logs()
    else:
        # Локально: чистим подпапку режима во всех слоях (master и sandbox)
        for layer in [config.MASTER_NAME, config.SANDBOX_NAME]:
            mode_storage = os.path.join(STORAGE_DIR, layer, target)
            silent_delete(mode_storage)
            
            # Уборка пустой папки слоя
            layer_dir = os.path.join(STORAGE_DIR, layer)
            if os.path.exists(layer_dir) and not os.listdir(layer_dir):
                silent_delete(layer_dir)

        if os.path.exists(STORAGE_DIR) and not os.listdir(STORAGE_DIR):
            silent_delete(STORAGE_DIR)
            
        clean_db_tables(target)
        clean_logs()

def get_target_menu(title):
    print(f"\n--- {title} ---")
    print("a. ALL (Global)")
    modes = list(config.WIKI_SETTINGS.keys())
    for i, mode in enumerate(modes, 1):
        print(f"{i}. {mode.capitalize()}")
    print("0. Cancel")
    c = input("Select: ").lower().strip()
    if c == 'a': return 'all'
    if c.isdigit() and 1 <= int(c) <= len(modes): return modes[int(c)-1]
    return None

def run_catalog_cleanup():
    target = get_target_menu("SPECIES CATALOG CLEANUP")
    if not target: return
    
    print(f"\nConfirm CATALOG delete: {target}? (y/n): ", end="")
    if input().lower() != 'y': return
    print()

    layers = [config.MASTER_NAME, config.SANDBOX_NAME]
    
    if target == 'all':
        for layer in layers:
            layer_path = os.path.join(STORAGE_DIR, layer)
            if not os.path.exists(layer_path): continue
            
            modes = [m for m in os.listdir(layer_path) if os.path.isdir(os.path.join(layer_path, m))]
            for mode in modes:
                catalog_path = os.path.join(layer_path, mode, "species_catalog.json")
                silent_delete(catalog_path)
    else:
        for layer in layers:
            catalog_path = os.path.join(STORAGE_DIR, layer, target, "species_catalog.json")
            silent_delete(catalog_path)

def main():
    while True:
        print(f"\n=== PFL RESET ===")
        print("r. Research Cleanup (Data/DB/Logs)")
        print("c. Catalog Cleanup  (species_catalog.json)")
        print("m. Models Cleanup   (Disk E Assets)")
        print("h. History Cleanup  (History logs)")
        print("0. Exit")
        
        choice = input("Select: ").lower().strip()
        if choice == 'r': 
            run_research_cleanup()
        elif choice == 'c': 
            run_catalog_cleanup()
        elif choice == 'm':
            target = get_target_menu("MODELS CLEANUP")
            if target:
                print(f"\nConfirm MODELS: {target}? (y/n): ", end="")
                if input().lower() == 'y':
                    smart_models_wipe(target)
        elif choice == 'h':
            print(f"\nConfirm HISTORY: history files? (y/n): ", end="")
            if input().lower() == 'y':
                print() 
                # Чистим историю и мастера, и песочницы
                silent_delete(os.path.join(BASE_DIR, "project_history.txt"))
                silent_delete(os.path.join(BASE_DIR, "sandbox_history.txt"))
        elif choice == '0': 
            break

if __name__ == "__main__":
    main()