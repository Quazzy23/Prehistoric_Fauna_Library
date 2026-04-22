import requests
from bs4 import BeautifulSoup
import re

def clean_formation_strict(text):
    """Оставляет только чистое название формации (напр. 'Morrison Formation')."""
    # 1. Ищем паттерн: Слово с большой буквы + слово Formation
    # (На случай двойных названий типа 'Hell Creek Formation')
    match = re.search(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+Formation)', text)
    
    if match:
        found = match.group(1).strip()
        
        # 2. Список слов-модификаторов, которые нужно удалить
        modifiers = [
            'Lower', 'Upper', 'Middle', 'Late', 'Early', 
            'High', 'Low', 'Uppermost', 'Basal', 'Member'
        ]
        
        # Убираем эти слова, если они попали в захват
        for mod in modifiers:
            found = re.sub(rf'\b{mod}\b', '', found, flags=re.IGNORECASE).strip()
        
        return found
    
    # Если 'Formation' не найдено, пробуем просто выцепить главное, если там есть запятые
    return text.split(',')[0].split('(')[0].strip()

def parse_paleo_site():
    url = "http://paleofile.com/Dinosaurs/Sauropoda/Brontosaurus.asp"
    headers = {'User-Agent': 'Mozilla/5.0'}

    try:
        response = requests.get(url, headers=headers)
        response.encoding = 'iso-8859-1'
        soup = BeautifulSoup(response.text, 'html.parser')

        # Извлекаем РОД
        genus_name = "Brontosaurus"
        genus_tag = soup.find(lambda t: t.name == 'p' and 'Genus:' in t.text)
        if genus_tag:
            genus_match = re.search(r"Genus:\s*([a-zA-Z]+)", genus_tag.get_text())
            if genus_match:
                genus_name = genus_match.group(1)

        print(f"РОД: {genus_name}")
        print("=" * 60)

        all_elements = soup.find_all(['p', 'blockquote', 'div'])

        for i, element in enumerate(all_elements):
            text = element.get_text(separator=" ", strip=True)
            
            if "Species:" in text[:15]: 
                species_part = text[text.find("Species:"):].replace("Species:", "").strip()
                main_info = re.split(r'=|Etymology:|Holotype:|Horizon:|Material:', species_part)[0].strip()
                
                words = main_info.split()
                if not words: continue
                species_name = words[0]
                
                years = re.findall(r'\d{4}', main_info)
                if years:
                    year = years[0]
                    name_end_pos = main_info.find(species_name) + len(species_name)
                    year_start_pos = main_info.find(year)
                    author = main_info[name_end_pos:year_start_pos].strip()
                    author = author.replace('(', '').replace(')', '').strip().rstrip(',')
                else:
                    year = "Unknown"; author = "Unknown"

                print(f"\nВИД: {species_name}")
                print(f"  Ученый: {author}")
                print(f"  Год:    {year}")

                # ИЩЕМ И ЧИСТИМ ГОЛОТИП И ФОРМАЦИЮ
                holotype = "Не найден"
                formation = "Не найдена"

                for j in range(i + 1, len(all_elements)):
                    next_text = all_elements[j].get_text(separator=" ", strip=True)
                    if "___" in next_text or "Species:" in next_text[:15]:
                        break
                    if "Referred material" in next_text:
                        break

                    if next_text.startswith("Holotype:"):
                        holotype = next_text.replace("Holotype:", "").strip().split('(')[0].strip()
                    
                    if next_text.startswith("Horizon:"):
                        raw_form = next_text.replace("Horizon:", "").strip()
                        formation = clean_formation_strict(raw_form)

                print(f"  Голотип:  {holotype}")
                print(f"  Формация: {formation}")
                print("-" * 60)

    except Exception as e:
        print(f"Ошибка: {e}")

if __name__ == "__main__":
    parse_paleo_site()