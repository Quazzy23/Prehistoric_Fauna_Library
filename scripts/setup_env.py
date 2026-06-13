import sys
sys.dont_write_bytecode = True
import os
import re
import config

# 1. ОПРЕДЕЛЕНИЕ ПУТЕЙ
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPTS_DIR)
LOCAL_PATH = os.path.join(SCRIPTS_DIR, "local_settings.py")
    
# Пути к шаблонам
T_SETTINGS = os.path.join(BASE_DIR, "templates", "local_settings_template.txt")
T_WORKSPACE = os.path.join(BASE_DIR, "templates", "workspace_template.json")

def run_setup():
    print("Starting script: SETUP_ENV")
    
    # 2. СЧИТЫВАНИЕ ТЕКУЩИХ ЗНАЧЕНИЙ (если файл уже есть)
    user_values = {}
    if os.path.exists(LOCAL_PATH):
        try:
            with open(LOCAL_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    # Ищем строки вида KEY = VALUE
                    match = re.search(r'^([A-Z_]+)\s*=\s*(.*)', line)
                    if match:
                        key = match.group(1)
                        val = match.group(2).strip()
                        user_values[key] = val
        except Exception:
            pass

    # 4. ИНИЦИАЛИЗАЦИЯ LOCAL_SETTINGS.PY
    if not os.path.exists(LOCAL_PATH):
        if os.path.exists(T_SETTINGS):
            try:
                with open(T_SETTINGS, "r", encoding="utf-8") as f:
                    content = f.read()
                with open(LOCAL_PATH, "w", encoding="utf-8") as f:
                    f.write(content)
                print(f"Local settings created from template.")
            except Exception as e: print(f"[ERROR] Settings copy failed: {e}")
        else: print(f"[ERROR] Template not found: {T_SETTINGS}")
    else:
        print("Local settings file already exists.")

    # 5. ГЕНЕРАЦИЯ ФАЙЛА WORKSPACE
    workspace_path = os.path.join(BASE_DIR, "PFL_Project.code-workspace")
    appdata_logs = os.path.join(os.getenv('LOCALAPPDATA', ''), 'PFL_Library', 'logs').replace('\\', '/')

    if os.path.exists(T_WORKSPACE):
        try:
            with open(T_WORKSPACE, "r", encoding="utf-8") as f:
                ws_template = f.read()
            
            # Заполняем переменные в JSON-шаблоне через замену текста
            ws_content = ws_template.replace("{logs_path}", appdata_logs)
            ws_content = ws_content.replace("{storage_name}", config.STORAGE_BASE_NAME)
            
            with open(workspace_path, "w", encoding="utf-8") as f:
                f.write(ws_content)
            print(f"VS Code Workspace updated.")
        except Exception as e: print(f"[ERROR] Workspace generation failed: {e}")

    print("Script ended: SETUP_ENV\n")

if __name__ == "__main__":
    run_setup()