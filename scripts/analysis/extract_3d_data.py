import os
import subprocess
import re
import logging
import sys

# --- ПУТИ ---
# УКАЖИ ЗДЕСЬ ПУТЬ К СВОЕМУ БЛЕНДЕРУ
BLENDER_PATH = r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
# УКАЖИ ПУТЬ К МОДЕЛИ
MODEL_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models", "Tyrannosaurus rex for scr.blend"))

# Настройка логов
LOG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "logs"))
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "extract_3d.log")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    filename=LOG_FILE,
    filemode='w',
    encoding='utf-8'
)

def extract_from_blender():
    logging.info("--- SCRIPT START: EXTRACT_3D_DATA ---")
    print("Starting script: EXTRACT_3D_DATA")
    print()

    if not os.path.exists(MODEL_PATH):
        msg = f"Model file not found: {MODEL_PATH}"
        logging.error(msg)
        print(f"[ERROR] {msg}")
        return

    logging.info(f"Opening Blender background for: {os.path.basename(MODEL_PATH)}")
    print(f"Processing model: {os.path.basename(MODEL_PATH)}")

    # ВНУТРЕННИЙ СКРИПТ ДЛЯ БЛЕНДЕРА (пишется прямо здесь как строка)
    # Этот код выполнится ВНУТРИ Блендера
    blender_internal_code = f"""
import bpy
import bmesh

def run():
    # Обновляем сцену, чтобы все расчеты были актуальны
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    
    spine = bpy.data.objects.get('Spine')
    body = bpy.data.objects.get('Body')
    
    spine_len = 0
    if spine and spine.type == 'CURVE':
        eval_spine = spine.evaluated_get(depsgraph)
        spine_len = eval_spine.data.splines[0].calc_length() * eval_spine.scale.x
        
    body_vol = 0
    if body and body.type == 'MESH':
        bm = bmesh.new()
        # Получаем финальный меш со всеми модификаторами
        eval_body = body.evaluated_get(depsgraph)
        mesh_data = eval_body.to_mesh()
        bm.from_mesh(mesh_data)
        
        # ЛЕЧЕНИЕ: Пересчитываем нормали наружу (как это делает Toolbox)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        
        # Применяем трансформацию объекта (масштаб в мире)
        bm.transform(body.matrix_world)
        
        # Считаем АБСОЛЮТНЫЙ объем (abs), чтобы вывернутые куски не вычитались
        body_vol = abs(bm.calc_volume())
        
        bm.free()
        eval_body.to_mesh_clear() # Очищаем память
        
    print(f"RESULT_SPINE:{{spine_len}}")
    print(f"RESULT_VOLUME:{{body_vol}}")

run()
"""

    # Запускаем Блендер в фоновом режиме
    # -b (background), -P (python code)
    try:
        process = subprocess.Popen([
            BLENDER_PATH,
            MODEL_PATH,
            "--background",
            "--python-expr", blender_internal_code
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        stdout, stderr = process.communicate()

        # Парсим результаты из вывода Блендера
        spine_match = re.search(r"RESULT_SPINE:(.*)", stdout)
        volume_match = re.search(r"RESULT_VOLUME:(.*)", stdout)

        if spine_match and volume_match:
            # Читаем как float и округляем до 6 знаков для проверки
            spine_len = round(float(spine_match.group(1)), 6)
            volume = round(float(volume_match.group(1)), 6)
            
            # ВЫВОД РЕЗУЛЬТАТОВ
            res_spine = f"Spine Length: {spine_len} m"
            res_vol = f"Body Volume: {volume} m3"
            
            print(res_spine)
            print(res_vol)
            logging.info(f"{os.path.basename(MODEL_PATH)}: {res_spine} | {res_vol}")
        else:
            logging.error("Could not parse data from Blender output. Check object names (Spine/Body).")
            print("[ERROR] Extraction failed. Check logs.")

    except Exception as e:
        logging.error(f"Failed to launch Blender: {e}")
        print(f"[ERROR] Blender launch failed: {e}")

    print()
    print("Script ended: EXTRACT_3D_DATA")
    logging.info("--- SCRIPT END: EXTRACT_3D_DATA ---")

if __name__ == "__main__":
    extract_from_blender()