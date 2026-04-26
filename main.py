import os
import sys
import json
from pathlib import Path
from datetime import datetime
from translator import Plan, validate_plan_full, translate_to_cadquery, normalize_plan_dict
from groq_client import plan_model
from build123d import *
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import io
import base64
import traceback
from werkzeug.utils import secure_filename

app = Flask(__name__)
CORS(app)

# Конфигурация
UPLOAD_FOLDER = Path("uploads")
CODE_FOLDER = Path("generated_code")
EXPORT_FOLDER = Path("exported_models")
ALLOWED_EXTENSIONS = {'txt', 'md'}

for folder in [UPLOAD_FOLDER, CODE_FOLDER, EXPORT_FOLDER]:
    folder.mkdir(parents=True, exist_ok=True)

def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)

def generate_model_from_description(description: str):
    """Генерирует модель по текстовому описанию"""
    print(f"\n📝 Описание: {description}\n")
    
    try:
        print("[1/4] Планирование модели через Groq...")
        plan_dict = plan_model(description)
        plan_dict = normalize_plan_dict(plan_dict)
        
        print("[2/4] Валидация JSON плана...")
        try:
            plan = Plan(**plan_dict)
        except Exception as e:
            print(f"❌ Ошибка валидации JSON: {e}")
            return {"error": f"Ошибка валидации JSON: {e}", "plan": plan_dict}
        
        errors = validate_plan_full(plan)
        if errors:
            print("❌ Ошибки в плане:")
            for err in errors:
                print(f"  • {err}")
            return {"error": f"Ошибки в плане: {errors}", "plan": plan_dict}
        print("  ✓ План валиден")
        
        print("[3/4] Трансляция в код build123d...")
        build123d_code = translate_to_cadquery(plan, description)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        code_file = CODE_FOLDER / f"model_{timestamp}.py"
        with open(code_file, "w", encoding="utf-8") as f:
            f.write(build123d_code)
        print(f"  ✓ Код сохранён в {code_file}")
        
        print("[4/4] Выполнение и экспорт...")
        exec_globals = {}
        exec(build123d_code, exec_globals)
        result = exec_globals.get("result")
        
        if result is None:
            return {"error": "Модель не сгенерирована", "code": build123d_code}
        
        # Приводим результат к Solid, если это ShapeList или Compound
        from build123d import Solid, Compound, Part, ShapeList
        
        if isinstance(result, ShapeList):
            print("  → Обнаружен ShapeList, объединяем в одно тело...")
            if len(result) == 0:
                return {"error": "Пустой ShapeList", "code": build123d_code}
            unified = result[0]
            for body in result[1:]:
                try:
                    unified = unified + body
                except:
                    pass
            result = unified
        
        if hasattr(result, 'solids'):
            solids = result.solids()
            if solids:
                result = solids[0]
        
        if hasattr(result, 'wrapped') and not hasattr(result, 'solids'):
            try:
                result = Solid(result.wrapped)
            except:
                pass
        
        # Экспорт в STL и STEP
        stl_file = EXPORT_FOLDER / f"model_{timestamp}.stl"
        step_file = EXPORT_FOLDER / f"model_{timestamp}.step"
        
        try:
            export_stl(result, str(stl_file))
        except Exception as e:
            print(f"  ✗ Ошибка экспорта STL: {e}")
            try:
                from OCP.StlAPI import StlAPI_Writer
                writer = StlAPI_Writer()
                writer.SetASCIIMode(False)
                writer.Write(result.wrapped, str(stl_file))
                print(f"  ✓ STL файл (через OCP): {stl_file}")
            except:
                stl_file = None
        
        try:
            export_step(result, str(step_file))
        except Exception as e:
            print(f"  ✗ Ошибка экспорта STEP: {e}")
            step_file = None
        
        # Чтение файлов в base64 для отправки клиенту
        result_data = {
            "success": True,
            "timestamp": timestamp,
            "code": build123d_code,
            "plan": plan_dict,
            "stl_base64": None,
            "step_base64": None
        }
        
        if stl_file and stl_file.exists():
            with open(stl_file, "rb") as f:
                result_data["stl_base64"] = base64.b64encode(f.read()).decode('utf-8')
        
        if step_file and step_file.exists():
            with open(step_file, "rb") as f:
                result_data["step_base64"] = base64.b64encode(f.read()).decode('utf-8')
        
        return result_data
        
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        traceback.print_exc()
        return {"error": str(e), "traceback": traceback.format_exc()}

# API Routes
@app.route('/')
def index():
    return send_file('index.html')

@app.route('/api/generate', methods=['POST'])
def generate():
    """Генерирует 3D модель по описанию"""
    data = request.get_json()
    description = data.get('description', '')
    
    if not description or not description.strip():
        return jsonify({"error": "Пустое описание"}), 400
    
    result = generate_model_from_description(description)
    return jsonify(result)

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "message": "CAD Agent is running"})

@app.route('/api/download/<filename>')
def download_file(filename):
    """Скачивание файла модели"""
    file_path = EXPORT_FOLDER / filename
    if file_path.exists():
        return send_file(file_path, as_attachment=True)
    return jsonify({"error": "File not found"}), 404

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
