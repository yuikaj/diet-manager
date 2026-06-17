"""
AI Recipe Data Cleaner (Final Optimized Version)
- 密度感知换算 (粉类 vs 液体)
- 蛋白质维度自动分类
- 烹饪时长与难度预估
- 强制 uses_wok = False (符合用户炖煮习惯)
- 移除了步骤开头的冗余序号
"""
import json
import os
import sys
import time
import re
from pathlib import Path

# 将项目根目录加入路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from google import genai
from google.genai import types

from db.init_db import get_connection
from db.recipes import get_ingredients, get_all_recipes

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("❌ 未找到 GEMINI_API_KEY 环境变量。")
    sys.exit(1)

client = genai.Client(api_key=api_key)

def extract_json(text: str) -> dict:
    """
    强大的 JSON 提取器：处理带有思考过程或 Markdown 标记的返回内容
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            json_str = match.group()
            return json.loads(json_str)
        raise ValueError("无法在 AI 的回复中找到合法的 JSON 块")

def build_prompt(recipe_name: str, raw_ingredients: list, raw_steps: list) -> str:
    ing_str = "\n".join([f"- 原文：{i['name']} | 用量描述：{i['unit']}" for i in raw_ingredients])
    steps_str = "\n".join([f"- {s}" for s in raw_steps])
    
    return f"""
你是一位拥有 20 年经验的资深大厨和数据专家。请对以下食谱数据进行深度结构化清洗。

【菜名】：{recipe_name}
【原始食材输入】：
{ing_str}
【原始步骤输入】：
{steps_str}

【任务 1：密度感知的精确换算】
请根据食材物理性质换算 amount_g，必须严格执行计算：
- **水/油/奶/生抽/醋/料酒/盐**：1 tsp = 5.0g, 1 tbsp = 15.0g。
- **泡打粉/干酵母/胡椒粉/五香粉/干香料**：1 tsp = 3.0g, 1 tbsp = 9.0g。
- **淀粉/面粉**：1 tsp = 4.0g, 1 tbsp = 12.0g。
- **糖/蜂蜜**：1 tsp = 4.5g, 1 tbsp = 14.0g。
- **其他单位**：1 lb (磅) = 453.6g, 1 cup (杯) = 240.0g, 1盎司(oz) = 28.3g。
- **分数处理**：1/4t = 1.25g (液体) 或 0.75g (轻粉)。

【任务 2：分类与预估】
- **category**：归类为 "纯蛋白" (肉/蛋/奶/鱼)、"半蛋白半素" 或 "纯素"。
- **cook_time_min**：预估从备菜到出锅的总时长（分钟）。
- **prep_difficulty**：归类为 "简单"、"中等" 或 "困难"。

【任务 3：步骤重写】
- 格式：动作 + （调料名 0.0g、调料名 0.0g） + 动作。
- **严格禁止**在步骤开头包含“第一步：”、“1.”等序号。

【输出 JSON 格式】：
{{
  "category": "分类",
  "cook_time_min": 整数,
  "prep_difficulty": "简单/中等/困难",
  "ingredients": [
    {{"name": "净化后的食材名", "amount_g": 换算后的数字, "is_condiment": true/false}}
  ],
  "revised_steps": ["步骤描述（内嵌调料及克重）..."]
}}
"""

def update_recipe_in_db(recipe_id: str, ai_data: dict):
    conn = get_connection()
    try:
        # 在 SQL 层面强制硬编码 uses_wok = 0
        conn.execute(
            """UPDATE recipes SET 
               steps = ?, 
               category = ?, 
               uses_wok = 0, 
               cook_time_min = ?, 
               prep_difficulty = ?,
               data_quality = 'complete' 
               WHERE id = ?""",
            (
                json.dumps(ai_data.get("revised_steps", []), ensure_ascii=False),
                ai_data.get("category", "半蛋白半素"),
                ai_data.get("cook_time_min", 30),
                ai_data.get("prep_difficulty", "中等"),
                recipe_id
            )
        )
        
        # 重新填充食材表
        conn.execute("DELETE FROM ingredients WHERE recipe_id = ?", (recipe_id,))
        for ing in ai_data.get("ingredients", []):
            conn.execute(
                """INSERT INTO ingredients (id, recipe_id, name, amount, unit, is_condiment, intake_ratio)
                   VALUES (lower(hex(randomblob(16))), ?, ?, ?, 'g', ?, 1.0)""",
                (recipe_id, ing["name"], float(ing["amount_g"]), 1 if ing["is_condiment"] else 0)
            )
        conn.commit()
    finally:
        conn.close()

def main():
    target_model = 'gemini-flash-lite-latest' 
    print(f"🤖 启动 AI 菜谱清洗引擎 (Model: {target_model}) ...\n")
    
    all_recipes = get_all_recipes()
    total = len(all_recipes)
    
    success_count = 0
    fail_count = 0
    
    for idx, recipe in enumerate(all_recipes, 1):
        recipe_id = recipe["id"]
        name = recipe["name"]
        
        # 全量清洗模式
        raw_ings = get_ingredients(recipe_id)
        raw_steps = recipe.get("steps", [])
        
        print(f"[{idx}/{total}] ⏳ 正在清洗: {name} ...")
        prompt = build_prompt(name, raw_ings, raw_steps)
        
        try:
            response = None
            for retry in range(3):
                try:
                    response = client.models.generate_content(
                        model=target_model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            temperature=0.1,
                        ),
                    )
                    break
                except Exception as e:
                    if "503" in str(e):
                        print(f"    ⚠️ 服务器拥堵，5秒后重试 ({retry+1}/3)...")
                        time.sleep(5)
                        continue
                    raise e

            if not response:
                raise ValueError("API 响应为空")

            ai_output = extract_json(response.text)
            update_recipe_in_db(recipe_id, ai_output)
            print(f"    ✅ 成功整理 {len(ai_output.get('ingredients', []))} 项食材，分类为: {ai_output.get('category')}")
            success_count += 1
            
        except Exception as e:
            print(f"    ❌ 失败: {e}")
            fail_count += 1
        
        # 维持 12 RPM 频率
        time.sleep(5)

    print("\n" + "="*40)
    print(f"🎉 清洗完毕！成功: {success_count} | 失败: {fail_count}")

if __name__ == "__main__":
    main()