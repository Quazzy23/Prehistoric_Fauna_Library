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

# ПУТЬ К ЛОГАМ (Вынос за пределы репозитория в системное хранилище Windows)
LOGS_DIR = os.path.join(os.getenv('LOCALAPPDATA', ''), 'PFL_Library', 'logs')

# БАЗОВЫЕ URL ВИКИПЕДИИ
BASE_WIKI_URL = "https://en.wikipedia.org"
GEO_WIKI_URL = f"{BASE_WIKI_URL}/wiki/Geologic_time_scale"


# [1] ГЛОБАЛЬНЫЙ РЕЖИМ ИССЛЕДОВАНИЯ (THE MASTER SWITCH)
# Выберите группу животных, с которой хотите работать. 
# Это влияет на выбор ссылок, имена таблиц в БД и структуру папок.
RESEARCH_MODE = "elephants"


# [2] НАСТРОЙКИ ИСТОЧНИКОВ (SOURCE MAPPING: CLADE TREE CRAWLER)
# start_article — начальная статья (откуда начинаем обход)
# stop_article  — стоп-граница (одиночная статья, список статей или None)
# taxonomy_node — опорный таксономический узел для дерева предков (Фильтр Бизона)
WIKI_SETTINGS = {
    "dinosaurs": {
        "start_article": "Dinosauromorpha",
        "stop_article": ["Avialae"],
        "taxonomy_node": "Dinosauromorpha"
    },
    "pterosaurs": {
        "start_article": "Pterosauromorpha",
        "stop_article": None,
        "taxonomy_node": "Pterosauromorpha"
    },
    "ichthyosaurs": {
        "start_article": "Ichthyosauromorpha",
        "stop_article": None,
        "taxonomy_node": "Ichthyosauromorpha"
    },
    "sauropterygians": {
        "start_article": "Sauropterygiformes",
        "stop_article": None,
        "taxonomy_node": "Sauropterygiformes"
    },
    "mosasaurs": {
        "start_article": "Mosasauria",
        "stop_article": None,
        "taxonomy_node": "Mosasauria"
    },
    "cariamiformes": {
        "start_article": "Cariamiformes",
        "stop_article": None,
        "taxonomy_node": "Cariamiformes"
    },
    "dicynodonts": {
        "start_article": "Dicynodontia",
        "stop_article": None,
        "taxonomy_node": "Dicynodontia"
    },
    "elephants": {
        "start_article": "Elephantidae",
        "stop_article": None,
        "taxonomy_node": "Elephantidae"
    },
    "pseudosuchians": {
        "start_article": "Pseudosuchia",
        "stop_article": None,
        "taxonomy_node": "Pseudosuchia"
    },
    "archosaurs": {
        "start_article": "Archosauriformes",
        "stop_article": ["Avialae"],
        "taxonomy_node": "Archosauriformes"
    },
}

# Динамическое извлечение и сборка URL на основе выбранного RESEARCH_MODE
_current = WIKI_SETTINGS.get(RESEARCH_MODE, WIKI_SETTINGS["dinosaurs"])

WIKI_START_URL = f"{BASE_WIKI_URL}/wiki/{_current['start_article']}"

# Сборка WIKI_STOP_URL (поддержка строки, списка или None)
_raw_stop = _current.get("stop_article")
if isinstance(_raw_stop, list):
    WIKI_STOP_URL = [f"{BASE_WIKI_URL}/wiki/{s}" for s in _raw_stop]
elif isinstance(_raw_stop, str):
    WIKI_STOP_URL = f"{BASE_WIKI_URL}/wiki/{_raw_stop}"
else:
    WIKI_STOP_URL = None

TAXONOMY_START_NODE = _current.get("taxonomy_node", "Dinosauromorpha")


# [3] ОПРЕДЕЛЕНИЕ СЛОЯ (THE MASTER LAYER SWITCH)
# True  — РЕЖИМ MASTER (Производственный эталон / Golden Data).
# False — РЕЖИМ SANDBOX (Песочница).
IS_MASTER = True

MASTER_NAME  = "master"
SANDBOX_NAME = "sandbox"
DATA_LAYER   = MASTER_NAME if IS_MASTER else SANDBOX_NAME


# [4] ЛОГИКА СБОРА ДАННЫХ (DATA INPUT & FETCH SETTINGS)
# USE_CUSTOM_LIST: True — парсить только список родов из custom_lists/, False — полный обход дерева
USE_CUSTOM_LIST = False
CUSTOM_LIST_NAME = "test_genera.txt"
CREATE_CUSTOM_LIST_DIR = True


# [5] ПРОИЗВОДИТЕЛЬНОСТЬ (PERFORMANCE)
# USE_PARALLEL: Использовать многопоточность для ускорения парсинга (ThreadPool).
# MAX_WORKERS: Количество одновременных потоков. Рекомендуется 20.
USE_PARALLEL = False
MAX_WORKERS = 20


# [6] НАСТРОЙКИ БАЗЫ ДАННЫХ (DATABASE)
MASTER_DB_NAME = "prehistoric_library.sqlite"
SANDBOX_DB_NAME = "sandbox_library.sqlite"
DB_NAME = MASTER_DB_NAME if IS_MASTER else SANDBOX_DB_NAME

# Названия таблиц формируются динамически под активный режим
TABLE_SPECIES = RESEARCH_MODE                 # Например: "dinosaurs" или "cariamiformes"
TABLE_TAXONOMY = f"{RESEARCH_MODE}_taxonomy" # Например: "dinosaurs_taxonomy"
TABLE_GEOLOGY = "geological_time"             # Общая таблица геохронологии для всех групп


# [7] ДИНАМИЧЕСКИЕ ПУТИ К РЕЕСТРАМ (REGISTRY PATHS)
CUSTOM_LISTS_DIR = "custom_lists"
STORAGE_BASE_NAME = "export"

# --- А) ПУТИ ДЛЯ ИССЛЕДОВАНИЯ (RESEARCH) ---
# Эти пути переключаются между master и sandbox
STORAGE_ROOT  = os.path.join(STORAGE_BASE_NAME, DATA_LAYER, RESEARCH_MODE)
TABLES_DIR    = os.path.join(STORAGE_ROOT, "tables")
SNAPSHOTS_DIR = os.path.join(STORAGE_ROOT, "snapshots")

MASTER_CATALOG   = os.path.join(STORAGE_ROOT, "species_catalog.json")
DELETED_REGISTRY = os.path.join(STORAGE_ROOT, "deleted_registry.json")
MIGRATIONS_FILE  = os.path.join(STORAGE_ROOT, "known_migrations.json")

# --- Б) ПУТИ ДЛЯ ПРОИЗВОДСТВА (PRODUCTION) ---
# Эти пути ВСЕГДА ведут строго в master
PROD_ROOT           = os.path.join(STORAGE_BASE_NAME, MASTER_NAME, RESEARCH_MODE)
PROD_TABLES_DIR     = os.path.join(PROD_ROOT, "tables")
PROD_MASTER_CATALOG = os.path.join(PROD_ROOT, "species_catalog.json")
PROD_DELETED_REG    = os.path.join(PROD_ROOT, "deleted_registry.json")
PROD_MIGRATIONS     = os.path.join(PROD_ROOT, "known_migrations.json")

# Файл истории изменений
HISTORY_FILE = "project_history.txt" if IS_MASTER else "sandbox_history.txt"


# [8] НАУЧНАЯ ИЕРАРХИЯ СТАТУСОВ (SCIENTIFIC WEIGHTS)
# 4 канонических статуса PFL
STATUS_WEIGHTS = {
    'nudum': 1,
    'dubious': 2,
    'provisional': 3,
    'valid': 4
}