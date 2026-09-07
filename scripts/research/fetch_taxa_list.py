import sys
sys.dont_write_bytecode = True

import os
import re
import csv
import copy
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

# Секции инфобокса, где находятся дочерние таксоны
TARGET_CHILD_HEADERS = [
    "subgroups", "subdivision", "subdivisions", "subtaxa", "sub-taxa",
    "families", "subfamilies", "tribes", "genera", "species", "subspecies",
    "members", "included taxa", "included groups", "other species",
    "major groups", "groups", "orders", "suborders", "clades"
]

# Секции, которые парсятся ИСКЛЮЧИТЕЛЬНО на страницах РОДА
GENUS_ONLY_CHILD_HEADERS = [
    "type species", "type genus"
]

# Секции, которые строго запрещены везде
FORBIDDEN_HEADERS = [
    "synonyms", "synonymy", "fossil range", "temporal range", 
    "conservation status", "scientific classification"
]

# Потокобезопасные блокировки
data_lock = threading.Lock()
log_lock = threading.Lock()


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
    """Проверяет достижение стоп-границы дерева (например, Avialae)."""
    if not STOP_PAGE or not url:
        return False
    target_slug = urlparse(STOP_PAGE).path.strip('/').split('/')[-1].lower()
    current_slug = urlparse(url).path.strip('/').split('/')[-1].lower()
    return target_slug == current_slug


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
    for tag in cleaned.find_all(['sup', 'small']):
        tag.decompose()
    for span in cleaned.find_all('span'):
        style = span.get('style', '').lower()
        if 'font-size' in style or 'small' in span.get('class', []):
            span.decompose()
    return cleaned


def parse_tree_items(element, is_genus_page):
    items = []
    li_list = element.find_all('li', recursive=False)
    if not li_list:
        li_list = element.find_all('li')

    for li in li_list:
        li_header = copy.copy(li)
        for sub in li_header.find_all(['ul', 'ol', 'table']):
            sub.decompose()

        clean_header = clean_node_from_metadata(li_header)

        # 1. Проверяем наличие ссылки в заголовке
        a_tag = clean_header.find('a')
        has_valid_link = False

        if a_tag and not a_tag.get('href', '').startswith('#'):
            norm_url = normalize_wiki_url(a_tag.get('href', ''))
            if norm_url:
                t_name = clean_text(a_tag.get_text(strip=True))

                # На странице рода ссылка ОБЯЗАНА быть курсивной
                if is_genus_page:
                    is_italic = bool(a_tag.find_parent(['i', 'em']) or a_tag.find(['i', 'em']))
                    if not is_italic:
                        norm_url = None

                if norm_url and t_name and t_name[0].isalpha():
                    items.append(('LINK', t_name, norm_url))
                    has_valid_link = True

        # 2. Если ссылки НЕ было:
        if not has_valid_link:
            if not is_genus_page:
                is_italic = bool(clean_header.find(['i', 'em']))
                if not is_italic:
                    bold = clean_header.find('b')
                    candidate_name = clean_text(bold.get_text(strip=True)) if bold else clean_text(clean_header.get_text(strip=True))
                    if candidate_name:
                        first_word = candidate_name.split()[0]
                        if first_word and first_word[0].isupper() and not any(c.isdigit() for c in first_word):
                            items.append(('NO_LINK', first_word, None))

            # Во вложенный список ныряем ТОЛЬКО если у текущей клады НЕ было своей ссылки
            for sub_list in li.find_all(['ul', 'ol'], recursive=False):
                items.extend(parse_tree_items(sub_list, is_genus_page))

    return items


def extract_child_items_from_infobox(infobox, is_genus_page=False):
    all_items = []
    rows = infobox.find_all('tr')

    for i, tr in enumerate(rows):
        th = tr.find('th')
        if not th:
            continue

        h_text = th.get_text(" ", strip=True).lower()
        if any(ex in h_text for ex in FORBIDDEN_HEADERS):
            continue

        if not is_genus_page and any(tg in h_text for tg in GENUS_ONLY_CHILD_HEADERS):
            continue

        is_type_block = is_genus_page and any(tg in h_text for tg in GENUS_ONLY_CHILD_HEADERS)
        is_target = is_type_block or any(target in h_text for target in TARGET_CHILD_HEADERS)
        if not is_target:
            continue

        td = tr.find('td')
        if not td:
            next_tr = tr.find_next_sibling('tr')
            if next_tr:
                td = next_tr.find('td')

        if not td:
            continue

        root_lists = [
            ul for ul in td.find_all(['ul', 'ol'])
            if not ul.find_parent('li')
        ]

        if root_lists:
            for r_list in root_lists:
                all_items.extend(parse_tree_items(r_list, is_genus_page))
        else:
            clean_td = clean_node_from_metadata(td)
            for a in clean_td.find_all('a'):
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
                        all_items.append(('LINK', t_name, norm_url))

    return all_items


# ==============================================================================
# ОБРАБОТКА СТРАНИЦЫ (ЯДРО КРАУЛЕРА)
# ==============================================================================

def inspect_single_page(display_name, page_url, session):
    """
    Скачивает одну страницу и возвращает:
    (rank, actual_name, final_url, parent_genus, child_links, warnings)
    """
    warnings = []
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

    canonical_tag = soup.find('link', rel='canonical')
    if canonical_tag and canonical_tag.get('href'):
        final_url = normalize_wiki_url(canonical_tag['href']) or resp.url
    else:
        final_url = normalize_wiki_url(resp.url) or page_url

    infobox = soup.find('table', class_=re.compile(r'infobox(\s+.*biota.*)?'))
    if not infobox:
        return 'NO_INFOBOX', display_name, final_url, None, [], warnings

    h1 = soup.find('h1', id='firstHeading')
    actual_title = clean_text(h1.get_text(strip=True)) if h1 else display_name

    if final_url != page_url or (display_name.lower() != actual_title.lower() and display_name.lower() not in actual_title.lower()):
        warnings.append(('WARNING', f"REDIRECT: {display_name} -> {actual_title}"))

    rank, actual_name, parent_genus = get_page_rank_and_name(infobox, actual_title)

    is_genus = (rank == "GENUS")
    raw_children = extract_child_items_from_infobox(infobox, is_genus_page=is_genus)

    # Возвращаем единый упорядоченный список элементов (и ссылки, и NO_LINK)
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
# РЕЖИМ 1: ПОШАГОВЫЙ DFS (FAST_CRAWL = False)
# ==============================================================================

def dfs_process_page(display_name, page_url, session, target_manifest, discovered_genera, visited_urls):
    if is_stop_boundary(page_url):
        logger.warning(f"CLADE (BOUNDARY STOP): {display_name}")
        return

    if page_url in visited_urls:
        logger.warning(f"CLADE (ALREADY VISITED): {display_name}")
        return

    rank, actual_name, final_url, parent_genus, child_items, warnings = inspect_single_page(display_name, page_url, session)

    visited_urls.add(page_url)
    visited_urls.add(final_url)

    # Выводим редиректы и ошибки самой страницы
    for level, msg in warnings:
        if level == 'WARNING': logger.warning(msg)
        elif level == 'ERROR': logger.error(msg)

    if rank in ['STOP', 'ERROR', 'NO_INFOBOX']:
        return

    # 1. Сначала логируем текущую страницу (CLADE: Phorusrhacidae или род/вид)
    record_result(rank, actual_name, final_url, parent_genus, display_name, target_manifest, discovered_genera)

    if not config.BRIEF_CONSOLE:
        sys.stdout.write(f"\rDiscovered: [Genera: {len(discovered_genera)}] [Targets: {len(target_manifest)}]")
        sys.stdout.flush()

    # 2. Затем идем строго по порядку появления дочерних элементов в HTML!
    if rank in ["CLADE", "GENUS"]:
        for item in child_items:
            item_type = item[0]
            if item_type == 'LINK':
                c_name, c_url = item[1], item[2]
                if c_url not in [page_url, final_url]:
                    dfs_process_page(c_name, c_url, session, target_manifest, discovered_genera, visited_urls)
            elif item_type == 'NO_LINK':
                # ВОРНИНГ ВЫВОДИТСЯ СТРОГО НА СВОЕМ МЕСТЕ В ДЕРЕВЕ!
                logger.warning(f"CLADE (NO LINK): {item[1]}")
                

# ==============================================================================
# РЕЖИМ 2: БЫСТРЫЙ BFS / МНОГОПОТОК (FAST_CRAWL = True)
# ==============================================================================

def fast_crawl_bfs(root_name, start_url, session, target_manifest, discovered_genera, visited_urls):
    use_parallel = getattr(config, 'USE_PARALLEL', False)
    max_workers = getattr(config, 'MAX_WORKERS', 20) if use_parallel else 1

    queue = [(root_name, start_url)]
    visited_urls.add(start_url)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while queue:
            batch = []
            while queue and len(batch) < (max_workers * 2):
                batch.append(queue.pop(0))

            future_to_item = {
                executor.submit(inspect_single_page, name, url, session): (name, url)
                for name, url in batch
            }

            for future in as_completed(future_to_item):
                orig_name, orig_url = future_to_item[future]
                try:
                    rank, actual_name, final_url, parent_genus, child_items, warnings = future.result()
                except Exception as e:
                    logger.error(f"Task error on {orig_url}: {e}")
                    continue

                with log_lock:
                    for level, msg in warnings:
                        if level == 'WARNING':
                            logger.warning(msg)
                        elif level == 'ERROR':
                            logger.error(msg)

                with data_lock:
                    visited_urls.add(orig_url)
                    visited_urls.add(final_url)
                    record_result(rank, actual_name, final_url, parent_genus, orig_name, target_manifest, discovered_genera)

                    # Разбираем дочерние элементы по типам: LINK или NO_LINK
                    if rank in ["CLADE", "GENUS"]:
                        for item in child_items:
                            item_type = item[0]
                            if item_type == 'LINK':
                                c_name, c_url = item[1], item[2]
                                if is_stop_boundary(c_url):
                                    with log_lock:
                                        logger.warning(f"CLADE (BOUNDARY STOP): {c_name}")
                                    continue
                                if c_url not in visited_urls and not any(q[1] == c_url for q in queue):
                                    visited_urls.add(c_url)
                                    queue.append((c_name, c_url))
                            elif item_type == 'NO_LINK':
                                with log_lock:
                                    logger.warning(f"CLADE (NO LINK): {item[1]}")

                if not config.BRIEF_CONSOLE:
                    sys.stdout.write(f"\rDiscovered: [Genera: {len(discovered_genera)}] [Targets: {len(target_manifest)}]")
                    sys.stdout.flush()


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
    logger.info(f"Fast Crawl Mode: {FAST_CRAWL}")
    logger.info(f"Config Parallel: {getattr(config, 'USE_PARALLEL', False)} (Workers: {getattr(config, 'MAX_WORKERS', 1)})")
    logger.info(f"Start URL: {START_PAGE}")
    logger.info(f"Stop URL: {STOP_PAGE if STOP_PAGE else 'None'}")

    session = requests.Session()
    session.headers.update(HEADERS)
    if getattr(config, 'USE_PARALLEL', False):
        adapter = requests.adapters.HTTPAdapter(pool_connections=config.MAX_WORKERS, pool_maxsize=config.MAX_WORKERS)
        session.mount('https://', adapter)

    target_manifest = []
    discovered_genera = set()
    visited_urls = set()

    root_name = config.RESEARCH_MODE.capitalize()

    # 1. Режим кастомного списка
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
        # 2. Полноценный обход дерева: имя стартовой клады берем из самого URL, а не из RESEARCH_MODE
        root_name = unquote(urlparse(START_PAGE).path.strip('/').split('/')[-1]).replace('_', ' ')
        if FAST_CRAWL:
            fast_crawl_bfs(root_name, START_PAGE, session, target_manifest, discovered_genera, visited_urls)
        else:
            dfs_process_page(root_name, START_PAGE, session, target_manifest, discovered_genera, visited_urls)

    if not config.BRIEF_CONSOLE:
        print()
        print("Crawl completed.")

    # ==============================================================================
    # СОХРАНЕНИЕ
    # ==============================================================================
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