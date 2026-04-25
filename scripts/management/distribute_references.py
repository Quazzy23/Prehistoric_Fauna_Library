import sys
sys.dont_write_bytecode = True  # Сначала запрещаем
import os
import shutil
import re

# Настройки путей
# Источник (где лежат твои скачанные файлы)
SOURCE_DIR = r"E:\Изображения\Dinosaurus\Dinosaurus 4.0\Оригиналы"

# Цель (наш проект через Junction на диске D)
# Мы предполагаем, что скрипт лежит в /scripts/utils/
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MODELS_DIR = os.path.join(BASE_DIR, "models")

# Поддерживаемые расширения файлов
EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp', '.pdf')

def get_genus_from_filename(filename):
    """
    Извлекает первое слово до первого разделителя (_ или -).
    Например: 'abditosaurus_by_...' -> 'abditosaurus'
    'diplodocus-by-...' -> 'diplodocus'
    """
    # Регулярное выражение: берем все буквы в начале строки до первого символа, не являющегося буквой
    match = re.match(r'^([a-zA-Z]+)', filename)
    if match:
        return match.group(1).lower()
    return None

def distribute():
    if not os.path.exists(SOURCE_DIR):
        print(f"Ошибка: Папка источника не найдена: {SOURCE_DIR}")
        return
    if not os.path.exists(MODELS_DIR):
        print(f"Ошибка: Папка моделей не найдена: {MODELS_DIR}")
        return

    print(f"[*] Сканирование источника: {SOURCE_DIR}")
    
    # 1. Собираем карту всех доступных файлов: { 'род': [пути_к_файлам] }
    source_map = {}
    for root, dirs, files in os.walk(SOURCE_DIR):
        for file in files:
            if file.lower().endswith(EXTENSIONS):
                genus = get_genus_from_filename(file)
                if genus:
                    if genus not in source_map:
                        source_map[genus] = []
                    source_map[genus].append(os.path.join(root, file))

    print(f"[*] Карта референсов построена. Найдено родов в источнике: {len(source_map)}")

    # 2. Проходим по папкам родов, которые уже созданы в models/
    processed_genera = 0
    copied_files_count = 0

    # Список папок в корне models (например: ['Diplodocus', 'Tyrannosaurus'])
    current_model_genera = [d for d in os.listdir(MODELS_DIR) if os.path.isdir(os.path.join(MODELS_DIR, d))]

    for g_folder in current_model_genera:
        g_key = g_folder.lower() # Ключ для поиска в карте (diplodocus)
        
        if g_key in source_map:
            processed_genera += 1
            files_to_copy = source_map[g_key]
            
            # Находим все папки видов внутри этого рода
            genus_path = os.path.join(MODELS_DIR, g_folder)
            species_folders = [d for d in os.listdir(genus_path) if os.path.isdir(os.path.join(genus_path, d))]
            
            for s_folder in species_folders:
                # Путь к папке sources конкретного вида
                target_sources_dir = os.path.join(genus_path, s_folder, "sources")
                os.makedirs(target_sources_dir, exist_ok=True)
                
                for src_file_path in files_to_copy:
                    file_name = os.path.basename(src_file_path)
                    dest_path = os.path.join(target_sources_dir, file_name)
                    
                    # Копируем только если файла там еще нет
                    if not os.path.exists(dest_path):
                        try:
                            shutil.copy2(src_file_path, dest_path)
                            copied_files_count += 1
                        except Exception as e:
                            print(f"[!] Ошибка при копировании {file_name}: {e}")

    print("\n" + "="*30)
    print(f"ИТОГ РАБОТЫ:")
    print(f"Обработано родов: {processed_genera}")
    print(f"Всего скопировано файлов: {copied_files_count}")
    print("="*30)

if __name__ == "__main__":
    distribute()