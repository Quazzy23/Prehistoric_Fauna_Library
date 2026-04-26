import os
import json
import shutil
import sys
import logging
import subprocess
import time

# [1] ПОДГОТОВКА И БЛОКИРОВКА КЭША
sys.dont_write_bytecode = True
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import local_settings

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LOG_FILE = os.path.join(BASE_DIR, "data", "logs", "migrate_assets.log")

# Настройка логирования: Только в файл, по утвержденной структуре
file_handler = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[file_handler]
)

def update_info_content(file_path, new_genus, new_species):
    """Обновляет внутренние метаданные в файле info.txt."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        updated_lines = []
        old_genus, old_species = "", ""
        
        for line in lines:
            if line.startswith("genus:"):
                old_genus = line.split(":", 1)[1].strip()
                updated_lines.append(f"genus: {new_genus}\n")
            elif line.startswith("species:"):
                old_species = line.split(":", 1)[1].strip()
                updated_lines.append(f"species: {new_species}\n")
            else:
                updated_lines.append(line)
                
        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(updated_lines)

        logging.info(f"[INTERNAL] Synced info.txt: genus: {old_genus} -> {new_genus}, species: {old_species} -> {new_species}")  
    except Exception as e:
        logging.error(f"ERROR: Failed to update info.txt content: {e}")

def update_blender_content(blend_file, old_name, new_name):
    """Переименовывает коллекции и объекты внутри .blend файла через Blender API."""
    old_b = old_name.replace(" ", "_")
    new_b = new_name.replace(" ", "_")

    # Скрипт теперь делает "снимок" списка и пишет конкретику в stdout
    script = f"""
import bpy
old_str = "{old_b}"
new_str = "{new_b}"

# 1. Сначала коллекции
for coll in list(bpy.data.collections):
    if old_str in coll.name and not coll.name.startswith(new_str):
        original = coll.name
        coll.name = coll.name.replace(old_str, new_str)
        print(f"[INTERNAL] Renamed Collection: '{{original}}' -> '{{coll.name}}'")

# 2. Затем объекты (меши и т.д.)
for obj in list(bpy.data.objects):
    if old_str in obj.name and not obj.name.startswith(new_str):
        original = obj.name
        obj.name = obj.name.replace(old_str, new_str)
        print(f"[INTERNAL] Renamed Object: '{{original}}' -> '{{obj.name}}'")

bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)
"""
    try:
        # Запускаем Блендер и ловим его ответ
        result = subprocess.run(
            [local_settings.BLENDER_PATH, "-b", blend_file, "--python-expr", script],
            capture_output=True,
            text=True,
            encoding='utf-8',
            check=True
        )
        
        # Вытаскиваем из ответа Блендера наши строки с [INTERNAL] и пишем в лог
        for line in result.stdout.split('\n'):
            if "[INTERNAL]" in line:
                logging.info(line.strip())
                
    except Exception as e:
        logging.error(f"ERROR: Blender API update failed: {e}")

def migrate():
    # КОНСОЛЬ: СТАРТ
    print("Starting script: MIGRATE_SPECIES")

    MAP_FILE = os.path.join(BASE_DIR, "data", "exports", "known_migrations.json")
    MODELS_ROOT = os.path.join(BASE_DIR, "models")

    # СТАРТОВЫЙ ОТЧЕТ В ЛОГ
    logging.info("--- SCRIPT START: MIGRATE_SPECIES ---")
    logging.info("Configuration loaded successfully")
    logging.info(f"Opening migration source: {os.path.abspath(MAP_FILE)}")

    if not os.path.exists(MAP_FILE):
        logging.error("Migration registry file not found.")
        print("[ERROR] Migration registry not found. Check logs.")
        return

    try:
        with open(MAP_FILE, 'r', encoding='utf-8') as f:
            migrations = json.load(f)
    except Exception as e:
        logging.error(f"Failed to parse migration JSON: {e}")
        print("[ERROR] Failed to parse JSON. Check logs.")
        return

    total_entries = len(migrations)
    logging.info(f"Detected {total_entries} scientific links")

    if total_entries == 0:
        print("Nothing to migrate.")
        logging.info("Nothing to migrate.")
        print("Script ended: MIGRATE_SPECIES")
        return

    print(f"Checking scientific updates for {total_entries} links...")
    
    migrated_count = 0
    items = list(migrations.items())

    for i, (old_full_name, new_genus) in enumerate(items, 1):
        # Консольный прогресс-бар
        sys.stdout.write(f"\rProcessing... [{i}/{total_entries}]")
        time.sleep(0.0001)
        sys.stdout.flush()

        # Разбор имен
        parts = old_full_name.split(' ', 1)
        if len(parts) < 2: 
            logging.warning(f"Invalid name format in JSON: {old_full_name}")
            continue
        
        old_genus, species = parts[0], parts[1]
        new_full_name = f"{new_genus} {species}"
        
        # Пути для логов и проверки
        old_p = os.path.join(MODELS_ROOT, old_genus, old_full_name)
        new_genus_dir = os.path.join(MODELS_ROOT, new_genus)
        new_p = os.path.join(new_genus_dir, new_full_name)
        log_old_path = f"models\\{old_genus}\\{old_full_name}"
        log_new_path = f"models\\{new_genus}\\{new_full_name}"

        # Лаконичный лог по стандарту
        if os.path.exists(old_p) and any(files for _, _, files in os.walk(old_p)):
            logging.warning(f"{old_full_name}: MIGRATION REQUIRED (-> {new_genus})")
            try:
                os.makedirs(new_genus_dir, exist_ok=True)
                if os.path.exists(new_p) and new_p != old_p:
                    shutil.rmtree(new_p)
                
                shutil.move(old_p, new_p)
                migrated_count += 1
                logging.info(f"MOVED FOLDER: {log_old_path} -> {log_new_path}")
                
                for filename in os.listdir(new_p):
                        file_path = os.path.join(new_p, filename)
                        
                        # Удаляем бэкапы сразу, чтобы они не путались под ногами
                        if filename.endswith(('.blend1', '.blend2', '.blend3')):
                            os.remove(file_path)
                            logging.info(f"CLEANUP: Removed old Blender backup: {filename}")
                            continue
                            
                        # Переименовываем файл, только если в нем есть старое имя и нет нового
                        if old_full_name in filename and not filename.startswith(new_full_name):
                            new_filename = filename.replace(old_full_name, new_full_name)
                            new_file_path = os.path.join(new_p, new_filename)
                            
                            os.rename(file_path, new_file_path)
                            logging.info(f"RENAMED FILE: {filename} -> {new_filename}")

                            if new_filename.endswith("info.txt"):
                                update_info_content(new_file_path, new_genus, species)
                            
                            if new_filename.endswith(".blend"):
                                update_blender_content(new_file_path, old_full_name, new_full_name)
                
                logging.info(f"{new_full_name}: SUCCESS (Migration complete)")
            except Exception as e:
                logging.error(f"{old_full_name}: CRITICAL ERROR ({e})")
        else:
            # Одна строка на проверенный вид
            logging.info(f"{old_full_name}: OK")

    # ЗАВЕРШЕНИЕ (Консоль + Лог)
    sys.stdout.write("\n")
    print("Migration completed.")
    print(f"Assets migrated: {migrated_count}")
    
    logging.info("Migration completed successfully.")
    logging.info(f"Total assets migrated: {migrated_count}")
    logging.info("--- SCRIPT END: MIGRATE_SPECIES ---")
    print("Script ended: MIGRATE_SPECIES")

if __name__ == "__main__":
    migrate()