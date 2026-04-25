import requests
from bs4 import BeautifulSoup
import os
import re
import csv
import logging
import sys
import time
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

# --- ПУТИ И НАСТРОЙКИ ---
WIKI_URL = config.GEO_WIKI_URL
USER_EMAIL = config.USER_EMAIL
HEADERS = {'User-Agent': f'PrehistoricFaunaLibraryCollector/1.0 (mailto:{USER_EMAIL})'}

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GEO_OUTPUT_FILE = os.path.join(BASE_DIR, "data", "exports", "geochronology_data.csv")

# Настройка логов
LOG_DIR = os.path.join(BASE_DIR, "data", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "geochronology.log")

MISSING_VAL = "-"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    filename=LOG_FILE,
    filemode='w',
    encoding='utf-8'
)

def is_clean_name(text):
    """Проверяет, является ли текст названием, а не описанием событий."""
    if not text or text in ["-", ""]: return False
    
    # Геологические названия могут содержать / (Upper/Late) или () (formerly...)
    # Но они не должны быть слишком длинными (описания событий обычно > 60 символов)
    if len(text) > 60: 
        return False
        
    # Если в тексте слишком много слов - это описание
    if len(text.split()) > 5: 
        return False
        
    # Ключевые слова, которые характерны для описаний событий, а не для названий
    bad_words = ["evolve", "climate", "volcanism", "occurs", "starts", "forms", "extinct"]
    if any(word in text.lower() for word in bad_words):
        return False
        
    return True

def fetch_geochronology():
    logging.info("--- SCRIPT START: FETCH_GEOCHRONOLOGY ---")
    if config:
        logging.info("Configuration loaded successfully")
    else:
        logging.error("Configuration loading failed")
    print("Starting script: FETCH_GEOCHRONOLOGY")

    logging.info(f"Connecting to: {WIKI_URL}")
    try:
        response = requests.get(WIKI_URL, headers=HEADERS, timeout=15)
        response.raise_for_status()
        total_bytes = len(response.content)
        logging.info("Successfully connected to Wikipedia")
    except Exception as e:
        msg = f"Connection error: {e}"
        logging.error(msg)
        print(f"[ERROR] {msg}")
        return

    soup = BeautifulSoup(response.text, 'html.parser')
    table = soup.find('table', class_='sticky-header')
    if not table:
        logging.error("HTML ERROR: Geologic time table (sticky-header) not found")
        return

    rows = table.find_all('tr')
    
    # Сразу оставляем только те строки, которые являются данными (не пустые и не шапка)
    data_rows = [tr for tr in rows if tr.find_all(['td', 'th']) and not ("Start" in tr.get_text() and "Eon" in tr.get_text())]
    
    total_data = len(data_rows)
    # Матрицу строим по размеру чистых данных
    grid = [[None for _ in range(7)] for _ in range(total_data)]
    
    logging.info(f"HTML: Building virtual matrix for {total_data} data rows")

    # 1. ЗАПОЛНЕНИЕ МАТРИЦЫ
    r_idx = 0
    for tr in data_rows: # Итерируем только по данным
        cells = tr.find_all(['td', 'th'])

        c_idx = 0
        for cell in cells:
            while c_idx < 7 and grid[r_idx][c_idx] is not None:
                c_idx += 1
            if c_idx >= 7: break

            for sup in cell.find_all('sup'): sup.decompose()
            val = cell.get_text(" ", strip=True)
            
            rs = int(cell.get('rowspan', 1))
            cs = int(cell.get('colspan', 1))

            for r in range(rs):
                for c in range(cs):
                    if r_idx + r < len(grid) and c_idx + c < 7:
                        grid[r_idx + r][c_idx + c] = val
            c_idx += cs
        
        r_idx += 1
        # Счетчик в консоли (теперь будет [117/117])
        sys.stdout.write(f"\rBuilding matrix... [{r_idx}/{total_data}]")
        sys.stdout.flush()
        # Плавный прогресс
        time.sleep(0.005)

    print()
    logging.info("Matrix built successfully")

    # 2. ИЗВЛЕЧЕНИЕ И ОЧИСТКА
    final_results = []
    logging.info("Starting data extraction and cleaning")

    for row_data in grid:
        if not row_data or row_data[0] is None or "Eonothem" in row_data[0]: continue
        
        # Предварительная очистка имен
        eon = row_data[0] if is_clean_name(row_data[0]) else MISSING_VAL
        era = row_data[1] if is_clean_name(row_data[1]) else MISSING_VAL
        period = row_data[2] if is_clean_name(row_data[2]) else MISSING_VAL
        epoch = row_data[3] if is_clean_name(row_data[3]) else MISSING_VAL
        stage = row_data[4] if is_clean_name(row_data[4]) else MISSING_VAL
        age_raw = row_data[6]

        def clean_val(t):
            if not t or t == "-": return MISSING_VAL
            t = re.sub(r'\(.*?\)', '', t)
            t = t.replace("'", "").replace('"', '').replace('[', '').replace(']', '')
            if '/' in t:
                parts = [p.strip() for p in t.split('/')]
                for p in ["Early", "Middle", "Late"]:
                    if p in parts: return p
                return parts[-1]
            return t.strip()

        eon, era, period, epoch, stage = map(clean_val, [eon, era, period, epoch, stage])

        if stage.lower() == epoch.lower() or stage.lower() == period.lower(): stage = "-"
        if epoch.lower() == period.lower() or epoch.lower() == era.lower(): epoch = "-"
        if period.lower() == era.lower(): period = "-"

        age_val, uncertainty = MISSING_VAL, MISSING_VAL
        age_source = age_raw if any(c.isdigit() for c in age_raw) else " ".join(row_data)
        age_source = age_source.replace(',', '').replace('−', '-').replace('–', '-')

        age_match = re.search(r'(\d+\.?\d*)', age_source)
        if age_match: age_val = age_match.group(1)
        
        error_match = re.search(r'±\s*(\d+\.?\d*)', age_source)
        if error_match: uncertainty = error_match.group(1)

        if stage in ["Stage / Age", "Stage", "Age"]: continue
        if eon == "-" and era == "-" and stage == "-": continue
        
        if final_results and final_results[-1]['stage'] == stage and final_results[-1]['start_ma'] == age_val:
            continue

        item = {
            'eon': eon, 'era': era, 'period': period,
            'epoch': epoch, 'stage': stage, 
            'start_ma': age_val, 'uncertainty': uncertainty
        }
        final_results.append(item)
        logging.info(f"{period} | {epoch} | {stage} | {age_val}")

    # 3. ЗАВЕРШЕНИЕ И СОХРАНЕНИЕ
    sys.stdout.write('\r' + ' ' * 40 + '\r')
    sys.stdout.flush()
    print("Discovery completed.")
    logging.info("Discovery completed.")

    size_mb = total_bytes / (1024 * 1024)
    size_report = f"Total data downloaded: {size_mb:.2f} MB"
    print(size_report)
    logging.info(size_report)

    count_msg = f"Total geological units found: {len(final_results)}"
    print(count_msg)
    logging.info(count_msg)

    save_geodata_to_csv(final_results, GEO_OUTPUT_FILE)

    print("Script ended: FETCH_GEOCHRONOLOGY")

    logging.info("--- SCRIPT END: FETCH_GEOCHRONOLOGY ---")

def save_geodata_to_csv(results, filename):
    """Сохраняет геохронологию в CSV."""
    keys = ["eon", "era", "period", "epoch", "stage", "start_ma", "uncertainty"]
    try:
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=keys, delimiter=';')
            writer.writeheader()
            writer.writerows(results)
        
        path_msg = f"Data saved to {os.path.abspath(filename)}"
        print(path_msg)
        logging.info(path_msg)
    except Exception as e:
        err_msg = f"FILES: ERROR (CSV export failed: {e})"
        logging.error(err_msg)
        print(f"[ERROR] {err_msg}")

if __name__ == "__main__":
    fetch_geochronology()