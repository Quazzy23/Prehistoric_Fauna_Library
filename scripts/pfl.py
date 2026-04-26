import sys
sys.dont_write_bytecode = True
import os
import json

# [1] ПОДГОТОВКА ПУТЕЙ
# Скрипт в /scripts/, поэтому корень на один уровень выше
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CATALOG_PATH = os.path.join(BASE_DIR, "data", "exports", "species_catalog.json")
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "user_settings.json")

def load_settings():
    """Загружает профиль пользователя из user_settings.json."""
    if not os.path.exists(SETTINGS_PATH):
        # Если настроек нет, используем гостевой профиль
        return {"artist_name": "Guest", "user_email": "placeholder@example.com"}
    try:
        with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] Failed to read settings: {e}")
        return None

def load_catalog():
    """Загружает меню видов из компактного JSON."""
    if not os.path.exists(CATALOG_PATH):
        print(f"[ERROR] Catalog not found! Run generate_artist_catalog.py first.")
        return []
    try:
        with open(CATALOG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] Failed to read catalog: {e}")
        return []

def check_species():
    """Интерактивный поиск статуса видов по роду."""
    catalog = load_catalog()
    if not catalog: return

    print("\n" + "="*45)
    search_genus = input("Enter Genus name to check: ").strip()
    print("="*45)
    
    # Поиск без учета регистра
    results = [s for s in catalog if s['genus'].lower() == search_genus.lower()]

    if not results:
        print(f"\n[-] No species found for genus: {search_genus}")
        return

    print(f"\nFound {len(results)} species for '{results[0]['genus']}':")
    
    for item in results:
        # Маппинг иконок статуса
        status_icons = {
            "free": "🟢",
            "busy": "🟡",
            "review": "🔵",
            "ready": "✅",
            "needs_fix": "🔴"
        }
        icon = status_icons.get(item['m_status'].lower(), "⚪")
        
        # Вывод данных (используем новые короткие ключи)
        print(f"\n{icon} {item['genus']} {item['species']}")
        print(f"   Scientific Status: {item['status']}")
        print(f"   Model Status:      {item['m_status'].upper()}")
        print(f"   Next Stage:        {item['stage'].upper()}")
        
        if item['user']:
            print(f"   Artist:            @{item['user']}")
        else:
            print(f"   Artist:            (None - FREE)")

    print("\n" + "="*45)

def main():
    settings = load_settings()
    if not settings: return

    user = settings.get('artist_name', 'Guest')
    
    while True:
        print(f"\n>>> PFL ASSET MANAGER | User: {user} <<<")
        print("1. [CHECK] Search species catalog")
        print("0. [EXIT]")
        
        choice = input("\nAction > ").strip()

        if choice == '1':
            check_species()
        elif choice == '0':
            print("Closing pipeline. See you!")
            break
        else:
            print("Invalid command. Please select 1 or 0.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nProcess interrupted. Goodbye!")
        sys.exit(0)