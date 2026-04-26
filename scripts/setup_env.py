import sys
sys.dont_write_bytecode = True
import os
import re

def run_setup():
    print("Starting script: SETUP_ENV")
    
    SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
    LOCAL_PATH = os.path.join(SCRIPTS_DIR, "local_settings.py")
    
    # 1. Считываем текущие значения из существующего файла (если он есть)
    user_values = {}
    if os.path.exists(LOCAL_PATH):
        try:
            with open(LOCAL_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    match = re.search(r'^([A-Z_]+)\s*=\s*(.*)', line)
                    if match:
                        key = match.group(1)
                        val = match.group(2).strip()
                        user_values[key] = val
        except Exception:
            pass

    # 2. ЭТАЛОННЫЙ ШАБЛОН (Твои комментарии и те самые заглушки)
    template_structure = [
        ("# [PFL LOCAL SETTINGS]", None),
        ("# This file is generated automatically. It is excluded from Git.", None),
        ("# Use it to configure your identity and local machine paths.", None),
        ("", None),
        ("# Your contact email for Wikipedia (helps avoid IP blocks)", "USER_EMAIL"),
        ("# Your nickname for production credits and 'claimed_by' field", "ARTIST_NAME"),
        ("# Full path to your Blender executable (e.g. C:\\Program Files\\...\\blender.exe)", "BLENDER_PATH"),
    ]

    # Дефолтные значения (Заглушки для новых пользователей)
    defaults = {
        "USER_EMAIL": '"Volk@.ru"',
        "ARTIST_NAME": '"YourNickname"',
        "BLENDER_PATH": r"r'C:\Program Files\Blender Foundation\Blender 4.0\blender.exe'"
    }

    # 3. Сборка контента (Слияние шаблона и данных пользователя)
    new_lines = []
    for comment, key in template_structure:
        if comment:
            new_lines.append(comment)
        
        if key:
            # Если у пользователя уже было значение — берем его, иначе — заглушку
            val = user_values.get(key, defaults[key])
            new_lines.append(f"{key} = {val}")
        elif not comment:
            new_lines.append("")

    final_content = "\n".join(new_lines).strip() + "\n"

    # 4. Проверка на изменения и запись
    if os.path.exists(LOCAL_PATH):
        with open(LOCAL_PATH, "r", encoding="utf-8") as f:
            if f.read() == final_content:
                print("Local settings are already up to date.")
                print(f"File path: {os.path.abspath(LOCAL_PATH)}")
                print("Script ended: SETUP_ENV\n")
                return

    with open(LOCAL_PATH, "w", encoding="utf-8") as f:
        f.write(final_content)
    
    print("Local settings synchronized successfully.")
    print(f"Local settings saved to: {os.path.abspath(LOCAL_PATH)}")
    print("Script ended: SETUP_ENV\n")

if __name__ == "__main__":
    run_setup()