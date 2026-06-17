# 个人饮食健康管理系统 — 产品需求文档 (PRD)

> 用途：将此文档作为 Claude Code 项目启动 prompt 的开头，完整描述系统需求。

---

## 项目概述

一个**个人使用 + GitHub 作品集展示**的全栈 AI 应用，管理家庭饮食的 Inventory、个人菜谱库、当日食谱规划，以及基于 USDA FoodData Central API 的营养分析。核心亮点：LLM-powered 营养顾问（Gemini API）+ 语义菜谱搜索（本地 Embedding + ChromaDB）。用餐人数：2人，营养目标为维持体重（蛋白质按体重 × 1.2–1.5 g/kg/day）。

**GitHub 定位**：展示 LLM API 集成、RAG/向量搜索、数据管道设计能力，对应 Biotech AI ML Scientist / LLM Engineer 求职方向。

---

## 技术栈

### 起步阶段（推荐先用，快速跑通）
- **框架**：Streamlit（Python，零学习成本，可直接 deploy 到 Streamlit Cloud 生成 live demo 链接）
- **数据库**：SQLite（via `sqlite3` 或 `SQLAlchemy`）
- **语言**：Python

### 进阶阶段（功能稳定后迁移）
- **框架**：Next.js 14（App Router）+ FastAPI 后端
- **数据库**：SQLite（via Prisma ORM）
- **语言**：TypeScript + Python

### AI / ML 组件（所有方案均零额外费用）
- **LLM 营养顾问**：Google Gemini 1.5 Flash API（免费 tier：15 RPM / 1500 RPD，无需绑卡）
- **菜谱 Embedding**：`sentence-transformers` 本地运行，模型 `paraphrase-multilingual-MiniLM-L12-v2`（支持中文，~400MB，完全离线）
- **向量数据库**：ChromaDB（本地持久化，无需服务器）

### 外部 API
- **营养数据**：USDA FoodData Central（免费，需申请 key）

### 安全规范（必须在项目第一步执行）
```bash
# 1. 创建 .env 文件存放所有 key
GEMINI_API_KEY=your_key_here
USDA_API_KEY=your_key_here

# 2. .gitignore 必须包含
.env
*.env
.env.local

# 3. 永远不要在代码里 hardcode 任何 key
# 用 os.getenv("GEMINI_API_KEY") 读取
```
⚠️ Claude Code 启动项目时，第一步必须检查 `.gitignore` 是否包含 `.env`，确认后再进行任何其他操作。

---

## 模块一：Inventory 管理

### 蔬菜 Inventory（三类）

#### 1. 常驻蔬菜（布尔值，有/缺）
无需计份数，只记录当前是否有货。

预设常驻列表（可在设置里增删）：
- 洋葱、胡萝卜、白萝卜、卷心菜、白菜、番茄
- 冻姜（标注：冷冻）、冻蒜（标注：冷冻）
- 葱（布尔值，但标注"容易忘记补"）

UI 行为：点击切换 有/缺，颜色区分（绿色/灰色）。

#### 2. 叶菜 / 时令蔬菜（克数计数）
- 单位：**克（g）**，直接存储实际克重（比"份"更精确，便于自动扣减）
- 快速录入按钮："+1磅（+453g）"，方便按整磅买入时录入；同时支持直接输入克数
- 支持操作：+453g（一磅）/ -453g / 手动输入任意克数，以及直接点击克数字段行内编辑
- **删除食材**：每条食材右侧有删除按钮（⛔ 或 ✕），可从 inventory 列表中移除
- **易坏标记（高优先）**：可对某个蔬菜打"🔴优先"标记，在食谱推荐时优先消耗
  - 预设易坏蔬菜：黄瓜、豆苗、西洋菜、空心菜（可自定义）
- 克重减到 0 时自动灰显
- **颜色逻辑**：克重多（≥ 2磅/900g）显示橙色提示——代表"需要优先消耗"；0g 灰显；正常克重无颜色

#### 3. Protein 冷冻库（克数计数）
- 单位：**克（g）**，直接存储实际克重
- 快速录入按钮："+200g"（默认单份）/ "+400g" / 手动输入，支持行内编辑克数
- **删除 Protein 条目**：每条右侧有删除按钮
- 每份备注字段：如"鸡腿去骨"、"猪五花切块"
- 颜色逻辑与叶菜一致（多则橙色提示优先消耗）
- 买菜时：手动批量录入新购入克数

#### 4. 干货柜（布尔值，有/缺）
常温储存、保质期长，不需要计份数的辅料食材。

预设干货列表（可增删）：海草、粉丝、腐竹、木耳、干香菇、虾皮、海米、魔芋、豆腐皮

UI 行为：独立分区，点击切换有/缺。推荐算法生成菜单时，若推荐了凉拌菜或需要辅料的菜，顺带在菜单旁显示"干货柜：海草 ✅ 木耳 ✅"作为提示，不参与消耗扣减。

### Inventory 交互规则
- 主界面一屏展示全部 inventory，分四列区域（常驻蔬菜 / 叶菜时令 / Protein / 干货柜）+ 预制菜独立分区（见模块七）
- **做菜后自动扣减**：确认今日菜单后，系统自动计算每道菜的食材用量（按菜谱克重），弹出扣减明细供用户确认后批量扣减 inventory；残余克重原样保留（如卷心菜用了250g剩150g，直接存150g），不做"份+零头"转换
- 推荐算法中，库存克重多（≥ 900g）的蔬菜权重加分，优先推荐消耗
- **购物模式**：单独入口，可批量 +Ng 录入当日购买的食材

---

## 模块二：个人菜谱库

### 数据库字段（每道菜）

```
Recipe {
  id              String   // 唯一 ID
  name            String   // 菜名
  source_url      String?  // 下厨房原链接（爬取来源）
  
  // 食材（数组）
  ingredients     Ingredient[]
  
  // 烹饪属性
  cooking_method  String[]  // 多选：炒 / 蒸 / 烤 / 煮 / 凉拌 / 炸 / 炖 / 煎
  uses_wok        Boolean   // 是否占用主炒锅（关键平行逻辑字段）
  prep_difficulty String    // 备菜难度：简单 / 中等 / 繁琐
  cook_time_min   Int       // 烹饪时长（分钟）
  is_parallel     Boolean   // 是否可以与其他菜同时进行（炖/蒸/烤=true，炒=false）
  
  // 分类 & 标签（双维度多选）
  category        String[]  // 多选，见下方分类体系
  tags            String[]  // 自由标签，如"快手"、"家常"、"海鲜"

  // 分类体系（两个维度独立标注，可组合）：
  // 【荤素维度】纯蛋白 / 荤菜 / 半荤半素 / 纯素
  //   - 纯蛋白：主料几乎全为肉/鱼/海鲜，无蔬菜（如清蒸鱼、白切鸡）
  //   - 荤菜：肉为主，少量蔬菜（如红烧肉、番茄炒蛋偏蛋版）
  //   - 半荤半素：肉菜各半（如番茄牛肉、蒜苗炒肉）
  //   - 纯素：无肉（如清炒青菜、凉拌黄瓜）
  // 【形态维度】汤 / 凉拌 / 主食（普通炒/蒸/烤/炖菜不加形态标签）
  // 示例：番茄牛肉汤 → ["半荤半素", "汤"]
  //       鸡毛菜汤   → ["纯素", "汤"]
  //       红烧鱼     → ["纯蛋白"]
  //       蒜苗炒肉   → ["半荤半素"]
  
  // 数据质量
  data_quality    String    // complete / needs_review / estimated
  // complete = 食材栏有精确克重含调料
  // needs_review = 调料藏在步骤里，需人工补全
  // estimated = 克重是范围值，已取中位数
  
  // 记录
  notes           String?   // 个人备注，如"口味调整：减半辣"
  created_at      DateTime
  last_cooked     DateTime?
}

Ingredient {
  id              String
  recipe_id       String
  name            String   // 食材名，如"猪五花"
  amount          Float    // 克重（范围值取中位数）
  unit            String   // 默认 g，特殊情况可填"个"、"片"
  is_condiment    Boolean  // 是否是调料/调味品（用于钠等微量营养素计算）
  intake_ratio    Float    // 调料实际摄入比例，默认 1.0（全部摄入）
                           // 适用场景：红烧鱼用50g生抽但实际吃入约25%，设为0.25
                           // UI 提供快捷选项：全部(1.0) / 75% / 50% / 25% / 忽略(0)
                           // 非调料食材（is_condiment=false）固定为 1.0，不显示此选项
                           // 营养计算时：该食材营养值 × intake_ratio
  usda_food_id    String?  // 匹配到的 USDA FoodData ID（手动或自动匹配）
}
```

### 菜谱录入方式

#### A. 一次性爬取下厨房
- 爬取目标：用户下厨房公开主页的所有菜谱
- 抓取字段：菜名、用料（名称+克重）、步骤文字、原链接
- 数据清洗逻辑：
  - 克重是范围（如 350g-400g）→ 取中位数，标记 `estimated`
  - 调料不在用料栏 → 标记 `needs_review`，步骤文字原文保留备查
  - 正常 → 标记 `complete`
- **反爬处理**：检测到"滑动验证"页面（page title 含"滑动验证"）时，自动等待 5 分钟后重试，而非直接放弃；支持 `--resume` 参数从上次失败的 URL 继续，避免重复抓取已成功的条目
- **AI 辅助补全**：对 `needs_review` 的菜谱，将步骤文字送给 Claude API，自动提取调料名称和克重，生成待确认列表，用户 confirm 或修改后保存

#### B. 系统内手动录入
- 提供完整表单，逐字段填写
- 支持从 `needs_review` 菜谱一键跳转到编辑界面补全

### 菜谱库界面
- 列表视图（默认）+ 搜索/筛选（按 category、tags、cooking_method、data_quality）
- 每条显示：菜名、难度标签、烹饪方式标签、是否占锅图标
- `needs_review` 菜谱顶部显示"待补全"角标
- 点击进入详情，显示所有字段 + 编辑按钮
- 调料编辑：每个 `is_condiment=true` 的食材旁显示摄入比例选择器（全部 / 75% / 50% / 25% / 忽略），默认"全部"，符合"方向感知 > 精确计量"原则

---

## 模块三：当日食谱规划

### 核心规则（推荐算法约束）

1. **易坏优先**：inventory 中打"🔴优先"标记的蔬菜，推荐时必须包含消耗该蔬菜的菜
2. **炒锅限制**：
   - 标准规则：`uses_wok=true` 且 `cook_time_min > 5` 的菜不超过 1 道
   - **轻占锅例外**：`uses_wok=true` 且 `cook_time_min ≤ 5` 的菜视为"轻占锅"（如快手炒青菜），可在已有一道标准占锅菜的情况下额外出现，最多 1 道轻占锅菜
   - 参数化配置：`max_wok_dishes=1`（标准），`allow_quick_wok=true`
3. **并行原则**：尽量选 `is_parallel=true` 的菜（烤/蒸/炖/凉菜）来搭配炒菜，保证做菜者能吃到热菜
4. **难度平衡**：如果有 1 道 `prep_difficulty=繁琐` 的菜，剩余菜应以"简单"为主
5. **营养覆盖**：2人份总蔬菜摄入 ≥ 700g，总蛋白质类食材 ≥ 400g（非纯蛋白，指食材重量）
6. **只推荐 inventory 中有货的食材**：推荐结果的主料必须在当前 inventory 里
7. **分类覆盖约束**（每套菜单尽量满足）：
   - 至少 1 道含 `纯蛋白` 或 `荤菜` tag 的菜
   - 至少 1 道含 `纯素` tag 的菜
   - 优先包含 1 道含 `汤` tag 的菜
   - 若已有 2 道荤菜，汤优先选含 `纯素` tag 的（如鸡毛菜汤）
8. **库存量权重**：inventory 克重 ≥ 900g 的蔬菜在推荐评分中加权，优先消耗存量多的食材
9. **防厌倦机制**：
   - 基础去重：读取菜谱表中的 last_cooked 字段，绝对不推荐最近 48 小时内（近两天）刚吃过的菜谱。
   - 易坏食材冲突处理：即使是打上了“🔴优先”标记的易坏食材，如果昨天（过去24小时内）刚吃过该食材（不论何种做法），今天也强制取消其必吃权重，顺延至明天再推荐。

### 三种规划方式

#### 1. AI 推荐组合
- 入口：点击"今日推荐"
- 系统读取当前 inventory，按以上规则，推荐 2–3 套菜单组合（每套 3–4 道菜）
- 每套组合展示：菜名列表、预计总时间、并行示意、难度评估
- 用户可接受某套组合，或手动替换其中某道菜

#### 2. 手动自选
- 从菜谱库中自由选菜，加入"今日菜单"
- 实时检查：是否违反炒锅限制（超出则警告）
- 实时预览营养估算

#### 3. 临时占位菜（Placeholder）
- 专门入口："临时加菜（不录库）"
- 交互：自由输入食材列表，格式如：
  ```
  猪五花 200g
  土豆 300g
  豆瓣酱 15g
  生抽 10ml
  ```
- 系统将每行食材送 USDA API 查询营养数据，汇总后合并进今日营养分析
- 此记录不保存到菜谱库，仅用于当日营养计算

### 今日菜单界面
- 顶部：今日已选菜单（可拖拽排序，直观呈现上菜顺序）
- 下方：实时营养预览卡片（随选菜实时更新）
- 底部：确认今日菜单按钮 → 触发完整营养分析

---

## 模块四：营养分析

### 数据来源
USDA FoodData Central API（https://fdc.nal.usda.gov/）
- 免费 API Key，申请后填入 `.env`
- 食材名称 → 搜索 USDA 数据库 → 匹配 food_id → 拉取每100g的营养数据 → 按实际克重计算

### USDA 食材匹配策略
- 菜谱录入时预匹配：每个 Ingredient 尝试自动匹配 USDA food_id（中文名需手动映射或用英文别名搜索）
- 维护一张本地**中英文食材映射表**（`ingredient_translations.json`），如：
  ```json
  { "猪五花": "pork belly", "空心菜": "water spinach", "生抽": "soy sauce light" }
  ```
- 未匹配的食材标红，提示用户手动搜索并绑定

### 输出营养数据（2人份合计 + 每人份）

**Macros（大量营养素）**
- 总热量（kcal）
- 蛋白质（g）—— 对比目标（体重 × 1.2–1.5 g/kg）
- 脂肪（g）
- 碳水化合物（g）

**Micros（微量营养素，重点展示）**
- 钠（mg）—— 重点，因调料含量高
- 维生素 C（mg）
- 铁（mg）
- 钙（mg）
- 钾（mg）
- 膳食纤维（g）

**展示形式**
- 进度条 + 数值，对比每日参考摄入量（DRI）
- 蛋白质单独显示"目标区间"（基于用户体重设置）
- 钠高亮警示（超过 2300mg/天则变红）

### 用户体重设置
- 设置页面录入两人体重（kg）
- 系统自动计算每日蛋白质目标范围

---

## 多语言策略

**核心原则**：数据存中文，UI 双语切换，README 纯英文。不需要完整 i18n 框架。

- **数据库**：所有字段存中文（菜名、食材名、标签）
- **UI**：顶部语言切换按钮（🇨🇳 / 🇺🇸），切换后界面标签变英文，数据内容保持中文原文
- **食材翻译**：维护 `ingredient_translations.json`，中英文对照，用于 USDA API 查询和 README 展示
- **GitHub README**：纯英文，UI 截图保留中文界面

---

## 营养数据来源策略（三级降级）

### 第一级：USDA FoodData Central
中文名 → `ingredient_translations.json` → 英文名 → USDA 搜索匹配。

**USDA 覆盖较好的中式食材**：芥兰（Gai lan）、莲藕（Lotus root）、茼蒿（Garland chrysanthemum）、山药（Chinese yam）、丝瓜（Luffa）、秋葵（Okra）、金针菇（Enoki mushroom）、杏鲍菇（King oyster mushroom）、芋艿（Taro）、莴笋（Celtuce）、西洋菜（Watercress）、豆苗（Pea shoots）

**USDA 覆盖不全**（需本地补充）：塔库菜、绣球菌、鸡毛菜、黄喉、黄鳝丝、脆鱼片、扇贝粉、ハマチ（鰤鱼）

### 第二级：本地补充营养表（`local_nutrition.json`）
对 USDA 找不到的食材，手动录入每 100g 营养数据：
```json
{
  "塔库菜": {
    "en_name": "Tatsoi",
    "per_100g": { "kcal": 22, "protein": 2.2, "fat": 0.3, "carbs": 3.1, "sodium": 40, "fiber": 1.5 },
    "source": "manual_estimate",
    "note": "参考类似菠菜数据估算"
  },
  "绣球菌": {
    "en_name": "Cauliflower mushroom",
    "per_100g": { "kcal": 35, "protein": 3.0, "fat": 0.4, "carbs": 5.5, "sodium": 5, "fiber": 2.0 },
    "source": "manual_estimate",
    "note": "参考菌类平均数据估算"
  }
}
```

### 第三级：预制菜 / 自定义营养标（见模块七）
用户完全自主输入，系统不查任何数据库。

---

## 模块七：预制菜 / 自定义营养标

### 使用场景
- Weee 购买的预制菜（太二酸菜鱼等），包装钠含量按整份汤计算，实际摄入远低于标注
- 用户希望自行估算并完全覆盖包装标注值

### 数据结构
```
PreparedFood {
  id              String
  name            String       // 如"太二酸菜鱼"
  brand           String?      // 如"太二"、"Weee 自有品牌"
  serving_weight  Float        // 你实际吃的部分克重（可与包装不同）

  custom_kcal     Float
  custom_protein  Float
  custom_fat      Float
  custom_carbs    Float
  custom_sodium   Float        // 重点：用户自己估算实际钠摄入
  custom_fiber    Float?

  note            String?      // 如"汤不喝，钠按鱼肉部分估算≈800mg"
  data_source     String       // = "user_custom"，UI 显示"⚠️ 估算值"
  inventory_count Int
  is_frozen       Boolean      // 默认 true
}
```

### UI 流程
1. Inventory 页面新增"预制菜"分区（与蔬菜、Protein 并列）
2. 添加时填写：品牌名、菜名、实际食用克重、逐项自定义营养值、估算备注
3. 今日食谱规划可选预制菜，营养计算直接使用自定义值，不查 USDA
4. 营养分析报告中，预制菜条目显示"⚠️ 估算值"角标，钠一栏附带用户的估算备注

---

## 预设 Inventory 种子数据

以下为用户实际 inventory 清单，系统初始化时预填，用户可增删改。

### 蔬菜（46 种）

**常驻类**（布尔值 有/缺，冰箱常备）：
洋葱、胡萝卜、白菜、番茄、卷心菜、土豆、山药、藕、冬瓜、芋艿

**叶菜 / 时令类**（份数计数，1磅/份）：
葱、豆苗、花菜、黄瓜、芥兰、金针菇、萝卜、茄子、青菜、青彩椒、秋葵、生菜、丝瓜、四季豆、塔库菜、茼蒿、莴笋、西葫芦、西蓝花、西洋菜、西芹、杏鲍菇、绣球菌、油菜、韭菜、鸡毛菜

**易坏高优先**（默认打🔴标记，推荐算法优先消耗）：
豆苗、黄瓜、西洋菜、鸡毛菜、生菜、韭菜

**调味/香料类**（布尔值）：
葱、香菜、冻姜（冷冻）、冻蒜（冷冻）

### Protein（40 种）

| 类别 | 品类 |
|------|------|
| 禽类 | 鸡腿、鸡翅、整鸡、鹌鹑、整鸭 |
| 牛肉类 | 肥牛卷、牛腩、牛腱、炒牛肉、牛排、牛小排、黄喉、牛筋、牛尾 |
| 猪肉类 | 猪小排、猪大排、猪肉末、猪梅肉、猪肉丝、猪五花、五花碎 |
| 羊肉类 | 羊排 |
| 鱼类 | 三文鱼、ハマチ（鰤鱼）、金鲳鱼、带鱼、海鲈鱼、黑鱼片、脆鱼片、黄鳝丝、鳗鱼 |
| 海鲜类 | 鱿鱼、章鱼、白虾、北极虾、虾排、扇贝粉 |

**预制类**（归入模块七，单独管理，不查 USDA）：酸菜鱼（太二等品牌）

### `ingredient_translations.json` 初始数据
```json
{
  "鸡腿": "chicken thigh", "鸡翅": "chicken wings", "整鸡": "whole chicken",
  "猪五花": "pork belly", "猪肉末": "ground pork", "猪梅肉": "pork shoulder",
  "牛腩": "beef brisket", "牛腱": "beef shank", "肥牛卷": "beef rolls",
  "牛排": "beef steak", "羊排": "lamb chops",
  "三文鱼": "salmon", "带鱼": "hairtail fish", "海鲈鱼": "sea bass",
  "白虾": "white shrimp", "北极虾": "arctic shrimp", "鱿鱼": "squid", "章鱼": "octopus",
  "芥兰": "chinese broccoli", "莲藕": "lotus root", "茼蒿": "garland chrysanthemum",
  "山药": "chinese yam", "秋葵": "okra", "金针菇": "enoki mushroom",
  "杏鲍菇": "king oyster mushroom", "芋艿": "taro", "莴笋": "celtuce",
  "丝瓜": "luffa", "西洋菜": "watercress", "豆苗": "pea shoots",
  "卷心菜": "cabbage", "白菜": "napa cabbage", "胡萝卜": "carrot",
  "洋葱": "onion", "番茄": "tomato", "土豆": "potato", "茄子": "eggplant",
  "西蓝花": "broccoli", "花菜": "cauliflower", "黄瓜": "cucumber",
  "四季豆": "green beans", "青彩椒": "bell pepper", "生菜": "lettuce",
  "韭菜": "chives", "油菜": "bok choy", "青菜": "bok choy",
  "冬瓜": "winter melon", "萝卜": "daikon radish", "藕": "lotus root"
}
```

---

## 模块五：LLM 营养顾问（Gemini API）

### 功能描述
用户完成当日食谱规划并查看营养分析后，可点击"听听 AI 怎么说"，触发一次 Gemini API 调用，返回个性化的自然语言营养建议。

### 输入给模型的结构化 Prompt

```python
def build_nutrition_prompt(today_nutrition, user_profile, inventory_urgent):
    return f"""
你是一位专业的营养师助手。请根据以下信息，给出简洁、具体、可操作的中文建议。

【今日营养摄入（2人合计）】
- 热量：{today_nutrition['kcal']} kcal（目标：{user_profile['target_kcal']} kcal）
- 蛋白质：{today_nutrition['protein']}g（目标：{user_profile['target_protein_min']}–{user_profile['target_protein_max']}g）
- 脂肪：{today_nutrition['fat']}g
- 碳水：{today_nutrition['carbs']}g
- 钠：{today_nutrition['sodium']}mg（上限：2300mg）
- 维生素C：{today_nutrition['vitc']}mg
- 铁：{today_nutrition['iron']}mg
- 膳食纤维：{today_nutrition['fiber']}g

【用户基本信息】
- 两人体重：{user_profile['weight_a']}kg 和 {user_profile['weight_b']}kg
- 目标：维持体重，保持健康

【冰箱里需要优先消耗的食材】
{', '.join(inventory_urgent) if inventory_urgent else '无'}

请用中文给出：
1. 今日饮食的 1-2 句总体评价（直接说结论，不要废话）
2. 最需要注意的 1-2 个营养问题，并给出明天具体的补救建议（精确到食物和份量）
3. 如果有优先消耗食材，顺带建议一道简单的菜

格式要求：简洁，每条建议不超过 2 句话，不要列很多条，不要说"根据您的数据"这类废话开头。
"""
```

### API 调用实现

```python
import google.generativeai as genai
import os

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-1.5-flash")  # 免费 tier

def get_nutrition_advice(today_nutrition, user_profile, inventory_urgent):
    prompt = build_nutrition_prompt(today_nutrition, user_profile, inventory_urgent)
    response = model.generate_content(prompt)
    return response.text
```

### UI 交互
- 营养分析页面底部有"AI 营养建议"卡片，默认折叠
- 点击展开后触发 API 调用，显示 loading 状态（"正在分析今日饮食..."）
- 结果显示后可"复制"或"保存到今日记录"
- **限流保护**：每日最多调用 3 次，超出提示"今日建议已用完"（防止误操作刷爆免费 quota）

### GitHub 展示价值
README 中说明：演示了 LLM API 集成、结构化 prompt 工程、以及将营养数据转化为可操作建议的 agentic 功能设计。

---

## 模块六：语义菜谱搜索（本地 Embedding + ChromaDB）

### 功能描述
在菜谱库页面顶部加入语义搜索框，支持自然语言查询，如"我想吃点清淡的海鲜"、"快手蔬菜"、"适合冬天的汤"，返回最匹配的菜谱，而非简单的关键字匹配。

### 技术实现

#### 初始化（一次性，菜谱导入后运行）

```python
from sentence_transformers import SentenceTransformer
import chromadb

model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
# 模型约 400MB，首次运行自动下载，之后完全离线

client = chromadb.PersistentClient(path="./data/chroma")
collection = client.get_or_create_collection("recipes")

def build_recipe_text(recipe):
    """把菜谱的多个字段拼成一段描述文字，用于 embedding"""
    ingredients = "、".join([i['name'] for i in recipe['ingredients']])
    categories = "、".join(recipe['category']) if isinstance(recipe['category'], list) else recipe['category']
    return f"{recipe['name']}。食材：{ingredients}。烹饪方式：{'、'.join(recipe['cooking_method'])}。分类：{categories}。标签：{'、'.join(recipe['tags'])}。"

def index_all_recipes(recipes):
    """将所有菜谱向量化并存入 ChromaDB"""
    texts = [build_recipe_text(r) for r in recipes]
    embeddings = model.encode(texts).tolist()
    collection.upsert(
        ids=[r['id'] for r in recipes],
        embeddings=embeddings,
        metadatas=[{"name": r['name'], "category": r['category']} for r in recipes],
        documents=texts
    )
```

#### 搜索

```python
def semantic_search(query: str, top_k: int = 5):
    query_embedding = model.encode([query]).tolist()
    results = collection.query(
        query_embeddings=query_embedding,
        n_results=top_k
    )
    return results['ids'][0]  # 返回最匹配的菜谱 ID 列表
```

#### 触发重新索引的时机
- 新增菜谱时自动更新对应 embedding
- 批量导入后一次性全量 index

### UI 交互
- 菜谱库页面顶部：搜索框 + "语义搜索"标签（区别于普通关键字筛选）
- 输入查询后实时显示匹配结果（本地推理，无需等待网络）
- 结果卡片显示：菜名、匹配分类、是否占锅、备菜难度
- 可直接从结果一键加入今日菜单

### GitHub 展示价值
README 中说明：演示了本地 sentence-transformers embedding、ChromaDB 向量数据库、以及多语言语义搜索的完整实现，与简历中的 RAG 项目形成技术栈呼应。

---


## 模块八：全日营养追踪

### 功能描述
在营养分析页面，除晚餐外，还可录入早餐和午餐数据，生成完整的全日营养报告。支持默认方案快速填入，以及偶发垃圾食品的手动输入。

### 默认早午餐方案（种子数据）

系统初始化时预填以下固定方案，用户可在设置里修改：

**默认早餐（每日固定）**
```
慢炖杂粮粥：
- 干杂豆（黑豆/鹰嘴豆/绿小扁豆混合）：35g
- 发芽钢切燕麦：25g
- 三色藜麦荞麦 mix：15g
- 块茎（紫薯/南瓜/山药/红薯随机）：80g
- 奇亚籽/亚麻籽/火麻仁（轮转）：15g
- 燕麦麸皮：10g
配餐：
- 水煮鸡蛋（大）：1个（约60g）
- 混合水果：200g
饮品：
- 自制欧蕾：黑咖啡 240ml + 2%超滤牛奶 150ml
```
预估营养（系统计算后缓存）：约 580 kcal，蛋白质 30g，fiber 16g

**默认午餐（每日固定）**
```
- 高蛋白可可饮：2%超滤牛奶 300ml + 未碱化可可粉 10g
- 混合坚果：10g
```
预估营养：约 210 kcal，蛋白质 15g

### UI 流程
1. 今日营养分析页面顶部显示"早餐"和"午餐"卡片，默认预填固定方案
2. 若今天吃了不同的东西，有三种修改方式：
   - **替换整餐**：选择其他预设方案（可创建多个方案，如"外食日午餐"）
   - **手动输入 nutrition facts**：直接输入 kcal / 蛋白质 / 脂肪 / 碳水 / 钠（适合外卖、垃圾食品）
   - **Placeholder 食材模式**：输入"麦当劳巨无霸 1个"，系统查 USDA 返回营养值
3. 全日合计 = 早餐 + 午餐 + 晚餐（含预制菜自定义值）
4. 减脂模式：设置页面可录入每日热量目标，全日合计旁显示缺口/盈余

### 数据结构
```
MealPreset {
  id          String
  name        String    // 如"默认早餐"、"默认午餐"、"外食日午餐"
  meal_type   String    // breakfast / lunch / dinner / snack
  items       Json      // 食材列表或自定义 nutrition facts
  is_default  Boolean
}

DailyLog {
  id            String
  date          Date
  breakfast     Json    // MealPreset id 或自定义 nutrition facts
  lunch         Json
  dinner_recipe_ids  String[]
  dinner_placeholder Json?
  total_kcal    Float
  total_protein Float
  total_sodium  Float
  notes         String?
}
```

---

## 模块九：每日菜单 PDF 打印

### 设计规格
单张 Letter 纸（8.5×11 英寸），双面打印，生成 PDF 文件。

### 正面：Prix Fixe 菜单
```
┌──────────────────────────────────────────┐
│                                          │
│         [日期，优雅字体]                  │
│         Monday, May 4, 2026              │
│                                          │
│  ────────── Tonight's Menu ──────────    │
│                                          │
│  ◆ 海皇粉丝煲                            │
│    Glass noodles with mixed seafood      │
│                                          │
│  ◆ 海草拌黄瓜                            │
│    Chilled seaweed & cucumber salad      │
│                                          │
│  ◆ 清炒青菜                              │
│    Sautéed seasonal greens               │
│                                          │
│  ◆ 脆皮鸡翅                              │
│    Crispy baked chicken wings            │
│                                          │
│  ────────────────────────────────────    │
│                                          │
│  今日营养摘要 / Daily Nutrition           │
│  早午餐  790 kcal  蛋白质 45g            │
│  晚  餐  680 kcal  蛋白质 62g            │
│  全日合计 1470 kcal  蛋白质 107g  ✓目标  │
│  钠 2100mg ⚠  纤维 28g ✓                │
│                                          │
└──────────────────────────────────────────┘
```

### 反面：四格菜谱执行卡
```
┌──────────────────┬──────────────────┐
│  海皇粉丝煲       │  海草拌黄瓜       │
│                  │                  │
│ 食材：           │ 食材：           │
│ 粉丝 50g         │ 干海草 15g（泡发）│
│ 虾 150g          │ 黄瓜 200g        │
│ 扇贝 100g        │ 生抽 8g          │
│ ...              │ 芝麻油 5g        │
│                  │                  │
│ 步骤：           │ 步骤：           │
│ 1. ...           │ 1. 提前泡发海草  │
│ 2. ...           │ 2. ...           │
├──────────────────┼──────────────────┤
│  清炒青菜         │  脆皮鸡翅        │
│                  │                  │
│ 食材：           │ 食材：           │
│ 青菜 300g        │ 鸡翅 400g（预制）│
│ 蒜 10g           │                  │
│ 盐 2g            │ 步骤：           │
│                  │ 1. 烤箱200°C预热 │
│ 步骤：           │ 2. 烤25分钟      │
│ 1. ...           │ （可与其他菜     │
│ 2. ...           │  同时进行）      │
└──────────────────┴──────────────────┘
```

若当天少于4道菜，空格显示当日营养小结或留白。

### 技术实现
```python
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table
from reportlab.lib.styles import getSampleStyleSheet

def generate_daily_menu_pdf(date, recipes, nutrition_summary, output_path):
    doc = SimpleDocTemplate(output_path, pagesize=letter)
    # 正面：Prix Fixe 排版（自定义字体 + 装饰线）
    # 反面：四格 Table 布局，每格含食材 + 步骤
    ...
```

输出路径：`~/Downloads/menu_YYYYMMDD.pdf`，生成后自动打开预览。

### UI 入口
今日菜单确认后，右上角出现"🖨️ 打印今日菜单"按钮，一键生成 PDF。

---

## 数据持久化与安全

### SQLite 存储结构
所有数据本地 SQLite 存储。数据库文件放在项目 `/data/` 目录。主要表：
- `recipes` / `ingredients`：菜谱库
- `inventory`：实时库存
- `nutrition_cache`：USDA 查询缓存（永久有效，无过期时间）
- `daily_logs`：每日饮食记录
- `meal_presets`：默认早午餐方案

### 营养数据查询优先级（四级降级）
```
1. nutrition_cache（SQLite本地缓存）  ← 首选，毫秒级，离线可用
        ↓ 未命中
2. local_nutrition.json（本地补充表）  ← 中式特有食材、USDA缺失条目
        ↓ 未命中
3. USDA FoodData Central API          ← 兜底，结果自动写入 nutrition_cache
        ↓ 预制菜 / 包装食品
4. 用户自定义 nutrition facts         ← 完全覆盖，标注"⚠️ 估算值"
```
**缓存策略**：永久有效，无需刷新（猪五花的营养数据不会因进化而改变😄）。用户可手动点击"刷新"强制重新查询 USDA。

**离线优先**：随着使用时间积累，nutrition_cache 覆盖率趋近100%，系统几乎完全本地运行，无需网络。

### 三层数据安全策略
**第一层：自动 JSON 导出备份**
每次新增或编辑菜谱，自动导出完整菜谱库到 `~/Library/Mobile Documents/com~apple~CloudDocs/diet_backup/recipes_YYYYMMDD.json`（iCloud Drive）或用户指定目录。

**第二层：GitHub 私有仓库**
`/data/` 目录（SQLite + JSON导出）定期 push 到 private repo，保留版本历史，支持误删回滚。设置说明写入 README。

**第三层：下厨房保留**
继续在下厨房保留菜谱收藏作为人工备份，三层冗余确保菜谱永不丢失。

无账号系统，无云同步主数据库，纯本地运行。

---

## 页面结构

```
/ (首页)
  ├── /inventory          — Inventory 管理
  ├── /recipes            — 菜谱库列表
  │   ├── /recipes/new    — 新增菜谱
  │   └── /recipes/[id]   — 菜谱详情 + 编辑
  ├── /plan               — 今日食谱规划
  │   └── /plan/nutrition — 今日营养分析报告
  └── /settings           — 用户设置（体重、常驻蔬菜配置）
```

---

## 开发优先级（建议顺序）

> 用 Streamlit 起步，快速验证核心功能，再考虑迁移。

### 起步阶段（Streamlit，目标：2-3 周内有可运行的 demo）

1. **Phase 1**：项目初始化 + 安全配置
   - 建 `.env` + `.gitignore`（第一步，不可跳过）
   - SQLite 数据库 Schema
   - Streamlit 基础框架

2. **Phase 2**：Inventory 管理页面（最高频使用，先跑通日常使用）

3. **Phase 3**：菜谱库 CRUD + 下厨房爬虫脚本（一次性工具）

4. **Phase 4**：USDA 食材映射表 + 营养计算引擎

5. **Phase 5**：今日食谱规划页面（手动选 + Placeholder）

6. **Phase 6（AI 功能，可独立部署到 Streamlit Cloud）**：
   - 模块六：本地 Embedding 索引 + ChromaDB 语义搜索（无 API 费用，先做）
   - 模块五：Gemini API 营养顾问（配置好 key 后开启）
   - 部署到 Streamlit Cloud，生成 live demo 链接写入 README

7. **Phase 7**：全日营养追踪 + 默认早午餐方案配置

8. **Phase 8**：每日菜单 PDF 打印生成（Prix Fixe 正面 + 四格菜谱反面）

### 进阶阶段（可选，Next.js 重构）
- 迁移到 Next.js + FastAPI，UI 更精细
- 适合 16 周计划结束后继续迭代

---

## 与 16 周求职计划的时间对齐建议

| 时间节点 | 饮食系统进度 | 呼应的求职技能 |
|---|---|---|
| 第 3-4 周 | Phase 1-2（Inventory + DB） | SQLite / 数据建模 |
| 第 5-6 周 | Phase 3-4（爬虫 + 营养计算） | 数据管道 / API 集成 |
| 第 7-8 周 | Phase 5-6（AI 功能上线） | Embedding / ChromaDB（呼应 Week 7 nanoGPT） |
| 第 10 周 | Gemini 营养顾问 + Streamlit Cloud 部署 | RAG / LLM API（呼应 Week 10 RAG 升级） |
| 第 12 周 | README 完善 + live demo 链接 | 项目包装 / STAR 话术 |

---

## GitHub README 结构建议（求职展示用）

```markdown
# 🥦 Personal Diet & Nutrition Manager

> A full-stack AI-powered diet management system with LLM nutrition advice 
> and semantic recipe search.

## Key Features
- 📦 Real-time inventory tracking (vegetables + frozen proteins + dry goods)
- 🍳 Smart meal planning with parallel-cooking logic
- 🔍 **Semantic recipe search** — powered by sentence-transformers + ChromaDB
- 🤖 **AI nutrition advisor** — Gemini 1.5 Flash analyzes daily macros/micros
- 📊 Full-day nutrition tracking (breakfast presets + dinner + custom inputs)
- 🖨️ Printable daily menu — Prix Fixe style front + recipe grid back
- 📊 Nutrition analysis via USDA FoodData Central API (offline-first cache)

## Tech Stack
[列出所有技术，重点突出 AI/ML 部分]

## Live Demo
[Streamlit Cloud 链接]

## Architecture
[一张系统架构图，展示数据流]
```

---

## 附：关键设计决策备忘

### 系统设计哲学（写入 README）
> **方向感知 > 精确计量。** 这个系统的目标不是替代营养师，而是在日常烹饪决策里提供大致方向感——今天钠偏高、蛋白质还差一点、蔬菜够了。这个精度完全够用，而且是你实际会坚持用的精度。

### API & 安全
- **API Key 安全**：`.env` + `.gitignore`，Claude Code 启动第一步必须确认
- **Gemini 限流保护**：前端每日最多触发 3 次营养顾问调用，避免误操作
- **Streamlit Cloud 部署**：通过 Streamlit Secrets 管理 key，不写入代码

### AI / ML 组件
- **Embedding 完全离线**：`sentence-transformers` 本地运行，无 API 费用，无泄露风险
- **ChromaDB 持久化路径**：`./data/chroma`，与 SQLite 同目录，方便统一备份
- **新增菜谱时自动触发 embedding 更新**：无需手动重新索引

### 营养计算
- **四级降级查询**：本地缓存 → local_nutrition.json → USDA API → 用户自定义
- **永久缓存**：nutrition_cache 无过期时间，手动刷新才重查 USDA
- **调料处理**：不进 inventory，菜谱食材字段保证克重，USDA 通用条目匹配
- **豆瓣酱**：钠含量特殊，单独录入 local_nutrition.json（参考包装标注）
- **克重范围**：取中位数，UI 显示"~估算"字样
- **预制菜钠**：完全尊重用户自定义估算值，标注"⚠️ 估算值 + 用户备注"

### 菜谱库
- **数据安全**：三层冗余（自动 JSON 导出到 iCloud + GitHub private repo + 下厨房保留）
- **菜谱质量**：`needs_review` 状态用 AI 辅助提取步骤中的调料，用户 confirm 后转 `complete`
- **下厨房爬虫**：一次性脚本，不是持续运行的服务；遇滑动验证自动暂停 5 分钟重试，支持 `--resume` 续抓
- **中英文映射表**：`ingredient_translations.json`，随时扩充
- **分类多维度**：category 为 `String[]`，荤素维度（纯蛋白/荤菜/半荤半素/纯素）+ 形态维度（汤/凉拌/主食）独立标注，支持组合
- **调料实际摄入**：`intake_ratio` 字段（1.0 / 0.75 / 0.5 / 0.25 / 0），默认 1.0；UI 快捷选档，营养计算时自动乘以该比例

### 烹饪逻辑
- **炒锅限制**：`max_wok_dishes=1`（标准占锅），额外允许最多 1 道"轻占锅"菜（`uses_wok=true` 且 `cook_time_min ≤ 5`），参数化配置
- **干货柜**：不参与消耗计算，仅在推荐凉拌/辅料菜时作提示显示

### Inventory
- **克数存储**：叶菜和 Protein 均以克（g）为单位存储，"+1磅"按钮 = +453g 快捷方式；做菜后确认菜单自动按菜谱克重扣减，残余量原样保留
- **颜色逻辑**：库存 ≥ 900g（约2磅）显示橙色（提示优先消耗），0g 灰显，正常无色；推荐算法中库存多的蔬菜权重加分
