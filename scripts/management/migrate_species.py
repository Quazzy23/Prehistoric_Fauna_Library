import os
import csv
import shutil
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

def migrate():
    BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    MAP_FILE = os.path.join(BASE_DIR, "data", "exports", "migration_map.csv")
    MODELS_ROOT = os.path.join(BASE_DIR, "models")

    if not os.path.exists(MAP_FILE):
        print("No migration map found.")
        return

    with open(MAP_FILE, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f, delimiter=';')
        for row in reader:
            old_genus, old_species = row['old_genus'], row['old_species']
            new_genus, new_species = row['new_genus'], row['new_species']
            
            old_folder_name = f"{old_genus} {old_species}"
            new_folder_name = f"{new_genus} {new_species}"
            
            old_p = os.path.join(MODELS_ROOT, old_genus, old_folder_name)
            new_genus_dir = os.path.join(MODELS_ROOT, new_genus)
            new_p = os.path.join(new_genus_dir, new_folder_name)

            if os.path.exists(old_p):
                # Проверяем наличие файлов (чтобы не двигать пустые заготовки)
                has_content = any(files for _, _, files in os.walk(old_p))
                
                if has_content:
                    print(f"[*] MIGRATING: {old_folder_name} -> {new_genus}")
                    os.makedirs(new_genus_dir, exist_ok=True)
                    
                    if os.path.exists(new_p):
                        shutil.rmtree(new_p) # Удаляем пустую заготовку, если она была
                    
                    shutil.move(old_p, new_p)
                    
                    # ПЕРЕИМЕНОВАНИЕ ФАЙЛОВ ВНУТРИ
                    for filename in os.listdir(new_p):
                        if old_folder_name in filename:
                            old_file_path = os.path.join(new_p, filename)
                            new_file_name = filename.replace(old_folder_name, new_folder_name)
                            new_file_path = os.path.join(new_p, new_file_name)
                            os.rename(old_file_path, new_file_path)
                            print(f"    [+] Renamed: {filename} -> {new_file_name}")
                else:
                    print(f"[ ] Skipping empty folder: {old_folder_name}")

if __name__ == "__main__":
    migrate()