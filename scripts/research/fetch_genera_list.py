import sys
sys.dont_write_bytecode = True

import os
import re
import copy
import time
import logging
import csv
import threading
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import local_settings

# --- ПУТИ И НАСТРОЙКИ ---
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

GENERA_CSV = os.path.join(BASE_DIR, config.TABLES_DIR, "genera_list.csv")
CUSTOM_LIST_PATH = os.path.join(BASE_DIR, config.CUSTOM_LISTS_DIR, config.CUSTOM_LIST_NAME)
SAMPLE_LIST_PATH = os.path.join(BASE_DIR, config.CUSTOM_LISTS_DIR, "sample_genera.txt")

LOG_FILE = os.path.join(config.LOGS_DIR, "fetch_genera_list.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logger = logging.getLogger("fetch_genera_list")
logger.setLevel(logging.INFO)
if logger.hasHandlers():
    logger.handlers.clear()

file_handler = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
logger.addHandler(file_handler)

USER_EMAIL = getattr(local_settings, 'USER_EMAIL', 'researcher@pfl-project.org')
HEADERS = {
    'User-Agent': f'PrehistoricFaunaLibraryCollector/2.0 (mailto:{USER_EMAIL})'
}

BASE_WIKI_URL = "https://en.wikipedia.org"
START_PAGE = getattr(config, 'WIKI_START_URL', "https://en.wikipedia.org/wiki/Dinosauromorpha")
STOP_PAGE = getattr(config, 'WIKI_STOP_URL', None)

USE_PARALLEL = config.USE_PARALLEL
MAX_WORKERS = config.MAX_WORKERS if USE_PARALLEL else 1

# Потокобезопасные хранилища
visited_lock = threading.Lock()
log_lock = threading.Lock()

visited_urls = set()
all_discovered_genera = set()
total_pages_visited = 0
total_bytes_downloaded = 0


def is_stop_boundary(url):
    """Проверяет нижнюю границу остановки по URL."""
    if not STOP_PAGE or not url:
        return False
    target_slug = urlparse(STOP_PAGE).path.strip('/').split('/')[-1].lower()
    current_slug = urlparse(url).path.strip('/').split('/')[-1].lower()
    return target_slug == current_slug


def clean_item_for_analysis(li_tag):
    """Изолирует строку: удаляет подсписки, сноски [1] и авторов (<small>, font-size)."""
    item = copy.copy(li_tag)
    for nested in item.find_all(['ul', 'ol', 'table', 'sup', 'small']):
        nested.decompose()
    for span in item.find_all('span'):
        if 'font-size' in span.get('style', ''):
            span.decompose()
    return item


def parse_genus_name(li_tag):
    """Извлекает только имя рода (курсив <i> или кавычки)."""
    item = clean_item_for_analysis(li_tag)

    full_li_text = item.get_text(separator=" ", strip=True)
    italic_tag = item.find('i')
    has_quotes = '"' in full_li_text or '“' in full_li_text

    if not italic_tag and not has_quotes:
        return None

    genus_link = item.find('a')
    if genus_link:
        raw_name = genus_link.get_text(strip=True)
    elif italic_tag:
        raw_name = italic_tag.get_text(strip=True)
    else:
        match = re.search(r'["“]([A-Z][a-z]+)["”]', full_li_text)
        raw_name = match.group(1) if match else None

    if not raw_name:
        return None

    genus_name = raw_name.replace('†', '').replace('?', '').replace('"', '').replace('“', '').replace('”', '').strip()
    if not genus_name or not genus_name[0].isupper() or len(genus_name.split()) > 1:
        genus_name = genus_name.split()[0] if genus_name else None

    return genus_name


def parse_clade_item(li_tag):
    """Определяет кладу со ссылкой или без."""
    item = clean_item_for_analysis(li_tag)

    full_li_text = item.get_text(separator=" ", strip=True)
    if item.find('i') or '"' in full_li_text or '“' in full_li_text:
        return None, None

    # 1. Клада с <b>
    bold = item.find('b')
    if bold:
        link = bold.find('a') or bold.find_parent('a')
        if link and link.get('href'):
            href = link['href'].strip()
            if not href.startswith('#') and 'redlink=1' not in href and 'action=edit' not in href:
                full_url = urljoin(BASE_WIKI_URL, href)
                clade_name = link.get_text(strip=True).replace('†', '').strip()

                if is_stop_boundary(full_url):
                    return 'STOP', (clade_name, full_url)

                return 'LINK', (clade_name, full_url)

        clade_name = bold.get_text(strip=True).replace('†', '').strip()
        if clade_name:
            return 'NO_LINK', clade_name
        return None, None

    # 2. Клада БЕЗ <b>
    link = item.find('a')
    if link and link.get('href'):
        href = link['href'].strip()
        if not href.startswith('#') and 'redlink=1' not in href and 'action=edit' not in href:
            clade_name = link.get_text(strip=True).replace('†', '').strip()
            full_url = urljoin(BASE_WIKI_URL, href)

            if is_stop_boundary(full_url):
                return 'STOP', (clade_name, full_url)

            if clade_name and clade_name[0].isupper() and not re.search(r'\d', clade_name):
                bad_words = ["extinct", "details", "see also"]
                if clade_name.lower() not in bad_words:
                    return 'LINK', (clade_name, full_url)

    return None, None


def find_taxa_section_by_layout(infobox):
    """Поиск блока списка чисто по верстке."""
    ignored_headers = ["scientific classification", "synonyms", "type species", "type genus", "temporal range"]

    for tr in infobox.find_all('tr'):
        th = tr.find('th')
        if th and th.get('colspan') == '2':
            header_text = th.get_text(strip=True).lower()
            if any(ign in header_text for ign in ignored_headers):
                continue

            next_tr = tr.find_next_sibling('tr')
            if next_tr:
                td = next_tr.find('td')
                if td and td.find(['ul', 'ol']):
                    return td
    return None


def process_taxa_list(list_element, found_genera, new_branches, log_buffer):
    """
    Разбор списка по Идее 1 (делегирование):
    Вложенные списки у ссылочных клад игнорируются, у NO_LINK — раскрываются.
    """
    direct_items = list_element.find_all('li', recursive=False)

    for li in direct_items:
        g_name = parse_genus_name(li)
        if g_name:
            found_genera.append(g_name)
            log_buffer.append(('INFO', f"GENUS: {g_name}"))
            continue

        action, clade_data = parse_clade_item(li)

        if action == 'STOP':
            clade_name, branch_url = clade_data
            log_buffer.append(('WARNING', f"CLADE (BOUNDARY STOP): {clade_name} ({branch_url})"))
            continue

        elif action == 'LINK':
            clade_name, branch_url = clade_data
            new_branches.append((clade_name, branch_url))
            continue

        elif action == 'NO_LINK':
            clade_name = clade_data
            log_buffer.append(('INFO', f"CLADE (NO LINK): {clade_name}"))
            for nested_list in li.find_all(['ul', 'ol'], recursive=False):
                process_taxa_list(nested_list, found_genera, new_branches, log_buffer)
            continue


def process_single_page(current_url, session):
    """
    Многопоточный обработчик одной страницы:
    Возвращает найденные роды, новые ссылки и атомарный лог.
    """
    global total_bytes_downloaded
    page_title = current_url.split("/")[-1].replace("_", " ")

    found_genera = []
    new_branches = []
    log_buffer = [('INFO', f"PAGE START: {page_title} ({current_url})")]

    try:
        resp = session.get(current_url, timeout=12)
        resp.raise_for_status()
        with visited_lock:
            total_bytes_downloaded += len(resp.content)
    except Exception as e:
        log_buffer.append(('ERROR', f"Failed to fetch {current_url}: {e}"))
        return found_genera, new_branches, log_buffer

    try:
        soup = BeautifulSoup(resp.text, 'html.parser')
        infobox = soup.find('table', class_='infobox biota')
        
        # Если вообще нет инфобокса — тогда да, выходим
        if not infobox:
            return found_genera, new_branches, log_buffer

        subgroups_td = find_taxa_section_by_layout(infobox)

        # [!] ЖЕСТКОЕ УСЛОВИЕ: Ищем слова "see" или "text" и обязательное наличие ссылки в ячейке подгрупп
        has_see_text = False
        if subgroups_td:
            td_text = subgroups_td.get_text(separator=" ", strip=True).lower()
            has_link = bool(subgroups_td.find('a'))
            if ("see" in td_text or "text" in td_text) and has_link:
                has_see_text = True

        # Если блок не найден ИЛИ в нем есть сигнал "see ... text" со ссылкой -> запускаем сканер таблиц
        if not subgroups_td or has_see_text:
            reason = "Empty infobox" if not subgroups_td else "Found 'see text' trigger"
            warn_msg = f"FALLBACK (WIKITABLE USED - {reason}) for {page_title} ({current_url})"
            log_buffer.append(('WARNING', warn_msg))

            table_genera = parse_genera_from_wikitables(soup, current_url)
            for g in table_genera:
                found_genera.append(g)
                log_buffer.append(('INFO', f"GENUS (TABLE): {g}"))

            return found_genera, new_branches, log_buffer

        # --- СТАНДАРТНАЯ ЛОГИКА ОБХОДА ИНФОБОКСА ---
        collapsible_tables = subgroups_td.find_all('table', class_='mw-collapsible')
        # ... дальше старый код обхода root_lists и collapsible_tables ...

        # 1. Основные списки
        root_lists = [
            elem for elem in subgroups_td.find_all(['ul', 'ol'])
            if not elem.find_parent('li') and not any(tbl in elem.parents for tbl in collapsible_tables)
        ]
        for root_list in root_lists:
            process_taxa_list(root_list, found_genera, new_branches, log_buffer)

        # 2. Свернутые таблицы (Possible)
        for col_table in collapsible_tables:
            header_div = col_table.find('th')
            header_title = header_div.get_text(strip=True) if header_div else "Possible taxa"
            log_buffer.append(('INFO', f"POSSIBLE: {header_title} ({current_url})"))

            col_root_lists = [
                elem for elem in col_table.find_all(['ul', 'ol'])
                if not elem.find_parent('li')
            ]
            for col_list in col_root_lists:
                process_taxa_list(col_list, found_genera, new_branches, log_buffer)

    except Exception as e:
        log_buffer.append(('ERROR', f"Error parsing {current_url}: {e}"))

    return found_genera, new_branches, log_buffer


def collect_genera():
    """Точка входа скрипта fetch_genera_list."""
    global total_pages_visited

    if config.BRIEF_CONSOLE:
        print("FETCH_GENERA_LIST...", end=" ", flush=True)
    else:
        print("Starting script: FETCH_GENERA_LIST")

    logger.info("--- SCRIPT START: FETCH_GENERA_LIST ---")
    logger.info("Configuration loaded successfully from config.py")
    logger.info(f"Upper Root Boundary: {START_PAGE}")
    logger.info(f"Lower Stop Boundary: {STOP_PAGE if STOP_PAGE else 'None'}")
    logger.info(f"Execution Mode: {'PARALLEL (Workers: ' + str(MAX_WORKERS) + ')' if USE_PARALLEL else 'SINGLE-THREADED'}")

    # 1. Инициализация образца
    if config.CREATE_CUSTOM_LIST_DIR:
        custom_dir = os.path.dirname(SAMPLE_LIST_PATH)
        if not os.path.exists(custom_dir):
            os.makedirs(custom_dir, exist_ok=True)
        if not os.path.exists(SAMPLE_LIST_PATH):
            sample_genera = [
                "Aardonyx", "Triceratops", "Tyrannosaurus", "Cryptarcus",
                "Obelignathus", "Spinosaurus", "Citipes", "Allosaurus",
                "Brontosaurus", "Velociraptor"
            ]
            with open(SAMPLE_LIST_PATH, 'w', encoding='utf-8') as f:
                f.write("\n".join(sample_genera))

    # 2. Кастомный фильтр
    genus_filter = set()
    if config.USE_CUSTOM_LIST:
        if os.path.exists(CUSTOM_LIST_PATH):
            with open(CUSTOM_LIST_PATH, 'r', encoding='utf-8') as f:
                genus_filter = {line.strip().lower() for line in f if line.strip()}
            logger.info(f"Filter active: using {config.CUSTOM_LIST_NAME} as whitelist ({len(genus_filter)} names)")

    # 3. Настройка HTTP-пула соединений (Keep-Alive)
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS)
    session.mount('https://', adapter)
    session.headers.update(HEADERS)

    # 4. Сбор родов: либо мгновенный перенос из TXT, либо обход Википедии
    if config.USE_CUSTOM_LIST:
      logger.info(
          "Custom list active: skipping Wikipedia crawl, loading directly from"
          " TXT."
      )
      for name in genus_filter:
        clean_name = name.strip()
        if clean_name:
          # Делаем первую букву заглавной
          formatted_name = clean_name[0].upper() + clean_name[1:]
          all_discovered_genera.add(formatted_name)
    else:
      # Полный многопоточный обход дерева Википедии
      with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        visited_urls.add(START_PAGE)
        futures = {
            executor.submit(process_single_page, START_PAGE, session): (
                START_PAGE
            )
        }

        while futures:
          done, _ = wait(futures, return_when=FIRST_COMPLETED)

          for f in done:
            url = futures.pop(f)
            total_pages_visited += 1

            try:
              found_genera, new_branches, log_buffer = f.result()
            except Exception as e:
              logger.error(f"Thread failed on {url}: {e}")
              continue

            with log_lock:
              for level, msg in log_buffer:
                if level == "WARNING":
                  logger.warning(msg)
                elif level == "ERROR":
                  logger.error(msg)
                else:
                  logger.info(msg)

            for g in found_genera:
              all_discovered_genera.add(g)

            with visited_lock:
              for clade_name, branch_url in new_branches:
                if is_stop_boundary(branch_url):
                  logger.warning(
                      f"CLADE (BOUNDARY STOP): {clade_name} ({branch_url})"
                  )
                  continue

                if branch_url in visited_urls:
                  logger.warning(
                      f"CLADE (ALREADY VISITED): {clade_name} ({branch_url})"
                  )
                else:
                  visited_urls.add(branch_url)
                  futures[
                      executor.submit(
                          process_single_page, branch_url, session
                      )
                  ] = branch_url

            if not config.BRIEF_CONSOLE:
              sys.stdout.write(
                  f"\rDiscovering clades... [{total_pages_visited} pages]"
                  f" ({len(all_discovered_genera)} genera)"
              )
              sys.stdout.flush()

    # 5. Фильтрация и сортировка
    final_genera = []
    for g_name in sorted(all_discovered_genera):
        if config.USE_CUSTOM_LIST and genus_filter:
            if g_name.lower() not in genus_filter:
                continue
        final_genera.append(g_name)

    if not config.BRIEF_CONSOLE:
        print()
        print("Discovery completed.")

    logger.info("Discovery completed.")

    # 6. Сохранение в CSV ТОЛЬКО одной колонки genus
    if final_genera:
        os.makedirs(os.path.dirname(GENERA_CSV), exist_ok=True)
        try:
            with open(GENERA_CSV, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f, delimiter=';')
                writer.writerow(["genus"])
                for g in final_genera:
                    writer.writerow([g])

            size_mb = total_bytes_downloaded / (1024 * 1024)
            size_report = f"Total data downloaded: {size_mb:.2f} MB"
            count_msg = f"Total unique genera found: {len(final_genera)}"
            path_msg = f"Genera list saved to {os.path.abspath(GENERA_CSV)}"

            logger.info(size_report)
            logger.info(count_msg)
            logger.info(path_msg)

            if config.BRIEF_CONSOLE:
                mode_str = "filtered" if config.USE_CUSTOM_LIST else "total"
                print(f"{len(final_genera)} genera found ({mode_str})")
            else:
                print(f"PAGES CRAWLED: {total_pages_visited}")
                print(size_report)
                print(count_msg)
                print(path_msg)
                print("Script ended: FETCH_GENERA_LIST")

        except Exception as e:
            err_msg = f"Save failed: {e}"
            logger.error(err_msg)
            print(f"[ERROR] {err_msg}")
    else:
        err_msg = "Discovery error: No genera were extracted."
        logger.error(err_msg)
        print(f"[ERROR] {err_msg}")

    logger.info("=== FINAL DATA AUDIT REPORT ===")
    logger.info(f"[1] TOTAL PAGES CRAWLED ({total_pages_visited})")
    logger.info(f"[2] TOTAL GENERA DISCOVERED ({len(final_genera)})")
    logger.info("--- SCRIPT END: FETCH_GENERA_LIST ---")


def parse_genera_from_wikitables(soup, current_url):
  """Резервный парсер: сканирует вики-таблицы (wikitable) на страницах,

  где инфобокс пуст или отправляет в текст (see text). Выдает WARNING в лог.
  """
  table_genera = set()
  wikitables = soup.find_all('table', class_='wikitable')

  for table in wikitables:
    for row in table.find_all('tr'):
      # Ищем все ссылки внутри курсива в таблице
      for it in row.find_all('i'):
        link = it.find('a')
        if not link:
          continue

        raw_name = link.get_text(strip=True)
        # Чистим от всего лишнего (крестики, вопросы, кавычки, сноски)
        clean_name = (
            raw_name.replace('†', '')
            .replace('?', '')
            .replace('"', '')
            .replace('“', '')
            .strip()
        )

        # Проверяем, что это похоже на имя рода (с заглавной буквы, без пробелов)
        if (
            clean_name
            and clean_name[0].isupper()
            and len(clean_name.split()) == 1
            and len(clean_name) > 1
        ):
          bad_words = [
              'genus',
              'taxonomy',
              'phylogeny',
              'description',
              'species',
              'age',
              'formation',
          ]
          if clean_name.lower() not in bad_words:
            table_genera.add(clean_name)

  return table_genera

if __name__ == "__main__":
    collect_genera()