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
# [!] УНИФИКАЦИЯ: Берем имя папки экспорта из конфига
STORAGE_DIR = os.path.join(BASE_DIR, config.STORAGE_BASE_NAME)
DB_DIR = os.path.join(BASE_DIR, "database")
DB_FILE = os.path.join(DB_DIR, config.DB_NAME)
MODELS_ROOT = os.path.join(BASE_DIR, "models")
HISTORY_FILE = os.path.join(BASE_DIR, "project_history.txt")

def is_junction(path):
    """Проверяет, является ли путь ссылкой или Junction (через атрибуты Windows)."""
    if not os.path.exists(path): return False
    try:
        import stat
        # Используем lstat, чтобы не переходить по ссылке
        return bool(os.lstat(path).st_file_attributes & 1024)
    except:
        return False

def get_rel_path(path):
    """Возвращает путь в Windows-стиле относительно корня."""
    return os.path.relpath(path, BASE_DIR).replace('/', '\\')

def silent_delete(path):
    """Удаляет объект и выводит статус: удалено или уже пусто."""
    rel = get_rel_path(path)
    if not os.path.exists(path):
        print(f"already empty: {rel}")
        return False
    try:
        # Если это ссылка - мы её не удаляем через rmtree (защита)
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

def smart_models_wipe(target_mode='all'):
    """Логика очистки моделей: строго по заданному формату."""
    print() # Пустая строка
    
    if not os.path.exists(MODELS_ROOT):
        print(f"already empty: models")
        return

    if target_mode == 'all':
        # 1. Сначала чистим всё внутри
        items = os.listdir(MODELS_ROOT)
        if items:
            for item in items:
                silent_delete(os.path.join(MODELS_ROOT, item))
        else:
            print("already empty: models contents")
        
        # 2. Вывод о самой папке
        if is_junction(MODELS_ROOT):
            print(f"keeped: models (Cannot call rmtree on a symbolic link)")
        else:
            silent_delete(MODELS_ROOT)
            
    else:
        # Локальный режим
        mode_path = os.path.join(MODELS_ROOT, target_mode)
        silent_delete(mode_path)

        # Если после удаления подпапки корень моделей пуст - решаем судьбу корня
        if os.path.exists(MODELS_ROOT) and not os.listdir(MODELS_ROOT):
            if is_junction(MODELS_ROOT):
                print(f"keeped: models (Cannot call rmtree on a symbolic link)")
            else:
                silent_delete(MODELS_ROOT)

def clean_db_tables(target_mode):
    """Удаляет таблицы режима и сообщает об их статусе."""
    if not os.path.exists(DB_FILE):
        print(f"already empty: database\\")
        return

    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Список таблиц для проверки
        target_tables = [target_mode, f"{target_mode}_taxonomy"]
        
        for t_name in target_tables:
            # Проверяем, существует ли таблица
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t_name,))
            if cursor.fetchone():
                cursor.execute(f"DROP TABLE IF EXISTS {t_name}")
                print(f"removed: DB table '{t_name}'")
            else:
                print(f"already empty: DB table '{t_name}'")
        
        conn.commit()

        # Проверяем, осталось ли что-то в базе кроме геохронологии
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        remaining = [r[0] for r in cursor.fetchall()]
        conn.close()

        # Если в базе только системные таблицы или геохронология — сносим всё
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
        # Глобально: удаляем всё хранилище и всю базу
        # Папка custom_lists НЕ ВХОДИТ в этот список, она в безопасности
        silent_delete(STORAGE_DIR)
        silent_delete(DB_DIR)
    else:
        # Локально: только подпапка режима в storage
        mode_storage = os.path.join(STORAGE_DIR, target)
        silent_delete(mode_storage)
        
        # Уборка пустой папки storage
        if os.path.exists(STORAGE_DIR) and not os.listdir(STORAGE_DIR):
            silent_delete(STORAGE_DIR)
            
        # Чистим только таблицы в БД
        clean_db_tables(target)

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

    if target == 'all':
        if not os.path.exists(STORAGE_DIR):
            print(f"already empty: {get_rel_path(STORAGE_DIR)}")
            return

        modes = [m for m in os.listdir(STORAGE_DIR) if os.path.isdir(os.path.join(STORAGE_DIR, m))]
        if not modes:
            print(f"already empty: {get_rel_path(STORAGE_DIR)} contents")
        else:
            for mode in modes:
                catalog_path = os.path.join(STORAGE_DIR, mode, "species_catalog.json")
                silent_delete(catalog_path)
    else:
        # Локальный путь: storage/[target]/species_catalog.json
        catalog_path = os.path.join(STORAGE_DIR, target, "species_catalog.json")
        silent_delete(catalog_path)

def main():
    while True:
        print(f"\n=== PFL RESET ===")
        print("r. Research Cleanup (Data/DB/Logs)")
        print("c. Catalog Cleanup  (species_catalog.json)") # НОВОЕ
        print("m. Models Cleanup   (Disk E Assets)")
        print("h. History Cleanup  (project_history.txt)")
        print("0. Exit")
        
        choice = input("Select: ").lower().strip()
        if choice == 'r': run_research_cleanup()
        elif choice == 'c': run_catalog_cleanup() # НОВОЕ
        elif choice == 'm':
            target = get_target_menu("MODELS CLEANUP")
            if target:
                print(f"\nConfirm MODELS: {target}? (y/n): ", end="")
                if input().lower() == 'y':
                    smart_models_wipe(target)
        elif choice == 'h':
            print(f"\nConfirm HISTORY: project_history.txt? (y/n): ", end="")
            if input().lower() == 'y':
                print() 
                silent_delete(HISTORY_FILE)
        elif choice == '0': break

if __name__ == "__main__":
    main()