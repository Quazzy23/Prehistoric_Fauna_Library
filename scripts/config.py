# ========================================================================
#
# PREHISTORIC FAUNA LIBRARY (PFL)
# Global Configuration & Ecosystem DNA
#
# This file governs the research pipeline, scientific weights,
# and dynamic path mapping for the entire production ecosystem.
#
# ========================================================================

import sys
import os

# [0] СИСТЕМНЫЕ НАСТРОЙКИ (SYSTEM)
# Глобальный запрет на создание папок __pycache__
sys.dont_write_bytecode = True

# BRIEF_CONSOLE: Режим вывода в консоль
# True  — лаконичные отчеты (одна строка на скрипт)
# False — подробная техническая информация и прогресс-бары
BRIEF_CONSOLE = False

# ПУТЬ К ЛОГАМ (Вынос за пределы проекта)
# Динамическое определение пути к логам в AppData текущего пользователя
# Это будет работать на любой машине автоматически
LOGS_DIR = os.path.join(os.getenv('LOCALAPPDATA', ''), 'PFL_Library', 'logs')


# [1] ГЛОБАЛЬНЫЙ РЕЖИМ ИССЛЕДОВАНИЯ (THE MASTER SWITCH)
# Выберите группу животных, с которой хотите работать. 
# Это влияет на выбор ссылок, имена таблиц в БД и структуру папок.
RESEARCH_MODE = "dinosaurs" # Доступные варианты: "dinosaurs", "pterosaurs"


# [2] НАСТРОЙКИ ИСТОЧНИКОВ (SOURCE MAPPING)
# Конфигурация для разных типов фауны
WIKI_SETTINGS = {
    "dinosaurs": {
        "list_url": "https://en.wikipedia.org/wiki/List_of_dinosaur_genera",
        "taxonomy_node": "Dinosauromorpha"
    },
    "pterosaurs": {
        "list_url": "https://en.wikipedia.org/wiki/List_of_pterosaur_genera",
        "taxonomy_node": "Pterosauria"
    }
}

# Динамическое извлечение настроек на основе выбранного RESEARCH_MODE
_current = WIKI_SETTINGS.get(RESEARCH_MODE, WIKI_SETTINGS["dinosaurs"])

WIKI_LIST_URL = _current["list_url"]
TAXONOMY_START_NODE = _current["taxonomy_node"]
BASE_WIKI_URL = "https://en.wikipedia.org/wiki/"
GEO_WIKI_URL = "https://en.wikipedia.org/wiki/Geologic_time_scale"


# [3] ИСТОЧНИКИ ДАННЫХ (DATA INPUT)
# USE_CUSTOM_LIST: Откуда скрипт берет список родов для глубокого парсинга:
# True  — из вашего файла в папке /data/custom_lists/ (например, для тестов)
# False — из общего списка Wikipedia (сгенерированного первым скриптом)
USE_CUSTOM_LIST = True
CUSTOM_LIST_NAME = "sample_genera.txt"

# Автоматически создавать папку /data/custom_lists/ и файл-образцы
CREATE_CUSTOM_LIST_DIR = True


# [4] ЛОГИКА СБОРА ДАННЫХ (FETCH SETTINGS)
# FETCH_SYNONYMS: Собирать ли синонимы со страниц видов
# INCLUDE_NOMINA_NUDA: Собирать ли роды без научного описания (nomen nudum).
# EXCLUDE_UNCERTAIN_STAGES: Игнорировать сомнительные упоминания возрастов (напр. "Possible Albian")
FETCH_SYNONYMS = True
INCLUDE_NOMINA_NUDA = True
EXCLUDE_UNCERTAIN_STAGES = True


# [5] ПРОИЗВОДИТЕЛЬНОСТЬ (PERFORMANCE)
# USE_PARALLEL: Использовать многопоточность для ускорения парсинга (ThreadPool).
# MAX_WORKERS: Количество одновременных потоков. Рекомендуется 20-25.
USE_PARALLEL = True
MAX_WORKERS = 20


# [6] НАСТРОЙКИ БАЗЫ ДАННЫХ (DATABASE)
# Все данные хранятся в едином файле prehistoric_library.sqlite
DB_NAME = "prehistoric_library.sqlite"

# Названия таблиц формируются динамически
TABLE_SPECIES  = RESEARCH_MODE             # Например: "dinosaurs" или "pterosaurs"
TABLE_TAXONOMY = f"{RESEARCH_MODE}_taxonomy" # Например: "dinosaurs_taxonomy"
TABLE_GEOLOGY  = "geological_time"         # Общая таблица для всех групп


# [7] ДИНАМИЧЕСКИЕ ПУТИ К РЕЕСТРАМ (REGISTRY PATHS)
# Папка для ваших ручных списков
CUSTOM_LISTS_DIR = "custom_lists"

# Корневая папка хранилища для текущего режима
STORAGE_BASE_NAME = "export" 
STORAGE_ROOT = os.path.join(STORAGE_BASE_NAME, RESEARCH_MODE)

# Подпапки внутри хранилища (унификация для всех скриптов)
TABLES_DIR    = os.path.join(STORAGE_ROOT, "tables")
SNAPSHOTS_DIR = os.path.join(STORAGE_ROOT, "snapshots")

# Основные реестры (пути относительно корня проекта)
MASTER_CATALOG   = os.path.join(STORAGE_ROOT, "species_catalog.json")
DELETED_REGISTRY = os.path.join(STORAGE_ROOT, "deleted_registry.json")
MIGRATIONS_FILE  = os.path.join(STORAGE_ROOT, "known_migrations.json")


# [8] НАУЧНАЯ ИЕРАРХИЯ СТАТУСОВ (SCIENTIFIC WEIGHTS)
# Веса валидности для разрешения конфликтов. Чем меньше число, тем статус важнее.
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