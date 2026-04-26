import json
import httpx
from groq import Groq

GROQ_API_KEY = "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"

SYSTEM_PROMPT = """
ТЫ — CAD-АРХИТЕКТОР. Твоя задача — спроектировать 3D-модель методом лофтинга.

ПРЕЖДЕ ЧЕМ ПИСАТЬ JSON, ТЫ ДОЛЖЕН:
1. ПОНЯТЬ ОПИСАНИЕ ПОЛЬЗОВАТЕЛЯ
2. РАЗБИТЬ МОДЕЛЬ НА ОПЕРАЦИИ (add - добавить, cut - вырезать)
3. ДЛЯ КАЖДОЙ ОПЕРАЦИИ ОПРЕДЕЛИТЬ ДВА СЕЧЕНИЯ (нижнее и верхнее)
4. ДЛЯ КАЖДОГО СЕЧЕНИЯ ОПРЕДЕЛИТЬ ФОРМУ, ВЫСОТУ И ПОЗИЦИЮ

ЧТО ТАКОЕ ЛОФТ:
Лофт — это поверхность, натянутая между двумя сечениями. Как если бы вы взяли две фигуры на разной высоте и соединили их стенками.

ФОРМАТ JSON:

{
  "operations": [
    {
      "type": "loft",
      "mode": "add",
      "sketches": [
        {
          "reference": "plane",
          "plane": "XY",
          "offset": 0,
          "primitives": [{"rect": [40, 40]}]
        },
        {
          "reference": "plane",
          "plane": "XY",
          "offset": 10,
          "primitives": [{"rect": [40, 40]}]
        }
      ],
      "next_face": "top"
    }
  ]
}

ПРАВИЛА ДЛЯ ПРИМИТИВОВ:
- ОДИН ПРИМИТИВ: {"circle": 10}
- НЕСКОЛЬКО ПРИМИТИВОВ С РАЗНЫМИ ПОЗИЦИЯМИ:
  "primitives": [
    {"circle": 3, "pos": [10, 10]},
    {"circle": 3, "pos": [-10, 10]},
    {"circle": 3, "pos": [10, -10]},
    {"circle": 3, "pos": [-10, -10]}
  ]
- pos — смещение по X и Y относительно центра (0,0)

ПРАВИЛА ДЛЯ REFERENCE:
- "reference": "plane" — абсолютная плоскость. Требует "plane" (XY/XZ/YZ) и "offset" (высота).
- "reference": "face" — грань предыдущего тела. Требует "face" (top/bottom/front/back/left/right).

ПРАВИЛА:
1. Первая операция — всегда reference="plane", plane="XY", offset=0.
2. Для cut (вырезание): 
   - Если отверстие сквозное — offset нижнего=0, offset верхнего=высота тела
   - Если отверстие глухое — offset нижнего = высота_дна, offset верхнего = высота_верха
3. Каждая операция — ровно 2 скетча.
4. Вырезы применяются после всех add-операций.

ПРИМЕР 1: Цилиндр высотой 40, радиус 15, сквозное отверстие радиус 5:
{
  "operations": [
    {
      "type": "loft",
      "mode": "add",
      "sketches": [
        {"reference": "plane", "plane": "XY", "offset": 0, "primitives": [{"circle": 15}]},
        {"reference": "plane", "plane": "XY", "offset": 40, "primitives": [{"circle": 15}]}
      ],
      "next_face": "top"
    },
    {
      "type": "loft",
      "mode": "cut",
      "sketches": [
        {"reference": "plane", "plane": "XY", "offset": 0, "primitives": [{"circle": 5}]},
        {"reference": "plane", "plane": "XY", "offset": 40, "primitives": [{"circle": 5}]}
      ],
      "next_face": "none"
    }
  ]
}

ПРИМЕР 2: Основание 40x40 высота 10, на нём цилиндр высотой 30 радиус 10, на нём куб 10x10 высотой 5, и 4 отверстия радиусом 3 на основании:
{
  "operations": [
    {
      "type": "loft",
      "mode": "add",
      "sketches": [
        {"reference": "plane", "plane": "XY", "offset": 0, "primitives": [{"rect": [40, 40]}]},
        {"reference": "plane", "plane": "XY", "offset": 10, "primitives": [{"rect": [40, 40]}]}
      ],
      "next_face": "top"
    },
    {
      "type": "loft",
      "mode": "add",
      "sketches": [
        {"reference": "face", "face": "top", "primitives": [{"circle": 10}]},
        {"reference": "plane", "plane": "XY", "offset": 40, "primitives": [{"circle": 10}]}
      ],
      "next_face": "top"
    },
    {
      "type": "loft",
      "mode": "add",
      "sketches": [
        {"reference": "face", "face": "top", "primitives": [{"rect": [10, 10]}]},
        {"reference": "plane", "plane": "XY", "offset": 45, "primitives": [{"rect": [10, 10]}]}
      ],
      "next_face": "none"
    },
    {
      "type": "loft",
      "mode": "cut",
      "sketches": [
        {
          "reference": "plane",
          "plane": "XY",
          "offset": 0,
          "primitives": [
            {"circle": 3, "pos": [10, 10]},
            {"circle": 3, "pos": [-10, 10]},
            {"circle": 3, "pos": [10, -10]},
            {"circle": 3, "pos": [-10, -10]}
          ]
        },
        {
          "reference": "plane",
          "plane": "XY",
          "offset": 10,
          "primitives": [
            {"circle": 3, "pos": [10, 10]},
            {"circle": 3, "pos": [-10, 10]},
            {"circle": 3, "pos": [10, -10]},
            {"circle": 3, "pos": [-10, -10]}
          ]
        }
      ],
      "next_face": "none"
    }
  ]
}

ВЫДАЙ ТОЛЬКО JSON. БЕЗ ПОЯСНЕНИЙ.
"""

timeout = httpx.Timeout(30.0, connect=30.0, read=90.0, write=30.0)
http_client = httpx.Client(timeout=timeout, verify=True)
client = Groq(api_key=GROQ_API_KEY, http_client=http_client)

def plan_model(description: str, retries=2) -> dict:
    print("  → Отправка запроса в Groq (llama-3.3-70b-versatile)...")
    for attempt in range(retries + 1):
        try:
            chat_completion = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": description}
                ],
                model="llama-3.3-70b-versatile",
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            content = chat_completion.choices[0].message.content
            plan_dict = json.loads(content)
            print("  → Получен JSON план")
            return plan_dict
        except Exception as e:
            print(f"  → Попытка {attempt+1} не удалась: {e}")
            if attempt == retries:
                raise
    raise RuntimeError("Не удалось получить валидный JSON от Groq")
