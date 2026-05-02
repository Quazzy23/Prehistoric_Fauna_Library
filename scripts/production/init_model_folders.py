import sys
sys.dont_write_bytecode = True
import os
import json
import logging
import time
import shutil
import subprocess

# [1] ПОДГОТОВКА ПУТЕЙ И КОНФИГА
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import metadata_utils
import local_settings

# [1] ПОДГОТОВКА ПУТЕЙ
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Папки моделей теперь разделены: models/dinosaurs, models/pterosaurs и т.д.
MODELS_ROOT = os.path.join(BASE_DIR, "models", config.RESEARCH_MODE)
DELETED_ROOT = os.path.join(MODELS_ROOT, "_deleted_")

# Реестры (динамически из config.py)
CATALOG_PATH = os.path.join(BASE_DIR, config.MASTER_CATALOG)
MIGRATIONS_FILE = os.path.join(BASE_DIR, config.MIGRATIONS_FILE)
LOG_FILE = os.path.join(BASE_DIR, "data", "logs", "init_model_folders.log")

def update_info_content(file_path, new_genus, new_species):
    """Обновляет внутренние метаданные в файле info.txt (Оригинальная логика)."""
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
        # Тот самый лог из мигратора
        logging.warning(f"[INTERNAL] Synced info.txt: genus: {old_genus} -> {new_genus}, species: {old_species} -> {new_species}")  
    except Exception as e:
        logging.error(f"ERROR: Failed to update info.txt content: {e}")

def update_blender_content(blend_file, old_name, new_name):
    """Переименовывает коллекции и объекты внутри .blend (Оригинальная логика)."""
    old_b = old_name.replace(" ", "_")
    new_b = new_name.replace(" ", "_")
    script = f"""
import bpy
old_str = "{old_b}"
new_str = "{new_b}"
for coll in list(bpy.data.collections):
    if old_str in coll.name and not coll.name.startswith(new_str):
        original = coll.name
        coll.name = coll.name.replace(old_str, new_str)
        print(f"[INTERNAL] Renamed Collection: '{{original}}' -> '{{coll.name}}'")
for obj in list(bpy.data.objects):
    if old_str in obj.name and not obj.name.startswith(new_str):
        original = obj.name
        obj.name = obj.name.replace(old_str, new_str)
        print(f"[INTERNAL] Renamed Object: '{{original}}' -> '{{obj.name}}'")
bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)
"""
    try:
        result = subprocess.run(
            [local_settings.BLENDER_PATH, "-b", blend_file, "--python-expr", script],
            capture_output=True, text=True, encoding='utf-8', check=True
        )
        for line in result.stdout.split('\n'):
            if "[INTERNAL]" in line:
                logging.warning(line.strip())
    except Exception as e:
        logging.error(f"ERROR: Blender API update failed: {e}")

def create_structure():
    print("Starting script: INIT_MODEL_FOLDERS")
    
    file_handler = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[file_handler]
    )

    logging.info("--- SCRIPT START: INIT_MODEL_FOLDERS ---")
    logging.info("Configuration loaded successfully")
    logging.info(f"Opening species source: {os.path.abspath(CATALOG_PATH)}")
    logging.info(f"Opening migration source: {os.path.abspath(MIGRATIONS_FILE)}")

    if not os.path.exists(CATALOG_PATH):
        print(f"[ERROR] Catalog not found: {CATALOG_PATH}")
        return

    with open(CATALOG_PATH, 'r', encoding='utf-8') as f:
        catalog = json.load(f)
    
    # Создаем множество имен живых видов (для быстрой проверки)
    active_names = {f"{e['genus']} {e['species']}".lower() for e in catalog}
    
    migrations = {}
    if os.path.exists(MIGRATIONS_FILE):
        with open(MIGRATIONS_FILE, 'r', encoding='utf-8') as f:
            migrations = json.load(f)

    total_species = len(catalog)
    logging.info(f"Detected {total_species} species in catalog")
    print(f"Total species to process: {total_species}")

    moved_count = 0
    created_count = 0
    archived_count = 0     # Добавлено
    resurrected_count = 0  # Добавлено

    for i, entry in enumerate(catalog, 1):
        sys.stdout.write(f"\rBuilding... [{i}/{total_species}]")
        sys.stdout.flush()

        genus = entry['genus'].strip().capitalize()
        species = entry['species'].strip().lower()
        full_name = f"{genus} {species}"
        
        target_dir = os.path.join(MODELS_ROOT, genus, full_name)
        info_file_path = os.path.join(target_dir, f"{full_name} info.txt")
        
        migration_performed = False # Флаг для исключения двойного лога

        # [0] ЛОГИКА ВОСКРЕШЕНИЯ (Проверка в архиве)
        if not os.path.exists(target_dir):
            archive_path = os.path.join(DELETED_ROOT, full_name)
            if os.path.exists(archive_path):
                logging.warning(f"[PHOENIX] Resurrecting from _deleted_: {full_name}")
                os.makedirs(os.path.dirname(target_dir), exist_ok=True)
                shutil.move(archive_path, target_dir)
                resurrected_count += 1 # Добавлено
                # Теперь папка на месте, и дальше скрипт обновит в ней info.txt

        # [1] ЛОГИКА МИГРАЦИИ
        if not os.path.exists(target_dir):
            old_full_name = None
            for old_n, new_g in migrations.items():
                if new_g.lower() == genus.lower() and old_n.split()[-1].lower() == species:
                    # 1. Проверяем в основном корне (обычный переезд)
                    old_path_root = os.path.join(MODELS_ROOT, old_n.split()[0], old_n)
                    # 2. Проверяем в архиве (воскрешение с переездом)
                    old_path_arch = os.path.join(DELETED_ROOT, old_n)
                    
                    if os.path.exists(old_path_root):
                        old_full_name = old_n
                        old_path = old_path_root
                        break
                    elif os.path.exists(old_path_arch):
                        old_full_name = old_n
                        old_path = old_path_arch
                        logging.warning(f"[PHOENIX] Found old version in archive for migration: {old_n}")
                        break
            
            if old_full_name:
                logging.warning(f"{old_full_name}: MIGRATION REQUIRED (-> {genus})")
                try:
                    os.makedirs(os.path.dirname(target_dir), exist_ok=True)
                    shutil.move(old_path, target_dir)
                    # [CLEANUP] Удаляем пустую папку старого рода (если в ней никого не осталось)
                    old_genus_dir = os.path.dirname(old_path)
                    if os.path.exists(old_genus_dir) and not os.listdir(old_genus_dir):
                        try:
                            os.rmdir(old_genus_dir)
                            logging.warning(f"CLEANUP: Removed empty old genus folder: {os.path.basename(old_genus_dir)}")
                        except: pass
                    moved_count += 1
                    migration_performed = True
                    
                    log_old = f"models\\{old_full_name.split()[0]}\\{old_full_name}"
                    log_new = f"models\\{genus}\\{full_name}"
                    logging.warning(f"MOVED FOLDER: {log_old} -> {log_new}")
                    
                    # ЭТАП 1: ГАРАНТИРОВАННАЯ ОЧИСТКА (Сначала удаляем всё лишнее)
                    all_files = os.listdir(target_dir)
                    for filename in all_files:
                        file_path = os.path.join(target_dir, filename)
                        
                        # Проверяем, является ли файл бэкапом (.blend1, .blend2 и т.д.)
                        ext = os.path.splitext(filename)[1].lower()
                        if ext.startswith('.blend') and ext[6:].isdigit():
                            try:
                                os.remove(file_path)
                                logging.warning(f"CLEANUP: Removed old Blender backup: {filename}")
                            except Exception as e:
                                logging.error(f"CLEANUP ERROR: Could not remove {filename}: {e}")

                    # ЭТАП 2: ПЕРЕИМЕНОВАНИЕ (Только для оставшихся файлов)
                    for filename in os.listdir(target_dir):
                        file_path = os.path.join(target_dir, filename)
                        
                        if old_full_name in filename and not filename.startswith(full_name):
                            new_fn = filename.replace(old_full_name, full_name)
                            new_fp = os.path.join(target_dir, new_fn)
                            
                            os.rename(file_path, new_fp)
                            logging.warning(f"RENAMED FILE: {filename} -> {new_fn}")

                            if new_fn.endswith(".blend"):
                                update_blender_content(new_fp, old_full_name, full_name)
                                # ГАРАНТИРОВАННАЯ ЧИСТОТА: Удаляем любые свежие бэкапы (от .blend1 до .blend5)
                                for b_ext in range(1, 6):
                                    fresh_backup = f"{new_fp}{b_ext}"
                                    if os.path.exists(fresh_backup):
                                        os.remove(fresh_backup)
                                        logging.warning(f"CLEANUP: Removed fresh Blender backup: {new_fn}{b_ext}")
                            if new_fn.endswith("info.txt"):
                                update_info_content(new_fp, genus, species)
                    
                    logging.warning(f"{full_name}: SUCCESS (Migration complete)")
                except Exception as e:
                    logging.error(f"{old_full_name}: CRITICAL ERROR ({e})")

        # [2] ЛОГИКА СОЗДАНИЯ
        is_new = False
        if not os.path.exists(target_dir):
            for sub in ["sources", "textures", "skeletal"]:
                os.makedirs(os.path.join(target_dir, sub), exist_ok=True)
            is_new = True
            created_count += 1

        # [3] ОБНОВЛЕНИЕ INFO.TXT
        template = metadata_utils.get_info_template(genus, species, entry['status'], data=entry)
        with open(info_file_path, 'w', encoding='utf-8') as f:
            f.write(template)

        if is_new:
            log_p = f"models\\{genus}\\{full_name}"
            logging.warning(f"{full_name}: CREATED (Path: {log_p})")
        elif not migration_performed:
            # Лог OK пишем только если не было миграции и создания
            logging.info(f"{full_name}: OK")

    # [4] ЛОГИКА АРХИВАЦИИ (Уборка)
    # Собираем список активных имен для проверки
    active_names = {f"{e['genus']} {e['species']}".lower() for e in catalog}
    
    for genus_name in os.listdir(MODELS_ROOT):
        genus_path = os.path.join(MODELS_ROOT, genus_name)
        if genus_name.startswith('.') or genus_name == "_deleted_" or not os.path.isdir(genus_path):
            continue
            
        for species_folder in os.listdir(genus_path):
            species_abs = os.path.join(genus_path, species_folder)
            if not os.path.isdir(species_abs): continue
            
            if species_folder.lower() not in active_names:
                archive_target = os.path.join(DELETED_ROOT, species_folder)
                os.makedirs(DELETED_ROOT, exist_ok=True)
                
                if os.path.exists(archive_target):
                    shutil.rmtree(archive_target)
                
                try:
                    shutil.move(species_abs, archive_target)
                    logging.warning(f"[ARCHIVE] Archived to _deleted_: {species_folder}")
                    archived_count += 1
                    
                    if not os.listdir(genus_path):
                        os.rmdir(genus_path)
                except Exception as e:
                    logging.error(f"[ARCHIVE] Failed to archive {species_folder}: {e}")

    # Уборка папки архива, если она пустая
    if os.path.exists(DELETED_ROOT) and not os.listdir(DELETED_ROOT):
        os.rmdir(DELETED_ROOT)
        logging.info("[CLEANUP] Deleted empty _deleted_ directory")

    # ПУНКТ 1: Уборка папки _deleted_ если она пустая
    if os.path.exists(DELETED_ROOT) and not os.listdir(DELETED_ROOT):
        os.rmdir(DELETED_ROOT)
        logging.info("[CLEANUP] Deleted empty _deleted_ directory")


    # [5] ЗАВЕРШЕНИЕ (Вывод итогов)
    sys.stdout.write("\n")
    print("Initialization completed.")
    print(f"Folders migrated: {moved_count}")
    print(f"Folders resurrected: {resurrected_count}")
    print(f"Folders archived: {archived_count}")
    print(f"New folders created: {created_count}")
    
    # Лаконичный лог в одну строку без лишних переменных
    logging.info(f"Initialization completed successfully. Migrated: {moved_count}, Resurrected: {resurrected_count}, Archived: {archived_count}, Created: {created_count}")
    logging.info("--- SCRIPT END: INIT_MODEL_FOLDERS ---")
    print("Script ended: INIT_MODEL_FOLDERS")

if __name__ == "__main__":
    create_structure()