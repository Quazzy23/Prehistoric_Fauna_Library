import sys
sys.dont_write_bytecode = True  # Сначала запрещаем
import requests
import json
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import re
import csv
import copy
import os
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
# Добавляем путь к папке scripts, чтобы увидеть config.py
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import local_settings

# --- ПУТИ И НАСТРОЙКИ ---
USE_CUSTOM_LIST = config.USE_CUSTOM_LIST
CUSTOM_LIST_NAME = config.CUSTOM_LIST_NAME

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# [!] УНИФИКАЦИЯ: Используем готовые переменные из конфига
DATA_ROOT = os.path.join(BASE_DIR, config.TABLES_DIR)
INPUT_CSV = os.path.join(DATA_ROOT, "genera_list.csv")
OUTPUT_FILE = os.path.join(DATA_ROOT, "raw_fauna.csv")
CLASSIFICATION_FILE = os.path.join(DATA_ROOT, "taxonomic_tree.csv")

CUSTOM_LIST_PATH = os.path.join(BASE_DIR, config.CUSTOM_LISTS_DIR, config.CUSTOM_LIST_NAME)
MIGRATIONS_FILE = os.path.join(BASE_DIR, config.MIGRATIONS_FILE)

# Настройка логов
LOG_FILE = os.path.join(config.LOGS_DIR, "parse_wiki_details.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

USE_PARALLEL = config.USE_PARALLEL
MAX_WORKERS = config.MAX_WORKERS

# [!] РЕЖИМ ЛОГИРОВАНИЯ
BUFFER_LOGS = True

taxon_cache = {} # Кэш для хранения древа классификации
lowest_units_seen = {} # НОВОЕ: только минимальные клады { "Thecodontosauridae": "Thecodontosaurus" }
taxon_lock = threading.Lock()
# Замок для безопасной записи данных из разных потоков
data_lock = threading.Lock()
log_lock = threading.Lock() 
# [!] Кэш страниц синонимов: { "bison": (False, None), "raptorex": (True, meta_dict) }
synonym_page_cache = {}

# [!] КЭШИ ДЛЯ ФИЛЬТРАЦИИ СИНОНИМОВ (КЕЙС БИЗОНА)
verified_synonyms_cache = set()  # Роды, подтвержденные как Dinosauromorpha
excluded_synonyms_cache = set()  # Чужаки (Bison, Crocodilus и т.д.)
visited_genera = set()  # Защита от повторов

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    filename=LOG_FILE,
    filemode='w',
    encoding='utf-8'
)

BASE_WIKI_URL = config.BASE_WIKI_URL
USER_EMAIL = local_settings.USER_EMAIL
HEADERS = {'User-Agent': f'PrehistoricFaunaLibrary/1.0 (mailto:{USER_EMAIL})'}
INCLUDE_UNCERTAIN_STAGES = config.INCLUDE_UNCERTAIN_STAGES
FETCH_SYNONYMS = config.FETCH_SYNONYMS

MISSING_VAL = "-"
WIKI_TIMEOUT = 5  # Время ожидания ответа от Википедии (в секундах)

# Глобальный счетчик байт
total_bytes_downloaded = 0
total_duplicates_ignored = 0 # <--- Добавить
current_session_facts = {} # Хранилище чистых данных для аудита (Ключ: Значение)

def extract_classification(infobox):
    # Находит Род, Кладу, Ma и Ярус (с поддержкой диапазонов)
    true_genus, clade, age, stage = MISSING_VAL, MISSING_VAL, MISSING_VAL, MISSING_VAL
    g_auth_raw, g_year = MISSING_VAL, MISSING_VAL
    genus_is_extant = True # По дефолту жив
    
    # 1. Temporal Range (Исправлено для "80.5 to 72 Ma")
    temp_div = infobox.find(lambda tag: tag.name == "div" and "Temporal range" in tag.get_text())
    if temp_div:
        temp_copy = copy.copy(temp_div)
        for noise in temp_copy.find_all(['div', 'style'], id='Timeline-row'): noise.decompose()
        for noise in temp_copy.find_all('sup'): noise.decompose()
        text = temp_copy.get_text(separator=" ", strip=True).replace("Temporal range:", "")
        # Если НЕ включаем сомнительные стадии, то отрезаем их
        if not INCLUDE_UNCERTAIN_STAGES:
            if "Possible" in text: text = text.split("Possible")[0].strip(" ,()")
        
        # Регулярка теперь понимает "to"
        ma_match = re.search(r'(\d+\.?\d*)\s*(?:to|[–\-\—])?\s*(\d*\.?\d*)\s*Ma', text)
        if ma_match:
            g1, g2 = ma_match.group(1), ma_match.group(2)
            age = f"{g1}–{g2}" if g2 else g1

        ians = re.findall(r'\b[A-Z][a-z]+ian\b', text)
        if ians:
            # Если нашли несколько, делаем диапазон, но только если они разные
            if len(ians) >= 2 and ians[0] != ians[-1]:
                stage = f"{ians[0]}-{ians[-1]}"
            else:
                stage = ians[0]
        else:
            period_match = re.search(r'\b((?:Early|Middle|Late|Upper|Lower)\s+)?(Cretaceous|Jurassic|Triassic|Permian)\b', text)
            if period_match: stage = period_match.group(0)

    # 2. Ищем Род и метаданные Рода
    rows = infobox.find_all('tr')
    for i, row in enumerate(rows):
        tds = row.find_all('td')
        if len(tds) == 2 and "Genus:" in tds[0].get_text():
            # Проверка: есть ли крестик у рода?
            if '†' in tds[1].get_text():
                genus_is_extant = False
                
            # Запасной вариант для автора/года
            genus_cell_text = tds[1].get_text(separator=" ", strip=True)
            y_match = re.findall(r'(\d{4})', genus_cell_text)
            if y_match:
                g_year = y_match[-1]
                g_auth_raw = genus_cell_text.split(g_year)[0]

            for extra in tds[1].find_all(['sup', 'small']): extra.decompose()
            true_genus = tds[1].get_text(strip=True).replace('†', '').replace('(', '').replace(')', '').strip().split()[0]
            prev_tds = rows[i-1].find_all('td')
            if len(prev_tds) == 2:
                for extra in prev_tds[1].find_all(['sup', 'small', 'div']): extra.decompose()
                clean_p = re.sub(r'\(.*?\)', '', prev_tds[1].get_text(separator=" ", strip=True)).replace('†', '').replace('?', '').strip()
                clade = clean_p.split()[0] if clean_p else MISSING_VAL
            break
            
    return true_genus, clade, age, stage, g_auth_raw, g_year, genus_is_extant

def clean_author_string(author_raw, genus_to_strip=None, species_to_strip=None):
    """Очистка автора: удаляет мусор, защищает Rich & Rich, обрабатывает 'emend'."""
    if not author_raw or author_raw == MISSING_VAL: return MISSING_VAL
    
    # 1. Удаляем сноски типа [1] или [2]
    text = re.sub(r'\[.*?\]', '', author_raw)
    
    # 2. Логика EMEND: если автор был исправлен, берем только последнего (того, кто ПОСЛЕ 'emend')
    if 'emend' in text.lower():
        # Сплитим по слову emend (независимо от регистра) и берем последний кусок
        text = re.split(r'emend', text, flags=re.IGNORECASE)[-1]
    
    # 3. Удаляем годы (1888) и года с буквами (1888a)
    text = re.sub(r'\d{4}[a-z]?', '', text)
    text = re.sub(r'\d+', '', text)
    
    # 4. Список технического шума (добавлены служебные слова заголовков)
    noise_list = [
        "nomen nudum", "nomen dubium", "originally", "vide", "preoccupied", 
        "in part", "sic", "nomen rejectum", "nomen conservandum", 
        "conserved name", "rejected name", "synonyms", "list", "of", "from"
    ]
    for noise in noise_list:
        text = re.compile(re.escape(noise), re.IGNORECASE).sub("", text)

    # 5. Принудительно удаляем род и вид, если они просочились
    if genus_to_strip:
        text = re.compile(r'\b' + re.escape(genus_to_strip) + r'\b', re.IGNORECASE).sub("", text)
    if species_to_strip:
        text = re.compile(r'\b' + re.escape(species_to_strip) + r'\b', re.IGNORECASE).sub("", text)
    
    # 6. Удаляем скобки и мусорные знаки, сохраняя запятые и &
    # Удаляем мусор, но ? удаляем только если он не часть логики статуса
    text = text.replace('†', '').replace('"', '').replace('(', '').replace(')', '')
    # Знак вопроса в авторе — это всегда шум
    text = text.replace('?', '')
    
    # 7. Финальная сборка и проверка на дубликаты (Paul, Paul)
    raw_parts = text.split()
    final_parts = []
    seen_names = set()
    
    for i, p in enumerate(raw_parts):
        clean_word = p.rstrip(",. ").strip()
        low_word = clean_word.lower()
        if not low_word: continue
        
        # Обработка et al.
        if low_word in ["et", "al", "etal"]:
            if "et" not in seen_names:
                final_parts.append("et al.")
                seen_names.add("et")
                seen_names.add("al")
            continue

        # Проверка заглавной буквы и слов-связок
        is_connector = low_word in ["&", "and", "in", "von", "de", "van", "da", "der"]
        has_capital = any(char.isupper() for char in clean_word)
        if not has_capital and not is_connector: continue

        # Защита Rich & Rich через проверку & перед словом
        is_duplicate = low_word in seen_names
        was_connected = i > 0 and raw_parts[i-1].lower() in ["&", "and"]
        
        if not is_duplicate or was_connected or is_connector:
            final_parts.append(p)
            if not is_connector: seen_names.add(low_word)
            
    res = " ".join(final_parts).replace(" ,", ",").strip(" ,.")
    res = re.sub(r'(et al\.?)+', 'et al.', res, flags=re.IGNORECASE)
    
    return res if len(res) > 1 else MISSING_VAL

def extract_data(element, true_genus, header_says_type, genus_is_extant): # Добавили аргумент
    """Извлекает данные о виде. Игнорирует курсив внутри тегов <small>."""
    temp_elem = copy.copy(element)
    
    # [!] НОВАЯ ЛОГИКА: вид жив только если РОД жив И у вида нет крестика
    is_extant = genus_is_extant and ('†' not in element.get_text())
    for tag in temp_elem.find_all(['abbr', 'sup', 'style']): tag.decompose()
    raw_text_full = temp_elem.get_text(separator=" ", strip=True)

    is_dubium = any(x in raw_text_full.lower() for x in ["dubium", "?"])
    found_type_marker = "type" in raw_text_full.lower()

    # 1. ПОИСК НАЗВАНИЯ ВИДА
    species_part = MISSING_VAL
    is_nudum = False # Флаг для отслеживания нудумов по кавычкам
    tech_italics = ["nomen", "nudum", "dubium", "sic", "reject", "conserv", "originally", "type", "et", "al"]
    
    scientific_tokens = []
    nodes_to_delete = []
    
    for it in temp_elem.find_all('i'):
        if it.find_parent('small'): continue
            
        it_txt = it.get_text(separator=" ", strip=True).replace('†', '').replace('?', '').strip()
        if any(c.isalpha() for c in it_txt) and not any(t in it_txt.lower().split() for t in tech_italics):
            scientific_tokens.append(it_txt)
            nodes_to_delete.append(it)
        else:
            if scientific_tokens: break

    if scientific_tokens:
        full_name = " ".join(scientific_tokens)
        full_name = re.sub(r'\bet\s+al\.?\b', '', full_name, flags=re.IGNORECASE).strip()
        name_parts = [w.strip(' ".,?') for w in full_name.split() if w.strip(' ".,?') and not w.strip(' ".,?').startswith('(')]
        
        if len(name_parts) > 0:
            # Первое слово — всегда род (даже если это не true_genus)
            # Ищем среди остальных слов то, что написано с маленькой буквы
            for part in name_parts[1:]:
                if part[0].islower() and part.lower() not in ["originally", "vide", "type"]:
                    species_part = part
                    break
        
        if species_part != MISSING_VAL:
            for node in nodes_to_delete: node.decompose()

    # Fallback 1: Кавычки (признак нудума)
    if species_part == MISSING_VAL:
        quoted = re.findall(r'"(.*?)"', raw_text_full)
        if quoted:
            q_parts = [w.strip(' ".,?') for w in quoted[0].split() if w.strip(' ".,?') and not w.strip(' ".,?').startswith('(')]
            if q_parts: 
                species_part = q_parts[-1]
                is_nudum = True # Триггер на статус nudum

    # 2. ИЗОЛЯЦИЯ МЕТАДАННЫХ
    metadata_raw = temp_elem.get_text(separator=" ", strip=True)
    if species_part != MISSING_VAL:
        metadata_raw = metadata_raw.replace(f'"{species_part}"', '')
        metadata_raw = re.sub(r'".*?"', '', metadata_raw)

    # 3. ПОИСК ГОДА И АВТОРА
    year = MISSING_VAL
    author_part_raw = metadata_raw
    years_found = re.findall(r'(\d{4})', metadata_raw)
    if years_found:
        year = years_found[-1]
        pre_year = metadata_raw.rsplit(year, 1)[0]
        author_part_raw = pre_year.rsplit(')', 1)[-1] if ')' in pre_year else pre_year

    if not species_part or species_part == MISSING_VAL: return None
    species_part = species_part.strip(".,? \"")
    if species_part.lower() in ["text", "see", "al", "none", MISSING_VAL]: return None

    author = clean_author_string(author_part_raw, true_genus, species_part)
    # Определяем финальный статус с учетом нудума
    final_status = "nudum" if is_nudum else ("dubious" if is_dubium else "valid")

    return {
        "genus": true_genus, "species": species_part, "author": author, 
        "year": year, "status": final_status,
        "is_type": header_says_type or found_type_marker,
        "is_extant": is_extant
    }

def add_species_to_results(all_results, info, clade, age, stage, reports, source_genus): # Добавили аргумент
    """Добавляет или обновляет вид, выбирая самый строгий статус и лучшие метаданные."""
    global total_duplicates_ignored
    if not info or not info['genus'] or not info['species']: return "error"
    
    info['clade'] = clade
    info['age'] = age
    info['stage'] = stage
    info['source_genus'] = source_genus # ЗАФИКСИРОВАЛИ СТРАНИЦУ-ИСТОЧНИК
    
    # Ищем существующую запись
    existing = next((res for res in all_results if res['genus'].lower() == info['genus'].lower() and res['species'].lower() == info['species'].lower()), None)
    
    if not existing:
        all_results.append(info)
        return "added"
    
    # --- ЛОГИКА СЛИЯНИЯ ---
    old_status = existing['status'].lower()
    new_status = info['status'].lower()
    
    # Определяем веса (4 — если статус не в списке)
    w_old = config.STATUS_WEIGHTS.get(old_status, 4)
    w_new = config.STATUS_WEIGHTS.get(new_status, 4)

    # 1. Выбираем ФИНАЛЬНЫЙ СТАТУС (самый строгий/низкий вес)
    final_status = new_status if w_new < w_old else old_status

    # 2. Проверяем, является ли текущий источник ПЕРВИЧНЫМ (своя страница)
    # Мы считаем источник первичным, если статус 'valid' или 'dubious'
    is_new_primary = new_status in ['valid', 'dubious']
    is_old_primary = old_status in ['valid', 'dubious']

    was_updated = False
    
    # Если зашли на основную страницу, а старые данные были из синонимов — ОБНОВЛЯЕМ МЕТАДАННЫЕ
    if is_new_primary and not is_old_primary:
        # Сохраняем строгий статус, но обновляем автора, год, кладу
        info['status'] = final_status 
        existing.update(info)
        was_updated = True
    elif final_status != old_status:
        # Если статус изменился на более строгий (даже если источник не первичный)
        existing['status'] = final_status
        was_updated = True

    if was_updated:
        msg = f"{info['genus']} {info['species']} (final status: {final_status})"
        with data_lock:
            reports['upgrades'].append(msg)
        return "upgraded"
    else:
        with data_lock:
            total_duplicates_ignored += 1
        return "duplicate"

def load_genera_list():
    """Загружает список родов и их исходных статусов."""
    target_path = CUSTOM_LIST_PATH if USE_CUSTOM_LIST else INPUT_CSV
    source_type = "CUSTOM TXT" if USE_CUSTOM_LIST else "MAIN CSV"
    
    if not os.path.exists(target_path):
        logging.error(f"Input file not found: {target_path}")
        return [], source_type, target_path

    genera_info = [] 
    try:
        if USE_CUSTOM_LIST:
            with open(target_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        # Для TXT статус неизвестен
                        genera_info.append({'name': line.strip(), 'status': MISSING_VAL})
        else:
            with open(target_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f, delimiter=';')
                for row in reader:
                    if row.get('genus'):
                        genera_info.append({
                            'name': row['genus'], 
                            'status': str(row.get('status', MISSING_VAL)).lower()
                        })
    except Exception as e:
        logging.error(f"Error reading {target_path}: {e}")
        
    return genera_info, source_type, target_path
    
def check_and_report_historical(element, true_genus, reports):
    """Находит миграции и сразу пишет их в реестр."""
    italic_tags = element.find_all('i')
    if not italic_tags: return

    name_only = " ".join(" ".join([it.get_text(separator=" ", strip=True) for it in italic_tags]).replace('†', '').replace('?', '').split())
    if not name_only: return

    parts = name_only.split()
    if not parts: return
    first_word = parts[0].strip()
    
    if (first_word and first_word[0].isupper() and 
        first_word.lower() != true_genus.lower() and 
        not (len(first_word) <= 2 and first_word.endswith('.'))):
        
        if first_word.lower() not in ["see", "main", "additional", "list"]:
            m_data = {}
            if os.path.exists(MIGRATIONS_FILE):
                try:
                    with open(MIGRATIONS_FILE, 'r', encoding='utf-8') as f:
                        m_data = json.load(f)
                except: pass
            
            if name_only not in m_data:
                m_data[name_only] = true_genus
                with open(MIGRATIONS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(m_data, f, indent=2, ensure_ascii=False)
                
                logging.warning(f"{true_genus}: [MIGRATION FOUND] {name_only} -> {true_genus}")
                reports['hist_notes'].append(f"{name_only} -> {true_genus}")

def extract_synonym_data(element, true_genus, genus_is_extant):
  """Извлекает синонимы, статус, авторов и прямую ссылку на статью таксона."""
  # 1. Забираем ссылку на сам таксон (строго вне <small> с авторами!)
  syn_url = None
  for a in element.find_all('a'):
    if not a.find_parent('small'):
      href = a.get('href', '').strip()
      if href and not href.startswith('#'):
        syn_url = (
            href
            if href.startswith('http')
            else f"https://en.wikipedia.org{href}"
        )
        break

  temp_elem = copy.copy(element)
  for nested in temp_elem.find_all(['ul', 'ol']):
    nested.decompose()
  for tag in temp_elem.find_all(['abbr', 'sup', 'style']):
    tag.decompose()

  raw_full_text = temp_elem.get_text(separator=" ", strip=True)

  # 2. Поиск токенов названия из курсива
  tech_italics = [
      "nomen",
      "nudum",
      "dubium",
      "sic",
      "reject",
      "conserv",
      "originally",
      "et",
      "al",
      "type",
      "vide",
  ]
  scientific_tokens = []
  nodes_to_delete = []

  for it in temp_elem.find_all('i'):
    if it.find_parent('small'):
      continue
    it_txt = (
        it.get_text(separator=" ", strip=True)
        .replace('†', '')
        .replace('?', '')
        .strip()
    )
    words_in_tag = it_txt.lower().split()
    if any(c.isalpha() for c in it_txt) and not any(
        t in words_in_tag for t in tech_italics
    ):
      scientific_tokens.append(it_txt)
      nodes_to_delete.append(it)
    else:
      if scientific_tokens:
        break

  name_text_raw = " ".join(scientific_tokens)

  # Проверка на кавычки
  quoted = re.findall(r'["“](.*?)["”]', raw_full_text)
  is_quoted = bool(quoted)
  if quoted and not name_text_raw:
    name_text_raw = quoted[0]

  if not name_text_raw:
    return None

  for node in nodes_to_delete:
    node.decompose()

  metadata_raw = temp_elem.get_text(separator=" ", strip=True)

  name_parts = [
      w.strip(' ".,?')
      for w in name_text_raw.split()
      if w.strip(' ".,?') and not w.strip(' ".,?').startswith('(')
  ]
  if not name_parts:
    return None

  s_genus = name_parts[0]
  if (len(s_genus) <= 2 and s_genus.endswith('.')) or (
      len(s_genus) == 1 and s_genus.isupper()
  ):
    if true_genus != MISSING_VAL:
      s_genus = true_genus

  s_species = None
  if len(name_parts) > 1:
    for part in name_parts[1:]:
      if part[0].islower():
        s_species = part
        break

  years = re.findall(r'(\d{4})', metadata_raw)
  year = years[-1] if years else MISSING_VAL
  author_raw = metadata_raw
  if year != MISSING_VAL:
    pre_year = metadata_raw.rsplit(year, 1)[0]
    author_raw = pre_year.rsplit(')', 1)[-1] if ')' in pre_year else pre_year

  author_final = clean_author_string(author_raw, s_genus, s_species)
  if author_final.lower() in [
      "synonyms",
      "synonyms of",
      "list",
      "list of synonyms",
      "-",
  ]:
    return None

  status = "synonym"
  if (
      "?" in raw_full_text
      or "possible" in raw_full_text.lower()
      or "dubium" in raw_full_text.lower()
  ):
    status = "possible synonym"
  if is_quoted or any(
      x in raw_full_text.lower() for x in ["nomen nudum", "nudum"]
  ):
    status = "possible nudum" if status == "possible synonym" else "nudum"

  return {
      "genus": s_genus,
      "species": s_species,
      "author": author_final,
      "year": year,
      "status": status,
      "is_type": False,
      "is_extant": genus_is_extant,
      "url": syn_url,  # <--- ССЫЛКА НА ТАКСОН
  }

def fetch_ancestral_taxa(genus_name, session):
    """Считывает древо классификации с поддержкой алиасов (суффиксов)."""
    start_node = getattr(config, "TAXONOMY_START_NODE", "Tetrapoda").lower()
    # Берем суффиксы динамически из config.py для текущего RESEARCH_MODE
    suffixes = config.WIKI_SUFFIXES
    
    for suffix in suffixes:
        url = f"https://en.wikipedia.org/wiki/Template:Taxonomy/{genus_name}{suffix}"
        retries = 2 # Немного уменьшим ретраи для каждого суффикса, чтобы не ждать вечно
        
        for attempt in range(retries):
            try:
                response = session.get(url, timeout=WIKI_TIMEOUT, allow_redirects=True)
                
                if response.status_code == 429: # Rate limit
                    time.sleep(5 * (attempt + 1))
                    continue
                
                if response.status_code == 404:
                    break # Пробуем следующий суффикс
                
                if response.status_code != 200:
                    return None, url # Техническая ошибка
                
                soup = BeautifulSoup(response.text, 'html.parser')
                rows = soup.find_all('tr', class_='taxonrow')
                if not rows: break # Пробуем следующий суффикс
                    
                lineage = []
                recording = False
                for row in rows:
                    tds = row.find_all('td')
                    if len(tds) < 2: continue
                    taxon_type = tds[0].get_text(strip=True).lower()
                    raw_name = tds[1].get_text(strip=True).replace('†', '').strip()
                    clean_name = re.sub(r'\s*\(.*?\)', '', raw_name).strip(" .")
                    if not clean_name: continue
                    
                    if clean_name.lower() == start_node: recording = True
                    if recording:
                        if "genus" in taxon_type or clean_name.lower() == genus_name.lower():
                            break
                        lineage.append(clean_name)
                
                # Если нашли и Tetrapoda (или нужный узел) была в списке
                if recording:
                    return lineage, url
                else:
                    return [], url # Нашли, но за пределами Dinosauromorpha

            except:
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                # Если все попытки для этого суффикса упали - попробуем следующий или вернем None
                break 

    return None, f"https://en.wikipedia.org/wiki/Template:Taxonomy/{genus_name}"

def verify_synonym_scope(s_genus, session):
  """Проверяет, относится ли род синонима к целевой группе (Dinosauromorpha).

  Использует кэш, шаблоны таксономии и инфобокс страницы.
  """
  g_low = s_genus.lower()

  with data_lock:
    if g_low in verified_synonyms_cache:
      return True, None
    if g_low in excluded_synonyms_cache:
      return False, None

  # 1. Проверяем цепочку предков через Template:Taxonomy
  lineage, _ = fetch_ancestral_taxa(s_genus, session)

  # Если шаблон найден и Dinosauromorpha есть в предках
  if lineage is not None and len(lineage) > 0:
    with data_lock:
      verified_synonyms_cache.add(g_low)
    return True, None

  # Если шаблон есть, но Dinosauromorpha в нем НЕТ (кейс Bison -> Mammalia)
  if lineage is not None and len(lineage) == 0:
    with data_lock:
      excluded_synonyms_cache.add(g_low)
    return False, None

  # 2. Если шаблона нет (404), идем на саму страницу рода (Polyonax, Agathaumas)
  syn_page_meta = None
  for suffix in config.WIKI_SUFFIXES:
    try:
      resp = session.get(BASE_WIKI_URL + s_genus + suffix, timeout=8)
      if resp.status_code == 200:
        soup = BeautifulSoup(resp.text, 'html.parser')
        infobox = soup.find('table', class_='infobox biota')
        if infobox:
          # Проверяем классификацию на целевой странице
          t_gen, clade, age, stage, _, _, extant = extract_classification(
              infobox
          )
          # Если семейство уже в нашем кэше динозавров
          with taxon_lock:
            is_known_dino = clade in taxon_cache

          if is_known_dino or (clade != MISSING_VAL and 'incertae' not in clade):
            with data_lock:
              verified_synonyms_cache.add(g_low)
            syn_page_meta = {
                'clade': clade,
                'age': age,
                'stage': stage,
                'is_extant': extant,
            }
            return True, syn_page_meta
        break
    except:
      pass

  # Если не подтвердили принадлежность — бракуем
  with data_lock:
    excluded_synonyms_cache.add(g_low)
  return False, None

def is_synonym_in_scope(s_genus, syn_url, true_genus, session, audit_buffer):
  """Фильтр-шлагбаум: проверяет только одно — принадлежит ли род к нашей группе.

  Возвращает True (свой, пропускаем) или False (чужак, блокируем).
  """
  s_genus_low = s_genus.lower()

  if "synonym_scope_cache" not in globals():
    global synonym_scope_cache
    synonym_scope_cache = {}

  with data_lock:
    if s_genus_low in synonym_scope_cache:
      return synonym_scope_cache[s_genus_low]

  target_url = syn_url or f"{BASE_WIKI_URL}{s_genus}"
  is_in_scope = True

  try:
    resp = session.get(target_url, timeout=8, allow_redirects=True)
    if resp.status_code == 200:
      # Редирект на самого себя (Sterrholophus -> Triceratops) = точно наш
      final_slug = resp.url.rstrip("/").split("/")[-1].lower()
      if final_slug == true_genus.lower():
        with data_lock:
          synonym_scope_cache[s_genus_low] = True
        return True

      syn_soup = BeautifulSoup(resp.text, "html.parser")
      syn_infobox = syn_soup.find("table", class_="infobox biota")
      if syn_infobox:
        _, p_clade, _, _, _, _, _ = extract_classification(syn_infobox)

        # 1. Если семейство уже в нашем кэше динозавров
        with taxon_lock:
          is_known = (
              (p_clade in taxon_cache)
              if (p_clade != MISSING_VAL and "incertae" not in p_clade.lower())
              else False
          )

        if is_known:
          is_in_scope = True
        else:
          # 2. Если семейство незнакомое — проверяем древо в Template:Taxonomy
          lineage, _ = fetch_ancestral_taxa(s_genus, session)
          if lineage is not None and len(lineage) == 0:
            is_in_scope = False  # КЕЙС БИЗОНА: родословная есть, но Dinosauromorpha нет!
          elif lineage is not None and len(lineage) > 0:
            is_in_scope = True
  except:
    is_in_scope = True  # Если сеть подвела, не рубим сгоряча

  with data_lock:
    synonym_scope_cache[s_genus_low] = is_in_scope
  return is_in_scope


def fetch_genus_page(genus, session, reports):
    """Поиск и скачивание страницы рода с умной обработкой редиректов и суффиксов."""
    global total_bytes_downloaded
    
    infobox = None
    redirected_to_other = False
    target_genus_name = ""
    
    for suffix in config.WIKI_SUFFIXES:
        success = False
        retries = 0
        while retries < 3:
            try:
                response = session.get(BASE_WIKI_URL + genus + suffix, timeout=10)
                with data_lock: 
                    total_bytes_downloaded += len(response.content)
                    
                if response.status_code == 429:
                    retries += 1
                    time.sleep(15)
                    continue
                    
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    h1_tag = soup.find('h1', id='firstHeading')
                    
                    if h1_tag:
                        actual_title = h1_tag.get_text(strip=True).replace('†', '').strip()
                        clean_title = re.sub(r'\s*\(.*?\)', '', actual_title).lower()
                        clean_input = re.sub(r'\s*\(.*?\)', '', genus).lower()
                        title_first_word = clean_title.split()[0]

                        # Ищем инфобокс СРАЗУ
                        current_infobox = soup.find('table', class_='infobox biota')

                        # Если названия не совпадают (нас редиректнуло)
                        if title_first_word != clean_input:
                            # Если на чужой странице ЕСТЬ инфобокс — это научный редирект (сдаемся)
                            if current_infobox:
                                redirected_to_other = True
                                target_genus_name = actual_title
                                success = True
                                break
                            # Если инфобокса НЕТ (как Triple-headed eagle) — игнорируем этот редирект и пробуем следующий суффикс!
                            else:
                                success = True
                                break
                        
                        # Если названия совпадают — это наша страница
                        if actual_title.lower() != genus.lower() or suffix != "":
                            display_name = actual_title if actual_title.lower() != genus.lower() else f"{genus}{suffix}"
                            logging.warning(f"{genus}: ALIAS (Found as {display_name})")
                            with data_lock:
                                reports['found_as'].append(f"{genus} -> {display_name}")
                                
                        if current_infobox:
                            infobox = current_infobox
                            success = True
                            break
                    
                    # Инфобокса нет, идем к следующему суффиксу
                    success = True
                    break
                    
                elif response.status_code == 404:
                    success = True
                    break
                else:
                    success = True
                    break
            except:
                retries += 1
                time.sleep(2)
                
        # Выходим из цикла суффиксов ТОЛЬКО если нашли инфобокс или нас перекинуло на другой реальный таксон
        if success and (infobox or redirected_to_other): 
            break
            
    if redirected_to_other:
        logging.warning(f"{genus}: SKIP (Redirected to {target_genus_name})")
        with data_lock: 
            reports['redirects'].append(f"{genus} -> {target_genus_name}")
        return None
        
    return infobox


def resolve_genus_taxonomy(genus, clade, session, audit_buffer, reports):
  """Проверка и построение таксономии рода через кэш и шаблоны."""
  if clade == MISSING_VAL:
    logging.error(f"{genus}: [TAXONOMY] REJECTED (No clade/family in infobox)")
    with data_lock:
      reports['out_of_class'].append(f"{genus} (No classification data)")
    return None

  is_incertae = 'incertae' in clade.lower()
  with taxon_lock:
    cached = taxon_cache.get(clade) if not is_incertae else None

  if cached:
    audit_buffer.append(
        f"{genus}: [TAXONOMY] Shared with '{clade}' (Data reused from"
        f" '{cached['source']}')"
    )
    return clade

  lineage, taxo_url = fetch_ancestral_taxa(genus, session)
  if lineage is None:
    logging.info(f"{genus}: ERROR (Could not fetch tree from {taxo_url})")
    with data_lock:
      reports['taxonomy_errors'].append(f"{genus}: could not fetch tree")
    return clade

  if len(lineage) == 0:
    start_cap = getattr(
        config, 'TAXONOMY_START_NODE', 'Dinosauromorpha'
    ).capitalize()
    logging.error(f"{genus}: ERROR (Taxonomy out of scope: {start_cap} not found)")
    with data_lock:
      reports['out_of_class'].append(f"{genus} (Out of scope)")
    return None

  if is_incertae:
    for node in reversed(lineage):
      if 'incertae' not in node.lower():
        audit_buffer.append(
            f"{genus}: [TAXONOMY] 'incertae sedis' replaced by parent clade:"
            f" '{node}'"
        )
        clade = node
        break

  with taxon_lock:
    current_path = []
    for node in lineage:
      current_path.append(node)
      if node not in taxon_cache:
        taxon_cache[node] = {'source': genus, 'path': list(current_path)}
    if clade not in taxon_cache:
      taxon_cache[clade] = {'source': genus, 'path': lineage}

  audit_buffer.append(
      f"{genus}: [TAXONOMY] New branch found. Fetched from: {taxo_url}"
  )
  return clade

def extract_infobox_items(data_td):
  """Универсальный разделитель видов в инфобоксе.

  - Если есть <li> — отдает их (100% совместимость со старым кодом).
  - Если 1 вид без списка — отдает [data_td] (100% совместимость).
  - Если несколько видов в одном <p> через <br/> (кейс Shastasaurus) —
  безопасно нарезает на отдельные элементы вместе с крестиком † и автором.
  """
  li_elements = data_td.find_all('li')
  if li_elements:
    return li_elements

  # Ищем все видовые курсивы вне <small>
  primary_i = [
      it for it in data_td.find_all('i') if not it.find_parent('small')
  ]
  if len(primary_i) <= 1:
    return [data_td]

  items = []
  for k, it in enumerate(primary_i):
    item_soup = BeautifulSoup('<div></div>', 'html.parser')
    container = item_soup.div

    # Находим базовый узел строки (на случай если <i> обернут в <b> или <span>)
    base_node = it
    while (
        base_node.parent
        and base_node.parent != data_td
        and base_node.parent.name not in ['p', 'div', 'td']
    ):
      base_node = base_node.parent

    # Захватываем крестик вымирания † перед названием, если он есть
    prev_node = base_node.find_previous_sibling()
    if prev_node and (
        '†' in prev_node.get_text() or prev_node.name in ['abbr', 'span']
    ):
      container.append(copy.copy(prev_node))

    # Добавляем само название
    container.append(copy.copy(base_node))

    # Собираем автора, год и сноски до следующего видового <i>
    next_it = primary_i[k + 1] if k + 1 < len(primary_i) else None
    curr = base_node.find_next_sibling()

    while curr:
      # Если дошли до следующего вида — останавливаемся
      if next_it and (
          curr == next_it
          or (hasattr(curr, 'find_all') and next_it in curr.find_all('i'))
      ):
        break
      container.append(copy.copy(curr))
      curr = curr.find_next_sibling()

    items.append(container)

  return items

def parse_main_section(
    rows,
    true_genus,
    genus_is_extant,
    g_auth_raw,
    g_year,
    clade,
    age,
    stage,
    seen_species,
    all_results,
    reports,
    audit_buffer,
):
  """Парсинг видов основного раздела."""
  main_count = 0
  type_species_ref = ''

  for row in rows:
    text = row.get_text().lower()
    if 'type species' in text or 'binomial name' in text:
      target = row.find_next_sibling('tr') if 'type species' in text else row
      if target and target.find('td'):
        parts = (
            target.find('td')
            .get_text(separator=' ', strip=True)
            .replace('†', '')
            .replace('?', '')
            .split()
        )
        if len(parts) >= 2:
          type_species_ref = parts[1].lower()
        elif len(parts) == 1:
          type_species_ref = parts[0].lower()
      if type_species_ref:
        break

  for j, row in enumerate(rows):
    header = row.find('th')
    if not header:
      continue
    h_text = header.get_text(strip=True).lower()
    if any(
        h in h_text
        for h in [
            'type species',
            'other species',
            'species',
            'binomial name',
        ]
    ):
      data_td = row.find('td') or (
          rows[j + 1].find('td') if j + 1 < len(rows) else None
      )
      if not data_td:
        continue

      items = extract_infobox_items(data_td)
      for item in items:
        is_type_header = 'type' in h_text or 'binomial' in h_text
        info = extract_data(item, true_genus, is_type_header, genus_is_extant)
        if not info:
          continue

        s_low = info['species'].lower()
        if s_low in seen_species:
          continue
        seen_species.add(s_low)

        is_candidate = (
            info['is_type']
            or s_low == type_species_ref
            or (h_text == 'species' and len(items) == 1)
        )
        meta_note = ''
        if is_candidate:
          if info['author'] == MISSING_VAL and g_auth_raw != MISSING_VAL:
            info['author'] = clean_author_string(g_auth_raw, true_genus)
            meta_note = '(metadata from genus)'
          if info['year'] == MISSING_VAL and g_year != MISSING_VAL:
            info['year'] = g_year
            meta_note = '(metadata from genus)'

        res = add_species_to_results(
            all_results, info, clade, age, stage, reports, true_genus
        )
        if res in ['added', 'upgraded']:
          main_count += 1
          with data_lock:
            current_session_facts[
                f"{info['genus']}:MAIN:{info['species']}"
            ] = (
                f"{info['status']} | {info['is_type']} | {info['author']} |"
                f" {info['year']}"
            )
          upg = '(upgraded metadata)' if res == 'upgraded' else ''
          audit_buffer.append({
              'type': 'MAIN',
              'genus': info['genus'],
              'species': info['species'],
              'status': info['status'],
              'is_type': info['is_type'],
              'is_extant': info['is_extant'],
              'author': info['author'],
              'year': info['year'],
              'upgrade_note': upg,
              'meta_note': meta_note,
          })
        else:
          with data_lock:
            reports['duplicates'].append(
                f"{info['genus']} {info['species']} (main repeat)"
            )
  return main_count


def parse_synonyms_section(
    rows,
    true_genus,
    genus_is_extant,
    clade,
    age,
    stage,
    seen_species,
    all_results,
    reports,
    audit_buffer,
    session,
):
  """Простой и надежный парсинг синонимов с отсевом чужаков (Бизона)."""
  syn_count = 0
  for j, row in enumerate(rows):
    header = row.find("th")
    if not (header and "synonyms" in header.get_text().lower()):
      continue
    data_td = rows[j + 1].find("td") if j + 1 < len(rows) else None
    if not data_td:
      continue

    li_items = data_td.find_all("li")
    genus_syn_links = {}

    for li in li_items:
      s_info = extract_synonym_data(li, true_genus, genus_is_extant)
      if not s_info:
        continue

      s_gen, s_sp = s_info["genus"], s_info["species"]
      s_gen_low = s_gen.lower()

      # Запоминаем ссылку на род, если она была в блоке родов
      if s_info.get("url") and s_gen:
        genus_syn_links[s_gen_low] = s_info["url"]

      # Если в строке нет вида (просто имя рода) — пропускаем
      if not s_sp or s_sp == MISSING_VAL:
        audit_buffer.append(
            f"{true_genus}: [SYNONYM] {s_gen} | - | {s_info['status']} |"
            f" {s_info['author']} | {s_info['year']} (ignored, no species"
            " provided)"
        )
        continue

      # Проверка на повтор эпитета внутри страницы
      if s_sp.lower() in seen_species:
        audit_buffer.append(
            f"{true_genus}: [SYNONYM] {s_gen} | {s_sp} | {s_info['status']} |"
            f" {s_info['author']} | {s_info['year']} (ignored, species epithet"
            " already processed)"
        )
        with data_lock:
          reports["duplicates"].append(f"{s_gen} {s_sp} (epithet repeat)")
        continue

      # [!] ПРОВЕРКА ЧУЖАКА (Шлагбаум для Бизона)
      if s_gen_low != true_genus.lower():
        target_url = s_info.get("url") or genus_syn_links.get(s_gen_low)
        in_scope = is_synonym_in_scope(
            s_gen, target_url, true_genus, session, audit_buffer
        )

        if not in_scope:
          # ЧУЖАК (БИЗОН) — ВЫБРАСЫВАЕМ И ПИШЕМ WARNING!
          warn_msg = (
              f"{true_genus}: [SYNONYM EXCLUDED] {s_gen} | {s_sp} (Out of"
              f" scope: {config.TAXONOMY_START_NODE} not found)"
          )
          audit_buffer.append(warn_msg)
          logging.warning(warn_msg)  # <--- СРАЗУ В ЛОГ КАК WARNING
          with data_lock:
            reports["out_of_class"].append(
                f"{true_genus}: [SYNONYM EXCLUDED] {s_gen} | {s_sp}"
            )
          continue

      # СВОЙ ДИНОЗАВР — сохраняем в базу!
      res = add_species_to_results(
          all_results, s_info, clade, age, stage, reports, true_genus
      )
      if res in ["added", "upgraded"]:
        syn_count += 1
        seen_species.add(s_sp.lower())
        with data_lock:
          current_session_facts[f"{s_info['genus']}:SYNONYM:{s_sp}"] = (
              f"{s_info['status']} | {s_info['author']} | {s_info['year']}"
          )
        upg = "(status updated)" if res == "upgraded" else ""
        audit_buffer.append({
            "type": "SYNONYM",
            "genus": s_info["genus"],
            "species": s_sp,
            "status": s_info["status"],
            "is_extant": s_info["is_extant"],
            "author": s_info["author"],
            "year": s_info["year"],
            "upgrade_note": upg,
        })
      else:
        audit_buffer.append(
            f"{true_genus}: [SYNONYM] {s_info['genus']} | {s_sp} |"
            f" {s_info['status']} | {s_info['author']} | {s_info['year']}"
            " (ignored, global duplicate)"
        )
        with data_lock:
          reports["duplicates"].append(f"{s_gen} {s_sp} (global duplicate)")

  return syn_count


def flush_genus_logs(
    genus, main_count, syn_count, all_results, audit_buffer, reports
):
  """Форматирование и вывод логов рода с поддержкой BUFFER_LOGS."""
  total = main_count + syn_count
  auto_type = False

  if total == 1:
    with data_lock:
      for res in reversed(all_results):
        if res['genus'].lower() == genus.lower() and not res['is_type']:
          res['is_type'] = True
          auto_type = True
          break

  formatted = []
  for entry in audit_buffer:
    if isinstance(entry, dict):

      def f(v):
        return (
            MISSING_VAL
            if (v is None or v == '' or str(v).lower() == 'unknown')
            else str(v)
        )

      upg = f" {entry.get('upgrade_note', '')}" if entry.get('upgrade_note') else ''
      meta = f" {entry.get('meta_note', '')}" if entry.get('meta_note') else ''

      if entry['type'] == 'MAIN':
        disp_type = True if auto_type else entry['is_type']
        suf = ' (auto-assigned type)' if auto_type else ''
        line = (
            f"{genus}: [MAIN] {f(entry['genus'])} | {f(entry['species'])} |"
            f" {f(entry['status'])} | {f(disp_type)} | {f(entry['is_extant'])}"
            f" | {f(entry['author'])} | {f(entry['year'])}{suf}{upg}{meta}"
        )
      else:
        line = (
            f"{genus}: [SYNONYM] {f(entry['genus'])} | {f(entry['species'])} |"
            f" {f(entry['status'])} | {f(entry['is_extant'])} |"
            f" {f(entry['author'])} | {f(entry['year'])}{upg}"
        )
      formatted.append(line)
    else:
      formatted.append(entry)

  formatted.append(
      f"{genus}: FINISHED (Found {main_count} main, {syn_count} synonyms)"
  )

  if globals().get('BUFFER_LOGS', False):
    with log_lock:
      for line in formatted:
        logging.info(line)
  else:
    for line in formatted:
      logging.info(line)

  if total == 0:
    logging.error(f"{genus}: ERROR (Found 0 species)")
    with data_lock:
      reports['zero_species'].append(f"{genus}: 0 species extracted")


def process_single_genus(genus, initial_status, session, all_results, reports):
  """Компактный дирижер обработки рода (40 строк)."""
  audit_buffer = []

  # 1. Nomen nudum
  if 'nudum' in str(initial_status).lower():
    logging.info(f"{genus}: STUB CREATED (nomen nudum - skipping Wikipedia)")
    stub = {
        'genus': genus,
        'species': MISSING_VAL,
        'author': MISSING_VAL,
        'year': MISSING_VAL,
        'status': 'nudum',
        'is_extant': False,
    }
    add_species_to_results(
        all_results,
        stub,
        MISSING_VAL,
        MISSING_VAL,
        MISSING_VAL,
        reports,
        genus,
    )
    return

  # 2. Скачивание страницы
  infobox = fetch_genus_page(genus, session, reports)
  if not infobox:
    logging.error(f"{genus}: ERROR (No infobox found)")
    with data_lock:
      reports['no_infobox'].append(f"{genus}: No infobox found")
    return

  true_genus, clade, age, stage, g_auth, g_year, extant = (
      extract_classification(infobox)
  )
  audit_buffer.append(f"{genus}: PARSING...")
  age_disp = (
      f"{str(age).strip()} Ma"
      if str(age).strip() not in [MISSING_VAL, '']
      else MISSING_VAL
  )
  audit_buffer.append(f"{genus}: [DATA] {clade} | {age_disp} | {stage}")
  with data_lock:
    current_session_facts[f'{genus}:DATA'] = (
        f'{clade} | {age_disp} | {stage}'
    )

  # 3. Таксономия
  clade = resolve_genus_taxonomy(genus, clade, session, audit_buffer, reports)
  if not clade:
    return

  # 4. Основные виды и синонимы
  rows = infobox.find_all('tr')
  seen_species = set()
  main_count = parse_main_section(
      rows,
      true_genus,
      extant,
      g_auth,
      g_year,
      clade,
      age,
      stage,
      seen_species,
      all_results,
      reports,
      audit_buffer,
  )

  syn_count = 0
  if config.FETCH_SYNONYMS:
    syn_count = parse_synonyms_section(
        rows,
        true_genus,
        extant,
        clade,
        age,
        stage,
        seen_species,
        all_results,
        reports,
        audit_buffer,
        session,
    )

  # 5. Вывод логов
  flush_genus_logs(
      genus, main_count, syn_count, all_results, audit_buffer, reports
  )

def start_mass_parsing():
    global total_bytes_downloaded
    # 1. СТАРТ (Консоль и Лог)
    logging.info("--- SCRIPT START: PARSE_WIKI_DETAILS ---")
    if config:
        logging.info("Configuration loaded successfully")
    else:
        logging.error("Configuration loading failed")
    if config.BRIEF_CONSOLE:
        print("PARSE_WIKI_DETAILS...", end=" ", flush=True)
    else:
        print("Starting script: PARSE_WIKI_DETAILS")
    
    # 2. ЗАГРУЗКА СПИСКА
    genera_to_parse, src_type, src_path = load_genera_list()
    if not genera_to_parse:
        msg = f"No genera to process. Path: {src_path}"
        logging.error(msg)
        print(f"[ERROR] {msg}")
        return

    logging.info(f"Successfully opened input file: {src_path}")
    logging.info(f"Detected {len(genera_to_parse)} genera in {os.path.basename(src_path)}")
    logging.info(f"Started parsing {len(genera_to_parse)} genera.")

    # 3. ИНИЦИАЛИЗАЦИЯ ПЕРЕМЕННЫХ (Важно: до использования в циклах!)
    total = len(genera_to_parse)
    all_results = []
    reports = {
        'hist_notes': [],
        'found_as': [],
        'redirects': [], 
        'zero_species': [],
        'no_infobox': [],
        'duplicates': [],
        'upgrades': [],
        'out_of_class': [],
        'taxonomy_errors': [],
    }
    
    session = requests.Session()
    session.headers.update(HEADERS)

    # 4. НАСТРОЙКА РЕЖИМА (Логирование отдельно)
    if USE_PARALLEL:
        logging.info(f"Mode: PARALLEL (Workers: {MAX_WORKERS})")
        adapter = requests.adapters.HTTPAdapter(pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS)
        session.mount('https://', adapter)
    else:
        logging.info("Mode: SINGLE-THREADED")

    # 5. ИНФОРМАЦИЯ В КОНСОЛЬ
    syn_status = "Enabled" if FETCH_SYNONYMS else "Disabled"
    logging.info(f"Synonyms Parsing: {syn_status}")
    if not config.BRIEF_CONSOLE:
        print(f"Synonyms Parsing: {syn_status}")
        print(f"Source: {os.path.basename(src_path)}")
        print(f"Total genera to process: {total}")

    # 6. ЗАПУСК ПАРСИНГА
    if USE_PARALLEL:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Формируем задачи только для тех, кто не является нудумом (если флаг False)
            tasks = []
            for gen_data in genera_to_parse:
                g_name = gen_data['name']
                g_stat = gen_data['status']
                
                if not config.INCLUDE_NOMINA_NUDA and "nudum" in str(g_stat).lower():
                    logging.info(f"{g_name}: SKIP (nomen nudum excluded by config)")
                    continue
                
                tasks.append((g_name, g_stat))

            total_tasks = len(tasks)
            futures = [executor.submit(process_single_genus, name, stat, session, all_results, reports) for name, stat in tasks]
            
            for i, future in enumerate(futures, 1):
                future.result()
                if not config.BRIEF_CONSOLE:
                    sys.stdout.write(f"\rParsing... [{i}/{total_tasks}]")
                    sys.stdout.flush()
    else:
        for i, gen_data in enumerate(genera_to_parse, 1):
            genus = gen_data['name']
            initial_status = gen_data['status']

            # ЛОГИКА ФЛАГА: Если нудумы запрещены, просто пропускаем
            if not config.INCLUDE_NOMINA_NUDA and "nudum" in str(initial_status).lower():
                logging.info(f"{genus}: SKIP (nomen nudum excluded by config)")
                if not config.BRIEF_CONSOLE:
                    sys.stdout.write(f"\rParsing... [{i}/{total}]")
                    sys.stdout.flush()
                continue

            process_single_genus(genus, initial_status, session, all_results, reports)
            if not config.BRIEF_CONSOLE:
                sys.stdout.write(f"\rParsing... [{i}/{total}]")
                sys.stdout.flush()

    # 7. ЗАВЕРШЕНИЕ
    logging.info("Parsing completed.")
    species_count_msg = f"Total species extracted: {len(all_results)}"
    size_mb = total_bytes_downloaded / (1024 * 1024)
    size_report = f"Total data downloaded: {size_mb:.2f} MB"
    logging.info(species_count_msg)
    logging.info(size_report)

    if not config.BRIEF_CONSOLE:
        print() 
        print("Parsing completed.")
        print(species_count_msg)
        print(size_report)

    save_to_csv(all_results, OUTPUT_FILE)
    save_classification_library(taxon_cache, CLASSIFICATION_FILE)

    # [!] ОТЧЕТ ПО РЕЕСТРУ МИГРАЦИЙ (Выводится всегда)
    msg_mig = f"Migration registry synced: {os.path.abspath(MIGRATIONS_FILE)}"
    logging.info(msg_mig)
    if not config.BRIEF_CONSOLE:
        print(msg_mig)

    # Подсчет общего количества проблемных случаев
    # (0 видов + нет инфобокса + не тетраподы)
    total_suspicious = len(reports['zero_species']) + len(reports['no_infobox']) + len(reports['out_of_class'])
    
    if config.BRIEF_CONSOLE:
        susp_msg = f" | {total_suspicious} suspicious" if total_suspicious > 0 else ""
        print(f"{len(all_results)} species extracted{susp_msg}")
    else:
        if total_suspicious > 0:
            print(f"Suspicious cases found: {total_suspicious}. Check logs for details.")
    
    # ФИНАЛЬНЫЙ ОТЧЕТ В ЛОГИ (Только по процессу сбора)
    logging.info("=== FINAL DATA FETCH REPORT ===")
    final_report_sections = [
        ('SCIENTIFIC MIGRATIONS FOUND', reports['hist_notes']),
        ('FOUND AS ALIASES', reports['found_as']),
        ('REDIRECTS / SKIPPED', reports['redirects']),
        ('OUT OF CLASSIFICATION SCOPE', reports['out_of_class']),
        ('DATA UPGRADES', reports['upgrades']),
        ('DUPLICATES IGNORED', reports['duplicates']),
        ('TAXONOMY FETCH ERRORS', reports['taxonomy_errors']),
        ('ZERO SPECIES FOUND', reports['zero_species']),
        ('NO INFOBOX FOUND', reports['no_infobox'])
    ]
    for idx, (title, items) in enumerate(final_report_sections, 1):
        logging.info(f"[{idx}] {title} ({len(items)})")
        for item in items:
            logging.info(item)

    if not config.BRIEF_CONSOLE:
        print("Script ended: PARSE_WIKI_DETAILS")
    logging.info("--- SCRIPT END: PARSE_WIKI_DETAILS ---")

    # УДАЛИЛИ RETURN: Теперь функция просто завершается

def save_to_csv(all_results, filename):
    keys = ["genus", "species", "status", "is_type", "is_extant", "clade", "stage", "age", "author", "year", "source_genus"]
    try:
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
            # Флаг extrasaction='ignore' заставит скрипт просто игнорировать поля, которых нет в keys
            writer = csv.DictWriter(f, fieldnames=keys, delimiter=';', extrasaction='ignore')
            writer.writeheader()
            for res in all_results:
                clean_row = {k: (" ".join(str(v).split()) if isinstance(v, str) else v) for k, v in res.items()}
                writer.writerow(clean_row)
        
        msg = f"Raw fauna data saved to: {os.path.abspath(filename)}"
        if not config.BRIEF_CONSOLE:
            print(msg)
        logging.info(msg) # Пишем в лог об успехе

    except Exception as e:
        error_msg = f"Could not save to CSV: {e}"
        print(f"[ERROR] {error_msg}")
        logging.error(error_msg) # Пишем в лог об ОШИБКЕ (включая Permission Denied)

def save_classification_library(cache, filename):
    """Сохраняет ВСЕ уникальные клады. Фильтрация subpath отключена для целостности БД."""
    try:
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
            if not cache: return
            
            # Находим максимальную глубину для заголовков
            max_depth = max(len(v['path']) for v in cache.values())
            headers = ["Lowest Unit", "Source Genus"] + [f"Level {i+1}" for i in range(max_depth)]
            writer = csv.writer(f, delimiter=';')
            writer.writerow(headers)
            
            # Пишем ВСЕ записи из кэша без исключения
            for unit, data in sorted(cache.items()):
                path = data['path']
                # Формируем строку: сама клада, откуда узнали, и цепочка предков
                row = [unit, data['source']] + path
                writer.writerow(row)
                    
        # ИСПРАВЛЕННЫЙ ВЫВОД:
        msg = f"Taxonomic tree saved to: {os.path.abspath(filename)}"
        if not config.BRIEF_CONSOLE:
            print(msg)
        logging.info(msg) # Теперь пишется и в лог-файл

    except Exception as e:
        error_msg = f"[ERROR] Could not save taxonomic tree: {e}"
        print(error_msg)
        logging.error(error_msg)

if __name__ == "__main__":
    start_mass_parsing()