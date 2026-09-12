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
RESEARCH_MODE = "dinosaurs"


# [2] НАСТРОЙКИ ИСТОЧНИКОВ (SOURCE MAPPING: CLADE TREE CRAWLER)
# start_url     — верхняя граница (откуда начинаем обход)
# stop_url      — нижняя граница (ссылка, на которой останавливаемся и не идем вглубь)
# taxonomy_node — опорный таксономический узел для дерева предков
# suffixes      — суффиксы для поиска статей и шаблонов таксономии (разрешение неоднозначностей)
WIKI_SETTINGS = {
    "dinosaurs": {
        "start_url": "https://en.wikipedia.org/wiki/Dinosauromorpha",
        "stop_url": "https://en.wikipedia.org/wiki/Avialae",
        "taxonomy_node": "Dinosauromorpha",
        "suffixes": ["", "_(dinosaur)", "_(reptile)", "_(archosaur)"]
    },
    "pterosaurs": {
        "start_url": "https://en.wikipedia.org/wiki/Pterosauromorpha",
        "stop_url": None,
        "taxonomy_node": "Pterosauromorpha",
        "suffixes": ["", "_(pterosaur)", "_(reptile)"],
    },
    "ichthyosaurs": {
        "start_url": "https://en.wikipedia.org/wiki/Ichthyosauromorpha",
        "stop_url": None,
        "taxonomy_node": "Ichthyosauromorpha",
        "suffixes": ["", "_(ichthyosaur)", "_(reptile)"],
    },
    "sauropterygians": {
        "start_url": "https://en.wikipedia.org/wiki/Sauropterygiformes",
        "stop_url": None,
        "taxonomy_node": "Sauropterygiformes",
        "suffixes": [
            "", "_(plesiosaur)", "_(pliosaur)", "_(reptile)", "_(sauropterygian)"],
    },
    "mosasaurs": {
        "start_url": "https://en.wikipedia.org/wiki/Mosasauria",
        "stop_url": None,
        "taxonomy_node": "Mosasauria",
        "suffixes": ["", "_(mosasaur)", "_(reptile)", "_(lizard)"],
    },
    # Добавлены для теста
    "cariamiformes": {
        "start_url": "https://en.wikipedia.org/wiki/Cariamiformes",
        "stop_url": None,
        "taxonomy_node": "Cariamiformes",
        "suffixes": ["", "_(bird)"]
    },
    "dicynodonts": {
        "start_url": "https://en.wikipedia.org/wiki/Dicynodontia",
        "stop_url": None,
        "taxonomy_node": "Dicynodontia",
        "suffixes": ["", "_(reptile)"]
    },
    "elephants": {
        "start_url": "https://en.wikipedia.org/wiki/Proboscidea",
        "stop_url": None,
        "taxonomy_node": "Proboscidea",
        "suffixes": ["", "_(mammal)"]
    },
    "psedosuchians": {
        "start_url": "https://en.wikipedia.org/wiki/Pseudosuchia",
        "stop_url": None,
        "taxonomy_node": "Pseudosuchia",
        "suffixes": ["", "_(crocodile)"]
    },
    "archosaurs": {
        "start_url": "https://en.wikipedia.org/wiki/Archosauriformes",
        "stop_url": "https://en.wikipedia.org/wiki/Avialae",
        "taxonomy_node": "Pseudosuchia",
        "suffixes": ["", "_kk"]
    },
}

# Динамическое извлечение настроек на основе выбранного RESEARCH_MODE
_current = WIKI_SETTINGS.get(RESEARCH_MODE, WIKI_SETTINGS["dinosaurs"])

WIKI_START_URL = _current.get("start_url")
WIKI_STOP_URL = _current.get("stop_url")
TAXONOMY_START_NODE = _current["taxonomy_node"]
WIKI_SUFFIXES = _current.get("suffixes", [""])

BASE_WIKI_URL = "https://en.wikipedia.org/wiki/"
GEO_WIKI_URL = "https://en.wikipedia.org/wiki/Geologic_time_scale"


# [3] ОПРЕДЕЛЕНИЕ СЛОЯ (THE MASTER LAYER SWITCH)
# True  — РЕЖИМ MASTER (Производственный эталон).
# False — РЕЖИМ SANDBOX (Песочница).
IS_MASTER = True

MASTER_NAME  = "master"
SANDBOX_NAME = "sandbox"
DATA_LAYER   = MASTER_NAME if IS_MASTER else SANDBOX_NAME


# [4] ЛОГИКА СБОРА И ИСТОЧНИКИ ДАННЫХ (DATA INPUT & FETCH SETTINGS)
if IS_MASTER:
    # --- АВТОМАТИЧЕСКИЕ НАСТРОЙКИ ДЛЯ MASTER (НЕЛЬЗЯ СЛОМАТЬ) ---
    USE_CUSTOM_LIST          = False  # Только полный обход дерева
    FETCH_SYNONYMS           = True   # Сбор всех синонимов обязателен
    INCLUDE_NOMINA_NUDA      = True   # Сбор нудумов обязателен
    INCLUDE_UNCERTAIN_STAGES = False  # Только твердо установленные ярусы
else:
    # --- СВОБОДНЫЕ НАСТРОЙКИ ДЛЯ SANDBOX (МЕНЯЙТЕ ДЛЯ ТЕСТОВ) ---
    USE_CUSTOM_LIST          = False  # True — тест по файлу из custom_lists/
    FETCH_SYNONYMS           = True   # False — быстрый тест без синонимов
    INCLUDE_NOMINA_NUDA      = True   # False — исключить нудумы
    INCLUDE_UNCERTAIN_STAGES = False  # True — включать "Possible Albian"

CUSTOM_LIST_NAME = "test_genera.txt"
CREATE_CUSTOM_LIST_DIR = True


# [5] ПРОИЗВОДИТЕЛЬНОСТЬ (PERFORMANCE)
# USE_PARALLEL: Использовать многопоточность для ускорения парсинга (ThreadPool).
# MAX_WORKERS: Количество одновременных потоков. Рекомендуется 20-25.
USE_PARALLEL = True
MAX_WORKERS = 20


# [6] НАСТРОЙКИ БАЗЫ ДАННЫХ (DATABASE)
# Все данные хранятся в едином файле prehistoric_library.sqlite
DB_NAME = "prehistoric_library.sqlite"

# Названия таблиц формируются динамически
TABLE_SPECIES = RESEARCH_MODE             # Например: "dinosaurs" или "pterosaurs"
TABLE_TAXONOMY = f"{RESEARCH_MODE}_taxonomy" # Например: "dinosaurs_taxonomy"
TABLE_GEOLOGY = "geological_time"         # Общая таблица для всех групп


# [7] ДИНАМИЧЕСКИЕ ПУТИ К РЕЕСТРАМ (REGISTRY PATHS)
CUSTOM_LISTS_DIR = "custom_lists"
STORAGE_BASE_NAME = "export"

# --- А) ПУТИ ДЛЯ ИССЛЕДОВАНИЯ (RESEARCH) ---
# Эти пути меняются в зависимости от флагов (master/sandbox)
STORAGE_ROOT  = os.path.join(STORAGE_BASE_NAME, DATA_LAYER, RESEARCH_MODE)
TABLES_DIR    = os.path.join(STORAGE_ROOT, "tables")
SNAPSHOTS_DIR = os.path.join(STORAGE_ROOT, "snapshots")

MASTER_CATALOG   = os.path.join(STORAGE_ROOT, "species_catalog.json")
DELETED_REGISTRY = os.path.join(STORAGE_ROOT, "deleted_registry.json")
MIGRATIONS_FILE  = os.path.join(STORAGE_ROOT, "known_migrations.json")

# --- Б) ПУТИ ДЛЯ ПРОИЗВОДСТВА (PRODUCTION) ---
# Эти пути ВСЕГДА ведут в master, чтобы защитить реальные папки моделей
PROD_ROOT           = os.path.join(STORAGE_BASE_NAME, MASTER_NAME, RESEARCH_MODE)
PROD_TABLES_DIR     = os.path.join(PROD_ROOT, "tables")
PROD_MASTER_CATALOG = os.path.join(PROD_ROOT, "species_catalog.json")
PROD_DELETED_REG    = os.path.join(PROD_ROOT, "deleted_registry.json")
PROD_MIGRATIONS     = os.path.join(PROD_ROOT, "known_migrations.json")

# База и история (динамические)
MASTER_DB_NAME = "prehistoric_library.sqlite"
SANDBOX_DB_NAME = "sandbox_library.sqlite"
DB_NAME = MASTER_DB_NAME if IS_MASTER else SANDBOX_DB_NAME
HISTORY_FILE = "project_history.txt" if IS_MASTER else "sandbox_history.txt"


# [8] НАУЧНАЯ ИЕРАРХИЯ СТАТУСОВ (SCIENTIFIC WEIGHTS)
# 4 базовых научных статуса PFL
STATUS_WEIGHTS = {
    'nudum': 1,
    'dubious': 2,
    'provisional': 3,
    'valid': 4
}