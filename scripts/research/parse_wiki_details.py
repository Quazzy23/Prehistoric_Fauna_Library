import sys
sys.dont_write_bytecode = True

import os
import re
import csv
import copy
import json
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
    def __init__(self, geo_csv_path):
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
          lambda tag: tag.name in ['div', 'th', 'td'] and 'Temporal range' in tag.get_text()
      )
      if not temp_div:
        return MISSING_VAL, MISSING_VAL

      temp_copy = copy.copy(temp_div)
      for noise in temp_copy.find_all(['div', 'style'], id='Timeline-row'):
        noise.decompose()
      for noise in temp_copy.find_all('sup'):
        noise.decompose()

      raw_text_full = temp_copy.get_text(separator=' ', strip=True)
      
      # [!] Забираем текст СТРОГО ПОСЛЕ фразы "Temporal range:" (отсекаем имя таксона Chunga)
      if "Temporal range:" in raw_text_full:
          raw_text = raw_text_full.split("Temporal range:")[-1].strip()
      else:
          raw_text = raw_text_full.replace("Temporal range", "").strip()

      clean_text = self._clean_typography(raw_text)

      # [!] Всегда отсекаем сомнительные ярусы (possible)
      if 'possible' in clean_text.lower():
          clean_text = self._clean_typography(
              re.split(r'possible', clean_text, flags=re.IGNORECASE)[0].strip(' ,()~-–—')
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
    def __init__(self, start_node):
        self.start_node = start_node.lower().strip()
        self.taxon_cache = {}
        self.lowest_units_seen = {}
        self.lock = threading.Lock()

    def fetch_lineage(self, genus_name, session):
        """Запрашивает цепочку предков напрямую через Template:Taxonomy/{genus_name}."""
        url = f"{config.BASE_WIKI_URL}/wiki/Template:Taxonomy/{genus_name}"
        for attempt in range(2):
            try:
                resp = session.get(url, timeout=WIKI_TIMEOUT, allow_redirects=True)
                if resp.status_code == 429:
                    time.sleep(5 * (attempt + 1))
                    continue
                if resp.status_code != 200:
                    return None, url

                soup = BeautifulSoup(resp.text, 'html.parser')
                rows = soup.find_all('tr', class_='taxonrow')
                if not rows:
                    taxo_table = soup.find('table', class_=re.compile(r'taxonomy|wikitable|infobox', re.I))
                    if taxo_table:
                        rows = taxo_table.find_all('tr')

                if not rows:
                    return None, url

                lineage = []
                recording = False
                for row in rows:
                    tds = row.find_all(['td', 'th'])
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
            except Exception:
                if attempt == 1:
                    return None, url
                time.sleep(2)

        return None, url

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
# [4] ПОИСК СТРАНИЦ РОДОВ (PAGE FETCHER)
# ========================================================================

def fetch_page_by_url(taxon_name, page_url, session, reports):
    """Скачивает страницу таксона (рода или вида) строго по готовому URL из target_pages.csv."""
    global total_bytes_downloaded
    retries = 0
    while retries < 3:
        try:
            response = session.get(page_url, timeout=WIKI_TIMEOUT, allow_redirects=True)
            with data_lock:
                total_bytes_downloaded += len(response.content)

            if response.status_code == 429:
                retries += 1
                time.sleep(15)
                continue

            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                infobox = soup.find('table', class_='infobox biota')
                if infobox:
                    return infobox, soup
                else:
                    logging.warning(f"{taxon_name}: ERROR (No infobox found at {page_url})")
                    with data_lock:
                        reports['no_infobox'].append(f"{taxon_name}: No infobox found")
                    return None, None
            else:
                logging.warning(f"{taxon_name}: ERROR (HTTP {response.status_code} at {page_url})")
                return None, None
        except Exception as e:
            retries += 1
            time.sleep(2)

    return None, None


# ==============================================================================
# [5] КЛАСС ИЗВЛЕЧЕНИЯ И АНАЛИЗА ВИДОВ (SPECIES DETAILS EXTRACTOR)
# ==============================================================================

def clean_text(raw_text):
    """Очищает строку от пометок вымирания, кавычек и лишних скобок."""
    if not raw_text:
        return ""
    text = re.sub(r'[†\?\"“”\(\)\[\]]', '', raw_text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip(" ,.:;")


class SpeciesDetailsExtractor:
    """
    Автономный движок извлечения видов, авторов, статусов и научных миграций.
    """
    def __init__(self, migrations_file_path=None):
        self.migrations_file = migrations_file_path or MIGRATIONS_FILE
        self.lock = threading.Lock()

    def extract_author_and_year(self, metadata_element, current_genus=None, current_species=None):
        r"""
        Автор и год:
        Год — это просто любые 4 цифры подряд (\b\d{4}\b).
        Автор — это весь текст строго до года.
        """
        if not metadata_element:
            return MISSING_VAL, MISSING_VAL

        raw_text = metadata_element.get_text(" ", strip=True) if hasattr(metadata_element, 'get_text') else str(metadata_element)
        
        # Удаляем сноски [1], [2], [MSW3]
        text = re.sub(r'\[.*?\]', '', raw_text)

        # Удаляем пометки [originally ...] и статусы
        text = re.sub(r'\[\s*originally.*?\s*\]', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\(\s*(?:originally|vide|preoccupied|conserved name|rejected name).*?\)', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\(?\s*nomen\s+(?:dubium|nudum|protectum|oblitum)\s*\)?', '', text, flags=re.IGNORECASE)

        # Если был emend — берем автора после emend
        if 'emend' in text.lower():
            text = re.split(r'emend\.?', text, flags=re.IGNORECASE)[-1]

        # Ищем любые 4 цифры года
        years = re.findall(r'\b\d{4}\b', text)
        if not years:
            # Если 4 цифр нет — весь текст считаем автором (без года)
            clean_auth = clean_text(text)
            return (clean_auth if len(clean_auth) > 1 else MISSING_VAL), MISSING_VAL

        year = years[-1]
        
        # Автор — это строка строго до года
        author_raw = text.split(year)[0].strip(" ,;()—–-")
        
        # Убираем имя рода/вида, если они попали перед автором
        if current_genus:
            author_raw = re.sub(r'\b' + re.escape(current_genus) + r'\b', '', author_raw, flags=re.IGNORECASE)
        if current_species:
            author_raw = re.sub(r'\b' + re.escape(current_species) + r'\b', '', author_raw, flags=re.IGNORECASE)

        author_clean = re.sub(r'[†\?\"“”\[\]]', '', author_raw).strip(" ,:;—–-")
        
        # Нормализуем et al. (всегда с точкой)
        author_clean = re.sub(r'\bet\s+al\b\.?', 'et al.', author_clean, flags=re.IGNORECASE)
        if author_clean.lower().endswith("et al"):
            author_clean += "."

        return (author_clean if len(author_clean) > 1 else MISSING_VAL), year

    def determine_status(self, raw_text):
        """Определяет 4 канонических статуса: valid, provisional, dubious, nudum."""
        text_lower = raw_text.lower()
        if '"' in raw_text or '“' in raw_text or 'nudum' in text_lower:
            return 'nudum'
        if 'dubium' in text_lower or 'dubious' in text_lower:
            return 'dubious'
        if '?' in raw_text:
            return 'provisional'
        return 'valid'

    def determine_extinction(self, raw_text, genus_is_extant):
        """Определяет статус вымирания: 'extant' или 'extinct'."""
        if '†' in raw_text:
            return 'extinct'
        return 'extant' if genus_is_extant else 'extinct'

    def check_migrations(self, type_species_element, sc_genus, reports):
        """
        Проверяет ТОЛЬКО блок Type species:
        Сравнивает род из Type species с родом из строки 'Genus:' (Scientific classification).
        Если не совпадают -> пишет в known_migrations.json.
        """
        if not type_species_element or not sc_genus or sc_genus == MISSING_VAL:
            return

        # Ищем название типового вида в курсиве (например, "Elephas americanus" или "Cariama cristata")
        name_tag = type_species_element.find(class_='binomial') or type_species_element.find(['i', 'em'])
        if not name_tag:
            return

        full_type_name = clean_text(name_tag.get_text(strip=True))
        parts = full_type_name.split()
        if len(parts) < 2:
            return

        type_genus = parts[0]  # Род из типового вида (например, "Elephas")

        # [!] СРАВНЕНИЕ: род из Type species против рода из Scientific classification (Genus:)
        if type_genus.lower() != sc_genus.lower() and type_genus[0].isupper():
            with self.lock:
                m_data = {}
                if os.path.exists(self.migrations_file):
                    try:
                        with open(self.migrations_file, 'r', encoding='utf-8') as f:
                            m_data = json.load(f)
                    except:
                        pass
                
                if full_type_name not in m_data:
                    m_data[full_type_name] = sc_genus
                    try:
                        os.makedirs(os.path.dirname(self.migrations_file), exist_ok=True)
                        with open(self.migrations_file, 'w', encoding='utf-8') as f:
                            json.dump(m_data, f, indent=2, ensure_ascii=False)
                        with data_lock:
                            reports['hist_notes'].append(f"{full_type_name} -> {sc_genus}")
                    except Exception as e:
                        logging.error(f"Failed to write migration file: {e}")

    def extract_species_from_item(self, element, true_genus, header_says_type, genus_is_extant):
        """Извлекает данные одного вида из элемента (li, p, div)."""
        temp_elem = copy.copy(element)
        extinct_status = self.determine_extinction(element.get_text(), genus_is_extant)

        for tag in temp_elem.find_all(['abbr', 'sup', 'style']):
            tag.decompose()

        raw_text_full = temp_elem.get_text(separator=" ", strip=True)
        species_part = MISSING_VAL
        found_type_marker = ("type" in raw_text_full.lower())

        # Ищем курсивное название вида
        scientific_tokens = []
        nodes_to_delete = []

        for it in temp_elem.find_all(['i', 'em']):
            if it.find_parent('small'):
                continue
            span_parent = it.find_parent('span')
            if span_parent and 'font-size' in str(span_parent.get('style', '')).lower():
                continue

            it_txt = it.get_text(separator=" ", strip=True).replace('†', '').replace('?', '').strip()
            if any(c.isalpha() for c in it_txt):
                scientific_tokens.append(it_txt)
                nodes_to_delete.append(it)

        if scientific_tokens:
            full_name = " ".join(scientific_tokens)
            full_name = re.sub(r'\bet\s+al\.?\b', '', full_name, flags=re.IGNORECASE).strip()
            parts = [w.strip(' ".,?') for w in full_name.split() if w.strip(' ".,?') and not w.startswith('(')]
            
            if len(parts) >= 2:
                if parts[1][0].islower():
                    species_part = parts[1].lower()
                elif len(parts) >= 3 and parts[2][0].islower():
                    species_part = parts[2].lower()
            elif len(parts) == 1:
                if parts[0][0].islower():
                    species_part = parts[0].lower()
                elif parts[0].lower() != true_genus.lower():
                    species_part = parts[0].lower()

            if species_part != MISSING_VAL:
                for node in nodes_to_delete:
                    node.decompose()

        # Поиск по кавычкам (nomen nudum)
        if species_part == MISSING_VAL:
            quoted = re.findall(r'["“](.*?)["”]', raw_text_full)
            if quoted:
                q_parts = [w.strip(' ".,?') for w in quoted[0].split() if w.strip(' ".,?')]
                if q_parts:
                    species_part = q_parts[-1].lower()

        if not species_part or species_part == MISSING_VAL:
            return None

        species_part = clean_text(species_part)
        if species_part.lower() in ["text", "see", "al", "none", MISSING_VAL]:
            return None

        # Автор и год — строго из мелкого шрифта
        meta_container = temp_elem.find(['small', 'div', 'span'])
        if meta_container:
            author, year = self.extract_author_and_year(meta_container, true_genus, species_part)
        else:
            author, year = self.extract_author_and_year(temp_elem, true_genus, species_part)

        final_status = self.determine_status(raw_text_full)

        return {
            "genus": true_genus,
            "species": species_part,
            "author": author,
            "year": year,
            "status": final_status,
            "is_type": header_says_type or found_type_marker,
            "is_extant": extinct_status
        }

    def parse_main_section(self, infobox, true_genus, genus_is_extant, clade, age, stage, seen_species, all_results, reports, audit_buffer):
        """Парсит виды на странице РОДА."""
        rows = infobox.find_all('tr')
        handled_tds = set()
        passed_classification = False

        for tr in rows:
            th = tr.find('th')
            th_text = th.get_text(" ", strip=True).lower() if th else ""

            if "synonym" in th_text:
                break

            if "scientific classification" in th_text or "genus:" in th_text:
                passed_classification = True
                continue

            tds = tr.find_all(['td', 'th'])
            if len(tds) >= 2 and ":" in tds[0].get_text():
                passed_classification = True
                continue

            if not passed_classification:
                continue

            td = tr.find('td')
            if not td and th and th.get('colspan') == '2':
                next_tr = tr.find_next_sibling('tr')
                if next_tr:
                    td = next_tr.find('td')

            if not td or id(td) in handled_tds:
                handled_tds.add(id(td))
                continue

            header_says_type = bool(th and any(w in th_text for w in ["type", "binomial"]))

            # Собираем элементы вида
            items = td.find_all('li')
            if not items:
                # Считаем, сколько видовых названий в курсиве лежит в этой ячейке
                primary_italics = [it for it in td.find_all(['i', 'em']) if not it.find_parent('small') and not (it.find_parent('div') and 'font-size' in str(it.find_parent('div').get('style', '')).lower())]
                
                # Если в ячейке НЕСКОЛЬКО видов через <br> (как у Chunga или Shastasaurus) — нарезаем по <br>
                if len(primary_italics) > 1:
                    items = []
                    current_chunk = BeautifulSoup('<div></div>', 'html.parser').div
                    content_source = td.find('p') or td
                    for child in content_source.children:
                        if getattr(child, 'name', None) == 'br':
                            if list(current_chunk.stripped_strings):
                                items.append(current_chunk)
                                current_chunk = BeautifulSoup('<div></div>', 'html.parser').div
                        else:
                            current_chunk.append(copy.copy(child))
                    if list(current_chunk.stripped_strings):
                        items.append(current_chunk)
                else:
                    # Если вид всего один (как в Type species у Strigogyps dubius) — берем ВСЮ ячейку целиком!
                    items = [td]

            for item in items:
                # Фиксируем миграцию только для блока Type species, передавая sc_genus (род из Scientific classification)
                if header_says_type:
                    self.check_migrations(item, true_genus, reports)

                info = self.extract_species_from_item(item, true_genus, header_says_type, genus_is_extant)
                if not info:
                    continue

                s_low = info['species'].lower()
                if s_low in seen_species:
                    continue
                seen_species.add(s_low)

                self.add_species_to_results(all_results, info, clade, age, stage, true_genus)

                audit_buffer.append({
                    'type': 'MAIN',
                    'genus': info['genus'],
                    'species': info['species'],
                    'status': info['status'],
                    'is_type': info['is_type'],
                    'is_extant': info['is_extant'],
                    'author': info['author'],
                    'year': info['year']
                })

            handled_tds.add(id(td))

    def add_species_to_results(self, all_results, info, clade, age, stage, source_genus):
        """Добавляет или обновляет вид."""
        if not info or not info.get('genus') or not info.get('species'):
            return "error"
        
        info['clade'] = clade
        info['age'] = age
        info['stage'] = stage
        info['source_genus'] = source_genus
        
        existing = next((res for res in all_results if res['genus'].lower() == info['genus'].lower() and res['species'].lower() == info['species'].lower()), None)
        
        if not existing:
            all_results.append(info)
            return "added"
        
        # Обновление полей страницы вида
        if info.get('age') != MISSING_VAL: existing['age'] = info['age']
        if info.get('stage') != MISSING_VAL: existing['stage'] = info['stage']
        if info.get('author') != MISSING_VAL: existing['author'] = info['author']
        if info.get('year') != MISSING_VAL: existing['year'] = info['year']
        if info.get('is_type'): existing['is_type'] = True

        return "upgraded"


# ========================================================================
# [3] ИНИЦИАЛИЗАЦИЯ ДВИЖКОВ
# ========================================================================

geo_extractor = TemporalRangeExtractor(GEO_REF_CSV)
taxonomy_engine = TaxonomyEngine(start_node=config.TAXONOMY_START_NODE)
species_extractor = SpeciesDetailsExtractor(migrations_file_path=MIGRATIONS_FILE)


# ========================================================================
# [6] ВЫВОД ЛОГОВ И ДИРИЖЕР ТАКСОНА (ORCHESTRATOR)
# ========================================================================

def flush_genus_logs(display_taxon, rank_type, page_url, data_str, audit_buffer, reports):
    formatted = []
    
    # 1. Первая строка: Ранг страницы и URL
    formatted.append(f"{rank_type}: {display_taxon} ({page_url})")
    
    # 2. Вторая строка: Объединенный блок [DATA]
    if data_str:
        formatted.append(f"{display_taxon}: [DATA] {data_str}")

    # 3. Строки видов [MAIN]
    main_count = 0
    for entry in audit_buffer:
        if isinstance(entry, dict) and entry.get('type') == 'MAIN':
            main_count += 1
            def f(v): return MISSING_VAL if (v is None or v == "" or str(v).lower() == "unknown") else str(v)
            line = f"{display_taxon}: [MAIN] {f(entry['genus'])} | {f(entry['species'])} | {f(entry['status'])} | {f(entry['is_type'])} | {f(entry['is_extant'])} | {f(entry['author'])} | {f(entry['year'])}"
            formatted.append(line)

    if BUFFER_LOGS:
        with log_lock:
            for line in formatted: logging.info(line)
    else:
        for line in formatted: logging.info(line)

    if main_count == 0:
        logging.error(f"{display_taxon}: ERROR (Found 0 species)")
        with data_lock:
            reports['zero_species'].append(f"{display_taxon}: 0 species extracted")


def process_single_taxon(taxon_name, page_url, initial_status, session, all_results, reports):
    """Дирижер обработки страницы таксона."""
    audit_buffer = []

    # 1. Nomen nudum
    if "nudum" in str(initial_status).lower():
        logging.info(f"{taxon_name}: STUB CREATED (nomen nudum - skipping Wikipedia)")
        stub = {"genus": taxon_name, "species": MISSING_VAL, "author": MISSING_VAL, "year": MISSING_VAL, "status": "nudum", "is_extant": "extinct"}
        species_extractor.add_species_to_results(all_results, stub, MISSING_VAL, MISSING_VAL, MISSING_VAL, taxon_name)
        return

    # 2. Скачивание страницы по точному URL
    infobox, _ = fetch_page_by_url(taxon_name, page_url, session, reports)
    if not infobox:
        return

    # 3. ОПРЕДЕЛЯЕМ РАНГ СТРАНИЦЫ ПО КЛАССИФИКАЦИИ
    rank_type = "GENUS"
    true_genus = taxon_name.split()[0]  # По умолчанию берем первое слово taxon_name (Cariama, Chunga и т.д.)
    clade = MISSING_VAL
    genus_is_extant = True

    rows = infobox.find_all('tr')
    for i, r in enumerate(rows):
        tds = r.find_all(['td', 'th'])
        if len(tds) >= 2:
            label = tds[0].get_text(" ", strip=True).lower().rstrip(':')
            val_td = tds[1]

            if label == "species":
                rank_type = "SPECIES"

            elif label == "genus":
                # Имя рода строго из тега <i> (НЕ из всего tds[1], чтобы не прилипал автор!)
                i_tag = val_td.find(['i', 'em'])
                if i_tag:
                    true_genus = clean_text(i_tag.get_text(strip=True))

                if '†' in val_td.get_text():
                    genus_is_extant = False

                # Кладу берем из строки перед Genus
                if i > 0:
                    prev_tds = rows[i-1].find_all(['td', 'th'])
                    if len(prev_tds) >= 2:
                        c_prev = copy.copy(prev_tds[1])
                        for sm in c_prev.find_all(['small', 'div', 'span', 'sup']): sm.decompose()
                        clade_cand = clean_text(c_prev.get_text(strip=True)).split()[0]
                        if clade_cand:
                            clade = clade_cand

    # 4. Извлечение геологии через класс TemporalRangeExtractor
    age, stage = geo_extractor.extract(infobox)
    age_disp = f"{age} Ma" if age != MISSING_VAL else MISSING_VAL

    # 5. Проверка таксономии через класс TaxonomyEngine
    taxo_note = f"new branch found '{clade}'"
    with taxonomy_engine.lock:
        if clade in taxonomy_engine.taxon_cache:
            taxo_note = f"data reused from '{taxonomy_engine.taxon_cache[clade]['source']}'"

    clade = taxonomy_engine.resolve(taxon_name, clade, session, audit_buffer, reports)
    if not clade: return

    data_str = f"{clade} | {age_disp} | {stage} ({taxo_note})"

    seen_species = set()

    # 6. ПАРСИНГ ВИДОВ В ЗАВИСИМОСТИ ОТ РАНГА СТРАНИЦЫ
    if rank_type == "SPECIES":
        # СТРАНИЦА ВИДА: вид и автор лежат СТРОГО в ячейке под заголовком Binomial name!
        sp_genus = true_genus
        sp_epithet = MISSING_VAL
        sp_author = MISSING_VAL
        sp_year = MISSING_VAL
        sp_status = "valid"
        is_type = True
        extinct_status = "extant" if genus_is_extant else "extinct"

        # 1. Ищем строку с заголовком Binomial name или Trinomial name
        binomial_td = None
        for r in rows:
            th = r.find('th')
            if th:
                th_t = th.get_text(" ", strip=True).lower()
                if "binomial" in th_t or "trinomial" in th_t:
                    # Ячейка данных находится либо в этой же строке, либо в следующей
                    binomial_td = r.find('td') or (r.find_next_sibling('tr').find('td') if r.find_next_sibling('tr') else None)
                    break

        # Если явного заголовка Binomial name не было — берем ячейку со span.binomial
        if not binomial_td:
            for r in rows:
                b_span = r.find(class_='binomial')
                if b_span:
                    binomial_td = r.find('td')
                    break

        if binomial_td:
            if '†' in binomial_td.get_text():
                extinct_status = "extinct"

            # 2. Название вида: строго из span.binomial или тега <i>
            name_tag = binomial_td.find(class_='binomial') or binomial_td.find(['i', 'em'])
            if name_tag:
                full_name_clean = clean_text(name_tag.get_text(" ", strip=True))
                words = full_name_clean.split()
                if len(words) >= 2:
                    sp_genus = words[0]
                    sp_epithet = words[1].lower()
                elif len(words) == 1:
                    sp_epithet = words[0].lower()

            # 3. Автор и Год: строго из тега с мелким шрифтом (<div style="font-size: 85%;"> или <small>)
            meta_tag = binomial_td.find(['small', 'div', 'span'])
            # Убеждаемся, что мы не взяли сам тег названия
            if meta_tag and meta_tag != name_tag and not meta_tag.find(class_='binomial'):
                meta_text = meta_tag.get_text(" ", strip=True)
            else:
                # Берем текст ячейки без названия вида
                td_copy = copy.copy(binomial_td)
                if name_tag:
                    for n in td_copy.find_all(['i', 'em', 'span']): n.decompose()
                meta_text = td_copy.get_text(" ", strip=True)

            # Чистим сноски [1], [2]
            meta_text = re.sub(r'\[.*?\]', '', meta_text)

            # Ищем любые 4 цифры года
            found_years = re.findall(r'\b\d{4}\b', meta_text)
            if found_years:
                sp_year = found_years[-1]
                # Автор — это строка строго до года
                raw_auth = meta_text.split(sp_year)[0].strip(" ,;()—–-")
                # Убираем случайные остатки рода/вида
                raw_auth = re.sub(r'\b' + re.escape(sp_genus) + r'\b', '', raw_auth, flags=re.IGNORECASE)
                raw_auth = re.sub(r'\b' + re.escape(sp_epithet) + r'\b', '', raw_auth, flags=re.IGNORECASE)
                sp_author = re.sub(r'[†\?\"“”\[\]]', '', raw_auth).strip(" ,:;—–-")
                # Нормализуем et al. (всегда с точкой)
                sp_author = re.sub(r'\bet\s+al\b\.?', 'et al.', sp_author, flags=re.IGNORECASE)
                if sp_author.lower().endswith("et al"):
                    sp_author += "."
                if not sp_author:
                    sp_author = MISSING_VAL

            sp_status = species_extractor.determine_status(binomial_td.get_text())

            info = {
                "genus": sp_genus,
                "species": sp_epithet,
                "author": sp_author,
                "year": sp_year,
                "status": sp_status,
                "is_type": is_type,
                "is_extant": extinct_status
            }
            species_extractor.add_species_to_results(all_results, info, clade, age, stage, taxon_name)
            audit_buffer.append({
                'type': 'MAIN',
                'genus': sp_genus,
                'species': sp_epithet,
                'status': sp_status,
                'is_type': is_type,
                'is_extant': extinct_status,
                'author': sp_author,
                'year': sp_year
            })

    else:
        # СТРАНИЦА РОДА: парсим виды из инфобокса через класс
        species_extractor.parse_main_section(
            infobox, true_genus, genus_is_extant, clade, age, stage,
            seen_species, all_results, reports, audit_buffer
        )

    # 7. Финальный вывод лога страницы
    flush_genus_logs(taxon_name, rank_type, page_url, data_str, audit_buffer, reports)

# ========================================================================
# [7] ГЛАВНЫЙ РАННЕР И ЭКСПОРТ (ENTRY POINT)
# ========================================================================

def load_target_pages():
    """Загружает список целевых страниц (родов и видов) из target_pages.csv."""
    target_path = CUSTOM_LIST_PATH if config.USE_CUSTOM_LIST else os.path.join(DATA_ROOT, "target_pages.csv")
    
    if not os.path.exists(target_path):
        logging.error(f"Target pages file not found: {target_path}")
        return [], target_path

    pages_info = [] 
    try:
        if config.USE_CUSTOM_LIST:
            with open(target_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        pages_info.append({'name': line.strip(), 'url': f"{config.BASE_WIKI_URL}{line.strip()}", 'status': MISSING_VAL})
        else:
            with open(target_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f, delimiter=';')
                for row in reader:
                    taxon = row.get('taxon')
                    url = row.get('url')
                    if taxon and url:
                        pages_info.append({
                            'name': taxon, 
                            'url': url,
                            'status': MISSING_VAL
                        })
    except Exception as e:
        logging.error(f"Error reading {target_path}: {e}")
        
    return pages_info, target_path


def start_mass_parsing():
    global total_bytes_downloaded
    logging.info("--- SCRIPT START: PARSE_WIKI_DETAILS ---")
    if config:
        logging.info("Configuration loaded successfully")
    else:
        logging.error("Configuration loading failed")
        
    if config.BRIEF_CONSOLE:
        print("PARSE_WIKI_DETAILS...", end=" ", flush=True)
    else:
        print("Starting script: PARSE_WIKI_DETAILS")

    target_pages, src_path = load_target_pages()
    if not target_pages:
        msg = f"No target pages to process at {src_path}"
        logging.error(msg)
        print(f"[ERROR] {msg}")
        return

    logging.info(f"Successfully opened input file: {src_path}")
    logging.info(f"Detected {len(target_pages)} targets in {os.path.basename(src_path)}")

    total = len(target_pages)
    all_results = []
    reports = {
        'hist_notes': [], 'found_as': [], 'redirects': [], 'zero_species': [],
        'no_infobox': [], 'duplicates': [], 'upgrades': [], 'out_of_class': [], 'taxonomy_errors': []
    }
    
    session = requests.Session()
    session.headers.update(HEADERS)

    if USE_PARALLEL:
        logging.info(f"Mode: PARALLEL (Workers: {MAX_WORKERS})")
        adapter = requests.adapters.HTTPAdapter(pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS)
        session.mount('https://', adapter)
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [
                executor.submit(
                    process_single_taxon, 
                    item['name'], item['url'], item['status'], 
                    session, all_results, reports
                ) 
                for item in target_pages
            ]
            for i, future in enumerate(futures, 1):
                future.result()
                if not config.BRIEF_CONSOLE:
                    sys.stdout.write(f"\rParsing... [{i}/{total}]")
                    sys.stdout.flush()
    else:
        logging.info("Mode: SINGLE-THREADED")
        for i, item in enumerate(target_pages, 1):
            process_single_taxon(
                item['name'], item['url'], item['status'], 
                session, all_results, reports
            )
            if not config.BRIEF_CONSOLE:
                sys.stdout.write(f"\rParsing... [{i}/{total}]")
                sys.stdout.flush()

    if not config.BRIEF_CONSOLE:
        print("\nParsing completed.")
    
    # [!] Формируем сообщения
    species_count_msg = f"Total species extracted: {len(all_results)}"
    size_mb = total_bytes_downloaded / (1024 * 1024)
    size_report = f"Total data downloaded: {size_mb:.2f} MB"
    
    # В логи пишется всё (включая размер данных)
    logging.info("Parsing completed.")
    logging.info(species_count_msg)
    logging.info(size_report)

    # В консоль пишется ТОЛЬКО количество видов (без размера данных)
    if not config.BRIEF_CONSOLE:
        print(species_count_msg)

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