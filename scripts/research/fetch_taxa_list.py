import sys
sys.dont_write_bytecode = True

import os
import re
import csv
import copy
import html
import json
import logging
import threading
from urllib.parse import urljoin, urlparse, unquote
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup

# Подключение корневого конфига PFL
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
try:
    import local_settings
except ImportError:
    local_settings = None

# ==============================================================================
# ПЕРЕКЛЮЧАТЕЛЬ СКОРОСТИ И РЕЖИМА ОБХОДА
# ==============================================================================
# True  — БЫСТРЫЙ РЕЖИМ (Очередь задач + многопоточность по config.USE_PARALLEL)
# False — ОТЛАДОЧНЫЙ РЕЖИМ (Синхронный DFS, строго сверху вниз по веточкам)
FAST_CRAWL = True

# ==============================================================================
# ПУТИ И НАСТРОЙКИ
# ==============================================================================

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TABLES_DIR = os.path.join(BASE_DIR, config.TABLES_DIR)

TARGET_PAGES_CSV = os.path.join(TABLES_DIR, "target_pages.csv")
CUSTOM_LIST_PATH = os.path.join(BASE_DIR, config.CUSTOM_LISTS_DIR, config.CUSTOM_LIST_NAME)

LOG_FILE = os.path.join(config.LOGS_DIR, "fetch_taxa_list.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logger = logging.getLogger("fetch_taxa_list")
logger.setLevel(logging.INFO)
if logger.hasHandlers():
    logger.handlers.clear()

file_handler = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
logger.addHandler(file_handler)

USER_EMAIL = local_settings.USER_EMAIL
HEADERS = {'User-Agent': f'PrehistoricFaunaLibraryCollector/2.0 (mailto:{USER_EMAIL})'}

START_PAGE = config.WIKI_START_URL
STOP_PAGE = config.WIKI_STOP_URL

# Секции, которые парсятся ИСКЛЮЧИТЕЛЬНО на страницах РОДА (типовой вид)
GENUS_ONLY_CHILD_HEADERS = [
    "type species", "type genus"
]

# Потокобезопасные блокировки
data_lock = threading.Lock()
log_lock = threading.Lock()


# Изначально в кэше подтвержденных потомков только сам стартовый узел
scope_cache = {
    config.TAXONOMY_START_NODE.lower().strip(): True
}
scope_lock = threading.Lock()

def is_in_taxonomy_scope(taxon_name, session, infobox=None):
    """
    Проверяет принадлежность к config.TAXONOMY_START_NODE (Фильтр Бизона).
    Ищет start_node в таблице предков Template:Taxonomy.
    """
    start_node = config.TAXONOMY_START_NODE.lower().strip()
    t_clean = taxon_name.lower().strip()

    if not t_clean:
        return False

    if t_clean == start_node:
        return True

    # 1. Проверка по кэшу подтвержденных таксонов
    with scope_lock:
        if t_clean in scope_cache:
            return scope_cache[t_clean]

    # 3. Переход по ссылке на Template:Taxonomy
    taxo_a = infobox.find('a', href=re.compile(r'Template:Taxonomy/', re.I)) if infobox else None
    if taxo_a and taxo_a.get('href'):
        raw_href = taxo_a.get('href').split('#')[0]
        taxo_url = urljoin(config.BASE_WIKI_URL, raw_href)
    else:
        taxo_url = f"{config.BASE_WIKI_URL}Template:Taxonomy/{taxon_name}"

    try:
        resp = session.get(taxo_url, timeout=10)
        if resp.status_code == 200:
            soup_taxo = BeautifulSoup(resp.text, 'html.parser')
            
            # Находим таблицу таксономии в шаблоне
            taxo_table = soup_taxo.find('table', class_=re.compile(r'taxonomy|wikitable|infobox', re.I))
            search_area = taxo_table if taxo_table else soup_taxo

            # Ищем ссылку на start_node (например, Dinosauromorpha) внутри таблицы предков
            for a in search_area.find_all('a'):
                href = a.get('href', '').lower()
                a_text = clean_text(a.get_text(strip=True)).lower()

                # Пропускаем пустые ссылки
                if not href and not a_text:
                    continue

                # Ищем целевой start_node (например, dinosauromorpha)
                if start_node in href or a_text == start_node:
                    with scope_lock:
                        scope_cache[t_clean] = True
                    return True

                # Проверяем по подтвержденным узлам в кэше
                with scope_lock:
                    if (a_text and scope_cache.get(a_text) is True) or any(node in href for node, ok in scope_cache.items() if ok and node):
                        scope_cache[t_clean] = True
                        return True

    except Exception as e:
        logger.error(f"Taxonomy fetch failed for {taxon_name}: {e}")

    # Если в Template:Taxonomy нет start_node — таксон ЧУЖОЙ (как Smok)
    with scope_lock:
        scope_cache[t_clean] = False
    return False

# ==============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==============================================================================

def clean_text(raw_text):
    """Очищает текст от пометок вымирания, скобок и мусора."""
    if not raw_text:
        return ""
    text = re.sub(r'[†\?\"“”\(\)\[\]]', '', raw_text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip(" ,.:;")


def is_stop_boundary(url):
    """
    Проверяет достижение любой из стоп-границ (поддерживает строку, список или None).
    """
    if not STOP_PAGE or not url:
        return False

    current_slug = unquote(urlparse(url).path.strip('/').split('/')[-1]).lower().replace('_', ' ')

    # Приводим к списку, даже если передана одна строка
    stop_list = STOP_PAGE if isinstance(STOP_PAGE, (list, tuple, set)) else [STOP_PAGE]

    for stop_item in stop_list:
        if not stop_item:
            continue
        stop_slug = unquote(urlparse(stop_item).path.strip('/').split('/')[-1]).lower().replace('_', ' ')
        if current_slug == stop_slug:
            return True

    return False


def normalize_wiki_url(href):
    """Нормализует URL статьи Википедии."""
    if not href:
        return None
    if '#' in href:
        href = href.split('#')[0]
    if not href or 'redlink=1' in href or 'action=edit' in href:
        return None

    parsed = urlparse(href)
    path = parsed.path
    if not path.startswith('/wiki/'):
        return None

    slug = path[len('/wiki/'):]
    if any(slug.startswith(f"{ns}:") for ns in ["File", "Help", "Special", "Talk", "Template", "Category", "Wikipedia"]):
        return None

    return urljoin(config.BASE_WIKI_URL, path)


# ==============================================================================
# ОПРЕДЕЛЕНИЕ РАНГА И ПОЛНОГО ИМЕНИ
# ==============================================================================

def get_page_rank_and_name(infobox, fallback_name):
    last_tag = None
    genus_name = None
    species_text = None

    for tr in infobox.find_all('tr'):
        tds = tr.find_all(['td', 'th'])
        if len(tds) >= 2:
            label = tds[0].get_text(" ", strip=True).lower()
            val = clean_text(tds[1].get_text(" ", strip=True))

            if label.startswith("genus:"):
                last_tag = "GENUS"
                genus_name = val.split()[0] if val else None
            elif label.startswith("species:"):
                last_tag = "SPECIES"
                species_text = val

    # 1. ВИД
    if last_tag == "SPECIES":
        # Убираем любые сноски и знаки, оставляем только чистые слова
        clean_words = re.findall(r'[A-Za-z\-]+', species_text) if species_text else []
        if genus_name and clean_words:
            # Видовой эпитет — это последнее валидное слово из букв (maximus, africana и т.д.)
            specific_epithet = clean_words[-1].lower()
            # Если случайно взялся сам род (например, если написано только Elephas), проверяем:
            if len(clean_words) >= 2 and specific_epithet == genus_name.lower():
                specific_epithet = clean_words[1].lower()
            full_species = f"{genus_name} {specific_epithet}"
        else:
            full_species = species_text if species_text else fallback_name
        return "SPECIES", full_species, genus_name

    # 2. РОД
    elif last_tag == "GENUS":
        return "GENUS", (genus_name if genus_name else fallback_name), None

    # 3. ИНАЧЕ ВСЕГДА КЛАДА
    else:
        return "CLADE", fallback_name, None


# ==============================================================================
# ОЧИСТКА МЕТАДАННЫХ И СБОР ЭЛЕМЕНТОВ
# ==============================================================================

def clean_node_from_metadata(element):
    cleaned = copy.copy(element)
    
    # 1. Безопасно удаляем сноски и теги small
    for tag in cleaned.find_all(['sup', 'small']):
        tag.decompose()
        
    # 2. Безопасно удаляем теги с уменьшенным шрифтом (авторы, даты)
    for tag in cleaned.find_all(['span', 'div', 'p']):
        if not hasattr(tag, 'attrs') or tag.attrs is None:
            continue
            
        style = str(tag.attrs.get('style', '')).lower()
        classes = tag.attrs.get('class', [])
        if isinstance(classes, list):
            class_str = " ".join(classes).lower()
        else:
            class_str = str(classes).lower()

        if 'font-size' in style or '85%' in style or 'small' in class_str:
            tag.decompose()

    return cleaned


def parse_tree_items(element, is_genus_page):
    items = []

    # Если передан ul/ol — берем его прямые li
    if element.name in ['ul', 'ol']:
        li_list = element.find_all('li', recursive=False)
    else:
        # Если передан td — ищем его списки
        direct_ul = element.find(['ul', 'ol'], recursive=False)
        if direct_ul:
            li_list = direct_ul.find_all('li', recursive=False)
        else:
            li_list = element.find_all('li', recursive=False)

    # Если li нет вообще (простой текст со ссылками через br)
    if not li_list:
        clean_container = clean_node_from_metadata(element)
        for a in clean_container.find_all('a'):
            if a.get('href', '').startswith('#'):
                continue
            if is_genus_page:
                is_italic = bool(a.find_parent(['i', 'em']) or a.find(['i', 'em']))
                if not is_italic:
                    continue
            norm_url = normalize_wiki_url(a.get('href', ''))
            if norm_url:
                t_name = clean_text(a.get_text(strip=True))
                if t_name and t_name[0].isalpha():
                    items.append(('LINK', t_name, norm_url))
        return items

    for li in li_list:
        li_header = copy.copy(li)
        for sub in li_header.find_all(['ul', 'ol', 'table']):
            sub.decompose()

        clean_header = clean_node_from_metadata(li_header)

        # Собираем все ссылки из заголовка этого li
        found_links = []
        for a_tag in clean_header.find_all('a'):
            if a_tag.get('href', '').startswith('#'):
                continue
            norm_url = normalize_wiki_url(a_tag.get('href', ''))
            if not norm_url:
                continue

            t_name = clean_text(a_tag.get_text(strip=True))
            if is_genus_page:
                is_italic = bool(a_tag.find_parent(['i', 'em']) or a_tag.find(['i', 'em']))
                if not is_italic:
                    continue

            if t_name and t_name[0].isalpha():
                found_links.append(('LINK', t_name, norm_url))

        if found_links:
            items.extend(found_links)
        else:
            # Ссылок нет — это NO_LINK
            if not is_genus_page:
                is_italic = bool(clean_header.find(['i', 'em']))
                if not is_italic:
                    bold = clean_header.find('b')
                    candidate_name = clean_text(bold.get_text(strip=True)) if bold else clean_text(clean_header.get_text(strip=True))
                    if candidate_name:
                        first_word = candidate_name.split()[0]
                        if first_word and first_word[0].isupper() and not any(c.isdigit() for c in first_word):
                            items.append(('NO_LINK', first_word, None))

            # Ныряем во вложенный список ТОЛЬКО если в строке не было своих ссылок
            for sub_list in li.find_all(['ul', 'ol'], recursive=False):
                items.extend(parse_tree_items(sub_list, is_genus_page))

    return items


def extract_child_items_from_infobox(infobox, is_genus_page=False):
    all_items = []
    rows = infobox.find_all('tr')

    # 1. Если мы на странице РОДА: дополнительно берем Type species
    if is_genus_page:
        for tr in rows:
            th = tr.find('th')
            if th and any(tg in th.get_text(" ", strip=True).lower() for tg in GENUS_ONLY_CHILD_HEADERS):
                td = tr.find('td') or (tr.find_next_sibling('tr').find('td') if tr.find_next_sibling('tr') else None)
                if td:
                    all_items.extend(parse_tree_items(td, is_genus_page=True))

    # 2. Ищем строки данных потомков (СТРОГО ПОСЛЕ классификации)
    handled_tds = set()
    passed_classification = False

    for tr in rows:
        th = tr.find('th')
        th_text = th.get_text(" ", strip=True).lower() if th else ""

        # Дошли до синонимов — ЖЕСТКИЙ СТОП
        if "synonym" in th_text:
            break

        # Отслеживаем классификацию предков
        if "scientific classification" in th_text:
            passed_classification = True
            continue

        tds = tr.find_all(['td', 'th'])
        if len(tds) >= 2 and ":" in tds[0].get_text():
            passed_classification = True
            continue

        # [!] ПОКА КЛАССИФИКАЦИЯ НЕ ПРОЙДЕНА — пропускаем всё для ЛЮБЫХ страниц (шапку, картинки, подписи к фото)
        if not passed_classification:
            continue

        # [!] На кладе блокируем Type species / Type genus и САМИХ ИХ ЯЧЕЙКИ:
        if not is_genus_page and any(tg in th_text for tg in GENUS_ONLY_CHILD_HEADERS):
            # Помечаем и текущий td, и td следующей строки как отработанные, чтобы не прочитать их как потомков!
            bad_td = tr.find('td') or (tr.find_next_sibling('tr').find('td') if tr.find_next_sibling('tr') else None)
            if bad_td:
                handled_tds.add(id(bad_td))
            continue

        # Пропускаем геологию, кладограммы и статус
        if any(ign in th_text for ign in ["temporal range", "fossil range", "conservation status", "binomial name", "cladogram", "phylogeny"]):
            continue

        # Находим td в текущей строке или в следующей (если th был colspan=2)
        td = tr.find('td')
        if not td and th and th.get('colspan') == '2':
            next_tr = tr.find_next_sibling('tr')
            if next_tr:
                td = next_tr.find('td')

        # Защита от дублирования одной и той же ячейки
        if td and id(td) not in handled_tds:
            handled_tds.add(id(td))
            
            root_lists = [ul for ul in td.find_all(['ul', 'ol']) if not ul.find_parent('li')]
            if root_lists:
                for r_list in root_lists:
                    all_items.extend(parse_tree_items(r_list, is_genus_page))
            else:
                all_items.extend(parse_tree_items(td, is_genus_page))

    return all_items


# ==============================================================================
# ОБРАБОТКА СТРАНИЦЫ (ЯДРО КРАУЛЕРА)
# ==============================================================================

def inspect_single_page(display_name, page_url, session):
    """
    Скачивает одну страницу и возвращает:
    (rank, actual_name, final_url, parent_genus, child_items, warnings)
    """
    warnings = []

    # 1. Проверка стоп-границы ДО запроса (по исходному URL)
    if is_stop_boundary(page_url):
        warnings.append(('WARNING', f"CLADE (BOUNDARY STOP): {display_name}"))
        return 'STOP', display_name, page_url, None, [], warnings

    try:
        resp = session.get(page_url, timeout=12)
        if resp.status_code != 200:
            return 'ERROR', display_name, page_url, None, [], warnings
        soup = BeautifulSoup(resp.text, 'html.parser')
    except Exception as e:
        warnings.append(('ERROR', f"Failed to fetch {page_url}: {e}"))
        return 'ERROR', display_name, page_url, None, [], warnings

    # 2. Определение канонического URL (после редиректа)
    canonical_tag = soup.find('link', rel='canonical')
    if canonical_tag and canonical_tag.get('href'):
        final_url = normalize_wiki_url(canonical_tag['href']) or resp.url
    else:
        final_url = normalize_wiki_url(resp.url) or page_url

    # 3. Название статьи из заголовка H1
    h1 = soup.find('h1', id='firstHeading')
    actual_title = clean_text(h1.get_text(strip=True)) if h1 else display_name

    # 4. Проверка стоп-границы ПОСЛЕ редиректа (например, Saphornithischia -> Ornithischia)
    if is_stop_boundary(final_url):
        warnings.append(('WARNING', f"CLADE (BOUNDARY STOP): {actual_title}"))
        return 'STOP', actual_title, final_url, None, [], warnings

    # 5. Единственная фиксация редиректа (без дублирования!)
    if final_url != page_url and (display_name.lower() != actual_title.lower() and display_name.lower() not in actual_title.lower()):
        warnings.append(('WARNING', f"REDIRECT: {display_name} -> {actual_title}"))

    # 6. Инфобокс страницы
    infobox = soup.find('table', class_=re.compile(r'infobox(\s+.*biota.*)?'))
    if not infobox:
        return 'NO_INFOBOX', display_name, final_url, None, [], warnings

    # 7. Определение ранга страницы (SPECIES / GENUS / CLADE)
    rank, actual_name, parent_genus = get_page_rank_and_name(infobox, actual_title)

    # 8. Фильтр Бизона (проверка принадлежности к TAXONOMY_START_NODE)
    taxon_to_check = parent_genus if parent_genus else actual_name
    if not is_in_taxonomy_scope(taxon_to_check, session, infobox):
        warnings.append(('WARNING', f"OUT OF SCOPE: {taxon_to_check} ({config.TAXONOMY_START_NODE} not in taxonomy)"))
        return 'OUT_OF_SCOPE', actual_name, final_url, None, [], warnings

    # 9. Сбор дочерних таксонов
    is_genus = (rank == "GENUS")
    raw_children = extract_child_items_from_infobox(infobox, is_genus_page=is_genus)

    return rank, actual_name, final_url, parent_genus, raw_children, warnings


# ==============================================================================
# ЕДИНАЯ РЕГИСТРАЦИЯ РЕЗУЛЬТАТОВ (ОБЩАЯ ДЛЯ ВСЕХ РЕЖИМОВ)
# ==============================================================================

def record_result(rank, actual_name, final_url, parent_genus, orig_name, target_manifest, discovered_genera):
    """
    Единая точка логирования и сохранения: формат вывода 100% одинаковый
    как для пошагового DFS, так и для быстрого BFS/многопотока.
    """
    if rank == "SPECIES":
        if parent_genus and (orig_name.lower() == parent_genus.lower() or parent_genus not in discovered_genera):
            discovered_genera.add(parent_genus)
            with log_lock:
                logger.info(f"GENUS: {parent_genus} ({final_url})")
            target_manifest.append({'taxon': parent_genus, 'url': final_url, 'rank': 'genus'})
        else:
            with log_lock:
                logger.info(f"SPECIES: {actual_name} ({final_url})")
            target_manifest.append({'taxon': actual_name, 'url': final_url, 'rank': 'species'})

    elif rank == "GENUS":
        if actual_name in discovered_genera:
            with log_lock:
                logger.warning(f"GENUS (ALREADY VISITED): {actual_name}")
        else:
            discovered_genera.add(actual_name)
            with log_lock:
                logger.info(f"GENUS: {actual_name} ({final_url})")
            target_manifest.append({'taxon': actual_name, 'url': final_url, 'rank': 'genus'})

    elif rank == "CLADE":
        with log_lock:
            logger.info(f"CLADE: {orig_name}")


# ==============================================================================
# ЕДИНОЕ ЯДРО ОБРАБОТКИ РЕЗУЛЬТАТОВ (ОБЩЕЕ ДЛЯ ВСЕХ РЕЖИМОВ)
# ==============================================================================

def process_page_result(orig_name, orig_url, expected_genus, inspect_res, target_manifest, discovered_genera, visited_urls):
    rank, actual_name, final_url, parent_genus, child_items, warnings = inspect_res

    # 1. Сначала выводим все предупреждения (включая REDIRECT!)
    with log_lock:
        for level, msg in warnings:
            if level == 'WARNING': logger.warning(msg)
            elif level == 'ERROR': logger.error(msg)

    if rank in ['STOP', 'ERROR', 'NO_INFOBOX', 'OUT_OF_SCOPE']:
        with data_lock:
            visited_urls.add(orig_url)
            visited_urls.add(final_url)
        return []

    # 2. Если страница после редиректа УЖЕ была посещена ранее (как Saphornithischia -> Ornithischia)
    if final_url in visited_urls and orig_url != final_url:
        with log_lock:
            logger.warning(f"CLADE (ALREADY VISITED): {actual_name}")
        with data_lock:
            visited_urls.add(orig_url)
        return []

    # 1. Защита от омонимов (коллизий таксонов)
    if expected_genus:
        is_foreign_clade = (rank == "CLADE")
        is_foreign_genus = (rank == "GENUS" and actual_name.lower() != expected_genus.lower())
        is_foreign_species = (rank == "SPECIES" and parent_genus and parent_genus.lower() != expected_genus.lower())

        if is_foreign_clade or is_foreign_genus or is_foreign_species:
            with log_lock:
                logger.warning(f"OUT OF SCOPE (HOMONYM COLLISION): {expected_genus} -> {actual_name} ({final_url})")
            return []

    # 2. Фиксация посещения и запись результатов
    next_tasks = []
    with data_lock:
        visited_urls.add(orig_url)
        visited_urls.add(final_url)

        if rank == "SPECIES":
            if parent_genus and (orig_name.lower() == parent_genus.lower() or parent_genus not in discovered_genera):
                discovered_genera.add(parent_genus)
                with log_lock: logger.info(f"GENUS: {parent_genus} ({final_url})")
                target_manifest.append({'taxon': parent_genus, 'url': final_url, 'rank': 'genus'})
            else:
                with log_lock: logger.info(f"SPECIES: {actual_name} ({final_url})")
                target_manifest.append({'taxon': actual_name, 'url': final_url, 'rank': 'species'})

        elif rank == "GENUS":
            if actual_name in discovered_genera:
                with log_lock: logger.warning(f"GENUS (ALREADY VISITED): {actual_name}")
            else:
                discovered_genera.add(actual_name)
                with log_lock: logger.info(f"GENUS: {actual_name} ({final_url})")
                target_manifest.append({'taxon': actual_name, 'url': final_url, 'rank': 'genus'})

        elif rank == "CLADE":
            with log_lock: logger.info(f"CLADE: {orig_name}")

        # 3. Подготовка следующих дочерних задач в исходном хронологическом порядке
        if rank in ["CLADE", "GENUS"]:
            next_expected = actual_name if rank == "GENUS" else expected_genus
            for item in child_items:
                if item[0] == 'LINK':
                    c_name, c_url = item[1], item[2]
                    if is_stop_boundary(c_url):
                        with log_lock: logger.warning(f"CLADE (BOUNDARY STOP): {c_name}")
                        continue
                    # Если ссылка уже посещена — передаем как задачу в очередь, чтобы напечатать строго по порядку!
                    if c_url in visited_urls:
                        next_tasks.append(('ALREADY_VISITED', c_name, c_url, None))
                        continue
                    next_tasks.append(('LINK', c_name, c_url, next_expected))
                elif item[0] == 'NO_LINK':
                    next_tasks.append(('NO_LINK', item[1], None, None))

        if not config.BRIEF_CONSOLE:
            sys.stdout.write(f"\rDiscovered: [Genera: {len(discovered_genera)}] [Targets: {len(target_manifest)}]")
            sys.stdout.flush()

    return next_tasks


# ==============================================================================
# ДИСПЕТЧЕРЫ ОБХОДА: ПОШАГОВЫЙ (DFS) И БЫСТРЫЙ (BFS/THREADPOOL)
# ==============================================================================

def dfs_process_page(display_name, page_url, session, target_manifest, discovered_genera, visited_urls, expected_genus=None):
    if is_stop_boundary(page_url):
        logger.warning(f"CLADE (BOUNDARY STOP): {display_name}")
        return

    if page_url in visited_urls:
        logger.warning(f"CLADE (ALREADY VISITED): {display_name}")
        return

    inspect_res = inspect_single_page(display_name, page_url, session)
    next_tasks = process_page_result(display_name, page_url, expected_genus, inspect_res, target_manifest, discovered_genera, visited_urls)

    for task_type, c_name, c_url, next_exp in next_tasks:
        if task_type == 'LINK':
            dfs_process_page(c_name, c_url, session, target_manifest, discovered_genera, visited_urls, next_exp)
        elif task_type == 'NO_LINK':
            logger.warning(f"CLADE (NO LINK): {c_name}")
        elif task_type == 'ALREADY_VISITED':
            logger.warning(f"CLADE (ALREADY VISITED): {c_name}")


def fast_crawl_bfs(root_name, start_url, session, target_manifest, discovered_genera, visited_urls):
    """Быстрый параллельный обход через очередь задач."""
    use_parallel = getattr(config, 'USE_PARALLEL', False)
    max_workers = getattr(config, 'MAX_WORKERS', 20) if use_parallel else 1

    queue = [(root_name, start_url, None)]
    visited_urls.add(start_url)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while queue:
            batch = []
            while queue and len(batch) < (max_workers * 2):
                batch.append(queue.pop(0))

            future_to_item = {
                executor.submit(inspect_single_page, name, url, session): (name, url, exp_genus)
                for name, url, exp_genus in batch
            }

            for future in as_completed(future_to_item):
                orig_name, orig_url, exp_genus = future_to_item[future]
                try:
                    inspect_res = future.result()
                except Exception as e:
                    logger.error(f"Task error on {orig_url}: {e}")
                    continue

                next_tasks = process_page_result(orig_name, orig_url, exp_genus, inspect_res, target_manifest, discovered_genera, visited_urls)

                with data_lock:
                    for task_type, c_name, c_url, next_exp in next_tasks:
                        if task_type == 'LINK':
                            if not any(q[1] == c_url for q in queue):
                                visited_urls.add(c_url)
                                queue.append((c_name, c_url, next_exp))
                        elif task_type == 'NO_LINK':
                            with log_lock: logger.warning(f"CLADE (NO LINK): {c_name}")
                        elif task_type == 'ALREADY_VISITED':
                            with log_lock:
                                logger.warning(f"CLADE (ALREADY VISITED): {c_name}")


# ==============================================================================
# ТОЧКА ВХОДА (RUNNER)
# ==============================================================================

def crawl_tree():
    if config.BRIEF_CONSOLE:
        print("FETCH_TAXA_LIST...", end=" ", flush=True)
    else:
        print("Starting script: FETCH_TAXA_LIST")

    logger.info("--- SCRIPT START: FETCH_TAXA_LIST ---")
    logger.info(f"Research Mode: {config.RESEARCH_MODE}")
    logger.info(f"Start URL: {START_PAGE}")
    if isinstance(STOP_PAGE, (list, tuple)):
        logger.info(f"Stop URLs: {', '.join(STOP_PAGE)}")
    else:
        logger.info(f"Stop URL: {STOP_PAGE if STOP_PAGE else 'None'}")

    session = requests.Session()
    session.headers.update(HEADERS)
    if getattr(config, 'USE_PARALLEL', False) and FAST_CRAWL:
        adapter = requests.adapters.HTTPAdapter(pool_connections=config.MAX_WORKERS, pool_maxsize=config.MAX_WORKERS)
        session.mount('https://', adapter)

    target_manifest = []
    discovered_genera = set()
    visited_urls = set()

    root_name = unquote(urlparse(START_PAGE).path.strip('/').split('/')[-1]).replace('_', ' ')

    if config.USE_CUSTOM_LIST and os.path.exists(CUSTOM_LIST_PATH):
        logger.info(f"Custom list mode active: reading {CUSTOM_LIST_PATH}")
        with open(CUSTOM_LIST_PATH, 'r', encoding='utf-8') as f:
            custom_genera = [clean_text(line) for line in f if clean_text(line)]

        for name in custom_genera:
            g_url = f"{config.BASE_WIKI_URL}{name}"
            if FAST_CRAWL:
                fast_crawl_bfs(name, g_url, session, target_manifest, discovered_genera, visited_urls)
            else:
                dfs_process_page(name, g_url, session, target_manifest, discovered_genera, visited_urls)
    else:
        if FAST_CRAWL:
            fast_crawl_bfs(root_name, START_PAGE, session, target_manifest, discovered_genera, visited_urls)
        else:
            dfs_process_page(root_name, START_PAGE, session, target_manifest, discovered_genera, visited_urls)

    if not config.BRIEF_CONSOLE:
        print()
        print("Crawl completed.")

    # Сохранение только target_pages.csv
    os.makedirs(TABLES_DIR, exist_ok=True)
    try:
        with open(TARGET_PAGES_CSV, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerow(["taxon", "url"])
            for row in target_manifest:
                writer.writerow([row['taxon'], row['url']])
        logger.info(f"Target pages saved to: {os.path.abspath(TARGET_PAGES_CSV)}")
    except Exception as e:
        logger.error(f"Failed to save {TARGET_PAGES_CSV}: {e}")

    summary_msg = f"Discovery finished. Total targets: {len(target_manifest)}"
    logger.info(summary_msg)

    if config.BRIEF_CONSOLE:
        print(f"{len(target_manifest)} targets discovered")
    else:
        print(f"TARGET PAGES DISCOVERED: {len(target_manifest)}")
        print(f"SAVED TO: {TARGET_PAGES_CSV}")
        print("Script ended: FETCH_TAXA_LIST")

    logger.info("--- SCRIPT END: FETCH_TAXA_LIST ---")


if __name__ == "__main__":
    crawl_tree()