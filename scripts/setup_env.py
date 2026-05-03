import sys
sys.dont_write_bytecode = True
import os
import re

def run_setup():
    print("Starting script: SETUP_ENV")
    
    # 1. ОПРЕДЕЛЕНИЕ ПУТЕЙ
    SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
    BASE_DIR = os.path.dirname(SCRIPTS_DIR)
    LOCAL_PATH = os.path.join(SCRIPTS_DIR, "local_settings.py")
    
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

    # 3. ЭТАЛОННЫЙ ШАБЛОН (Структура и комментарии)
    template_structure = [
        ("# [PFL LOCAL SETTINGS]", None),
        ("# This file is generated automatically. It is excluded from Git.", None),
        ("# Use it to configure your identity and local machine paths.", None),
        ("", None),
        ("# Your contact email for Wikipedia (helps avoid IP blocks)", "USER_EMAIL"),
        ("# Your nickname for production credits and 'claimed_by' field", "USER_NAME"),
        ("# List of authorized curators who can approve stages", "CURATORS"),
        ("# Full path to your Blender executable (e.g. C:\\Program Files\\...\\blender.exe)", "BLENDER_PATH"),
    ]

    # Значения по умолчанию (заглушки)
    defaults = {
        "USER_EMAIL": '"udot@.dot"',
        "USER_NAME": '"PedrPedrovichPedrov_naebashil_mnogodrov"',
        "CURATORS": '["Quazzy", "Warpath"]',
        "BLENDER_PATH": r"r'C:\Program Files\Blender Foundation\Blender 5.1\blender.exe'"
    }

    # 4. СБОРКА КОНТЕНТА LOCAL_SETTINGS.PY
    new_lines = []
    for comment, key in template_structure:
        if comment:
            new_lines.append(comment)
        
        if key:
            # Берем старое значение пользователя, если оно было, иначе дефолт
            val = user_values.get(key, defaults[key])
            new_lines.append(f"{key} = {val}")
        elif not comment:
            new_lines.append("")

    final_content = "\n".join(new_lines).strip() + "\n"

    # Запись local_settings.py (только если есть изменения)
    write_settings = True
    if os.path.exists(LOCAL_PATH):
        with open(LOCAL_PATH, "r", encoding="utf-8") as f:
            if f.read() == final_content:
                print("Local settings are already up to date.")
                write_settings = False

    if write_settings:
        with open(LOCAL_PATH, "w", encoding="utf-8") as f:
            f.write(final_content)
        print(f"Local settings synchronized: {os.path.abspath(LOCAL_PATH)}")

    # 5. ГЕНЕРАЦИЯ ФАЙЛА WORKSPACE ДЛЯ VS CODE
    workspace_path = os.path.join(BASE_DIR, "PFL.code-workspace")
    # Динамически получаем AppData текущего юзера для конфига VS Code
    appdata_path = os.path.join(os.getenv('LOCALAPPDATA', ''), 'PFL_Library', 'logs').replace('\\', '/')
    
    workspace_content = f"""{{
    "folders": [
        {{
            "name": "Prehistoric_Fauna_Library",
            "path": "."
        }},
        {{
            "name": "logs",
            "path": "{appdata_path}"
        }}
    ],
    "settings": {{
        "files.exclude": {{
            "PFL.code-workspace": true
        }}
    }}
}}"""

    try:
        with open(workspace_path, "w", encoding="utf-8") as wf:
            wf.write(workspace_content)
        print(f"VS Code Workspace updated: {os.path.abspath(workspace_path)}")
    except Exception as e:
        print(f"[ERROR] Failed to generate workspace: {e}")

    print("Script ended: SETUP_ENV\n")

if __name__ == "__main__":
    run_setup()