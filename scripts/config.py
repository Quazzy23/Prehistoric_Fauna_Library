import os
import sys
sys.dont_write_bytecode = True  # Глобальный запрет на __pycache__

# ========================================================================
# ГЛОБАЛЬНЫЕ НАСТРОЙКИ PREHISTORIC FAUNA LIBRARY
# ========================================================================

# [1] ИДЕНТИФИКАЦИЯ
# Wikipedia просит указывать контактный email для ботов.
# Это помогает избежать блокировок при интенсивных запросах.
USER_EMAIL = "23spinosaurus@mail.ru"


# [2] ИСТОЧНИКИ ДАННЫХ
# Флаг USE_CUSTOM_LIST определяет, откуда скрипт fetch_details берет роды:
# True  — из вашего файла в папке /data/custom_lists/
# False — из общего списка Wikipedia (сгенерированного первым скриптом)
USE_CUSTOM_LIST = False
CUSTOM_LIST_NAME = "genera.txt"

# Автоматически создавать папку /data/custom_lists/, если она отсутствует
CREATE_CUSTOM_LIST_DIR = True


# [3] ЛОГИКА СБОРА ДАННЫХ (FETCH SETTINGS)
# FETCH_SYNONYMS: Собирать ли синонимы со страниц динозавров
# INCLUDE_NOMINA_NUDA: Собирать ли роды без научного описания (nomen nudum).
# EXCLUDE_UNCERTAIN_STAGES: Игнорировать сомнительные упоминания возрастов (напр. "Possible Albian")
FETCH_SYNONYMS = True
INCLUDE_NOMINA_NUDA = True
EXCLUDE_UNCERTAIN_STAGES = True


# [4] ТАКСОНОМИЯ И КЛАССИФИКАЦИЯ
# Таксон, с которого начинается отсчет дерева (всё, что выше него, игнорируется).
TAXONOMY_START_NODE = "Dinosauromorpha"


# [5] ПРОИЗВОДИТЕЛЬНОСТЬ
# USE_PARALLEL: Использовать многопоточность для ускорения парсинга (в 10-15 раз быстрее).
# MAX_WORKERS: Количество одновременных запросов к Wikipedia. Не ставьте больше 20-25.
USE_PARALLEL = True
MAX_WORKERS = 20


# [6] НАСТРОЙКИ БАЗЫ ДАННЫХ (SQLITE)
# Имя файла и названия таблиц в вашей итоговой базе данных.
DB_NAME = "dinosaurs.sqlite"

TABLE_SPECIES  = "dinosaurs"       # Основная таблица видов
TABLE_TAXONOMY = "taxonomy"        # Иерархия классификации
TABLE_GEOLOGY  = "geological_time" # Геохронологический справочник


# [7] НАУЧНАЯ ИЕРАРХИЯ СТАТУСОВ (INTERNAL)
# Веса валидности для разрешения конфликтов. Чем меньше число, тем важнее статус.
STATUS_WEIGHTS = {
    'excluded': 0,
    'synonym': 1,
    'nudum': 1,
    'preoccupied': 1,
    'possible synonym': 2,
    'possible nudum': 2,
    'dubious': 3,
    'valid': 4
}


# [8] ТЕХНИЧЕСКИЕ ССЫЛКИ (URLS)
BASE_WIKI_URL = "https://en.wikipedia.org/wiki/"
WIKI_LIST_URL = "https://en.wikipedia.org/wiki/List_of_dinosaur_genera"
GEO_WIKI_URL  = "https://en.wikipedia.org/wiki/Geologic_time_scale"


# [9] Путь к исполняемому файлу Blender для фоновых задач
# (Обязательно используй префикс r перед кавычками, чтобы Windows понимала слэши)
BLENDER_PATH = r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"