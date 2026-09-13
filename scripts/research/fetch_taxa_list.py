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

# Подключение корневой конфигурации PFL
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
# ПУТИ И ИНИЦИАЛИЗАЦИЯ ЛОГИРОВАНИЯ
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

USER_EMAIL = getattr(local_settings, 'USER_EMAIL', 'researcher@pfl-project.org')
HEADERS = {'User-Agent': f'PrehistoricFaunaLibraryCollector/2.0 (mailto:{USER_EMAIL})'}

START_PAGE = config.WIKI_START_URL
STOP_PAGE = config.WIKI_STOP_URL

# Секции инфобокса, которые обрабатываются исключительно на страницах рода
GENUS_ONLY_CHILD_HEADERS = ["type species", "type genus"]


# ==============================================================================
# ВСПОМОГАТЕЛЬНЫЕ УТИЛИТЫ ОЧИСТКИ ТЕКСТА И URL
# ==============================================================================

def clean_text(raw_text):
    """Очищает строку от пометок вымирания (†), знаков вопроса, кавычек и скобок."""
    if not raw_text:
        return ""
    text = re.sub(r'[†\?\"“”\(\)\[\]]', '', raw_text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip(" ,.:;")


def normalize_wiki_url(href):
    """Приводит ссылку к абсолютному каноническому URL статьи Википедии."""
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

    # Исключаем служебные пространства имен MediaWiki
    slug = path[len('/wiki/'):]
    if any(slug.startswith(f"{ns}:") for ns in ["File", "Help", "Special", "Talk", "Template", "Category", "Wikipedia"]):
        return None

    return urljoin(config.BASE_WIKI_URL, path)


def clean_node_from_metadata(element):
    """
    Безопасно удаляет из HTML-узла сноски, теги small и элементы с уменьшенным шрифтом.
    Предотвращает ошибочный захват авторов, годов и пометок 'type'.
    """
    cleaned = copy.copy(element)
    
    # 1. Удаление сносок и явных тегов small
    for tag in cleaned.find_all(['sup', 'small']):
        tag.decompose()
        
    # 2. Удаление тегов с мелким стилем оформления
    for tag in cleaned.find_all(['span', 'div', 'p']):
        if not hasattr(tag, 'attrs') or tag.attrs is None:
            continue
            
        style = str(tag.attrs.get('style', '')).lower()
        classes = tag.attrs.get('class', [])
        class_str = " ".join(classes).lower() if isinstance(classes, list) else str(classes).lower()

        if 'font-size' in style or '85%' in style or 'small' in class_str:
            tag.decompose()

    return cleaned


# ==============================================================================
# КЛАСС 1: ВАЛИДАТОР СКОУПА ТАКСОНОМИИ (ФИЛЬТР БИЗОНА)
# ==============================================================================

class TaxonomyScopeValidator:
    """
    Отвечает за филогенетическую валидацию таксонов.
    Проверяет, восходит ли таксон к опорному узлу config.TAXONOMY_START_NODE.
    Изолирует посторонние группы (например, крокодиломорфа Smok при сборе динозавров).
    """
    def __init__(self, start_node):
        self.start_node = start_node.lower().strip()
        self.scope_cache = {self.start_node: True}
        self.lock = threading.Lock()

    def is_in_scope(self, taxon_name, session, infobox=None):
        """
        Проверяет принадлежность таксона к целевому филогенетическому узлу.
        1. Проверка по кэшу подтвержденных потомков.
        2. Проверка по ссылкам классификации инфобокса статьи.
        3. Запрос к странице Template:Taxonomy.
        """
        t_clean = taxon_name.lower().strip()
        if not t_clean:
            return False

        if t_clean == self.start_node:
            return True

        # Шаг 1: Проверка в памяти кэша
        with self.lock:
            if t_clean in self.scope_cache:
                return self.scope_cache[t_clean]

        # Шаг 2: Проверка прямых ссылок классификации в инфобоксе статьи
        if infobox:
            for a in infobox.find_all('a'):
                href = a.get('href', '').lower()
                a_text = clean_text(a.get_text(strip=True)).lower()
                if not href and not a_text:
                    continue

                if href.endswith(f"/{self.start_node}") or a_text == self.start_node:
                    with self.lock:
                        self.scope_cache[t_clean] = True
                    return True

                with self.lock:
                    if (a_text and self.scope_cache.get(a_text) is True) or any(node in href for node, ok in self.scope_cache.items() if ok and node):
                        self.scope_cache[t_clean] = True
                        return True

        # Шаг 3: Запрос к официальному шаблону таксономии Template:Taxonomy
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
                taxo_table = soup_taxo.find('table', class_=re.compile(r'taxonomy|wikitable|infobox', re.I))
                search_area = taxo_table if taxo_table else soup_taxo

                for a in search_area.find_all('a'):
                    href = a.get('href', '').lower()
                    a_text = clean_text(a.get_text(strip=True)).lower()
                    if not href and not a_text:
                        continue

                    if self.start_node in href or a_text == self.start_node:
                        with self.lock:
                            self.scope_cache[t_clean] = True
                        return True

                    with self.lock:
                        if (a_text and self.scope_cache.get(a_text) is True) or any(node in href for node, ok in self.scope_cache.items() if ok and node):
                            self.scope_cache[t_clean] = True
                            return True

        except Exception as e:
            logger.error(f"Taxonomy fetch failed for {taxon_name}: {e}")

        # Если стартовый узел не найден среди предков — таксон чужой
        with self.lock:
            self.scope_cache[t_clean] = False
        return False


# ==============================================================================
# КЛАСС 2: ГЛАВНЫЙ КРАУЛЕР ФИЛОГЕНЕТИЧЕСКОГО ДЕРЕВА
# ==============================================================================

class TaxaTreeCrawler:
    """
    Главный оркестратор графового обхода таксономического дерева.
    Поддерживает:
    - Структурный парсинг инфобокса (subdivision).
    - Задел на извлечение из раздела Phylogeny и списков в теле статьи.
    - Переключение DFS (пошаговый отладочный) / BFS (быстрый многопоточный).
    """
    def __init__(self, start_url, stop_url, scope_validator, fast_mode=True):
        self.start_url = start_url
        self.stop_url = stop_url
        self.validator = scope_validator
        self.fast_mode = fast_mode

        self.target_manifest = []
        self.discovered_genera = set()
        self.visited_urls = set()

        self.data_lock = threading.Lock()
        self.log_lock = threading.Lock()

    # --------------------------------------------------------------------------
    # ПРОВЕРКА ГРАНИЦ И РАНГОВ
    # --------------------------------------------------------------------------

    def is_stop_boundary(self, url):
        """Проверяет достижение любой из стоп-границ (поддерживает список или одиночный URL)."""
        if not self.stop_url or not url:
            return False

        current_slug = unquote(urlparse(url).path.strip('/').split('/')[-1]).lower().replace('_', ' ')
        stop_list = self.stop_url if isinstance(self.stop_url, (list, tuple, set)) else [self.stop_url]

        for stop_item in stop_list:
            if not stop_item:
                continue
            stop_slug = unquote(urlparse(stop_item).path.strip('/').split('/')[-1]).lower().replace('_', ' ')
            if current_slug == stop_slug:
                return True

        return False

    def determine_page_rank(self, infobox, fallback_name):
        """
        Определяет ранг страницы по последней строке научной классификации:
        1. Species: -> SPECIES (с биноминальным именем).
        2. Genus:   -> GENUS.
        3. Иначе    -> CLADE.
        """
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

        # Ранг 1: Вид (разворачиваем биноминал)
        if last_tag == "SPECIES":
            clean_words = re.findall(r'[A-Za-z\-]+', species_text) if species_text else []
            if genus_name and clean_words:
                specific_epithet = clean_words[-1].lower()
                if len(clean_words) >= 2 and specific_epithet == genus_name.lower():
                    specific_epithet = clean_words[1].lower()
                full_species = f"{genus_name} {specific_epithet}"
            else:
                full_species = species_text if species_text else fallback_name
            return "SPECIES", full_species, genus_name

        # Ранг 2: Род
        elif last_tag == "GENUS":
            return "GENUS", (genus_name if genus_name else fallback_name), None

        # Ранг 3: Клада
        else:
            return "CLADE", fallback_name, None

    # --------------------------------------------------------------------------
    # СТРАТЕГИИ ИЗВЛЕЧЕНИЯ ДОЧЕРНИХ ТАКСОНОВ (EXTRACTION PIPELINE)
    # --------------------------------------------------------------------------

    def parse_tree_items(self, element, is_genus_page):
        """
        Универсальный обходчик HTML-списков (ul / ol / li).
        - На странице рода: собирает только курсивные ссылки (виды).
        - На странице клады: собирает ссылки на подклады/роды и фиксирует NO_LINK.
        """
        items = []

        if element.name in ['ul', 'ol']:
            li_list = element.find_all('li', recursive=False)
        else:
            direct_ul = element.find(['ul', 'ol'], recursive=False)
            li_list = direct_ul.find_all('li', recursive=False) if direct_ul else element.find_all('li', recursive=False)

        # Обработка ячеек без <li> (текст через <br/>)
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

            # 1. Собираем все ссылки из заголовка текущего пункта
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
                # 2. Если ссылок нет — проверяем на бесссылочную кладу (NO_LINK)
                if not is_genus_page:
                    is_italic = bool(clean_header.find(['i', 'em']))
                    if not is_italic:
                        bold = clean_header.find('b')
                        candidate_name = clean_text(bold.get_text(strip=True)) if bold else clean_text(clean_header.get_text(strip=True))
                        if candidate_name:
                            first_word = candidate_name.split()[0]
                            if first_word and first_word[0].isupper() and not any(c.isdigit() for c in first_word):
                                items.append(('NO_LINK', first_word, None))

                # Во вложенный подсписок ныряем ТОЛЬКО если у элемента не было своих ссылок
                for sub_list in li.find_all(['ul', 'ol'], recursive=False):
                    items.extend(self.parse_tree_items(sub_list, is_genus_page))

        return items

    def _extract_from_taxobox(self, infobox, is_genus_page):
        """
        Базовый экстрактор: структурный сбор из infobox biota.
        Парсит всё, что расположено ниже классификации и выше синонимов.
        """
        all_items = []
        rows = infobox.find_all('tr')

        # 1. На странице рода: забираем Type species (если оформлен отдельной строкой)
        if is_genus_page:
            for tr in rows:
                th = tr.find('th')
                if th and any(tg in th.get_text(" ", strip=True).lower() for tg in GENUS_ONLY_CHILD_HEADERS):
                    td = tr.find('td') or (tr.find_next_sibling('tr').find('td') if tr.find_next_sibling('tr') else None)
                    if td:
                        all_items.extend(self.parse_tree_items(td, is_genus_page=True))

        # 2. Сбор строк потомков (subdivision)
        handled_tds = set()
        passed_classification = False

        for tr in rows:
            th = tr.find('th')
            th_text = th.get_text(" ", strip=True).lower() if th else ""

            # Дошли до синонимов — жесткий СТОП
            if "synonym" in th_text:
                break

            # Отслеживаем окончание предков
            if "scientific classification" in th_text:
                passed_classification = True
                continue

            tds = tr.find_all(['td', 'th'])
            if len(tds) >= 2 and ":" in tds[0].get_text():
                passed_classification = True
                continue

            # До классификации пропускаем шапки и фото
            if not passed_classification:
                continue

            # На кладе блокируем Type species
            if not is_genus_page and any(tg in th_text for tg in GENUS_ONLY_CHILD_HEADERS):
                bad_td = tr.find('td') or (tr.find_next_sibling('tr').find('td') if tr.find_next_sibling('tr') else None)
                if bad_td:
                    handled_tds.add(id(bad_td))
                continue

            # Пропускаем геологию и статус
            if any(ign in th_text for ign in ["temporal range", "fossil range", "conservation status", "binomial name", "cladogram", "phylogeny"]):
                continue

            td = tr.find('td')
            if not td and th and th.get('colspan') == '2':
                next_tr = tr.find_next_sibling('tr')
                if next_tr:
                    td = next_tr.find('td')

            # Защита от дублей ячеек
            if td and id(td) not in handled_tds:
                handled_tds.add(id(td))
                root_lists = [ul for ul in td.find_all(['ul', 'ol']) if not ul.find_parent('li')]
                if root_lists:
                    for r_list in root_lists:
                        all_items.extend(self.parse_tree_items(r_list, is_genus_page))
                else:
                    all_items.extend(self.parse_tree_items(td, is_genus_page))

        return all_items

    def _extract_from_phylogeny_section(self, soup):
        """
        Заготовка / задел на будущее:
        Извлечение таксонов из раздела == Phylogeny == или == Classification == в теле статьи.
        """
        return []

    def _extract_from_body_lists(self, soup):
        """
        Заготовка / задел на будущее:
        Извлечение видов из списков в теле статьи, если инфобокс пуст.
        """
        return []

    def extract_child_taxa(self, soup, infobox, is_genus_page):
        """
        Главная точка входа пайплайна извлечения дочерних таксонов.
        Объединяет базовый сбор из инфобокса и расширенные экстракторы тела статьи.
        """
        # 1. Основной источник — структурный таксобокс
        items = self._extract_from_taxobox(infobox, is_genus_page)

        # 2. Резервный источник — раздел Phylogeny (если инфобокс пуст)
        if not items and not is_genus_page:
            items.extend(self._extract_from_phylogeny_section(soup))

        # 3. Резервный источник — списки в теле статьи
        if not items:
            items.extend(self._extract_from_body_lists(soup))

        return items

    # --------------------------------------------------------------------------
    # ЯДРО ОБРАБОТКИ СТРАНИЦЫ
    # --------------------------------------------------------------------------

    def inspect_page(self, display_name, page_url, session):
        """Скачивает страницу и извлекает ранг, ссылки и предупреждения."""
        warnings = []

        # 1. Проверка границы ДО запроса
        if self.is_stop_boundary(page_url):
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

        # 2. Определение канонического URL
        canonical_tag = soup.find('link', rel='canonical')
        if canonical_tag and canonical_tag.get('href'):
            final_url = normalize_wiki_url(canonical_tag['href']) or resp.url
        else:
            final_url = normalize_wiki_url(resp.url) or page_url

        h1 = soup.find('h1', id='firstHeading')
        actual_title = clean_text(h1.get_text(strip=True)) if h1 else display_name

        # 3. Фиксация редиректа
        if final_url != page_url and (display_name.lower() != actual_title.lower() and display_name.lower() not in actual_title.lower()):
            warnings.append(('WARNING', f"REDIRECT: {display_name} -> {actual_title}"))

        # 4. Проверка границы ПОСЛЕ редиректа
        if self.is_stop_boundary(final_url):
            warnings.append(('WARNING', f"CLADE (BOUNDARY STOP): {actual_title}"))
            return 'STOP', actual_title, final_url, None, [], warnings

        infobox = soup.find('table', class_=re.compile(r'infobox(\s+.*biota.*)?'))
        if not infobox:
            return 'NO_INFOBOX', display_name, final_url, None, [], warnings

        # 5. Определение ранга и валидация скоупа
        rank, actual_name, parent_genus = self.determine_page_rank(infobox, actual_title)

        taxon_to_check = parent_genus if parent_genus else actual_name
        if not self.validator.is_in_scope(taxon_to_check, session, infobox):
            warnings.append(('WARNING', f"OUT OF SCOPE: {taxon_to_check} ({config.TAXONOMY_START_NODE} not in taxonomy)"))
            return 'OUT_OF_SCOPE', actual_name, final_url, None, [], warnings

        # 6. Извлечение дочерних элементов через Extraction Pipeline
        is_genus = (rank == "GENUS")
        child_items = self.extract_child_taxa(soup, infobox, is_genus_page=is_genus)

        return rank, actual_name, final_url, parent_genus, child_items, warnings

    def handle_page_result(self, orig_name, orig_url, expected_genus, inspect_res):
        """
        Единая регистрация результатов (общая для DFS и BFS):
        Логирование, отсечение омонимов и подготовка дочерних задач.
        """
        rank, actual_name, final_url, parent_genus, child_items, warnings = inspect_res

        # Вывод предупреждений
        with self.log_lock:
            for level, msg in warnings:
                if level == 'WARNING': logger.warning(msg)
                elif level == 'ERROR': logger.error(msg)

        if rank in ['STOP', 'ERROR', 'NO_INFOBOX', 'OUT_OF_SCOPE']:
            with self.data_lock:
                self.visited_urls.add(orig_url)
                self.visited_urls.add(final_url)
            return []

        # Защита от повторного входа после редиректа
        if final_url in self.visited_urls and orig_url != final_url:
            with self.log_lock:
                logger.warning(f"CLADE (ALREADY VISITED): {actual_name}")
            with self.data_lock:
                self.visited_urls.add(orig_url)
            return []

        # Защита от омонимов (переход со страницы рода на чужой таксон)
        if expected_genus:
            is_foreign_clade = (rank == "CLADE")
            is_foreign_genus = (rank == "GENUS" and actual_name.lower() != expected_genus.lower())
            is_foreign_species = (rank == "SPECIES" and parent_genus and parent_genus.lower() != expected_genus.lower())

            if is_foreign_clade or is_foreign_genus or is_foreign_species:
                with self.log_lock:
                    logger.warning(f"OUT OF SCOPE (HOMONYM COLLISION): {expected_genus} -> {actual_name} ({final_url})")
                return []

        next_tasks = []
        with self.data_lock:
            self.visited_urls.add(orig_url)
            self.visited_urls.add(final_url)

            # Сохранение результатов
            if rank == "SPECIES":
                if parent_genus and (orig_name.lower() == parent_genus.lower() or parent_genus not in self.discovered_genera):
                    self.discovered_genera.add(parent_genus)
                    with self.log_lock: logger.info(f"GENUS: {parent_genus} ({final_url})")
                    self.target_manifest.append({'taxon': parent_genus, 'url': final_url, 'rank': 'genus'})
                else:
                    with self.log_lock: logger.info(f"SPECIES: {actual_name} ({final_url})")
                    self.target_manifest.append({'taxon': actual_name, 'url': final_url, 'rank': 'species'})

            elif rank == "GENUS":
                if actual_name in self.discovered_genera:
                    with self.log_lock: logger.warning(f"GENUS (ALREADY VISITED): {actual_name}")
                else:
                    self.discovered_genera.add(actual_name)
                    with self.log_lock: logger.info(f"GENUS: {actual_name} ({final_url})")
                    self.target_manifest.append({'taxon': actual_name, 'url': final_url, 'rank': 'genus'})

            elif rank == "CLADE":
                with self.log_lock: logger.info(f"CLADE: {orig_name}")

            # Подготовка задач
            if rank in ["CLADE", "GENUS"]:
                next_expected = actual_name if rank == "GENUS" else expected_genus
                for item in child_items:
                    if item[0] == 'LINK':
                        c_name, c_url = item[1], item[2]
                        if self.is_stop_boundary(c_url):
                            with self.log_lock: logger.warning(f"CLADE (BOUNDARY STOP): {c_name}")
                            continue
                        if c_url in self.visited_urls:
                            next_tasks.append(('ALREADY_VISITED', c_name, c_url, None))
                            continue
                        next_tasks.append(('LINK', c_name, c_url, next_expected))
                    elif item[0] == 'NO_LINK':
                        next_tasks.append(('NO_LINK', item[1], None, None))

            if not config.BRIEF_CONSOLE:
                sys.stdout.write(f"\rDiscovered: [Genera: {len(self.discovered_genera)}] [Targets: {len(self.target_manifest)}]")
                sys.stdout.flush()

        return next_tasks

    # --------------------------------------------------------------------------
    # ДИСПЕТЧЕРЫ ОБХОДА (DFS / BFS)
    # --------------------------------------------------------------------------

    def _dfs_crawl(self, display_name, page_url, session, expected_genus=None):
        """Синхронный пошаговый обход в глубину (DFS) для точной отладки."""
        if self.is_stop_boundary(page_url):
            logger.warning(f"CLADE (BOUNDARY STOP): {display_name}")
            return

        if page_url in self.visited_urls:
            logger.warning(f"CLADE (ALREADY VISITED): {display_name}")
            return

        inspect_res = self.inspect_page(display_name, page_url, session)
        next_tasks = self.handle_page_result(display_name, page_url, expected_genus, inspect_res)

        for task_type, c_name, c_url, next_exp in next_tasks:
            if task_type == 'LINK':
                self._dfs_crawl(c_name, c_url, session, next_exp)
            elif task_type == 'NO_LINK':
                logger.warning(f"CLADE (NO LINK): {c_name}")
            elif task_type == 'ALREADY_VISITED':
                logger.warning(f"CLADE (ALREADY VISITED): {c_name}")

    def _bfs_crawl(self, root_name, start_url, session):
        """Быстрый параллельный обход в ширину (BFS + ThreadPoolExecutor)."""
        use_parallel = getattr(config, 'USE_PARALLEL', False)
        max_workers = getattr(config, 'MAX_WORKERS', 20) if use_parallel else 1

        queue = [(root_name, start_url, None)]
        self.visited_urls.add(start_url)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            while queue:
                batch = []
                while queue and len(batch) < (max_workers * 2):
                    batch.append(queue.pop(0))

                future_to_item = {
                    executor.submit(self.inspect_page, name, url, session): (name, url, exp_genus)
                    for name, url, exp_genus in batch
                }

                for future in as_completed(future_to_item):
                    orig_name, orig_url, exp_genus = future_to_item[future]
                    try:
                        inspect_res = future.result()
                    except Exception as e:
                        logger.error(f"Task error on {orig_url}: {e}")
                        continue

                    next_tasks = self.handle_page_result(orig_name, orig_url, exp_genus, inspect_res)

                    with self.data_lock:
                        for task_type, c_name, c_url, next_exp in next_tasks:
                            if task_type == 'LINK':
                                if not any(q[1] == c_url for q in queue):
                                    self.visited_urls.add(c_url)
                                    queue.append((c_name, c_url, next_exp))
                            elif task_type == 'NO_LINK':
                                with self.log_lock: logger.warning(f"CLADE (NO LINK): {c_name}")
                            elif task_type == 'ALREADY_VISITED':
                                with self.log_lock: logger.warning(f"CLADE (ALREADY VISITED): {c_name}")

    # --------------------------------------------------------------------------
    # ЗАПУСК И ЭКСПОРТ
    # --------------------------------------------------------------------------

    def run(self):
        """Главная точка запуска краулера."""
        if config.BRIEF_CONSOLE:
            print("FETCH_TAXA_LIST...", end=" ", flush=True)
        else:
            print("Starting script: FETCH_TAXA_LIST")

        logger.info("--- SCRIPT START: FETCH_TAXA_LIST ---")
        logger.info(f"Research Mode: {config.RESEARCH_MODE}")
        logger.info(f"Start URL: {self.start_url}")
        if isinstance(self.stop_url, (list, tuple)):
            logger.info(f"Stop URLs: {', '.join(self.stop_url)}")
        else:
            logger.info(f"Stop URL: {self.stop_url if self.stop_url else 'None'}")

        session = requests.Session()
        session.headers.update(HEADERS)
        if getattr(config, 'USE_PARALLEL', False) and self.fast_mode:
            adapter = requests.adapters.HTTPAdapter(pool_connections=config.MAX_WORKERS, pool_maxsize=config.MAX_WORKERS)
            session.mount('https://', adapter)

        root_name = unquote(urlparse(self.start_url).path.strip('/').split('/')[-1]).replace('_', ' ')

        # 1. Режим кастомного списка
        if config.USE_CUSTOM_LIST and os.path.exists(CUSTOM_LIST_PATH):
            logger.info(f"Custom list mode active: reading {CUSTOM_LIST_PATH}")
            with open(CUSTOM_LIST_PATH, 'r', encoding='utf-8') as f:
                custom_genera = [clean_text(line) for line in f if clean_text(line)]

            for name in custom_genera:
                g_url = f"{config.BASE_WIKI_URL}{name}"
                if self.fast_mode:
                    self._bfs_crawl(name, g_url, session)
                else:
                    self._dfs_crawl(name, g_url, session)
        else:
            # 2. Обход дерева
            if self.fast_mode:
                self._bfs_crawl(root_name, self.start_url, session)
            else:
                self._dfs_crawl(root_name, self.start_url, session)

        if not config.BRIEF_CONSOLE:
            print()
            print("Crawl completed.")

        # Сохранение манифеста target_pages.csv
        os.makedirs(TABLES_DIR, exist_ok=True)
        try:
            with open(TARGET_PAGES_CSV, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f, delimiter=';')
                writer.writerow(["taxon", "url"])
                for row in self.target_manifest:
                    writer.writerow([row['taxon'], row['url']])
            logger.info(f"Target pages saved to: {os.path.abspath(TARGET_PAGES_CSV)}")
        except Exception as e:
            logger.error(f"Failed to save {TARGET_PAGES_CSV}: {e}")

        summary_msg = f"Discovery finished. Total targets: {len(self.target_manifest)}"
        logger.info(summary_msg)

        if config.BRIEF_CONSOLE:
            print(f"{len(self.target_manifest)} targets discovered")
        else:
            print(f"TARGET PAGES DISCOVERED: {len(self.target_manifest)}")
            print(f"SAVED TO: {TARGET_PAGES_CSV}")
            print("Script ended: FETCH_TAXA_LIST")

        logger.info("--- SCRIPT END: FETCH_TAXA_LIST ---")


# ==============================================================================
# ТОЧКА ВХОДА (ENTRY POINT)
# ==============================================================================

def main():
    validator = TaxonomyScopeValidator(start_node=config.TAXONOMY_START_NODE)
    crawler = TaxaTreeCrawler(
        start_url=START_PAGE,
        stop_url=STOP_PAGE,
        scope_validator=validator,
        fast_mode=FAST_CRAWL
    )
    crawler.run()


if __name__ == "__main__":
    main()