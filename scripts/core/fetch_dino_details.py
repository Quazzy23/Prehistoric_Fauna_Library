import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import re
import csv
import copy
import os
import time
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
# Добавляем путь к папке scripts, чтобы увидеть config.py
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.dont_write_bytecode = True
import config

# --- ПУТИ И НАСТРОЙКИ ---
USE_CUSTOM_LIST = config.USE_CUSTOM_LIST
CUSTOM_LIST_NAME = config.CUSTOM_LIST_NAME

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
INPUT_CSV = os.path.join(BASE_DIR, "data", "exports", "genera_list.csv")
CUSTOM_LIST_PATH = os.path.join(BASE_DIR, "data", "custom_lists", config.CUSTOM_LIST_NAME)
OUTPUT_FILE = os.path.join(BASE_DIR, "data", "exports", "dinosaurs_data.csv")
CLASSIFICATION_FILE = os.path.join(BASE_DIR, "data", "exports", "classification_library.csv")
MIGRATION_MAP_FILE = os.path.join(BASE_DIR, "data", "exports", "migration_map.csv")

taxon_cache = {} # Кэш для хранения древа классификации
lowest_units_seen = {} # НОВОЕ: только минимальные клады { "Thecodontosauridae": "Thecodontosaurus" }
taxon_lock = threading.Lock()

LOG_DIR = os.path.join(BASE_DIR, "data", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "fetch_details.log")

USE_PARALLEL = config.USE_PARALLEL
MAX_WORKERS = config.MAX_WORKERS

# Замок для безопасной записи данных из разных потоков
data_lock = threading.Lock()

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
USER_EMAIL = config.USER_EMAIL
HEADERS = {'User-Agent': f'PrehistoricFaunaLibrary/1.0 (mailto:{USER_EMAIL})'}
EXCLUDE_UNCERTAIN_STAGES = config.EXCLUDE_UNCERTAIN_STAGES
FETCH_SYNONYMS = config.FETCH_SYNONYMS

MISSING_VAL = "-"
WIKI_TIMEOUT = 5  # Время ожидания ответа от Википедии (в секундах)

# Глобальный счетчик байт
total_bytes_downloaded = 0
total_duplicates_ignored = 0 # <--- Добавить

def extract_classification(infobox):
    # Находит Род, Кладу, Ma и Ярус (с поддержкой диапазонов)
    true_genus, clade, age, stage = MISSING_VAL, MISSING_VAL, MISSING_VAL, MISSING_VAL
    g_auth_raw, g_year = MISSING_VAL, MISSING_VAL
    
    # 1. Temporal Range (Исправлено для "80.5 to 72 Ma")
    temp_div = infobox.find(lambda tag: tag.name == "div" and "Temporal range" in tag.get_text())
    if temp_div:
        temp_copy = copy.copy(temp_div)
        for noise in temp_copy.find_all(['div', 'style'], id='Timeline-row'): noise.decompose()
        for noise in temp_copy.find_all('sup'): noise.decompose()
        text = temp_copy.get_text(separator=" ", strip=True).replace("Temporal range:", "")
        if EXCLUDE_UNCERTAIN_STAGES:
            if "Possible" in text: text = text.split("Possible")[0].strip(" ,()")
        
        # Регулярка теперь понимает "to"
        ma_match = re.search(r'(\d+\.?\d*)\s*(?:to|[–\-\—])?\s*(\d*\.?\d*)\s*Ma', text)
        if ma_match:
            g1, g2 = ma_match.group(1), ma_match.group(2)
            age = f"{g1}–{g2}" if g2 else g1

        ians = re.findall(r'\b[A-Z][a-z]+ian\b', text)
        if ians: stage = f"{ians[0]}-{ians[-1]}" if len(ians) >= 2 else ians[0]
        else:
            period_match = re.search(r'\b((?:Early|Middle|Late|Upper|Lower)\s+)?(Cretaceous|Jurassic|Triassic|Permian)\b', text)
            if period_match: stage = period_match.group(0)

    # 2. Ищем Род и метаданные Рода
    rows = infobox.find_all('tr')
    for i, row in enumerate(rows):
        tds = row.find_all('td')
        if len(tds) == 2 and "Genus:" in tds[0].get_text():
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
            
    return true_genus, clade, age, stage, g_auth_raw, g_year

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

def extract_data(element, true_genus, header_says_type):
    """Извлекает данные о виде. Игнорирует курсив внутри тегов <small>."""
    temp_elem = copy.copy(element)
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
    if species_part.lower() in [true_genus.lower(), "text", "see", "al", "none", MISSING_VAL]: return None

    author = clean_author_string(author_part_raw, true_genus, species_part)
    # Определяем финальный статус с учетом нудума
    final_status = "nudum" if is_nudum else ("dubious" if is_dubium else "valid")

    return {
        "genus": true_genus, "species": species_part, "author": author, 
        "year": year, "status": final_status,
        "is_type": header_says_type or found_type_marker
    }

def add_species_to_results(all_results, info, clade, age, stage, reports):
    """Добавляет или обновляет вид, выбирая самый строгий статус и лучшие метаданные."""
    global total_duplicates_ignored
    if not info or not info['genus'] or not info['species']: return "error"
    
    info['clade'] = clade
    info['age'] = age
    info['stage'] = stage
    
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
    """Проверка на Historical Note: гарантирует ровно один пробел между словами."""
    italic_tags = element.find_all('i')
    if not italic_tags: 
        return

    # 1. Собираем весь текст из всех курсивов через пробел
    raw_combined = " ".join([it.get_text(separator=" ", strip=True) for it in italic_tags])
    
    # 2. Чистим от крестов и вопросов
    raw_combined = raw_combined.replace('†', '').replace('?', '').strip()
    
    # 3. МАГИЯ ПРОБЕЛОВ: .split() разбивает строку по ЛЮБОМУ кол-ву пробелов, 
    # а " ".join() склеивает их обратно строго через один пробел.
    name_only = " ".join(raw_combined.split())
    
    if not name_only: 
        return

    parts = name_only.split()
    if not parts: 
        return

    first_word = parts[0].strip()
    
    # Проверка на заглавную букву и не-наш-род
    if (first_word and first_word[0].isupper() and 
        first_word.lower() != true_genus.lower() and 
        not (len(first_word) <= 2 and first_word.endswith('.'))):
        
        if first_word.lower() not in ["see", "main", "additional", "list"]:
            # 1. Текстовый отчет (для итогового списка в конце лога)
            reports['hist_notes'].append(f"{true_genus} -> {name_only}")
            
            # 2. Подробный лог в файл (в процессе работы)
            log_msg = f"{true_genus}: [HISTORICAL NOTE] Found synonym link: {name_only}"
            logging.warning(log_msg)

            # 3. Добавление в карту миграции
            if len(parts) >= 2:
                old_species = parts[1].strip('.,? ')
                if old_species and old_species[0].islower():
                    migration_entry = {
                        'old_genus': first_word,
                        'old_species': old_species,
                        'new_genus': true_genus,
                        'new_species': old_species
                    }
                    with data_lock:
                        reports['migrations'].append(migration_entry)
                    # НОВОЕ: Лог о том, что миграция зафиксирована
                    logging.info(f"{true_genus}: [MIGRATION MAPPED] {first_word} {old_species} -> {true_genus}")

def extract_synonym_data(element, true_genus):
    """Извлекает синонимы. Исправлено сохранение авторов (small) и вложенные списки."""
    # 0. Подготовка
    temp_elem = copy.copy(element)
    # Отрезаем только вложенные списки, чтобы не ловить чужие имена (кейс Suchosaurus)
    for nested in temp_elem.find_all(['ul', 'ol']):
        nested.decompose()
        
    # Чистим технические теги (но НЕ чистим small, там автор!)
    for tag in temp_elem.find_all(['abbr', 'sup', 'style']): 
        tag.decompose()
        
    raw_full_text = temp_elem.get_text(separator=" ", strip=True)

    # 1. ПОИСК НАЗВАНИЯ (Токены из курсива)
    tech_italics = ["nomen", "nudum", "dubium", "sic", "reject", "conserv", "originally", "et", "al", "type", "vide"]
    scientific_tokens = []
    nodes_to_delete = []
    is_quoted_species = False
    
    for it in temp_elem.find_all('i'):
        # Пропускаем курсив, если он внутри small (это пометки типа et al. или sic)
        if it.find_parent('small'): continue
            
        it_txt = it.get_text(separator=" ", strip=True).replace('†', '').replace('?', '').strip()
        words_in_tag = it_txt.lower().split()
        
        if any(c.isalpha() for c in it_txt) and not any(t in words_in_tag for t in tech_italics):
            scientific_tokens.append(it_txt)
            nodes_to_delete.append(it)
        else:
            # Название заканчивается, если встретили технический курсив вне small
            if scientific_tokens: break

    name_text_raw = " ".join(scientific_tokens)

    # ПРОВЕРКА НА КОВЫЧКИ (Кейс "sternbergi")
    remaining_text_for_quotes = temp_elem.get_text(separator=" ", strip=True)
    quoted = re.findall(r'"(.*?)"', remaining_text_for_quotes)
    if quoted:
        name_text_raw += f" {quoted[0]}"
        is_quoted_species = True

    if not name_text_raw: return None

    # 2. ИЗОЛЯЦИЯ МЕТАДАННЫХ (Удаляем только само название)
    for node in nodes_to_delete: 
        node.decompose()
    
    # Теперь в metadata_raw останется и текст, и содержимое <small> (авторы)
    metadata_raw = temp_elem.get_text(separator=" ", strip=True)

    # 3. РАЗБОР НА РОД И ВИД
    name_parts = [w.strip(' ".,?') for w in name_text_raw.split() if w.strip(' ".,?') and not w.strip(' ".,?').startswith('(')]
    if not name_parts: return None
    
    s_genus = name_parts[0]
    if (len(s_genus) <= 2 and s_genus.endswith('.')) or (len(s_genus) == 1 and s_genus.isupper()):
        if true_genus != MISSING_VAL: s_genus = true_genus
    
    s_species = None
    if len(name_parts) > 1:
        for part in name_parts[1:]:
            if part[0].islower(): # Вид всегда со строчной
                s_species = part
                break

    # 4. ПОИСК ГОДА И АВТОРА
    years = re.findall(r'(\d{4})', metadata_raw)
    year = years[-1] if years else MISSING_VAL
    
    author_raw = metadata_raw
    if year != MISSING_VAL:
        pre_year = metadata_raw.rsplit(year, 1)[0]
        author_raw = pre_year.rsplit(')', 1)[-1] if ')' in pre_year else pre_year
    
    author_final = clean_author_string(author_raw, s_genus, s_species)
    # Проверка на служебные заголовки (кейс Suchosaurus "Synonyms of...")
    if author_final.lower() in ["synonyms", "synonyms of", "list", "list of synonyms", "-"]:
        return None

    # 5. СТАТУС
    status = "synonym"
    if "?" in raw_full_text or "possible" in raw_full_text.lower() or "dubium" in raw_full_text.lower():
        status = "possible synonym"
    if is_quoted_species or any(x in raw_full_text.lower() for x in ["nomen nudum", "nudum"]):
        status = "possible nudum" if status == "possible synonym" else "nudum"

    return {
        "genus": s_genus, 
        "species": s_species,
        "author": author_final, 
        "year": year, 
        "status": status, 
        "is_type": False
    }

def fetch_ancestral_taxa(genus_name, session):
    """Считывает древо классификации с поддержкой алиасов (суффиксов)."""
    start_node = getattr(config, 'TAXONOMY_START_NODE', 'Tetrapoda').lower()
    # Список суффиксов для проверки (такой же, как для основных страниц)
    suffixes = ["", "_(dinosaur)", "_(reptile)", "_(archosaur)"]
    
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

def process_single_genus(genus, initial_status, session, all_results, reports):
    """Обработка одного рода с буферизацией логов для умного присвоения типов."""
    global total_bytes_downloaded
    
    infobox = None
    redirected_to_other = False
    target_genus_name = ""
    true_genus, clade, age, stage = MISSING_VAL, MISSING_VAL, MISSING_VAL, MISSING_VAL
    
    # Список для временного хранения логов по текущему роду
    audit_buffer = []
    # Список для контроля уникальности видов на ОДНОЙ странице
    seen_species_on_page = set()
    # --- ЛОГИКА СТЕРИЛИЗАЦИИ НУДУМОВ ---
    if "nudum" in str(initial_status).lower():
        logging.info(f"{genus}: STUB CREATED (nomen nudum - skipping Wikipedia)")
        info_stub = {
            "genus": genus, "species": MISSING_VAL, "author": MISSING_VAL, 
            "year": MISSING_VAL, "status": "nudum"
        }
        add_species_to_results(all_results, info_stub, MISSING_VAL, MISSING_VAL, MISSING_VAL, reports)
        return

    # --- ПОИСК СТРАНИЦЫ ---
    for suffix in ["", "_(dinosaur)", "_(reptile)", "_(archosaur)"]:
        success = False
        retries = 0
        while retries < 3:
            try:
                response = session.get(BASE_WIKI_URL + genus + suffix, timeout=10)
                with data_lock: total_bytes_downloaded += len(response.content)
                if response.status_code == 429:
                    retries += 1
                    time.sleep(15)
                    continue
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    h1_tag = soup.find('h1', id='firstHeading')
                    if h1_tag:
                        actual_title = h1_tag.get_text(strip=True).replace('†', '').strip()
                        # Очищаем заголовок и ввод от скобок для честного сравнения
                        clean_title = re.sub(r'\s*\(.*?\)', '', actual_title).lower()
                        clean_input = re.sub(r'\s*\(.*?\)', '', genus).lower()
                        
                        # ПЕРВОЕ СЛОВО заголовка (на случай если там "Yi (dinosaur)")
                        title_first_word = clean_title.split()[0]

                        # Если это ДРУГОЙ род (напр. запрос Yi -> заголовок Nicolia)
                        if title_first_word != clean_input:
                            redirected_to_other = True
                            target_genus_name = actual_title
                            success = True
                            break
                        
                        # Если это ТОТ ЖЕ род, но найден через суффикс (напр. Yi -> Yi (dinosaur))
                        if actual_title.lower() != genus.lower() or suffix != "":
                            display_name = actual_title if actual_title.lower() != genus.lower() else f"{genus}{suffix}"
                            logging.warning(f"{genus}: ALIAS (Found as {display_name})")
                            with data_lock:
                                reports['found_as'].append(f"{genus} -> {display_name}")
                    infobox = soup.find('table', class_='infobox biota')
                    if infobox:
                        true_genus, clade, age, stage, g_auth_raw, g_year = extract_classification(infobox)
                        success = True
                        break
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
        if success and (infobox or redirected_to_other): break
    
    if redirected_to_other:
        logging.warning(f"{genus}: SKIP (Redirected to {target_genus_name})")
        with data_lock: reports['redirects'].append(f"{genus} -> {target_genus_name}")
        return

    if not infobox:
        logging.error(f"{genus}: ERROR (No infobox found)")
        with data_lock: reports['no_infobox'].append(f"{genus}: No infobox found")
        return

    # --- НАЧАЛО ОТЧЕТА ПО РОДУ ---
    logging.info(f"{genus}: PARSING...")
    
    # Сразу выводим данные о времени и кладе (напрямую в лог)
    age_clean = str(age).strip()
    age_display = f"{age_clean} Ma" if age_clean not in [MISSING_VAL, ""] else MISSING_VAL
    logging.info(f"{genus}: [DATA] {clade} | {age_display} | {stage}")

    # --- ЛОГИКА ПОЛНОЙ КЛАССИФИКАЦИИ ---
    if clade != MISSING_VAL:
        # ПРОВЕРКА: не кэшируем incertae sedis, так как это статус, а не уникальный таксон
        is_incertae_status = "incertae" in clade.lower()
        
        with taxon_lock:
            cached_data = taxon_cache.get(clade) if not is_incertae_status else None
        
        if cached_data:
            source_genus = cached_data['source']
            logging.info(f"{genus}: [TAXONOMY] Shared with '{clade}' (Data reused from '{source_genus}')")
        else:
            lineage, taxo_url = fetch_ancestral_taxa(genus, session)
            
            if lineage is not None:
                # ПРОВЕРКА НА АЛИАС ТАКСОНОМИИ
                base_taxo_url = f"https://en.wikipedia.org/wiki/Template:Taxonomy/{genus}"
                if taxo_url != base_taxo_url:
                    logging.warning(f"{genus} [TAXONOMY] Alias (found on {taxo_url})")

                if len(lineage) > 0: # Сценарий 1: Успех
                    # --- НОВОЕ: ЛОГИКА ТАКСОНОМИЧЕСКОГО ПРЫЖКА ДЛЯ INCERTAE SEDIS ---
                    if is_incertae_status:
                        # Ищем в цепочке lineage последнего нормального предка (не incertae)
                        refined_clade = MISSING_VAL
                        for node in reversed(lineage):
                            if "incertae" not in node.lower():
                                refined_clade = node
                                break
                        
                        if refined_clade != MISSING_VAL:
                            logging.info(f"{genus}: [TAXONOMY] 'incertae sedis' replaced by parent clade: '{refined_clade}'")
                            clade = refined_clade # Заменяем "incertae" на реальную группу в CSV
                    # ---------------------------------------------------------------

                    with taxon_lock:
                        refinement_node = None
                        for node in reversed(lineage):
                            if node in lowest_units_seen:
                                refinement_node = node
                                break
                        
                        if refinement_node:
                            old_source = lowest_units_seen[refinement_node]
                            logging.warning(f"{genus}: [TAXONOMY] Branch '{refinement_node}' (from {old_source}) refined to '{clade}'")
                        
                        current_path = []
                        for node in lineage:
                            current_path.append(node)
                            if node not in taxon_cache:
                                taxon_cache[node] = {'source': genus, 'path': list(current_path)}
                        
                        if clade not in lowest_units_seen:
                            lowest_units_seen[clade] = genus
                            if clade not in taxon_cache:
                                taxon_cache[clade] = {'source': genus, 'path': lineage}
                    
                    if not refinement_node and not is_incertae_status:
                        logging.info(f"{genus}: [TAXONOMY] New branch found. Fetched from: {taxo_url}")
                
                else: # Сценарий 2: lineage == [] (Вне рамок)
                    start_node_cap = getattr(config, 'TAXONOMY_START_NODE', 'Tetrapoda').capitalize()
                    msg = f"{genus}: ERROR (Taxonomy out of scope: {start_node_cap} not found)"
                    logging.error(msg)
                    with data_lock:
                        reports['out_of_class'].append(f"{genus} (Out of scope)")
                    return 
            
            else: # Сценарий 3: lineage is None (Ошибка сети)
                logging.info(f"{genus}: ERROR (Could not fetch tree from {taxo_url})")
                with data_lock:
                    reports['taxonomy_errors'].append(f"{genus}: could not fetch tree")
    else:
        logging.error(f"{genus}: [TAXONOMY] REJECTED (No clade/family in infobox to verify lineage)")
        with data_lock:
            reports['out_of_class'].append(f"{genus} (No classification data)")
        return # ПРЕРЫВАЕМ, так как не можем подтвердить принадлежность к Dinosauromorpha

    try:
        rows = infobox.find_all('tr')
        main_species_count = 0
        syn_species_count = 0

        # --- ПОИСК ИМЕНИ ТИПОВОГО ВИДА ДЛЯ ПОДСТАНОВКИ (ДЛЯ ЛОГОВ) ---
        type_species_for_ref = ""
        for row in rows:
            text = row.get_text().lower()
            if "type species" in text or "binomial name" in text:
                target = row.find_next_sibling('tr') if "type species" in text else row
                if target:
                    td = target.find('td')
                    if td:
                        clean_t = td.get_text(separator=" ", strip=True).replace('†', '').replace('?', '')
                        t_parts = clean_t.split()
                        if len(t_parts) >= 2: type_species_for_ref = t_parts[1].lower()
                        elif len(t_parts) == 1: type_species_for_ref = t_parts[0].lower()
                if type_species_for_ref: break

        # ШАГ 1: ПАРСИНГ ОСНОВНОГО РАЗДЕЛА
        for j, row in enumerate(rows):
            header = row.find('th')
            if not header: continue
            h_text = header.get_text(strip=True).lower()
            if any(h in h_text for h in ["type species", "other species", "species", "binomial name"]):
                data_td = row.find('td')
                if not data_td and j + 1 < len(rows): data_td = rows[j+1].find('td')
                if data_td:
                    li_items = data_td.find_all('li')
                    items = li_items if li_items else [data_td]
                    for item in items:
                        # 1. Определяем, говорит ли ЗАГОЛОВОК, что это тип
                        is_type_by_header = "type" in h_text or "binomial" in h_text
                        
                        # 2. Вызываем экстрактор, передавая это знание
                        info = extract_data(item, true_genus, is_type_by_header)
                        
                        if info:
                            s_name_low = info['species'].lower()
                            if s_name_low in seen_species_on_page: continue 
                            seen_species_on_page.add(s_name_low)
                            
                            # 3. Итоговое значение is_type берем из того, что вернула функция
                            actual_is_type = info['is_type']
                            
                            # Кандидат на метаданные (для Barrosasaurus)
                            is_type_candidate = actual_is_type or info['species'].lower() == type_species_for_ref or (h_text == "species" and len(items) == 1)

                            # --- ЛОГИКА ПОДСТАНОВКИ ИЗ РОДА (Кейс Barrosasaurus) ---
                            current_meta_note = ""
                            if is_type_candidate:
                                if info['author'] == MISSING_VAL and g_auth_raw != MISSING_VAL:
                                    info['author'] = clean_author_string(g_auth_raw, true_genus)
                                    current_meta_note = "(metadata from genus)"
                                if info['year'] == MISSING_VAL and g_year != MISSING_VAL:
                                    info['year'] = g_year # <--- Теперь год гарантированно подставится
                                    current_meta_note = "(metadata from genus)"

                            # Сохраняем в итоговый словарь
                            info['is_type'] = actual_is_type
                            res_status = add_species_to_results(all_results, info, clade, age, stage, reports)
                            
                            if res_status == "added":
                                main_species_count += 1
                                audit_buffer.append({'type': 'MAIN', 'genus': info['genus'], 'species': info['species'], 'status': info['status'], 'is_type': actual_is_type, 'author': info['author'], 'year': info['year'], 'meta_note': current_meta_note})
                                check_and_report_historical(item, true_genus, reports)
                            elif res_status == "upgraded":
                                main_species_count += 1
                                audit_buffer.append({
                                    'type': 'MAIN', 'genus': info['genus'], 'species': info['species'], 'status': info['status'], 'is_type': actual_is_type, 'author': info['author'], 'year': info['year'],
                                    'upgrade_note': '(upgraded metadata)', 'meta_note': current_meta_note
                                })
                            else:
                                with data_lock: 
                                    reports['duplicates'].append(f"{info['genus']} {info['species']} (main section repeat) (on {genus} page)")

        # ШАГ 2: ПАРСИНГ СИНОНИМОВ
        if FETCH_SYNONYMS:
            for j, row in enumerate(rows):
                header = row.find('th')
                if header and "synonyms" in header.get_text().lower():
                    data_td = rows[j+1].find('td')
                    if data_td:
                        li_items = data_td.find_all('li')
                        if li_items:
                            for li in li_items:
                                s_info = extract_synonym_data(li, true_genus)
                                if s_info:
                                    s_species = s_info['species']
                                    # Проверяем, есть ли реальное имя вида
                                    if s_species and s_species != MISSING_VAL:
                                        # 1. Проверка на дубликат внутри страницы
                                        if s_species.lower() in seen_species_on_page:
                                            audit_buffer.append(f"{genus}: [SYNONYM] {s_info['genus']} | {s_species} | {s_info['status']} | {s_info['author']} | {s_info['year']} (ignored, species epithet already processed)")
                                            with data_lock:
                                                reports['duplicates'].append(f"{s_info['genus']} {s_species} (epithet repeat) (on {genus} page)")
                                            continue
                                            
                                        # 2. Попытка добавить в общую базу
                                        res_status = add_species_to_results(all_results, s_info, clade, age, stage, reports)
                                        
                                        if res_status == "added":
                                            syn_species_count += 1
                                            seen_species_on_page.add(s_species.lower())
                                            audit_buffer.append({
                                                'type': 'SYNONYM', 'genus': s_info['genus'], 'species': s_species,
                                                'status': s_info['status'], 'author': s_info['author'], 'year': s_info['year']
                                            })
                                        elif res_status == "upgraded":
                                            syn_species_count += 1
                                            audit_buffer.append({
                                                'type': 'SYNONYM', 'genus': s_info['genus'], 'species': s_species,
                                                'status': s_info['status'], 'author': s_info['author'], 'year': s_info['year'],
                                                'upgrade_note': '(status updated)'
                                            })
                                        else:
                                            audit_buffer.append(f"{genus}: [SYNONYM] {s_info['genus']} | {s_species} | {s_info['status']} | {s_info['author']} | {s_info['year']} (ignored, global duplicate)")
                                            with data_lock:
                                                reports['duplicates'].append(f"{s_info['genus']} {s_species} (global duplicate) (on {genus} page)")
                                    else:
                                        # ВИДА НЕТ (Родовой синоним) — теперь это условие сработает!
                                        audit_buffer.append(f"{genus}: [SYNONYM] {s_info['genus']} | - | {s_info['status']} | {s_info['author']} | {s_info['year']} (ignored, no species provided)")
                    break 
        else:
            audit_buffer.append(f"{genus}: [SYNONYM] SKIPPED (fetching disabled by flag)")

        # --- ОБРАБОТКА AUTO-ASSIGNED TYPE ---
        total_found = main_species_count + syn_species_count
        auto_type_triggered = False

        if total_found == 1:
            # Ищем этот единственный вид в результатах и ставим ему тип, если его нет
            with data_lock:
                for res in reversed(all_results):
                    if res['genus'].lower() == genus.lower():
                        if not res['is_type']:
                            res['is_type'] = True
                            auto_type_triggered = True
                        break

        for entry in audit_buffer:
            if isinstance(entry, dict):
                def f(v): 
                    if v is None or v == "" or str(v).lower() == "unknown" or str(v) == MISSING_VAL: 
                        return MISSING_VAL
                    return str(v)

                upg = f" {entry.get('upgrade_note', '')}" if entry.get('upgrade_note') else ""
                meta_info = f" {entry.get('meta_note', '')}" if entry.get('meta_note') else ""
                
                if entry['type'] == 'MAIN':
                    display_type = True if auto_type_triggered else entry['is_type']
                    suffix = " (auto-assigned type)" if auto_type_triggered else ""
                    line = f"{genus}: [MAIN] {f(entry['genus'])} | {f(entry['species'])} | {f(entry['status'])} | {f(display_type)} | {f(entry['author'])} | {f(entry['year'])}{suffix}{upg}{meta_info}"
                else:
                    line = f"{genus}: [SYNONYM] {f(entry['genus'])} | {f(entry['species'])} | {f(entry['status'])} | {f(entry['author'])} | {f(entry['year'])}{upg}"
                logging.info(line)
            else:
                logging.info(entry)

        logging.info(f"{genus}: FINISHED (Found {main_species_count} main, {syn_species_count} synonyms)")

        if total_found == 0:
            logging.error(f"{genus}: ERROR (Found 0 species)")
            with data_lock: reports['zero_species'].append(f"{genus}: 0 species extracted")

    except Exception as e:
        logging.error(f"{genus}: ERROR (Parsing failed: {e})")

def start_mass_parsing():
    global total_bytes_downloaded
    # 1. СТАРТ (Консоль и Лог)
    logging.info("--- SCRIPT START: FETCH_DINO_DETAILS ---")
    if config:
        logging.info("Configuration loaded successfully")
    else:
        logging.error("Configuration loading failed")
    print("Starting script: FETCH_DINO_DETAILS")
    
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
        'migrations': [],
        'found_as': [],
        'redirects': [], 
        'zero_species': [],
        'no_infobox': [],
        'duplicates': [],
        'upgrades': [],
        'out_of_class': [],
        'taxonomy_errors': []  # <--- Новый список
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
    print(f"Synonyms Parsing: {syn_status}")
    logging.info(f"Synonyms Parsing: {syn_status}")
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
                sys.stdout.write(f"\rParsing... [{i}/{total_tasks}]")
                sys.stdout.flush()
    else:
        for i, gen_data in enumerate(genera_to_parse, 1):
            genus = gen_data['name']
            initial_status = gen_data['status']

            # ЛОГИКА ФЛАГА: Если нудумы запрещены, просто пропускаем
            if not config.INCLUDE_NOMINA_NUDA and "nudum" in str(initial_status).lower():
                logging.info(f"{genus}: SKIP (nomen nudum excluded by config)")
                sys.stdout.write(f"\rParsing... [{i}/{total}]")
                sys.stdout.flush()
                continue

            process_single_genus(genus, initial_status, session, all_results, reports)
            sys.stdout.write(f"\rParsing... [{i}/{total}]")
            sys.stdout.flush()

    # 7. ЗАВЕРШЕНИЕ
    print() # Просто переносим курсор на новую строку, сохраняя счетчик [N/N]
    print("Parsing completed.")
    logging.info("Parsing completed.")

    species_count_msg = f"Total species extracted: {len(all_results)}"
    print(species_count_msg)
    logging.info(species_count_msg)

    size_mb = total_bytes_downloaded / (1024 * 1024)
    size_report = f"Total data downloaded: {size_mb:.2f} MB"
    print(size_report)
    logging.info(size_report)

    save_to_csv(all_results, OUTPUT_FILE)
    save_classification_library(taxon_cache, CLASSIFICATION_FILE)
    save_migration_map(reports['migrations'], MIGRATION_MAP_FILE)

    # Подсчет общего количества проблемных случаев
    # (0 видов + нет инфобокса + не тетраподы)
    total_suspicious = len(reports['zero_species']) + len(reports['no_infobox']) + len(reports['out_of_class'])
    
    if total_suspicious > 0:
        print(f"Suspicious cases found: {total_suspicious}. Check logs for details.")

    print("Script ended: FETCH_DINO_DETAILS")
    
    # ФИНАЛЬНЫЙ ОТЧЕТ В ЛОГИ
    logging.info("=== FINAL DATA AUDIT REPORT ===")
    final_audit_data = [
        ('HISTORICAL NOTES', reports['hist_notes']),
        ('FOUND AS ALIASES', reports['found_as']),
        ('REDIRECTS / SKIPPED', reports['redirects']),
        ('OUT OF CLASSIFICATION SCOPE', reports['out_of_class']),
        ('DATA UPGRADES (Secondary -> Primary)', reports['upgrades']),
        ('DUPLICATES IGNORED', reports['duplicates']),
        ('TAXONOMY FETCH ERRORS', reports['taxonomy_errors']),
        ('ZERO SPECIES FOUND', reports['zero_species']),
        ('NO INFOBOX FOUND', reports['no_infobox'])
    ]
    for idx, (title, items) in enumerate(final_audit_data, 1):
        logging.info(f"[{idx}] {title} ({len(items)})")
        for item in items:
            logging.info(item)
    logging.info("--- SCRIPT END: FETCH_DINO_DETAILS ---")

def save_to_csv(all_results, filename):
    keys = ["genus", "species", "status", "is_type", "clade", "stage", "age", "author", "year"]
    try:
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
            # Флаг extrasaction='ignore' заставит скрипт просто игнорировать поля, которых нет в keys
            writer = csv.DictWriter(f, fieldnames=keys, delimiter=';', extrasaction='ignore')
            writer.writeheader()
            for res in all_results:
                clean_row = {k: (" ".join(str(v).split()) if isinstance(v, str) else v) for k, v in res.items()}
                writer.writerow(clean_row)
        
        msg = f"Data saved to {os.path.abspath(filename)}"
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
        msg = f"Classification library saved to {os.path.abspath(filename)}"
        print(msg)
        logging.info(msg) # Теперь пишется и в лог-файл

    except Exception as e:
        error_msg = f"[ERROR] Could not save classification: {e}"
        print(error_msg)
        logging.error(error_msg)

def save_migration_map(migrations, filename):
    """Сохраняет связи старых и новых названий для миграции папок."""
    if not migrations: 
        logging.info("Migration Map: No entries to save.")
        return
        
    keys = ['old_genus', 'old_species', 'new_genus', 'new_species']
    try:
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=keys, delimiter=';')
            writer.writeheader()
            
            seen = set()
            count = 0
            for m in migrations:
                # ИСПРАВЛЕНО: создаем строку-идентификатор для проверки дубликатов
                identifier = f"{m['old_genus']}_{m['old_species']}_{m['new_genus']}".lower()
                
                if identifier not in seen:
                    writer.writerow(m)
                    seen.add(identifier)
                    count += 1
                    
        # ИСПРАВЛЕННЫЙ ВЫВОД:
        msg = f"Migration map saved to {os.path.abspath(filename)}"
        print(msg)          # Убрали \n в начале
        logging.info(msg)   # Пишем стандартную строку в лог

    except Exception as e:
        error_msg = f"[ERROR] Failed to save migration map: {e}"
        print(error_msg)
        logging.error(error_msg)

if __name__ == "__main__":
    start_mass_parsing()