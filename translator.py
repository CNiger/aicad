from pydantic import BaseModel, validator
from typing import List, Literal, Union, Dict, Any, Optional
import re

# ---------- Pydantic модели с поддержкой pos ----------
class Rect(BaseModel):
    rect: List[float]
    pos: Optional[List[float]] = None

class Circle(BaseModel):
    circle: float
    pos: Optional[List[float]] = None

class Polygon(BaseModel):
    polygon: Dict[str, List[List[float]]]
    pos: Optional[List[float]] = None

Primitive = Union[Rect, Circle, Polygon]

class Sketch(BaseModel):
    reference: Literal["plane", "face"]
    plane: Literal["XY", "XZ", "YZ"] | None = None
    offset: float | None = None
    face: Literal["top", "bottom", "front", "back", "left", "right"] | None = None
    primitives: List[Primitive] = []

    @validator('offset')
    def offset_required_for_plane(cls, v, values):
        if values.get('reference') == 'plane' and v is None:
            raise ValueError('offset обязателен для reference="plane"')
        return v

    @validator('face')
    def face_required_for_face(cls, v, values):
        if values.get('reference') == 'face' and v is None:
            raise ValueError('face обязателен для reference="face"')
        return v

    @validator('plane')
    def plane_required_for_plane(cls, v, values):
        if values.get('reference') == 'plane' and v is None:
            raise ValueError('plane обязателен для reference="plane"')
        return v

class LoftOperation(BaseModel):
    type: Literal["loft"]
    mode: Literal["add", "cut"]
    sketches: List[Sketch]
    next_face: Literal["top", "bottom", "front", "back", "left", "right", "none"]

    @validator('sketches')
    def exactly_two_sketches(cls, v):
        if len(v) != 2:
            raise ValueError('Каждый лофт должен содержать ровно 2 скетча')
        return v

class Plan(BaseModel):
    operations: List[LoftOperation]

# ---------- Валидация ----------
def count_vertices(primitive: Primitive) -> int:
    if isinstance(primitive, Rect) or isinstance(primitive, Circle):
        return 4
    elif isinstance(primitive, Polygon):
        return len(primitive.polygon["points"])
    return 0

def validate_primitive_compatibility(sketch1: Sketch, sketch2: Sketch) -> bool:
    if not sketch1.primitives or not sketch2.primitives:
        return True
    v1 = count_vertices(sketch1.primitives[0])
    v2 = count_vertices(sketch2.primitives[0])
    return v1 == v2

def validate_plan_full(plan: Plan) -> List[str]:
    errors = []
    first_op = plan.operations[0]
    if first_op.sketches[0].reference != "plane":
        errors.append("Первый скетч первой операции должен быть от плоскости")
    if first_op.sketches[0].plane != "XY" or first_op.sketches[0].offset != 0:
        errors.append("Первый скетч первой операции должен быть XY offset 0")
    
    for i, op in enumerate(plan.operations):
        for j, sk in enumerate(op.sketches):
            if not sk.primitives:
                errors.append(f"Операция {i+1}, скетч {j+1}: отсутствуют примитивы")
        if not validate_primitive_compatibility(op.sketches[0], op.sketches[1]):
            errors.append(f"Операция {i+1}: примитивы несовместимы по числу вершин")
    
    always_existing_faces = {"top", "bottom", "front", "back", "left", "right"}
    existing_faces = set(always_existing_faces)
    for i, op in enumerate(plan.operations):
        for sk in op.sketches:
            if sk.reference == "face":
                if sk.face not in existing_faces:
                    errors.append(f"Операция {i+1}: грань '{sk.face}' не существует")
        if op.next_face != "none":
            existing_faces.add(op.next_face)
    return errors

# ---------- Нормализация JSON ----------
def normalize_plan_dict(plan_dict: dict) -> dict:
    if not isinstance(plan_dict, dict):
        return plan_dict
    if "operations" not in plan_dict:
        return plan_dict
    
    for op in plan_dict["operations"]:
        for sk in op.get("sketches", []):
            ref = sk.get("reference")
            if isinstance(ref, str) and ref.startswith("plane"):
                offset_str = ref[5:]
                sk["reference"] = "plane"
                if "plane" not in sk:
                    sk["plane"] = "XY"
                if "offset" not in sk:
                    sk["offset"] = int(offset_str) if offset_str.isdigit() else 0
            
            if "reference" not in sk:
                if "plane" in sk:
                    sk["reference"] = "plane"
                elif "face" in sk:
                    sk["reference"] = "face"
            
            new_primitives = []
            for prim in sk.get("primitives", []):
                if "rectangle" in prim:
                    new_prim = {"rect": prim["rectangle"]}
                elif "rect" in prim:
                    new_prim = {"rect": prim["rect"]}
                elif "circle" in prim:
                    new_prim = {"circle": prim["circle"]}
                elif "polygon" in prim:
                    new_prim = {"polygon": prim["polygon"]}
                else:
                    new_prim = prim.copy() if isinstance(prim, dict) else prim
                
                if isinstance(prim, dict) and "pos" in prim:
                    new_prim["pos"] = prim["pos"]
                
                new_primitives.append(new_prim)
            sk["primitives"] = new_primitives
            
            if sk.get("reference") == "plane" and "offset" not in sk:
                sk["offset"] = 0.0
            if sk.get("reference") == "plane" and "plane" not in sk:
                sk["plane"] = "XY"
            if sk.get("reference") == "face" and "face" not in sk:
                sk["face"] = "top"
            
            if sk.get("reference") == "plane":
                sk.pop("face", None)
            elif sk.get("reference") == "face":
                sk.pop("plane", None)
                sk.pop("offset", None)
    
    return plan_dict

# ---------- Генератор кода build123d ----------
def shape_code(prim: Primitive) -> str:
    if isinstance(prim, Rect):
        w, l = prim.rect
        code = f"Rectangle({w}, {l})"
    elif isinstance(prim, Circle):
        r = prim.circle
        code = f"Circle({r})"
    elif isinstance(prim, Polygon):
        pts = prim.polygon["points"]
        pts_str = ", ".join(f"({p[0]}, {p[1]})" for p in pts)
        code = f"Polygon({pts_str})"
    else:
        raise ValueError(f"Неизвестный тип примитива: {type(prim)}")
    
    if prim.pos:
        x, y = prim.pos
        code = f"Pos({x}, {y}, 0) * {code}"
    
    return code

def build_sketch_code(primitives: List[Primitive], location: str) -> str:
    """Собирает несколько примитивов в один эскиз"""
    if not primitives:
        return None
    shapes = [shape_code(p) for p in primitives]
    if len(shapes) == 1:
        return f"{location} * {shapes[0]}"
    else:
        union = " + ".join(shapes)
        return f"{location} * ({union})"

def get_face_location(face: str, current_height: str) -> str:
    if face == "top":
        return f"Pos(0, 0, {current_height})"
    elif face == "bottom":
        return "Pos(0, 0, 0)"
    elif face == "front":
        return f"Pos(0, -{current_height}/2, {current_height}/2) * Rot(X=90)"
    elif face == "back":
        return f"Pos(0, {current_height}/2, {current_height}/2) * Rot(X=90)"
    elif face == "left":
        return f"Pos(-{current_height}/2, 0, {current_height}/2) * Rot(Y=90)"
    elif face == "right":
        return f"Pos({current_height}/2, 0, {current_height}/2) * Rot(Y=90)"
    else:
        return "Pos(0, 0, 0)"

def translate_to_cadquery(plan: Plan, description: str = "") -> str:
    lines = []
    lines.append("from build123d import *")
    lines.append("")
    lines.append("result = None")
    lines.append("")
    lines.append("# ===== ФАЗА 1: СТРОИМ ТЕЛО ИЗ ADD-ОПЕРАЦИЙ =====")
    lines.append("add_bodies = []")
    lines.append("current_height = 0.0")
    lines.append("")
    
    for i, op in enumerate(plan.operations):
        if op.mode != "add":
            continue
            
        sk_low = op.sketches[0]
        sk_up = op.sketches[1]

        if sk_low.reference == "plane":
            h_low = sk_low.offset if sk_low.offset is not None else 0.0
            loc_low = f"Pos(0, 0, {h_low})"
            new_height = h_low
        else:
            loc_low = get_face_location(sk_low.face, "current_height")
            new_height = "current_height"

        if sk_up.reference == "plane":
            h_up = sk_up.offset if sk_up.offset is not None else 0.0
            loc_up = f"Pos(0, 0, {h_up})"
            new_height = h_up
        else:
            loc_up = get_face_location(sk_up.face, "current_height")
            new_height = "current_height"

        if not sk_low.primitives or not sk_up.primitives:
            lines.append(f"# ADD ОПЕРАЦИЯ {i+1} ПРОПУЩЕНА: отсутствуют примитивы")
            lines.append("")
            continue

        sketch_low = build_sketch_code(sk_low.primitives, loc_low)
        sketch_up = build_sketch_code(sk_up.primitives, loc_up)

        lines.append(f"# ADD-операция {i+1}")
        lines.append(f"sk_lower = {sketch_low}")
        lines.append(f"sk_upper = {sketch_up}")
        lines.append(f"lofted = loft([sk_lower, sk_upper])")
        lines.append("add_bodies.append(lofted)")
        
        if isinstance(new_height, (int, float)):
            lines.append(f"current_height = {new_height}")
        lines.append("")
    
    lines.append("# Объединяем все add-тела в одно тело")
    lines.append("if add_bodies:")
    lines.append("    result = add_bodies[0]")
    lines.append("    for body in add_bodies[1:]:")
    lines.append("        result = result + body")
    lines.append("else:")
    lines.append("    result = None")
    lines.append("")
    
    lines.append("# ===== ФАЗА 2: ВЫРЕЗАЕМ ИЗ ТЕЛА =====")
    lines.append("if result is not None:")
    lines.append("")
    
    # Разбиваем составные cut-операции на отдельные
    for i, op in enumerate(plan.operations):
        if op.mode != "cut":
            continue
            
        sk_low = op.sketches[0]
        sk_up = op.sketches[1]
        
        if sk_low.reference == "plane":
            h_low = sk_low.offset if sk_low.offset is not None else 0.0
            loc_low = f"Pos(0, 0, {h_low})"
        else:
            loc_low = "Pos(0, 0, 0)"

        if sk_up.reference == "plane":
            h_up = sk_up.offset if sk_up.offset is not None else 0.0
            loc_up = f"Pos(0, 0, {h_up})"
        else:
            loc_up = "Pos(0, 0, 0)"

        if not sk_low.primitives or not sk_up.primitives:
            lines.append(f"    # CUT ОПЕРАЦИЯ {i+1} ПРОПУЩЕНА: отсутствуют примитивы")
            lines.append("")
            continue
        
        # Если в скетче несколько примитивов — разбиваем на отдельные cut-операции
        if len(sk_low.primitives) > 1 or len(sk_up.primitives) > 1:
            # Берем примитивы из нижнего скетча (должны совпадать с верхним)
            for j, prim in enumerate(sk_low.primitives):
                # Для каждого примитива создаём отдельную cut-операцию
                single_prim_low = [prim]
                single_prim_up = [sk_up.primitives[j]] if j < len(sk_up.primitives) else [prim]
                
                sketch_low = build_sketch_code(single_prim_low, loc_low)
                sketch_up = build_sketch_code(single_prim_up, loc_up)
                
                lines.append(f"    # CUT-операция {i+1}.{j+1}")
                lines.append(f"    sk_lower = {sketch_low}")
                lines.append(f"    sk_upper = {sketch_up}")
                lines.append(f"    cut_tool = loft([sk_lower, sk_upper])")
                lines.append("    result = result - cut_tool")
                lines.append("")
        else:
            # Одиночный примитив
            sketch_low = build_sketch_code(sk_low.primitives, loc_low)
            sketch_up = build_sketch_code(sk_up.primitives, loc_up)
            
            lines.append(f"    # CUT-операция {i+1}")
            lines.append(f"    sk_lower = {sketch_low}")
            lines.append(f"    sk_upper = {sketch_up}")
            lines.append(f"    cut_tool = loft([sk_lower, sk_upper])")
            lines.append("    result = result - cut_tool")
            lines.append("")
    
    lines.append("# Готово")
    return "\n".join(lines)
