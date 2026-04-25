import os
import json
import shutil
import sys
import logging
import subprocess

# [1] ПОДГОТОВКА И БЛОКИРОВКА КЭША
sys.dont_write_bytecode = True
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LOG_FILE = os.path.join(BASE_DIR, "data", "logs", "migrate_assets.log")

# Настройка логирования: Только в файл, без пустых строк
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
        with open(file_path, 'w', encoding='utf-8') as f:
            for line in lines:
                if line.startswith("genus:"):
                    f.write(f"genus: {new_genus}\n")
                elif line.startswith("species:"):
                    f.write(f"species: {new_species}\n")
                else:
                    f.write(line)
        logging.info("INTERNAL UPDATE: Metadata (genus/species) synced in info.txt")
    except Exception as e:
        logging.error(f"ERROR: Failed to update info.txt content: {e}")

def update_blender_content(blend_file, old_name, new_name):
    """Переименовывает коллекции и объекты внутри .blend файла через Blender API."""
    old_b = old_name.replace(" ", "_")
    new_b = new_name.replace(" ", "_")

    script = f"""
import bpy
for coll in bpy.data.collections:
    if "{old_b}" in coll.name:
        coll.name = coll.name.replace("{old_b}", "{new_b}")
for obj in bpy.data.objects:
    if "{old_b}" in obj.name:
        obj.name = obj.name.replace("{old_b}", "{new_b}")
bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)
"""
    try:
        subprocess.run(
            [config.BLENDER_PATH, "-b", blend_file, "--python-expr", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            check=True
        )
        logging.info("INTERNAL UPDATE: Blender collections and objects renamed via API")
    except Exception as e:
        logging.error(f"ERROR: Blender API update failed: {e}")

def migrate():
    MAP_FILE = os.path.join(BASE_DIR, "data", "exports", "known_migrations.json")
    MODELS_ROOT = os.path.join(BASE_DIR, "models")

    # СТАРТОВЫЙ ОТЧЕТ В ЛОГ
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
    logging.info("--- ASSET MIGRATION STARTED ---")

    if total_entries == 0:
        print("Nothing to migrate.")
        return

    print(f"Checking scientific updates for {total_entries} links...")
    
    migrated_count = 0
    items = list(migrations.items())

    for i, (old_full_name, new_genus) in enumerate(items, 1):
        # Консольный прогресс-бар
        sys.stdout.write(f"\rProcessing... [{i}/{total_entries}]")
        sys.stdout.flush()

        parts = old_full_name.split(' ', 1)
        if len(parts) < 2: continue
        
        old_genus, species = parts[0], parts[1]
        new_full_name = f"{new_genus} {species}"
        
        old_p = os.path.join(MODELS_ROOT, old_genus, old_full_name)
        new_genus_dir = os.path.join(MODELS_ROOT, new_genus)
        new_p = os.path.join(new_genus_dir, new_full_name)

        # Пути для логов
        log_old_path = f"models\\{old_genus}\\{old_full_name}"
        log_new_path = f"models\\{new_genus}\\{new_full_name}"

        if os.path.exists(old_p):
            has_content = any(files for _, _, files in os.walk(old_p))
            
            if has_content:
                logging.info(f"Analyzing: {old_full_name}")
                logging.info(f"MIGRATED: {old_full_name} -> {new_genus}")
                
                try:
                    os.makedirs(new_genus_dir, exist_ok=True)
                    if os.path.exists(new_p) and new_p != old_p:
                        shutil.rmtree(new_p)
                    
                    shutil.move(old_p, new_p)
                    logging.info(f"MOVED FOLDER: {log_old_path} -> {log_new_path}")
                    
                    for filename in os.listdir(new_p):
                        if old_full_name in filename:
                            new_filename = filename.replace(old_full_name, new_full_name)
                            old_file_path = os.path.join(new_p, filename)
                            new_file_path = os.path.join(new_p, new_filename)
                            
                            os.rename(old_file_path, new_file_path)
                            logging.info(f"RENAMED FILE: {filename} -> {new_filename}")

                            if new_filename.endswith("info.txt"):
                                update_info_content(new_file_path, new_genus, species)
                            
                            if new_filename.endswith(".blend"):
                                update_blender_content(new_file_path, old_full_name, new_full_name)
                    
                    logging.info(f"SUCCESS: Migration complete for {new_full_name}")
                    migrated_count += 1
                except Exception as e:
                    logging.error(f"CRITICAL ERROR for {old_full_name}: {e}")

    # ЗАВЕРШЕНИЕ
    sys.stdout.write("\n")
    print(f"Assets migrated: {migrated_count}")
    
    final_msg = f"Migration report saved to {os.path.abspath(LOG_FILE)}"
    print(final_msg)
    
    logging.info("--- ASSET MIGRATION FINISHED ---")
    logging.info(f"Total assets migrated: {migrated_count}")
    logging.info(final_msg)

if __name__ == "__main__":
    migrate()