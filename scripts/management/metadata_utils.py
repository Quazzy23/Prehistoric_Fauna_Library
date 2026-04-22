def get_info_template(genus, species, data=None):
    """
    Единый стандарт структуры файла info.txt.
    """
    if data is None:
        data = {}

    # Вспомогательная функция для чистки значений
    def v(key, default=""):
        val = data.get(key, default)
        if val in ["-", "None", None, ""]:
            return ""
        return str(val).strip()

    # Маппинг для поля mesh (поддержка старого формата 'model')
    mesh_val = v('mesh') if v('mesh') else v('model')
    status_val = v('status') if v('status') else "free"

    # ИТОГОВЫЙ ШАБЛОН (Единственное место для правок структуры)
    template = (
        f"genus: {genus}\n"
        f"species: {species}\n"
        f"\n"
        f"status: {status_val}\n"
        f"claimed_by: {v('claimed_by')}\n"
        f"\n"
        f"base_specimen: {v('base_specimen')}\n"
        f"scale_specimen: {v('scale_specimen')}\n"
        f"skeletal: {v('skeletal')}\n"
        f"mesh: {mesh_val}\n"
        f"texture: {v('texture')}\n"
        f"rig: {v('rig')}\n"
        f"notes: {v('notes')}\n"
    )
    return template