import os
import csv
import sys

# [!] ИСПРАВЛЕНИЕ ПУТИ: config лежит в той же папке /scripts/
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

def create_structure():
    # BASE_DIR указывает на корень проекта
    BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    INPUT_CSV = os.path.join(BASE_DIR, "data", "exports", "dinosaurs_for_models.csv")
    MODELS_ROOT = os.path.join(BASE_DIR, "models")

    if not os.path.exists(INPUT_CSV):
        print(f"Error: CSV file not found! Run utils/export_db_to_csv.py first.")
        return

    print(f"Starting folder structure generation in: {MODELS_ROOT}")
    
    created_count = 0
    
    with open(INPUT_CSV, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f, delimiter=';')
        for row in reader:
            # Чистим данные
            raw_genus = row['genus'].strip()
            raw_species = row['species'].strip()
            
            # Название папки рода: Всегда с большой буквы
            genus_folder = raw_genus.capitalize()
            
            # Название папки вида: Полное имя, как ты просил (Tyrannosaurus rex)
            if raw_species in ["-", "", "sp.", "null"]:
                species_folder = genus_folder
            else:
                species_folder = f"{genus_folder} {raw_species.lower()}"

            # Пути
            genus_path = os.path.join(MODELS_ROOT, genus_folder)
            species_path = os.path.join(genus_path, species_folder)
            
            # Создаем папки
            os.makedirs(os.path.join(species_path, "sources"), exist_ok=True)
            os.makedirs(os.path.join(species_path, "textures"), exist_ok=True)
            
            # Файл инфо: "Tyrannosaurus rex info.txt"
            info_file_name = f"{species_folder} info.txt"
            info_file_path = os.path.join(species_path, info_file_name)
            
            if not os.path.exists(info_file_path):
                template = (
                    f"base_specimen:\n"
                    f"scale_specimen:\n"
                    f"skeletal:\n"
                    f"model:\n"
                    f"texture:\n"
                    f"rig:\n"
                )
                with open(info_file_path, 'w', encoding='utf-8') as info_f:
                    info_f.write(template)
                created_count += 1

    print(f"Done! New info files initialized: {created_count}")

if __name__ == "__main__":
    create_structure()