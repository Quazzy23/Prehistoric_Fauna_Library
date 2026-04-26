import sys
sys.dont_write_bytecode = True
import json
import os

def get_info_template(genus, species, scientific_status="empty", data=None):
    """
    Финальный стандарт структуры файла info.txt v4.0.
    """
    if data is None:
        data = {}

    def v(key):
        # Ищем значение в разных вариантах написания (для совместимости)
        val = data.get(key)
        if val is None and key == 'mesh': val = data.get('model')
        if val is None and key == 'model_status': val = data.get('status') # старый маппинг
        
        if val in ["-", "None", None, "", "free"]: # "free" чистим, чтобы подставить дефолт ниже
            return ""
        return str(val).strip()

    # Логика определения статусов
    # Если в data есть научный статус - берем его, иначе из аргумента
    sci_stat = v('status') if v('status') else scientific_status
    # Статус модели: приоритет из файла, иначе по умолчанию 'free'
    m_stat = v('model_status') if v('model_status') else "free"

    template = (
        f"genus: {genus}\n"
        f"species: {species}\n"
        f"status: {sci_stat}\n"
        f"\n"
        f"model_status: {m_stat}\n"
        f"claimed_by: {v('claimed_by')}\n"
        f"\n"
        f"base_specimen: {v('base_specimen')}\n"
        f"scale_specimen: {v('scale_specimen')}\n"
        f"\n"
        f"skeletal: {v('skeletal')}\n"
        f"mesh: {v('mesh')}\n"
        f"texture: {v('texture')}\n"
        f"rig: {v('rig')}\n"
        f"\n"
        f"skeletal_approved_by: {v('skeletal_approved_by')}\n"
        f"mesh_approved_by: {v('mesh_approved_by')}\n"
        f"texture_approved_by: {v('texture_approved_by')}\n"
        f"rig_approved_by: {v('rig_approved_by')}\n"
        f"\n"
        f"notes: {v('notes')}\n"
    )
    return template


def get_user_email(default_email):
    """Подтягивает email из user_settings.json, если он есть."""
    # Путь к настройкам относительно этого файла
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "user_settings.json")
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get("user_email", default_email)
        except Exception:
            return default_email
    return default_email