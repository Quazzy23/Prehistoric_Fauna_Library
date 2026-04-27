import sys
sys.dont_write_bytecode = True
import os
import json
import time

# [1] ПОДГОТОВКА ПУТЕЙ
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path: sys.path.append(SCRIPTS_DIR)
import config
import local_settings

try:
    from management import generate_artist_catalog # type: ignore
except ImportError:
    import generate_artist_catalog # type: ignore

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MODELS_ROOT = os.path.join(BASE_DIR, "models")
CATALOG_PATH = os.path.join(BASE_DIR, "data", "exports", "species_catalog.json")

def get_input(prompt):
    """Глобальный обработчик ввода с функцией мгновенного выхода."""
    val = input(prompt).strip().lower()
    if val in ['0', 'exit', 'quit']:
        print("\nClosing pipeline. See you.\n")
        sys.exit(0)
    return val

def get_optional_note():
    """Запрашивает необязательный комментарий, игнорируя пустой ввод."""
    note = input("You can add a comment if you want here > ").strip()
    return note if note else None

def load_catalog():
    if not os.path.exists(CATALOG_PATH): return []
    try:
        with open(CATALOG_PATH, 'r', encoding='utf-8') as f: return json.load(f)
    except: return []

def save_catalog_silently(catalog):
    try:
        with open(CATALOG_PATH, 'w', encoding='utf-8') as jf:
            jf.write("[\n")
            for i, entry in enumerate(catalog):
                line = json.dumps(entry, ensure_ascii=False)
                comma = "," if i < len(catalog) - 1 else ""
                jf.write(f"    {line}{comma}\n")
            jf.write("]")
        return True
    except: return False

def update_info_file(genus, full_name, artist, action, stage=None, note=None):
    info_path = os.path.join(MODELS_ROOT, genus, full_name, f"{full_name} info.txt")
    if not os.path.exists(info_path): return False
    try:
        with open(info_path, 'r', encoding='utf-8') as f: lines = f.readlines()
        
        # 1. Сначала узнаем, кто записан как текущий владелец (claimed_by)
        current_owner = ""
        for line in lines:
            if line.startswith("claimed_by:"):
                current_owner = line.split(":", 1)[1].strip()
                break

        # 2. Переписываем файл
        with open(info_path, 'w', encoding='utf-8') as f:
            for line in lines:
                if line.startswith("model_status:"):
                    if action == 'claim': f.write("model_status: busy\n")
                    elif action == 'submit': f.write("model_status: review\n")
                    elif action == 'approve':
                        f.write(f"model_status: {'finished' if stage == 'rig' else 'free'}\n")
                    elif action == 'reject': f.write("model_status: needs_fix\n")
                    elif action == 'release':
                        # Если автор этапа уже был вписан (т.е. это возврат из needs_fix), статус остается needs_fix
                        has_author = False
                        for l in lines:
                            if stage and l.startswith(f"{stage}:") and l.split(":", 1)[1].strip():
                                has_author = True; break
                        f.write(f"model_status: {'needs_fix' if has_author else 'free'}\n")
                
                elif line.startswith("claimed_by:"):
                    # Очищаем только при полной отмене (release) или финальном одобрении (approve)
                    if action in ['release', 'approve']: f.write("claimed_by: \n")
                    elif action == 'claim': f.write(f"claimed_by: {artist}\n")
                    else: f.write(line) # При submit и reject автор ОСТАЕТСЯ в claimed_by
                
                # Вписываем автора в строку этапа ТОЛЬКО ПРИ ОДОБРЕНИИ
                elif stage and line.startswith(f"{stage}:") and action == 'approve':
                    f.write(f"{stage}: {current_owner}\n")
                
                elif stage and line.startswith(f"{stage}_approved_by:") and action == 'approve':
                    f.write(f"{stage}_approved_by: {artist}\n")
                
                elif note and line.startswith("notes:"):
                    f.write(line)
                    # Добавляем тип действия в квадратных скобках
                    timestamp = time.strftime('%Y-%m-%d %H:%M')
                    f.write(f"{timestamp} [{artist}] [{action.upper()}]: {note}\n")
                else:
                    f.write(line)
        return True
    except: return False

def format_sp_name(item):
    return f"{item['genus'].capitalize()} {item['species']}"

def print_species_card(item):
    status_icons = {"free": "🟢", "busy": "🟡", "review": "🔵", "finished": "⚪", "needs_fix": "🔴"}
    m_stat = item['m_status'].lower()
    icon = status_icons.get(m_stat, "⚪")
    
    print(f"\n{icon} {format_sp_name(item)} \t ({item['status']})")
    print(f"   status: {m_stat}")
    if m_stat != "finished":
        print(f"   stage:  {item['stage'].lower()}")
    
    if item['user']:
        # Если работа сдана или требует правок — пишем 'author', если просто в работе — 'claimed_by'
        label = "author" if m_stat in ["review", "needs_fix"] else "claimed_by"
        print(f"   {label}:     @{item['user']}")

def review_mode(catalog, user):
    if user not in getattr(local_settings, 'CURATORS', []):
        print(f"[-] Access Denied. @{user} is not a registered curator.\n"); return
    
    pending = [s for s in catalog if s['m_status'].lower() == 'review']
    if not pending: print("[-] No assets waiting for review.\n"); return

    print("\nASSETS WAITING FOR REVIEW:")
    for item in pending: print_species_card(item)
    print() # Отступ после списка

    while True:
        raw_input = get_input("Enter FULL NAME to approve > ").strip()
        target = raw_input.lower()
        # Если введена любая системная команда - возвращаем её в main для "прыжка"
        if target in ['-', 'back', '*', 's', '!', 'release', 'submit', 'review']: 
            if target in ['-', 'back']: print() # Красивый отступ для возврата
            return target
        
        match = next((s for s in pending if f"{s['genus']} {s['species']}".lower() == target), None)
        if not match: print(f"[-] '{raw_input}' not found in review list.\n"); continue

        display_name = format_sp_name(match)
        current_stage_name = match['stage'].upper()
        ans = get_input(f"Approve {current_stage_name} for '{display_name}'? (y/n) > ")
        
        if ans == 'y':
            user_note = get_optional_note()
            if update_info_file(match['genus'], display_name, user, 'approve', match['stage'], user_note):
                # ... (здесь твой старый код переключения стадий) ...
                stages_flow = ["skeletal", "mesh", "texture", "rig", "finished"]
                curr_stage = match['stage']
                try:
                    next_idx = stages_flow.index(curr_stage) + 1
                    match['stage'] = stages_flow[next_idx]
                except: pass
                match['m_status'] = "finished" if curr_stage == "rig" else "free"
                match['user'] = ""
                save_catalog_silently(catalog)
                print_species_card(match)
                print(f"\n[+] {current_stage_name} approved. Asset is now {match['m_status'].upper()}.\n")
                if match['m_status'] == "finished":
                    print("THE MODEL IS READY! HOORAY! REX✨\n")
                return 
            else: print(f"[-] Error updating info file.\n")
            
        elif ans == 'n':
            # ЛОГИКА ОТКЛОНЕНИЯ С ЦИКЛОМ ПРОВЕРКИ ПУСТОТЫ
            while True:
                reason = input("Enter reason for rejection (note) > ").strip()
                if not reason:
                    confirm_empty = get_input("Are you sure you want to set NEEDS_FIX status without comments? (y/n) > ")
                    if confirm_empty == 'y':
                        note_to_save = None
                        break
                    else:
                        continue # Возврат к вводу причины
                else:
                    note_to_save = reason
                    break
            
            if update_info_file(match['genus'], display_name, user, 'reject', match['stage'], note_to_save):
                match['m_status'] = "needs_fix"
                save_catalog_silently(catalog)
                print_species_card(match)
                if note_to_save:
                    print(f"\n[!] Status changed to NEEDS_FIX. Note added to info.txt.\n")
                else:
                    print(f"\n[!] Status changed to NEEDS_FIX. No notes was added.\n")
                return
            else: print(f"[-] Error updating info file.\n")
            
        else: 
            print("[-] Action cancelled.\n")
            return

def release_my_claims(catalog, user):
    my_tasks = [s for s in catalog if s['user'].lower() == user.lower() and s['m_status'].lower() == 'busy']
    if not my_tasks: print("[-] You don't have any active claims.\n"); return

    print("\nYOUR ACTIVE CLAIMS:")
    for item in my_tasks: print_species_card(item)
    print() # Отступ после списка
        
    while True:
        raw_input = get_input("Enter FULL NAME to release > ").strip()
        target = raw_input.lower()
        # Если введена любая системная команда - возвращаем её в main для "прыжка"
        if target in ['-', 'back', '*', 's', '!', 'release', 'submit', 'review']: 
            if target in ['-', 'back']: print() # Красивый отступ для возврата
            return target
        if target == '*': 
            for item in my_tasks: print_species_card(item)
            print(); continue
            
        match = next((s for s in my_tasks if f"{s['genus']} {s['species']}".lower() == target), None)
        if not match: print(f"[-] '{raw_input}' is not in your claims list.\n"); continue
            
        display_name = format_sp_name(match)
        if get_input(f"Release '{display_name}'? (y/n) > ") == 'y':
            user_note = get_optional_note()
            if update_info_file(match['genus'], display_name, user, 'release', note=user_note):
                match['m_status'], match['user'] = 'free', ''
                save_catalog_silently(catalog)
                print_species_card(match)
                print(f"\n[*] '{display_name}' successfully released.\n")
                return 
            else: print(f"[-] Error.\n")
        else: 
            print("[-] Action cancelled.\n")
            return

def submit_my_work(catalog, user):
    my_tasks = [s for s in catalog if s['user'].lower() == user.lower() and s['m_status'].lower() == 'busy']
    if not my_tasks: print("[-] You don't have any active assets to submit.\n"); return

    print("\nYOUR ASSETS READY FOR SUBMISSION:")
    for item in my_tasks: print_species_card(item)
    print() # Отступ после списка
        
    while True:
        raw_input = get_input("Enter FULL NAME to submit > ").strip()
        target = raw_input.lower()
        # Если введена любая системная команда - возвращаем её в main для "прыжка"
        if target in ['-', 'back', '*', 's', '!', 'release', 'submit', 'review']: 
            if target in ['-', 'back']: print() # Красивый отступ для возврата
            return target
        if target == '*': 
            print("\nYOUR ASSETS READY FOR SUBMISSION:")
            for item in my_tasks: print_species_card(item)
            print(); continue
            
        match = next((s for s in my_tasks if f"{s['genus']} {s['species']}".lower() == target), None)
        if not match: print(f"[-] '{raw_input}' is not in your assets to submit list.\n"); continue
            
        display_name = format_sp_name(match)
        current_stage = match['stage'].upper()
        if get_input(f"Submit {current_stage} for '{display_name}' for review? (y/n) > ") == 'y':
            user_note = get_optional_note()
            if update_info_file(match['genus'], display_name, user, 'submit', match['stage'], user_note):
                match['m_status'] = 'review'
                save_catalog_silently(catalog)
                print_species_card(match)
                print(f"\n[+] Successfully submitted. Waiting for review.\n")
                return 
            else: print(f"[-] Error.\n")
        else: 
            print("[-] Action cancelled.\n")
            return

def main():
    user = local_settings.ARTIST_NAME
    catalog = load_catalog()
    print(f"\n>>> PFL ASSET MANAGER \t Hello, {user}! :)")
    print(f"0 / exit \n- / back \n* / release \ns / submit \n! / review\n")
    
    last_results = [] 
    jump_action = None # Переменная для мгновенного перехода

    while True:
        # Если из подменю пришла команда, используем её, иначе просим ввод
        if jump_action:
            action = jump_action
            jump_action = None
        else:
            prompt = "Enter command or genus name > " if not last_results else "Enter command, genus or species name > "
            action = get_input(prompt)

        if action in ['-', 'back']: 
            last_results = []
            continue 

        # Команды теперь сохраняют результат (возможный переход) в jump_action
        if action in ['*', 'release']: 
            jump_action = release_my_claims(catalog, user)
            last_results = []
            continue
            
        if action in ['s', 'submit']: 
            jump_action = submit_my_work(catalog, user)
            last_results = []
            continue
            
        if action in ['!', 'review']: 
            jump_action = review_mode(catalog, user)
            last_results = []
            continue

        # Глобальные фильтры (all ...)
        if action.startswith("all"):
            parts = action.split()
            filtered_list = catalog
            if len(parts) > 1:
                for part in parts[1:]:
                    if part in ["free", "busy", "review", "finished", "needs_fix"]:
                        filtered_list = [s for s in filtered_list if s['m_status'].lower() == part]
                    elif part in ["skeletal", "mesh", "texture", "rig"]:
                        filtered_list = [s for s in filtered_list if s['stage'].lower() == part]
            
            if not filtered_list:
                print(f"[-] No species match these filters: {action}\n")
            else:
                for item in filtered_list: print_species_card(item)
                print()
                last_results = filtered_list 
            continue

        species_match = None
        if last_results:
            species_match = next((s for s in last_results if s['species'].lower() == action), None)
        if not species_match:
            species_match = next((s for s in catalog if f"{s['genus']} {s['species']}".lower() == action), None)

        if species_match:
            display_name = format_sp_name(species_match)
            m_status = species_match['m_status'].lower()
            if m_status in ['free', 'needs_fix']:
                if m_status == 'needs_fix' and species_match['user']:
                    if user.lower() != species_match['user'].lower():
                        print(f"\n[!] WARNING: This asset was previously worked on by @{species_match['user']}.")
                        if get_input("Are you sure you want to take it? (y/n) > ") != 'y':
                            print("[-] Claim cancelled.\n"); continue

                current_stage = species_match['stage'].upper()
                if get_input(f"Claim {current_stage} for '{display_name}'? (y/n) > ") == 'y':
                    user_note = get_optional_note() # Спрашиваем заметку
                    if update_info_file(species_match['genus'], display_name, user, 'claim', species_match['stage'], user_note):
                        species_match['m_status'], species_match['user'] = 'busy', user
                        save_catalog_silently(catalog)
                        print_species_card(species_match)
                        print(f"\n[+] '{display_name}' successfully claimed. Have a good work! :)\n")
                        last_results = [] 
                else: print("[-] Claim cancelled.\n")
            else:
                status_text = m_status
                owner_info = f"by @{species_match['user']}" if species_match['user'] else "and waiting for review"
                if m_status == 'finished': owner_info = "(complete)"
                print(f"[-] '{display_name}' is {status_text} {owner_info}\n")
            continue

        results = [s for s in catalog if s['genus'].lower() == action]
        if not results: print(f"[-] No results for '{action}'\n"); continue
        last_results = results
        for item in results: print_species_card(item)
        print()

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: print("\n\nProcess interrupted. Goodbye."); sys.exit(0)