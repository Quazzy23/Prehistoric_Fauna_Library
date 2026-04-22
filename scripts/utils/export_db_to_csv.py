import sqlite3
import csv
import os
import sys

# [!] ИСПРАВЛЕНИЕ ПУТИ: Поднимаемся из /scripts/utils/ в /scripts/
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

def export_dinosaurs_to_csv():
    # BASE_DIR теперь указывает на корень проекта (Prehistoric_Fauna_Library)
    BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    
    # [!] ИСПРАВЛЕНИЕ: БД лежит в папке /database/
    DB_PATH = os.path.join(BASE_DIR, "database", config.DB_NAME)
    OUTPUT_CSV = os.path.join(BASE_DIR, "data", "exports", "dinosaurs_for_models.csv")
    
    print(f"Looking for database at: {DB_PATH}")
    
    if not os.path.exists(DB_PATH):
        print(f"Error: Database not found at {DB_PATH}")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute(f"SELECT genus, species FROM {config.TABLE_SPECIES} ORDER BY genus, species")
        rows = cursor.fetchall()
        
        os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
        
        with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerow(['genus', 'species'])
            writer.writerows(rows)
            
        print(f"Success! Exported {len(rows)} species to {OUTPUT_CSV}")
        conn.close()
        
    except Exception as e:
        print(f"Database error: {e}")

if __name__ == "__main__":
    export_dinosaurs_to_csv()