# 项目快速上下文

PRD 位置：`diet_system_PRD.md`（项目根目录）。实现前必须阅读对应 Phase 的 PRD 描述。

---

## 当前进度

| Phase | 描述 | 状态 |
|-------|------|------|
| Phase 1 | 项目初始化、.env/.gitignore、SQLite schema、Streamlit 骨架 | ✅ 完成 |
| Phase 2 | Inventory UI — 克数计数、±453g/±200g 快捷键、颜色逻辑、删除按钮 | ✅ 完成 |
| Phase 3 | 菜谱库 CRUD（`views/recipes.py`）、steps 字段、Gemini AI 清洗脚本 | ✅ 完成 |
| Phase 4 | USDA 营养引擎、四级降级查询、📊 营养分析页、食材明细 USDA 链接列 | ✅ 完成 |
| Phase 5 | 今日食谱规划页：本地推荐器 + 手动选菜 + Placeholder + 营养预估 + 库存扣减 | ✅ 完成 |
| Phase 6 | 本地 Embedding（sentence-transformers）+ ChromaDB 语义搜索 + Gemini 营养顾问 | ✅ 完成 |
| Phase 7 | 全日营养追踪（早午餐固定 + 水果选择 + 晚餐）、DRI 进度条、每日记录、历史折线图 | ✅ 完成 |
| Phase 8 | PDF 生成（reportlab + PingFang 中文字体）：Prix Fixe 正面 + 2×2 菜谱执行卡反面 | ✅ 完成 |

---

## 与原始 PRD 的偏差

### 1. Phase 3 — 爬虫已废弃，改用 Gemini AI 清洗
下厨房加强了反爬，爬虫方案放弃。用户通过其他方式下载菜谱数据后，使用 `scripts/clean_recipes_ai.py`（Gemini Flash，JSON 模式）批量清洗和结构化，写入 DB。

### 2. Phase 6 实现顺序后移
Phase 7（全日营养）和 Phase 8（PDF 打印）先于 Phase 6 完成。Phase 6 已补全：`utils/semantic_search.py`（ChromaDB 语义搜索）+ `utils/nutrition_advisor.py`（Gemini 营养顾问，3次/天限制）+ `scripts/build_recipe_embeddings.py`（一次性建索引脚本）。

### 3. 营养数据系统大幅扩展
PRD 描述的是三级降级；实际实现了**四级降级**（SQLite cache → local_nutrition.json → USDA API → 用户自定义），并扩展了完整工具链：
- `scripts/seed_nutrition_ai.py`：Gemini + Google Search grounding 批量填充营养数据，支持 `--recipe`、`--ingredient`、`--manual-update` 等模式
- USDA `fdcId` 存入缓存，食材明细表显示可点击 🔗 链接方便核验
- `local_nutrition.json` 中已修正 USDA 错误匹配（冬瓜、番茄）并补充常见食材（鸡蛋等）

### 4. 调料摄入比例改为 per-recipe
原 PRD 设计为全局设置（Settings 页 25% 滑块）。现改为每道菜独立字段 `condiment_ratio`，默认 1.0（100%）。DB migration step 7 已将原 0.25 全部更新为 1.0。Settings 页全局滑块已移除。

### 5. 分类体系全面重构
原分类混用荤素维度（荤/素/荤素）与形态维度（汤/凉拌/主食），语义混乱。已重构为：
- **荤素维度**（可留空）：荤、素、荤素 — 记录肉类情况，用于推荐器食材多样性策略
- **形态维度**（单选）：菜肴、主食、甜点、早餐、饮料、冷冻、预制
- "汤"和"凉拌"从 category 迁移至 `cooking_method` 字段
- DB migration step 8 已完成全量迁移（246 条菜谱）
- 晚餐推荐器仅从 `菜肴` + `主食` 中取菜（`_DINNER_CATS` 过滤）

### 6. 晚餐结构化推荐
推荐器从纯随机升级为结构化 slot 填充：目标 1凉拌 + 2热菜 + 1汤，凉拌/汤各尽力填 1 个，不足时用热菜补位。荤素分层策略仅作用于热菜池（`cat_pools_hot`）。

**易坏食材权重调优**（迭代后）：
- Score 加权：每个易坏食材 `+1.5`（原 `+3.0`，单食材 4× 权重过强），单菜累计封顶 `+3.0`
- Pass 1 硬约束**已移除**：原"必须覆盖所有易坏食材"会导致单一易坏库存（如只有黄瓜）100% 出现。现在 3-pass 系统简化为 2-pass（仅 disjoint 约束），易坏覆盖纯靠 score 偏好
- 实测：单易坏库存下，黄瓜出现率从 **100% → 40%**，仍偏好但有惊喜
- 覆盖/未覆盖状态仍在推荐卡片显示（🔴 覆盖易坏 / ⚠️ 未覆盖易坏食材）作为软提示

### 7. PDF 打印排版全面升级
原版：中文名 + 荤素/时长小字。新版：
- **正面（餐厅菜单风格）**：每道菜：◆ **中文名**（15pt）→ 英文名（10pt）→ 诗意简介（9pt 灰色，zh_desc 优先 en_desc 兜底）。打印顺序：凉拌优先 → 热菜 → 汤菜最后。**全营养摘要块**底部固定（bottom-up 绘制），完整 14 种营养素：
  - 头条 bold：热量 + 蛋白质
  - 宏量（灰）：脂肪 + 碳水
  - 钠 + 膳食纤维（带警告/达标标记）
  - 微量行 1：Ca / Fe / VitC / K
  - 微量行 2：VitD / VitA / Mg / Zn
- **背面（后厨执行卡）**：2×2 动态自适应网格。底部 **"Mise en place" (备料总览)** 区域分为两节：
  - **主料 / Main**：所有 `is_condiment == False` 的食材（去重）
  - **调料 / Condiments**：所有 `is_condiment == True` 的食材（去重）
  
  方便烹饪前先备齐主料再切调料，符合后厨实际工作流。Footer 高度从 1.0 inch 扩到 1.5 inch 以容纳两节。
- 菜谱新增三字段：`en_name`、`en_desc`、`zh_desc`（DB migration step 9）
- `scripts/gen_recipe_descriptions.py`：Gemini 批量生成英文/中文描述（batch 12，sleep 12s）

### 8. Inventory 五分类重构 + 份数模型
原分类（荤菜区 / 素菜区 / 调味料 / 常驻蔬菜 / 干货）不合理，「常驻蔬菜」废弃。现为：
- **叶菜/时令**（`leafy_veg`）：按**份数**记录（默认每份 500g）
- **蛋白/冷库**（`protein`）：按**份数**记录（默认每份 300g），含冷冻预制品
- **调味**（`seasoning`）：布尔有货/缺货
- **干货**（`dry_goods`）：布尔有货/缺货
- **其他**（`other`）：布尔有货/缺货，原常驻蔬菜迁移至此
- DB migration step 10 将所有 `staple_veg` 行改为 `other`
- 删除功能移入折叠 expander（减少误操作），布尔项 3列网格展示，`＋` 键改为 primary 蓝色

**份数模型（演化升级）**：原版按克数追踪（453g/200g 步进），实际使用中"半斤葱""一袋鸡胸"等天然分份更直观。现在：
- 新增 `portion_weight_g REAL` 列：每份对应克重（叶菜默认 500g，蛋白默认 300g，蛋白单条样品如 0.45 等也可手动设置）
- 新增 `is_frozen INTEGER` 列：冷冻标记
- `quantity` 字段现存储**份数**（而非克数），UI 显示 `X 份`
- 编辑：`number_input` 替代纯按钮，**可直接键入份数**，± 按钮辅助步进 1 份
- 顶部 metric 显示「X 份 · 约 Y 天份」估算（按 1kg 叶菜/天、0.6kg 蛋白/天）
- 叶菜/蛋白 tab 内现支持**混合两种模式**：主区按份追踪 + 「🛒 常备免记量区」（如葱、鸡蛋这类永远有的，用布尔标记不计份）；添加表单 `_add_mixed_form` 通过 `is_staple` 复选框选择 `boolean` 或 `quantity` 类型
- **UI 美化**（normal mode）：每项配 `●●●○○○○○○○` 份数填充条（`_portion_bar`，封顶 10 dots，超出加 `+`），一眼可见库存丰俭；按状态自动分组渲染（`_group_by_status` → 🔴 易坏优先 / 🟠 囤货较多 / 🟢 正常 / 🔘 已用完），组头自带条目数。shopping 模式保留扁平列表方便按原序批量录入

### 9. 今日规划「仅显示可做的菜」过滤
手动选菜区新增 toggle：开启后只显示库存中所有主料（非调料）均有货的菜谱。
- 严格逻辑：未录入库存的食材 = 无货（不视为"不影响"）
- 批量查询：单次 SQL 取全部菜谱主料，避免 N+1 查询

### 10. 全日营养追踪大幅升级
原版早午餐为硬编码固定值，晚餐无主食选项。现升级为：
- **早餐/午餐**：保留默认值，支持「今日不在家」skip toggle，支持自定义食材 expander（按行输入，覆盖默认）
- **晚餐主食**：可选 🍚 白米饭（默认 100g）/ 🚫 不吃 / ✏️ 自定义，独立于菜肴营养计算
- **晚餐临时加菜**（`fd_dinner_addons_txt`）：独立 expander，应对"今日乱炖/酸菜鱼里临时加莴笋/打个鸡蛋"等场景。文本按 `食材名 克数 单位` 格式逐行输入（**填总克数**，与菜肴一起 ÷2 算每人份），nutrition.py 和 plan.py 备餐控制台共用同一 widget key
- **保存按钮**改为 primary 蓝色，置于计算结果上方，醒目易找
- **7日分析**新增：食材多样性（唯一食材数、水果品种）、DRI 热力图（14 种营养素，✅🟡🔴 三色）
- 新增 DB 列：`dinner_staple`、`ingredients_snapshot`、`total_nutrients_json`（migration step 11）

### 11b. AI 营养数据批量录入
食材库 tab 顶部新增「⚡ AI 录入营养数据」expander：粘贴食材名（逗号/换行分隔），Gemini + Google Search grounding 联网查询每 100g 营养（14 项），返回 JSON 后展示为可编辑 `st.data_editor`。两步流程：
1. 文本输入两种模式，prompt 内自动识别：
   - **A) 仅食材名**（如「黄豆酱、冬瓜、芥兰苗」）→ Gemini 用 Google Search grounding 联网查 USDA / 中国食物成分表
   - **B) 原文数据**（从网页/书复制的成分表）→ 直接解析数值，**不触发联网搜索**（省 quota、更准、更快）
   
   模型：`gemini-2.5-flash` + `Tool(google_search=...)`（flash-lite 不支持工具；用户 tier 只支持 2.5+ 调用工具，配额 25 RPD 但用频率低足够）
2. 预览面板显示 14 项营养 + en_name + source_note，🟢 已缓存覆盖 / 🔵 新建。用户可手动校正再点「✅ 入库」→ `save_to_cache(source="ai_manual", usda_food_id=f"ai_manual_{name}")`

应对场景：seed 脚本批量填的数据有错（如 USDA 把"冬瓜"匹配成"冬瓜糖"），原本需要手动改 `data/local_nutrition.json` 或写 SQL，现在 UI 内直接修复。

### 11a. AI 库存批量录入
库存页顶部新增「⚡ AI 批量录入」expander：粘贴自由文本（如「黄瓜3份 羊肉两份 秋葵2份 半份生菜 一包冻虾仁」），Gemini 解析为结构化条目。两步流程：
1. 文本输入 → Gemini (`gemini-flash-lite-latest`，高配额避免 429) 输出 JSON：每项含 name / portions / category / item_type / is_perishable，支持中文数字（"两"="半"等）
2. 预览面板每项可调整分类和份数；🟢 累加（库存已存在则增加份数 / 切换 in_stock=true）、🔵 新建（创建新条目，AI 推断分类 + 易坏标记 + 默认 portion_weight_g）→ 确认入库

应对场景：从超市回来一次录入多种食材，比手动每个 expander 添加快很多。

### 11. AI 菜谱快速入库
菜谱库新增「⚡ AI 入库」按钮，三步流程：
1. 粘贴任意格式菜谱原文 → Gemini 解析（步骤内嵌食材克重、单位换算、分类判断、双语描述）
2. 预览解析结果（食材分主料/调料展示，步骤默认展开）→ 确认入库 + 自动建 ChromaDB 索引
3. 触发营养查询（四级降级），缺失食材显示 CLI 补充命令提示
- Prompt 基于 `clean_recipes_ai.py` 风格升级，使用 `gemini-2.0-flash`
- 同时生成 `en_name`/`zh_desc`/`en_desc` 三个描述字段

### 12. 个人摄入份量（serving_ratio）
每道菜新增 `serving_ratio` 字段（DB migration step 12，默认 1.0）：
- 菜谱编辑页新增滑块（0-100%，步进 5%，新建默认 50%）
- 参与晚餐营养计算：`食材克重 × serving_ratio ÷ 2 = 每人实际摄入`
- 菜谱详情页显示「个人份量」指标
- 适用场景：大份量汤/砂锅只喝一小碗、共享菜只取小份等

### 13. 烹饪时间拆分（实操 + 等待）
原 `cook_time_min` 单字段会把腌制、炖煮、蒸制等无需看守的时间也算进去，规划时无法判断"真正占人手"的强度。现拆分为：
- **`active_time_min`**（实操时间）：切菜、翻炒、调味等需要持续操作的时间
- **`idle_time_min`**（等待时间）：腌制、炖煮、蒸制、发酵等无需看守的时间
- `cook_time_min` 自动 = 两者之和（向后兼容 recommender、PDF、营养页等使用方）
- 炒锅冲突判断（`≤5min` → 轻占锅）改用 `active_time_min`（炖 40min 期间锅是空的，不算占锅）
- DB migration step 13：存量菜谱 `active_time_min = cook_time_min`，`idle_time_min = 0`
- AI 入库 prompt 同步要求 Gemini 输出两个字段

### 14a. 单菜时间预算过滤
推荐器 `recommend()` 新增 `max_single_dish_min` 参数：过滤总时长（`active_time_min + idle_time_min`）超过预算的菜。plan.py 推荐区一个 `number_input`（默认 90min，范围 15-300，步进 15），避免推荐 3 小时炖大菜在工作日下班场景。idle 时间也计入，因为是 wall-clock 时间，再快的菜也必须等 idle 阶段过完。

### 14. 菜系联动 (Cuisine Resonance) + 强搭配 (Recipe Anchoring)
新增 `cuisine` 字段和 `pairing_ids` JSON 数组（DB migration step 14）：
- **菜系联动**：菜谱编辑页 selectbox 选菜系（家常/川菜/日式/韩式等 20 个预设）。推荐器在 `_dynamic_boost` 中实时重算权重——同菜系菜品 ×2.5，自然涌现"日式定食/韩式家庭餐"风格的整餐。`_combo_stats` 输出 `dominant_cuisine`，在推荐卡片显示「🍱 菜系联动：日式」徽章。
- **强搭配**：菜谱编辑页 multi-select 选搭配菜（从全部菜谱中选）。推荐器中匹配到任一已选菜的 `pairing_ids` 时权重 +5.0（强信号）。手动选菜界面顶部出现「💡 推荐搭配」面板，一键添加，实现"鳗鱼饭 → 黄瓜小菜"灵魂伴侣式配餐。
- 选菜界面新增菜系下拉过滤；选中带菜系的菜后顶部显示「已锁定菜系」提示。
- 菜谱详情页显示菜系徽章和可点击的搭配菜谱卡片（点击跳转）。

### 15a. 确认扣减 → 自动保存营养记录
原 `_section_confirm` 在确认菜单后只扣减库存 + `mark_cooked`，**未保存营养记录** → 用户必须先去 📊 营养分析手动保存，否则确认后 `plan_rids` 被清空就再也算不出当天数据了。修复：在 `bc1` 按钮处理器开头加 JIT 调用 `compute_fullday_silent()` + `save_daily_log()`，**先存营养再扣库存**（顺序关键：silent compute 读 plan_rids，必须在清空前调）。失败容错：营养保存出错只显示 warning，不阻断扣减流程。

### 15. PDF 打印营养计算 JIT 化 + 跨页备餐控制台
原痛点：在 `plan.py` 打印 PDF 时，临时加菜 / 主食 / 水果 的最新状态必须先切到 `nutrition.py` 点击「计算全日营养」按钮才能生效，否则 PDF 使用过期或默认数据，体验割裂。

**三层重构方案**：
- **逻辑层**：`views/nutrition.py` 的 `_do_compute_fullday()` 拆分为：
  - `compute_fullday_silent()` — 纯计算，无 UI 副作用，可从任何页面调用，返回完整 fd_result 字典
  - `_do_compute_fullday()` — 瘦 UI 包装：spinner + 写 session_state + rerun
- **接口层**：`nutrition.py` 暴露 `get_pdf_nutrition_dict()` — JIT 调用 silent 计算，转换为 PDF 所需格式
- **UI 层**：`views/plan.py` 在打印按钮下方新增「🎛️ 备餐控制台」expander，复用与 `nutrition.py` 完全相同的 widget key（`fd_staple_choice` / `fd_fruits` / `fd_dinner_addons_txt` 等）。Streamlit 的 key 机制自动实现跨页双向状态绑定——任一页修改另一页同步可见。
- **简化**：`_print_pdf` 移除原 ~60 行的双分支兜底估算逻辑，改为单行 `nutr = get_pdf_nutrition_dict()`，消除"幽灵状态"

### 16a. 购物清单模块（PRD 完全未覆盖）
全新 `views/shopping.py` 页（侧边栏「📋 购物清单」），适配用户"看超市广告而不是看菜谱买菜"的真实习惯：
- **多店并排** `text_area`（每行最多 3 个）：每家店一个独立清单，自由文本一行一项，支持 `,` 或双空格分隔备注（如 `冷饮  $8`）
- **持久化**：单 JSON blob 存 `user_settings` 表（key=`shopping_list`），跨重启保留
- **自动保存**：每次 textarea 变更对比后写回 DB，无需点保存
- **跨店分析**：
  - 总需求 N 项独特食材 metric
  - 「📍 覆盖最多：Hmart (5/8 项)」推荐先去哪家
  - 「🔄 在多家店都列入：冷饮 → Hmart $8 · Costco」帮决策
- **⚡ AI 解析 WeChat 文本**（`gemini-flash-lite-latest`，无 grounding）：粘贴 `hmart: 西瓜, 黄瓜\ncostco: 大米` 类自由文本，自动拆店 + 拆项，预览确认后**累加**到现有清单（不覆盖）
- **采购完成 → 📦 入库**：选店后，逐项展示该店清单 + 每项可调整购买份数（0 = 没买）。点击「入库」：
  - 已在库存中的食材（按名字匹配）→ 直接累加份数 / 设为有货（无 AI 调用）
  - 新食材 → 批量传给 `views.inventory._ai_parse_inventory()` 让 Gemini 分类（叶菜/蛋白/调味/干货/其他）+ 判断 perishable + 设默认 portion_weight_g
  - 完成后清空该店清单 + bump textarea 版本计数器（避免 Streamlit 状态残留）
- **关键 Streamlit 技巧**：textarea 的程序化清空用 **per-store 版本计数器**（key=`shop_text_{store}__v{ver}`），bump `ver` 后下次渲染创建新 widget identity 强制 re-init from value=。直接 pop session_state key 不够可靠

### 16b. 食愿之书 Wishlist 模块
全新 `views/wishlist.py` 页（侧边栏「🌌 食愿之书」，文件名/内部 key 保留 `wishlist`），覆盖"提前记下下周想做的菜以防忘记"的场景：
- **数据**：`user_settings.wishlist` JSON list，每项 `{id, recipe_id, notes, target_date, added_at}`，target_date 可选
- **添加 UI**：multiselect 从菜谱库选（含「🥕 仅显示库存可做的菜」过滤）+ 可选备注 + 可选目标日期
- **状态徽章**：每项实时按当前库存计算 🟢 可做 / 🔴 缺料（显示具体缺哪几种），过期日期红色删除线警示
- **顶部汇总**：总计 / 现在可做数 / 今天 + 过期数 / 缺料食材独特种类，并提供「缺料汇总」code 块（方便复制到购物清单）
- **三处集成**：
  - **推荐器** `_score()` 新增 `wishlist_ids` 参数：今天/过期/无日期的 wishlist 菜 **+5.0**（同 pairing 强度），未来日期的菜不参与（避免被推到今天而违背原意）
  - **plan.py picker**：wishlist 内的菜带 ⭐ 标记，扫一眼就知道哪些是你想做的
  - **plan.py 确认扣减流程**：执行后自动 `remove_by_recipe_ids(rids)` 清理 wishlist，避免做完还要手动删
- **日期感知排序**：列表按 (过期/今天 → 无日期 → 未来日期) 三档分组，过期日期单独红色提示

### 16. PRD 未涉及的新功能
- 菜谱库页面"🗄️ 食材营养库"直跳按钮，通过 `_nav_pending` session state 机制切换页面
- 食材营养库"🔄 同步 local_nutrition.json"按钮：批量将 seed 脚本新写入的条目导入 SQLite 缓存，使其可在 UI 中直接编辑
- 菜谱库语义搜索 expander（ChromaDB，支持索引初始化按钮）
- 全日营养页 AI 营养建议 expander（Gemini，3次/天，按日重置）

---

## 待优化方向（未实现，按优先级排序）

1. **购物清单生成**：对比计划菜谱的主料总需求 vs 当前库存，输出缺口清单，按叶菜/蛋白/干货分组。现有数据已齐全，只缺 UI 入口和汇总逻辑。

2. **营养持续低值预警**：在历史 tab 加汇总行，标出"连续 N 天低于 80% DRI"的营养素（如铁、维D），比每次盯热力图更直接。

3. **`@st.cache_data` 性能优化**：`get_all_recipes()` 在多处反复调用均打开 SQLite，加 TTL=30s 的缓存可降低 200+ 条菜谱库的加载压力，推荐+营养计算同时触发时体感明显。（注：推荐器的 N+1 食材查询瓶颈已通过 `get_all_ingredients_grouped()` bulk fetch 修复，单次 recommend 从 ~650ms 降到 ~35ms。）

4. **双语界面切换（备用）**：用 `st.session_state["lang"]` toggle + `STRINGS = {"zh": {...}, "en": {...}}` 字典管理所有 UI 文案，英文侧用机器翻译初稿。架构不难，主要工作量在提取全部 UI 字符串（4 个 view 文件），适合展示 demo 时启用。

5. **多样性降权 (Category Cooldown)**：引入品类冷却时间。如果近期连续食用"虾类"，即使库存充足，也自动调低相关菜系的推荐权重，强制进行物种多样性轮换。

---

## 已知问题 / 待决策

1. **部分菜谱食材名不规范**：来自原始菜谱的非标准名（如"各种海鲜"、"冷水"、"适量"）会出现在 seed 脚本的缺失列表中，查询无意义。可在 `scripts/seed_nutrition_ai.py` 的 `_collect_missing` 中加过滤词列表，或手动忽略。

2. **Gemini 配额**：`gen_recipe_descriptions.py` 默认 `gemini-flash-lite-latest`（高配额）；`seed_nutrition_ai.py` 默认 `gemini-2.0-flash`（1500 RPD）；AI 入库也使用 `gemini-2.0-flash`；营养顾问使用 `gemini-2.5-flash`（25 RPD，但有 3次/天 应用层限制）。

3. **菜谱数据质量**：DB 中仍有约 149 条 `needs_review` 菜谱（调料未结构化）。可用 `scripts/clean_recipes_ai.py` 补全，或在 UI 中手动编辑。

4. **en_name/en_desc 覆盖率**：`gen_recipe_descriptions.py` 已批量生成，但部分菜谱可能因 Gemini 配额耗尽未完成。可用 `--dry-run` 查看缺口，`--force` 强制重新生成。

5. **serving_ratio 存量菜谱**：DB migration 默认值为 1.0（100%），存量菜谱均为 100%。可在各菜谱编辑页按实际食用习惯调整。

6. **语义搜索偏离**：当前的语义索引在处理“清淡一些”等模糊口味描述时存在偏移（由于向量空间中负面词汇的关联）。短期方案：手动在 UI 过滤或通过 cuisine/tag 硬过滤。

---

## 关键文件速查

| 路径 | 说明 |
|------|------|
| `app.py` | 单页 Streamlit 入口，侧边栏导航，支持 `_nav_pending` 跨页跳转 |
| `db/init_db.py` | SQLite schema 初始化（幂等），含 migration 逻辑（步骤 1-12） |
| `db/recipes.py` | 菜谱/食材 CRUD，含 `condiment_ratio`、`serving_ratio`、`en_name`/`en_desc`/`zh_desc` |
| `db/daily_log.py` | 每日记录 CRUD，含 `dinner_staple`、`ingredients_snapshot`、`total_nutrients_json` |
| `utils/nutrition_lookup.py` | 四级降级营养查询核心，`calc_nutrition_with_breakdown` 返回含 USDA 链接的明细 |
| `utils/recommender.py` | 加权随机推荐器，3-pass 系统 + 结构化 slot 填充（1凉拌+2热菜+1汤） |
| `utils/semantic_search.py` | ChromaDB 语义搜索，`paraphrase-multilingual-MiniLM-L12-v2`，持久化于 `./data/chroma` |
| `utils/nutrition_advisor.py` | Gemini 营养顾问，3次/天限制存于 `user_settings` |
| `utils/pdf_generator.py` | reportlab PDF 生成，PingFang 中文字体，餐厅菜单风格正面 |
| `views/inventory.py` | 库存 UI，5分类标签页，购物模式，份数（直接键入）/常备布尔混合双模式 |
| `db/inventory.py` | 库存 CRUD，含 `portion_weight_g`、`is_frozen`、`is_perishable` 字段 |
| `views/recipes.py` | 菜谱库 UI，含 AI 入库（`_view_ai_onboard`）、语义搜索、CRUD 表单 |
| `views/plan.py` | 今日规划 UI，含「仅显示可做的菜」库存过滤 toggle |
| `views/nutrition.py` | 全日营养 UI，含早午餐 skip/自定义、主食选项、7日 DRI 热力图 |
| `data/local_nutrition.json` | 手工/AI 维护的营养数据，优先于 USDA API |
| `data/ingredient_translations.json` | 中文→英文食材翻译，用于 USDA 查询 |
| `data/chroma/` | ChromaDB 持久化目录（recipes 集合，cosine 距离） |
| `scripts/seed_nutrition_ai.py` | Gemini 批量填充营养数据，支持 `--manual-update --data "..."` |
| `scripts/clean_recipes_ai.py` | Gemini 批量清洗菜谱结构（食材克重、步骤、分类） |
| `scripts/gen_recipe_descriptions.py` | Gemini 批量生成 en_name/en_desc/zh_desc，支持 `--recipe`/`--force`/`--dry-run` |
| `scripts/build_recipe_embeddings.py` | 一次性建立 ChromaDB 索引（全量菜谱） |

## 运行环境

- Python 3.9（系统 `python` 是 2.7，始终用 `python3.9`）
- 启动：`python3.9 -m streamlit run app.py`
- DB：`data/diet.db`，通过 `python3.9 db/init_db.py` 初始化（幂等）
- 首次使用语义搜索：`python3.9 scripts/build_recipe_embeddings.py`（或在菜谱库 UI 点击初始化按钮）
