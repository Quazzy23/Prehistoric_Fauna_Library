import sys
sys.dont_write_bytecode = True

import os
import re
import csv
import copy
import time
import logging
import threading
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor

# Подтягиваем конфигурацию проекта
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import local_settings

# --- ПУТИ И НАСТРОЙКИ ---
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

DATA_ROOT = os.path.join(BASE_DIR, config.TABLES_DIR)
INPUT_CSV = os.path.join(DATA_ROOT, "genera_list.csv")
OUTPUT_FILE = os.path.join(DATA_ROOT, "raw_fauna.csv")
CLASSIFICATION_FILE = os.path.join(DATA_ROOT, "taxonomic_tree.csv")
GEO_REF_CSV = os.path.join(DATA_ROOT, "geochronology_ref.csv")

CUSTOM_LIST_PATH = os.path.join(BASE_DIR, config.CUSTOM_LISTS_DIR, config.CUSTOM_LIST_NAME)
MIGRATIONS_FILE = os.path.join(BASE_DIR, config.MIGRATIONS_FILE)

# Настройка системного лог-файла в AppData
LOG_FILE = os.path.join(config.LOGS_DIR, "parse_wiki_details.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

# [!] РЕЖИМ ЛОГИРОВАНИЯ
BUFFER_LOGS = True

# Потокобезопасные структуры данных и замки
current_session_facts = {}
data_lock = threading.Lock()
log_lock = threading.Lock()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    filename=LOG_FILE,
    filemode='w',
    encoding='utf-8'
)

BASE_WIKI_URL = config.BASE_WIKI_URL
USER_EMAIL = getattr(local_settings, 'USER_EMAIL', 'researcher@pfl-project.org')
HEADERS = {'User-Agent': f'PrehistoricFaunaLibrary/2.0 (mailto:{USER_EMAIL})'}

USE_PARALLEL = config.USE_PARALLEL
MAX_WORKERS = config.MAX_WORKERS
WIKI_TIMEOUT = 10
MISSING_VAL = "-"

total_bytes_downloaded = 0


# ========================================================================
# [1] КЛАСС ИЗВЛЕЧЕНИЯ ГЕОХРОНОЛОГИИ (TEMPORAL RANGE EXTRACTOR)
# ========================================================================

class TemporalRangeExtractor:
    """
    Автономный экстрактор датировок и ярусов:
    - Загружает канонические ярусы и эпохи из geochronology_ref.csv.
    - Учитывает флаг config.INCLUDE_UNCERTAIN_STAGES.
    - Маскирует скобки и раскрывает связки (Mid-Late).
    - Корректно понимает даты со знаками вопроса (~145? Ma).
    """
    def __init__(self, geo_csv_path, include_uncertain=False):
        self.include_uncertain = include_uncertain
        self.stages_dict, self.epochs_dict = self._load_ics_hierarchy(geo_csv_path)

    def _load_ics_hierarchy(self, csv_path):
        stages = set()
        epochs_and_periods = set()
        
        if not os.path.exists(csv_path):
            return stages, epochs_and_periods

        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f, delimiter=';')
            for row in reader:
                p = row.get('period', '').strip()
                ep = row.get('epoch', '').strip()
                st = row.get('stage', '').strip()
                
                if st and st not in ["-", "Late", "Early", "Middle", "Upper", "Lower"]:
                    stages.add(st)
                    
                if p and p != "-":
                    epochs_and_periods.add(p)
                    for mod in ["Early", "Middle", "Late"]:
                        epochs_and_periods.add(f"{mod} {p}")

                if ep and ep not in ["-", "Late", "Early", "Middle", "Upper", "Lower"]:
                    epochs_and_periods.add(ep)
                    for mod in ["Early", "Middle", "Late"]:
                        epochs_and_periods.add(f"{mod} {ep}")

        epochs_and_periods.update(["Recent", "Present", "Holocene", "Extant", "Ediacaran", "Precambrian"])
        return stages, epochs_and_periods

    def _clean_typography(self, text):
        text = re.sub(r'\s+([,.:;\)\?])', r'\1', text)
        text = re.sub(r'\(\s+', '(', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip(" ,.;:~-–—")

    def _expand_shorthand_ranges(self, text):
        text = re.sub(r'\bMid\b', 'Middle', text, flags=re.IGNORECASE)
        pattern = r'\b(Early|Middle|Late|Upper|Lower)\s*(?:to|[-–—])\s*(Early|Middle|Late|Upper|Lower)\s+([A-Za-z]+)\b'
        def repl(m):
            return f"{m.group(1)} {m.group(3)} - {m.group(2)} {m.group(3)}"
        return re.sub(pattern, repl, text, flags=re.IGNORECASE)

    def _extract_stages_from_text(self, text_segment):
        found = []
        sorted_stages = sorted(list(self.stages_dict), key=len, reverse=True)
        matches_with_pos = []
        for st in sorted_stages:
            pattern = r'\b' + re.escape(st) + r'\b'
            for m in re.finditer(pattern, text_segment, re.IGNORECASE):
                matches_with_pos.append((m.start(), st))
                
        matches_with_pos.sort(key=lambda x: x[0])
        for _, st in matches_with_pos:
            if st not in found:
                found.append(st)

        if len(found) >= 2:
            return f"{found[0]}-{found[-1]}"
        elif len(found) == 1:
            return found[0]
        return None

    def _resolve_single_time_unit(self, text_part):
      # 1. Если есть запятая (например, "Late Cretaceous, Maastrichtian" или "Middle Jurassic, Bathonian")
      if ',' in text_part:
        sub_parts = [p.strip() for p in text_part.split(',') if p.strip()]
        # Ищем точный ярус с конца (где обычно написано уточнение)
        for sp in reversed(sub_parts):
          st = self._extract_stages_from_text(sp)
          if st:
            return st
        for sp in sub_parts:
          ep = self._resolve_single_time_unit(sp)
          if ep and ep != sp:
            return ep

      # 2. Ищем ярусы в тексте
      st_res = self._extract_stages_from_text(text_part)
      if st_res:
        return st_res

      # 3. Ищем эпохи и периоды
      sorted_epochs = sorted(list(self.epochs_dict), key=len, reverse=True)
      for ep in sorted_epochs:
        if re.search(r'\b' + re.escape(ep) + r'\b', text_part, re.IGNORECASE):
          return ep

      return text_part.strip(' ,()~-–—')

    def extract(self, infobox):
      """Извлекает age_ma и stage из HTML инфобокса."""
      if not infobox:
        return MISSING_VAL, MISSING_VAL

      temp_div = infobox.find(
          lambda tag: tag.name == 'div' and 'Temporal range' in tag.get_text()
      )
      if not temp_div:
        return MISSING_VAL, MISSING_VAL

      temp_copy = copy.copy(temp_div)
      for noise in temp_copy.find_all(['div', 'style'], id='Timeline-row'):
        noise.decompose()
      for noise in temp_copy.find_all('sup'):
        noise.decompose()

      raw_text = (
          temp_copy.get_text(separator=' ', strip=True)
          .replace('Temporal range:', '')
          .strip()
      )
      clean_text = self._clean_typography(raw_text)

      # [!] Обработка флага сомнительных ярусов
      if not self.include_uncertain and 'possible' in clean_text.lower():
        clean_text = self._clean_typography(
            re.split(r'possible', clean_text, flags=re.IGNORECASE)[0].strip(
                ' ,()~-–—'
            )
        )

      # 1. Извлечение цифр (Ma) с ГРАНИЦЕЙ СЛОВА \bMa\b
      age_val = MISSING_VAL
      ma_match = re.search(
          r'([~≈]?\s*\d+\.?\d*\s*\??)\s*(?:to|[–\-\—])?\s*([~≈]?\s*\d*\.?\d*\s*\??)\s*\bMa\b',
          clean_text,
          re.IGNORECASE,
      )
      if ma_match:
        g1, g2 = ma_match.group(1).strip(), ma_match.group(2).strip()
        g1 = re.sub(r'([~≈])\s+', r'\1', g1).replace('?', '').strip()
        g2 = re.sub(r'([~≈])\s+', r'\1', g2).replace('?', '').strip()
        age_val = f'{g1}–{g2}' if g2 else f'{g1}'

      # 2. Вырезаем блок Ma ТОЛЬКО если это отдельное слово \bMa\b (защита слова Maastrichtian!)
      text_no_digits = re.sub(
          r'(?:[~≈]\s*)?[\d\.,]+\s*\??\s*(?:to|[–\-\—])?\s*[~≈]?\s*[\d\.,]*\s*\??\s*\bMa\b',
          '',
          clean_text,
          flags=re.IGNORECASE,
      )
      text_no_digits = re.sub(
          r'\b(?:circa|c\.|ca\.|approx\.?)\b',
          '',
          text_no_digits,
          flags=re.IGNORECASE,
      )
      text_no_digits = re.sub(r'[~≈]', '', text_no_digits)
      text_no_digits = self._expand_shorthand_ranges(
          self._clean_typography(text_no_digits)
      )

      # 3. Правило диапазона в скобках
      paren_matches = re.findall(r'\((.*?)\)', text_no_digits)
      for p_content in paren_matches:
        stages_in_paren = self._extract_stages_from_text(p_content)
        if stages_in_paren and '-' in stages_in_paren:
          return age_val, stages_in_paren

      # 4. Маскирование скобок
      parens_tokens = {}

      def mask_paren(match):
        tok = f'__PAREN_TOKEN_{len(parens_tokens)}__'
        parens_tokens[tok] = match.group(0)
        return f' {tok} '

      masked_text = self._clean_typography(
          re.sub(r'\(.*?\)', mask_paren, text_no_digits)
      )
      range_parts = [
          p.strip()
          for p in re.split(
              r'\s+to\s+|\s*[–—]\s*|\s+-\s+', masked_text, flags=re.IGNORECASE
          )
          if p.strip()
      ]

      restored_parts = []
      for p in range_parts:
        for tok, original in parens_tokens.items():
          p = p.replace(tok, original)
        restored_parts.append(self._clean_typography(p))

      # 5. Резолвинг фрагментов
      resolved_units = []
      for part in restored_parts:
        paren_match = re.search(r'\((.*?)\)', part)
        if paren_match:
          paren_text = paren_match.group(1).strip()
          st_p = self._extract_stages_from_text(paren_text)
          if st_p:
            resolved_units.append(st_p)
            continue
          ep_p = self._resolve_single_time_unit(paren_text)
          if (
              ep_p
              and ep_p != paren_text
              and (ep_p in self.epochs_dict or ep_p in self.stages_dict)
          ):
            resolved_units.append(ep_p)
            continue

        unit = self._resolve_single_time_unit(part)
        if unit:
          resolved_units.append(unit)

      # 6. Финальная сборка
      if len(resolved_units) >= 2 and resolved_units[0] != resolved_units[-1]:
        final_stage = f'{resolved_units[0]}-{resolved_units[-1]}'
      elif len(resolved_units) >= 1:
        final_stage = resolved_units[0]
      else:
        final_stage = MISSING_VAL

      return age_val, final_stage


# ========================================================================
# [2] КЛАСС УПРАВЛЕНИЯ ТАКСОНОМИЕЙ (TAXONOMY ENGINE)
# ========================================================================

class TaxonomyEngine:
    """
    Автономный движок филогенетической классификации:
    - Хранит кэш деревьев таксономии (taxon_cache).
    - Выполняет запросы к Template:Taxonomy с перебором суффиксов.
    - Обрабатывает таксономический прыжок для 'incertae sedis'.
    - Проверяет принадлежность родов к TAXONOMY_START_NODE (Фильтр Бизона).
    - Экспортирует чистовую таблицу taxonomic_tree.csv.
    """
    def __init__(self, start_node="Tetrapoda", suffixes=None):
        self.start_node = start_node.lower()
        self.suffixes = suffixes or ["", "_(dinosaur)", "_(reptile)", "_(archosaur)"]
        self.taxon_cache = {}
        self.lowest_units_seen = {}
        self.lock = threading.Lock()

    def fetch_lineage(self, genus_name, session):
        """Запрашивает цепочку предков через Template:Taxonomy."""
        for suffix in self.suffixes:
            url = f"https://en.wikipedia.org/wiki/Template:Taxonomy/{genus_name}{suffix}"
            for attempt in range(2):
                try:
                    resp = session.get(url, timeout=WIKI_TIMEOUT, allow_redirects=True)
                    if resp.status_code == 429:
                        time.sleep(5 * (attempt + 1))
                        continue
                    if resp.status_code == 404:
                        break
                    if resp.status_code != 200:
                        return None, url

                    soup = BeautifulSoup(resp.text, 'html.parser')
                    rows = soup.find_all('tr', class_='taxonrow')
                    if not rows: break

                    lineage = []
                    recording = False
                    for row in rows:
                        tds = row.find_all('td')
                        if len(tds) < 2: continue
                        taxon_type = tds[0].get_text(strip=True).lower()
                        raw_name = tds[1].get_text(strip=True).replace('†', '').strip()
                        clean_name = re.sub(r'\s*\(.*?\)', '', raw_name).strip(" .")
                        if not clean_name: continue

                        if clean_name.lower() == self.start_node:
                            recording = True
                        if recording:
                            if "genus" in taxon_type or clean_name.lower() == genus_name.lower():
                                break
                            lineage.append(clean_name)

                    if recording:
                        return lineage, url
                    else:
                        return [], url
                except:
                    break
        return None, f"https://en.wikipedia.org/wiki/Template:Taxonomy/{genus_name}"

    def resolve(self, genus, clade, session, audit_buffer, reports):
        """Проверяет принадлежность рода к дереву предков и регистрирует ветку."""
        if clade == MISSING_VAL:
            logging.error(f"{genus}: [TAXONOMY] REJECTED (No clade/family in infobox)")
            with data_lock: reports['out_of_class'].append(f"{genus} (No classification data)")
            return None

        is_incertae = "incertae" in clade.lower()
        with self.lock:
            cached = self.taxon_cache.get(clade) if not is_incertae else None

        if cached:
            audit_buffer.append(f"{genus}: [TAXONOMY] Shared with '{clade}' (Data reused from '{cached['source']}')")
            return clade

        lineage, taxo_url = self.fetch_lineage(genus, session)
        if lineage is None:
            logging.info(f"{genus}: ERROR (Could not fetch tree from {taxo_url})")
            with data_lock: reports['taxonomy_errors'].append(f"{genus}: could not fetch tree")
            return clade

        if len(lineage) == 0:
            start_cap = self.start_node.capitalize()
            logging.error(f"{genus}: ERROR (Taxonomy out of scope: {start_cap} not found)")
            with data_lock: reports['out_of_class'].append(f"{genus} (Out of scope)")
            return None

        # Прыжок через incertae sedis
        if is_incertae:
            for node in reversed(lineage):
                if "incertae" not in node.lower():
                    audit_buffer.append(f"{genus}: [TAXONOMY] 'incertae sedis' replaced by parent clade: '{node}'")
                    clade = node
                    break

        with self.lock:
            current_path = []
            for node in lineage:
                current_path.append(node)
                if node not in self.taxon_cache:
                    self.taxon_cache[node] = {'source': genus, 'path': list(current_path)}
            if clade not in self.taxon_cache:
                self.taxon_cache[clade] = {'source': genus, 'path': lineage}

        audit_buffer.append(f"{genus}: [TAXONOMY] New branch found. Fetched from: {taxo_url}")
        return clade

    def is_in_scope(self, genus_name, session):
        """Быстрый фильтр: принадлежит ли род к целевой группе."""
        g_low = genus_name.lower()
        with self.lock:
            if g_low in self.taxon_cache:
                return True

        lineage, _ = self.fetch_lineage(genus_name, session)
        return bool(lineage and len(lineage) > 0)

    def export_tree(self, filepath):
        """Сохраняет чистовое дерево классификации в CSV."""
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
                with self.lock:
                    if not self.taxon_cache: return
                    max_depth = max(len(v['path']) for v in self.taxon_cache.values())
                    headers = ["Lowest Unit", "Source Genus"] + [f"Level {i+1}" for i in range(max_depth)]
                    writer = csv.writer(f, delimiter=';')
                    writer.writerow(headers)
                    for unit, data in sorted(self.taxon_cache.items()):
                        writer.writerow([unit, data['source']] + data['path'])
            logging.info(f"Taxonomic tree saved to: {os.path.abspath(filepath)}")
        except Exception as e:
            logging.error(f"Could not save taxonomic tree: {e}")


# ========================================================================
# [3] ИНИЦИАЛИЗАЦИЯ ДВИЖКОВ
# ========================================================================

geo_extractor = TemporalRangeExtractor(GEO_REF_CSV, include_uncertain=config.INCLUDE_UNCERTAIN_STAGES)
taxonomy_engine = TaxonomyEngine(start_node=config.TAXONOMY_START_NODE, suffixes=config.WIKI_SUFFIXES)


# ========================================================================
# [4] ПОИСК СТРАНИЦ РОДОВ (PAGE FETCHER)
# ========================================================================

def fetch_genus_page(genus, session, reports):
    """Поиск и скачивание страницы рода с перебором суффиксов из config.py."""
    global total_bytes_downloaded
    for suffix in config.WIKI_SUFFIXES:
        retries = 0
        while retries < 3:
            try:
                response = session.get(BASE_WIKI_URL + genus + suffix, timeout=WIKI_TIMEOUT)
                with data_lock:
                    total_bytes_downloaded += len(response.content)
                if response.status_code == 429:
                    retries += 1
                    time.sleep(15)
                    continue
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    h1_tag = soup.find('h1', id='firstHeading')
                    current_infobox = soup.find('table', class_='infobox biota')

                    if h1_tag:
                        actual_title = h1_tag.get_text(strip=True).replace('†', '').strip()
                        clean_title = re.sub(r'\s*\(.*?\)', '', actual_title).lower()
                        clean_input = re.sub(r'\s*\(.*?\)', '', genus).lower()
                        title_first_word = clean_title.split()[0]

                        # Редирект на другой род
                        if title_first_word != clean_input:
                            if current_infobox:
                                logging.warning(f"{genus}: SKIP (Redirected to {actual_title})")
                                with data_lock: reports['redirects'].append(f"{genus} -> {actual_title}")
                                return None
                            else:
                                break  # Пробуем следующий суффикс

                        if actual_title.lower() != genus.lower() or suffix != "":
                            display_name = actual_title if actual_title.lower() != genus.lower() else f"{genus}{suffix}"
                            logging.warning(f"{genus}: ALIAS (Found as {display_name})")
                            with data_lock: reports['found_as'].append(f"{genus} -> {display_name}")

                    if current_infobox:
                        return current_infobox
                    break
                elif response.status_code == 404:
                    break
                else:
                    break
            except:
                retries += 1
                time.sleep(2)
    return None


# ========================================================================
# [5] ЗАГЛУШКИ ДЛЯ ПАРСИНГА ВИДОВ И СИНОНИМОВ (БЛОК №2)
# ========================================================================

def parse_main_section_stub(infobox, true_genus, extant, clade, age, stage, seen_species, all_results, reports, audit_buffer):
    return 0

def parse_synonyms_section_stub(infobox, true_genus, extant, clade, age, stage, seen_species, all_results, reports, audit_buffer, session):
    return 0


# ========================================================================
# [6] ВЫВОД ЛОГОВ И ДИРИЖЕР РОДА (ORCHESTRATOR)
# ========================================================================

def flush_genus_logs(genus, main_count, syn_count, all_results, audit_buffer, reports):
    """Форматирование и атомарный вывод логов рода в строгом стандарте PFL."""
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
            def f(v): return MISSING_VAL if (v is None or v == "" or str(v).lower() == "unknown") else str(v)
            upg = f" {entry.get('upgrade_note', '')}" if entry.get('upgrade_note') else ""
            meta = f" {entry.get('meta_note', '')}" if entry.get('meta_note') else ""
            
            if entry['type'] == 'MAIN':
                disp_type = True if auto_type else entry['is_type']
                suf = " (auto-assigned type)" if auto_type else ""
                line = f"{genus}: [MAIN] {f(entry['genus'])} | {f(entry['species'])} | {f(entry['status'])} | {f(disp_type)} | {f(entry['is_extant'])} | {f(entry['author'])} | {f(entry['year'])}{suf}{upg}{meta}"
            else:
                line = f"{genus}: [SYNONYM] {f(entry['genus'])} | {f(entry['species'])} | {f(entry['status'])} | {f(entry['is_extant'])} | {f(entry['author'])} | {f(entry['year'])}{upg}"
            formatted.append(line)
        else:
            formatted.append(entry)

    formatted.append(f"{genus}: FINISHED (Found {main_count} main, {syn_count} synonyms)")

    if BUFFER_LOGS:
        with log_lock:
            for line in formatted: logging.info(line)
    else:
        for line in formatted: logging.info(line)

    if total == 0:
        logging.error(f"{genus}: ERROR (Found 0 species)")
        with data_lock: reports['zero_species'].append(f"{genus}: 0 species extracted")


def process_single_genus(genus, initial_status, session, all_results, reports):
    """Компактный дирижер обработки рода."""
    audit_buffer = []

    # 1. Nomen nudum
    if "nudum" in str(initial_status).lower():
        logging.info(f"{genus}: STUB CREATED (nomen nudum - skipping Wikipedia)")
        stub = {"genus": genus, "species": MISSING_VAL, "author": MISSING_VAL, "year": MISSING_VAL, "status": "nudum", "is_extant": False}
        with data_lock:
            all_results.append(stub)
        return

    # 2. Поиск страницы
    infobox = fetch_genus_page(genus, session, reports)
    if not infobox:
        logging.error(f"{genus}: ERROR (No infobox found)")
        with data_lock: reports['no_infobox'].append(f"{genus}: No infobox found")
        return

    # 3. Извлечение имени рода, семейства и крестика
    true_genus = genus
    clade = MISSING_VAL
    genus_is_extant = True

    rows = infobox.find_all('tr')
    for i, r in enumerate(rows):
        tds = r.find_all('td')
        if len(tds) == 2 and "Genus:" in tds[0].get_text():
            if '†' in tds[1].get_text():
                genus_is_extant = False
            true_genus = tds[1].get_text(strip=True).replace('†', '').replace('(', '').replace(')', '').strip().split()[0]
            if i > 0:
                prev_tds = rows[i-1].find_all('td')
                if len(prev_tds) == 2:
                    clade = prev_tds[1].get_text(separator=" ", strip=True).replace('†', '').replace('?', '').strip().split()[0]
            break

    # 4. Извлечение геологии через класс TemporalRangeExtractor
    age, stage = geo_extractor.extract(infobox)

    audit_buffer.append(f"{genus}: PARSING...")
    age_disp = f"{age} Ma" if age != MISSING_VAL else MISSING_VAL
    audit_buffer.append(f"{genus}: [DATA] {clade} | {age_disp} | {stage}")
    with data_lock: current_session_facts[f"{genus}:DATA"] = f"{clade} | {age_disp} | {stage}"

    # 5. Проверка таксономии через класс TaxonomyEngine
    clade = taxonomy_engine.resolve(genus, clade, session, audit_buffer, reports)
    if not clade: return

    # 6. Парсинг видов (заглушки)
    seen_species = set()
    main_count = parse_main_section_stub(infobox, true_genus, genus_is_extant, clade, age, stage, seen_species, all_results, reports, audit_buffer)
    syn_count = 0
    if config.FETCH_SYNONYMS:
        syn_count = parse_synonyms_section_stub(infobox, true_genus, genus_is_extant, clade, age, stage, seen_species, all_results, reports, audit_buffer, session)

    # 7. Вывод логов
    flush_genus_logs(genus, main_count, syn_count, all_results, audit_buffer, reports)


# ========================================================================
# [7] ГЛАВНЫЙ РАННЕР И ЭКСПОРТ (ENTRY POINT)
# ========================================================================

def load_genera_list():
    """Загружает входной список родов."""
    target_path = CUSTOM_LIST_PATH if config.USE_CUSTOM_LIST else INPUT_CSV
    source_type = "CUSTOM TXT" if config.USE_CUSTOM_LIST else "MAIN CSV"
    
    if not os.path.exists(target_path):
        return [], source_type, target_path

    genera_info = []
    try:
        if config.USE_CUSTOM_LIST:
            with open(target_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        genera_info.append({'name': line.strip(), 'status': MISSING_VAL})
        else:
            with open(target_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f, delimiter=';')
                for row in reader:
                    if row.get('genus'):
                        genera_info.append({'name': row['genus'], 'status': str(row.get('status', MISSING_VAL)).lower()})
    except Exception as e:
        logging.error(f"Error reading {target_path}: {e}")
        
    return genera_info, source_type, target_path


def start_mass_parsing():
    global total_bytes_downloaded
    logging.info("--- SCRIPT START: PARSE_WIKI_DETAILS ---")
    if config.BRIEF_CONSOLE:
        print("PARSE_WIKI_DETAILS...", end=" ", flush=True)
    else:
        print("Starting script: PARSE_WIKI_DETAILS")

    genera_to_parse, src_type, src_path = load_genera_list()
    if not genera_to_parse:
        print(f"[ERROR] No genera to process at {src_path}")
        return

    total = len(genera_to_parse)
    all_results = []
    reports = {
        'hist_notes': [], 'found_as': [], 'redirects': [], 'zero_species': [],
        'no_infobox': [], 'duplicates': [], 'upgrades': [], 'out_of_class': [], 'taxonomy_errors': []
    }
    
    session = requests.Session()
    session.headers.update(HEADERS)

    if USE_PARALLEL:
        adapter = requests.adapters.HTTPAdapter(pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS)
        session.mount('https://', adapter)
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            tasks = [(g['name'], g['status']) for g in genera_to_parse]
            futures = [executor.submit(process_single_genus, name, stat, session, all_results, reports) for name, stat in tasks]
            for i, future in enumerate(futures, 1):
                future.result()
                if not config.BRIEF_CONSOLE:
                    sys.stdout.write(f"\rParsing... [{i}/{total}]")
                    sys.stdout.flush()
    else:
        for i, g in enumerate(genera_to_parse, 1):
            process_single_genus(g['name'], g['status'], session, all_results, reports)
            if not config.BRIEF_CONSOLE:
                sys.stdout.write(f"\rParsing... [{i}/{total}]")
                sys.stdout.flush()

    if not config.BRIEF_CONSOLE:
        print("\nParsing completed.")
    logging.info("Parsing completed.")

    # Сохраняем результаты
    save_to_csv(all_results, OUTPUT_FILE)
    taxonomy_engine.export_tree(CLASSIFICATION_FILE)

    # Итоговый аудит лога
    logging.info("=== FINAL DATA FETCH REPORT ===")
    final_sections = [
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
    for idx, (title, items) in enumerate(final_sections, 1):
        logging.info(f"[{idx}] {title} ({len(items)})")
        for item in items: logging.info(item)

    if not config.BRIEF_CONSOLE:
        print("Script ended: PARSE_WIKI_DETAILS")
    logging.info("--- SCRIPT END: PARSE_WIKI_DETAILS ---")


def save_to_csv(all_results, filename):
    keys = ["genus", "species", "status", "is_type", "is_extant", "clade", "stage", "age", "author", "year", "source_genus"]
    try:
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=keys, delimiter=';', extrasaction='ignore')
            writer.writeheader()
            for res in all_results:
                clean_row = {k: (" ".join(str(v).split()) if isinstance(v, str) else v) for k, v in res.items()}
                writer.writerow(clean_row)
        logging.info(f"Raw fauna data saved to: {os.path.abspath(filename)}")
    except Exception as e:
        logging.error(f"Could not save to CSV: {e}")


if __name__ == "__main__":
    start_mass_parsing()