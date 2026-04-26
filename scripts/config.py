import sys
import os

# [0] СИСТЕМНЫЕ НАСТРОЙКИ
sys.dont_write_bytecode = True  # Глобальный запрет на создание папок __pycache__

# ========================================================================
# ГЛОБАЛЬНЫЕ НАСТРОЙКИ PREHISTORIC FAUNA LIBRARY (CORE)
# ========================================================================

# [1] ИСТОЧНИКИ ДАННЫХ
# Флаг USE_CUSTOM_LIST определяет, откуда скрипт fetch_details берет роды:
# True  — из вашего файла в папке /data/custom_lists/
# False — из общего списка Wikipedia (сгенерированного первым скриптом)
USE_CUSTOM_LIST = False
CUSTOM_LIST_NAME = "genera.txt"

# Автоматически создавать папку /data/custom_lists/, если она отсутствует
CREATE_CUSTOM_LIST_DIR = True


# [2] ЛОГИКА СБОРА ДАННЫХ (FETCH SETTINGS)
# FETCH_SYNONYMS: Собирать ли синонимы со страниц динозавров
# INCLUDE_NOMINA_NUDA: Собирать ли роды без научного описания (nomen nudum).
# EXCLUDE_UNCERTAIN_STAGES: Игнорировать сомнительные упоминания возрастов (напр. "Possible Albian")
FETCH_SYNONYMS = True
INCLUDE_NOMINA_NUDA = True
EXCLUDE_UNCERTAIN_STAGES = True


# [3] ТАКСОНОМИЯ И КЛАССИФИКАЦИЯ
# Таксон, с которого начинается отсчет дерева (всё, что выше него, игнорируется).
TAXONOMY_START_NODE = "Dinosauromorpha"


# [4] ПРОИЗВОДИТЕЛЬНОСТЬ
# USE_PARALLEL: Использовать многопоточность для ускорения парсинга.
# MAX_WORKERS: Количество одновременных запросов к Wikipedia. Не ставьте больше 20-25.
USE_PARALLEL = True
MAX_WORKERS = 20


# [5] НАСТРОЙКИ БАЗЫ ДАННЫХ (SQLITE)
# Имя файла и названия таблиц в вашей итоговой базе данных.
DB_NAME = "dinosaurs.sqlite"

TABLE_SPECIES  = "dinosaurs"       # Основная таблица видов
TABLE_TAXONOMY = "taxonomy"        # Иерархия классификации
TABLE_GEOLOGY  = "geological_time" # Геохронологический справочник


# [6] НАУЧНАЯ ИЕРАРХИЯ СТАТУСОВ (INTERNAL)
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


# [7] ТЕХНИЧЕСКИЕ ССЫЛКИ (URLS)
BASE_WIKI_URL = "https://en.wikipedia.org/wiki/"
WIKI_LIST_URL = "https://en.wikipedia.org/wiki/List_of_dinosaur_genera"
GEO_WIKI_URL  = "https://en.wikipedia.org/wiki/Geologic_time_scale"