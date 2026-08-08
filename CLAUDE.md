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
- 菜谱编辑页新增滑块（0-100%，步进 5%，**新建默认 100%**）
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

### 17. 性能优化：`utils/cache.py` 缓存层
排查"感觉运行卡"问题后发现两处主因并修复：
- **`init_database()` 曾在每次 rerun 都执行**：`app.py` 顶层无条件调用，Streamlit 每次任何页面的任何交互都会重跑整个脚本，导致建表 SQL + 14 步 migration 检查每次点击都跑一遍。改用 `@st.cache_resource` 包裹，全进程只跑一次。settings 页里重复的 `init_database()` 调用一并去掉。
- **零缓存的高频 DB 读**：`get_all_recipes()` / `get_all_inventory()` / `get_all_ingredients_grouped()` 在菜谱库、今日规划、营养分析、食愿之书、购物清单、推荐器等处反复调用，每次都开新连接查全表 + 解析 JSON，`plan.py` 单次 render 内甚至会连续调 `get_all_inventory()` 2-3 次。新增 `utils/cache.py`：`@st.cache_data` 包裹三者（inventory TTL=15s，recipes/ingredients TTL=30s），`toggle_in_stock`/`set_quantity`/`add_item`/`delete_item`/`toggle_perishable` 包装为"写入后自动 `.clear()`" 的版本，调用方无需手动记得失效缓存；`recipes.py`/`plan.py` 里的 create/update/delete/mark_cooked 调用点显式调 `invalidate_recipes_cache()`。
- db/ 层保持无 streamlit 依赖（框架无关），缓存包装统一放在 `utils/cache.py`，视图和 `utils/recommender.py` 都改为从这里导入而非直接 `db.recipes`/`db.inventory`。
- 顺带装了 `watchdog`（加入 requirements.txt），消除 Streamlit 无该模块时的轮询式文件监听警告。

### 18. 正式上线：Cloudflare Tunnel + Access + launchd
自建 Mac 常驻服务，而非用免费 PaaS——后者的临时磁盘跟本项目依赖的本地 sentence-transformers 模型 + ChromaDB + 需要真正持久化写入的 SQLite 天生不合（容器重启/重新部署会清空数据）。
- 域名 `<your-domain>`（Cloudflare 注册），实际访问地址 `diet.<your-domain>`
- `cloudflared` 装在 `~/.local/bin`（这台 Mac 的 Command Line Tools 太旧，`brew install` 编译失败，改用 GitHub Releases 的预编译 darwin-amd64 二进制），命名 tunnel `diet-manager`（`~/.cloudflared/config.yml`，ingress 指向 `http://localhost:8501`）
- **launchd** 两个 LaunchAgent 常驻：`~/Library/LaunchAgents/com.dietmanager.streamlit.plist`（`streamlit run app.py --server.headless true --server.port 8501 --server.address 127.0.0.1 --server.fileWatcherType none`）和 `com.dietmanager.cloudflared.plist`（`cloudflared tunnel run diet-manager`），`RunAtLoad` + `KeepAlive` 实现开机自启/崩溃重启。改代码后需要 `launchctl kickstart -k gui/$(id -u)/com.dietmanager.streamlit` 才会生效（不是简单保存文件就自动重载）
- **`--server.address 127.0.0.1` 是安全要求，别去掉**：Streamlit 默认绑 `*:8501`，也就是同一个家庭 WiFi 下**任何设备都能直连 `http://<内网IP>:8501`，完全绕过 Cloudflare Access**；应用层密码门又已经关掉，那条路径上等于零认证。cloudflared 是从本机 localhost 连过去的（见 `~/.cloudflared/config.yml` 的 ingress），所以绑定到回环地址不影响正常访问。实测：局域网地址连接被拒绝，`diet.<your-domain>` 正常返回 Access 登录页
- **认证**：Cloudflare Access（Zero Trust）在边缘拦截未授权请求，登录方式为 **One-Time PIN**（邮箱验证码，免注册 Cloudflare 账号），Access controls → Applications 里创建，Policy 白名单两个家庭成员邮箱，Session Duration 30 天。踩过的坑：Application Domain 的 Subdomain 字段只能填 `diet`（填成完整 `diet.<your-domain>` 会被和 Domain 字段拼成 `diet.<your-domain>.<your-domain>` 导致证书报错）；Authentication 标签页要把 "Accept all identity providers" 关掉、只勾 One-Time PIN，否则会连带弹出 "Sign in with Cloudflare" 账号登录
- 应用层原有的简单密码（`.env` 的 `APP_ACCESS_PASSWORD`，`app.py` 的 `_check_password()`）已停用（值置空即跳过校验），代码逻辑保留作为 Access 失效时的本地兜底，不需要删除
- Streamlit 自定义主题：`.streamlit/config.toml`（暖橘/奶油白配色，替代默认红）

### 19. 手机端适配
- `st.set_page_config` 的 `initial_sidebar_state` 从 `expanded` 改 `auto`（窄屏默认收起，不占屏幕）
- `app.py` 顶部注入全局 CSS（`@media (max-width: 640px)`）：按钮/输入框触控目标放大、`input/textarea/select` 字号锁 16px（防 iOS Safari 聚焦自动缩放页面）、`h1`~`h4` 按层级缩放字号（`1.5rem → 1.3rem → 1.15rem → 1.05rem`）+ `word-break: keep-all`（防止「今夜のおすすめ」这类日文假名连写在窄屏被从奇怪的位置拆成两行、菜名/小节标题字号大于页面主标题的头重脚轻问题）
- 宽表格/dataframe 加 `overflow-x: auto`，防止撑开整页横向滚动
- **重要坑**：`st.columns()` 在窄屏（约 ≤640px）下会强制纵向堆叠成整行块级元素，这是 Streamlit 内置行为，靠调列宽比例改不了。需要"同一行不换行"的紧凑列表（比如一行一个菜名+操作按钮）时，改用较新版本 Streamlit（本项目 1.50.0）的 `st.container(horizontal=True, horizontal_alignment="distribute")`——这是真正的横向 flexbox 布局，不受该断点影响，`distribute` 效果类似 `justify-content: space-between`（详见 `views/wishlist.py` 的菜谱浏览列表）

### 20. 🍽️「今夜のおすすめ」页面（新增，`views/tonight.py`）
家人（不需要单独账号，走 Cloudflare Access 邮箱验证码即可访问）打开链接后的只读视图，展示当日晚餐菜单 + 每人营养摘要，替代"每天口头/微信告知晚上吃什么"。
- 数据来源：`views/plan.py`「📢 发布今日菜单」按钮**手动**写入 `user_settings.today_menu`（JSON blob，含日期戳）——刻意不做实时同步，避免家人看到你还在纠结、反复横跳的选菜过程
- `get_today_menu()` 按日期戳判断是否当天有效，跨天自动失效显示"还没有发布菜单"
- **营养口径必须和 `nutrition.py` 一致**：晚餐 = (菜肴 + 占位菜 + 临时加菜) ÷ 2 + 主食（主食本身就是按每人份记的，不除以 2）。初版只算了菜肴，漏掉主食和加菜，实测比真实值**低 27%**。现在 `publish_today_menu()` 在发布时把 `staple_ings` / `addon_ings` 从 `fd_*` 控件快照进 blob（而不是渲染时读 session state），这样家人看到的数字既完整、也不会因为你事后改了备餐控制台而漂移
- 读取端对老格式 blob（没有这两个 key）做了兜底，不会崩
- 已设为默认首页（`app.py` 的 `PAGES` 字典第一项），符合"打开链接第一眼就看到今晚吃什么"的核心使用场景

### 21. 喵喵亭品牌化
项目更名/UI 主题从通用「饮食健康管理」改为「喵喵亭」（域名谐音），吉祥物喵喵实际是一只博美犬（不是猫），但 🐾/"喵"语气词纯走可爱风保留，不追求物种准确。
- `assets/mimi/`（**已加入 `.gitignore`**，私人宠物照片不随公开仓库分发）存放裁切好的素材：`favicon.png`（浏览器标签图标）、`avatar.jpg`（侧边栏圆形头像）、`hero_tonight.jpg`（「今夜のおすすめ」页头图）、`pool_*.jpg`（「今日份喵喵」轮换池，当前 10 张）
- 侧边栏头像用 `st.container` + base64 内联 `<img>` 实现圆形裁切（而非 `st.image`，方便精确控制 CSS：`.mimi-avatar-wrap` 外圈固定 `box-sizing: content-box` + `overflow: hidden`，避免 Streamlit 全局盒模型把描边和图片挤到重叠）
- 「今日份喵喵」按**日期哈希**（非随机数）从池子里选图，同一天所有人看到的是同一张、次日自动换——不用 `random`，因为希望它是"今天的固定一张"而不是每次刷新都跳
- `app.py` 的 `_mimi_asset()`/`_mimi_img_tag()` 做了防御：素材文件不存在时优雅降级（favicon 退回 🐈 emoji），保证仓库被裸克隆（没有 `assets/mimi/`）也能正常跑
- 多处关键提示文案猫咪化（🐾 + 喵语气词）：今夜のおすすめ空状态、今日规划的菜单为空/营养预估占位/发布/确认扣减成功提示等

### 22. 今日规划确认流程保留菜单（按菜追踪已扣减）
原逻辑：点「✅ 确认并结束」扣减库存后立即清空 `plan_rids`/`plan_ph`，导致确认后没法再打印 PDF 或发布今日菜单（📢 按钮消失，因为判断条件是 `if rids`）。

改为菜单**不清空**后，"整单是否已完成"用一个布尔量表达是不够的——第一版用 `plan_done` bool，结果确认后再加一道菜就永远扣不了它的库存（确认按钮已被"已完成"面板取代）。现在改为**按菜粒度**追踪：
- `plan_deducted`（list[str]）记录今天已经扣过库存的 recipe ID；`pending = rids - deducted` 才是待扣的
- `pending` 非空 → 显示确认按钮（文案变成「新增了 N 道菜，确认并扣减」），且 `_compute_deductions(pending)` / `mark_cooked` / 食愿之书消书都**只作用于 pending**，已结算的菜不会被重复扣
- `pending` 为空且 `deducted` 非空 → 显示"已确认"面板 + 「🆕 开始新一轮规划」。注意判空条件是 `if not rids and not deducted`（不能只判 `rids`，否则确认后把菜全删光会卡在无按钮可点的死状态）
- **跨午夜必须整体重置**（`_init`）：只清 `plan_deducted` 而留着 `plan_rids` 是灾难——昨晚的菜单还在页面上，次日随便一点，整桌菜的库存**再被扣一遍**，`mark_cooked` 重打时间戳，而且 `save_daily_log(datetime.now(), 昨天的rids)` 会把今天的营养记录覆盖成昨天的晚餐。现在跨天时 `_RIDS`/`_PH`/`_CONFIRM` 一并清空
- **`plan_deducted` 会从数据库补种**（`_cooked_today()`，查 `date(last_cooked)=今天`）：光靠 session state 撑不住——刷新、开第二个标签页、服务重启都会让它归零，而扣减是永久性的 DB 写入，于是重新选一遍同样的菜就能二次扣减。`mark_cooked` 和扣减在同一个 handler 里执行，所以 `last_cooked` 正好是可靠凭据
- 菜单里已扣减的菜显示 `✓ 已扣库存` 绿标——否则"删掉再加回来"的菜会神秘地没有确认入口，用户看不出原因
- **分批确认不会重复扣共用食材**：扣减是按**食材**（`_compute_deductions` 用 set 去重，每种食材一行默认 1 份），而确认是按**菜品**，两者粒度不一致。先确认 A 再加 B，若两道菜共用毛豆，毛豆会各扣一次；一次性确认两道则只扣一次——同一顿饭、点法不同结果差 1 份。现在把已确认菜品的主料算作 `covered`，在后续批次里默认 0 并标注「今天已扣过」（仍可手动改），两种点法结果一致
- **纯「✏️ 临时占位菜」的菜单也能确认**：守卫原本只看 `rids`，占位菜菜单连确认按钮都没有；而且 `compute_fullday_silent()` 压根不读 `plan_ph`，所以这条路径录入的食材**永远进不了 `daily_logs`**（只在规划页右侧的实时预估里出现过一次）。现在 `plan_ph` 并入 dinner_ings（同样 ÷2），并用 `plan_ph_done` 存占位菜列表的签名来判断是否已记录（改了内容会重新变为待确认）
- `_start_new_round()` 清 `plan_deduct_*` widget key 和 `plan_ph_raw`（否则占位菜文本框还留着上一轮原文，误点「✔ 更新占位菜」会整体复活），但**不清 `plan_deducted`**（那是"今天已经从库存里扣走了什么"的事实，清掉就能靠重新选菜二次扣减）。撤回已发布的家人菜单是**有条件的**：只有当已发布的菜单确实是正在清空的这一单时才撤——否则晚上 6 点做完饭想顺手规划明天，会把家人正在看的今晚菜单当场清空
- 防厌倦提示跳过 `deducted` 中的菜：`mark_cooked` 刚打上时间戳，菜单又不清空了，否则会立刻自己警告自己"48小时内刚做过"
- 确认结果（尤其是"营养记录失败"的报错）存进 `plan_confirm_msg` 由下一次 render 显示——原来直接 `st.success()` 后紧跟 `st.rerun()`，rerun 会丢弃当前 run 渲染的一切，营养保存失败的警告用户根本看不到
- 手动选菜区「🥕 仅显示可做的晚餐」toggle 默认改为**开启**（原来要手动开）

### 23. 食愿之书交互重做 + 自定义心愿
原来的 `multiselect` 下拉在手机上体验差（点击直接弹键盘/搜索面板，遮挡屏幕），改为跟「今日规划」picker 同款的两段式：暂存区（选中但未保存的菜，可移除、统一填日期/备注）+ 可折叠浏览列表（一行一个菜名，`st.container(horizontal=True, horizontal_alignment="distribute")` 实现名称靠左、"＋"顶格靠右，手机不会被拆行）。
- 新增**自定义心愿**支持（菜谱库里还没收录的菜也能先记下来）：暂存列表里用 `"custom:"` 字符串前缀标记非菜谱条目；写入时对应记录变成 `recipe_id: None` + `custom_name: "菜名"`。为此加固了下游所有假设 `recipe_id` 一定存在的地方：`get_active_wishlist_recipe_ids()`、`_summary_metrics()`、`views/plan.py` 的 `wishlist_rids` 集合、`_list_view()` 的展示与可做/缺料判断，均已加 `recipe_id` 为空时的跳过逻辑
- **踩过的三个坑（都已修，改这块时别改回去）**：
  1. 暂存区的移除按钮 key 曾用条目内容本身（`key=f"wish_unstage_{item}"`）。菜谱选择有"已选"禁用保护不会重复，但自定义名字没有——同一个名字输两次就触发 `StreamlitDuplicateElementKey`，**整页从那一行往下全部停止渲染**（保存按钮、浏览列表、卷宗全消失）。现在 key 改用**列表下标**，并且加入前先查重
  2. 自定义条目**不参与去重**：`existing_rids` 只收集 `recipe_id`，自定义的 `recipe_id` 是 None，于是同名心愿会无声堆叠。现在按 `custom_name` 单独去重
  3. 保存后 `st.rerun()` 会丢弃当前 run 渲染的一切，所以"已在书中被跳过"的提示用户根本看不到，条目却从暂存区消失了 → 看起来像凭空蒸发。现在结果存进 `wish_stage_msg` 由下一次 render 显示；同时浏览列表把已在书中的菜标成灰字「已在书中」+ 禁用 ＋ 按钮，从源头避免这种无效操作
  4. 「＋ 记下」按钮原来带 `disabled=not custom_name.strip()`：Streamlit 里输入框的内容要等失焦触发 rerun 才到服务端，所以**第一次点击总是落在还处于禁用态的按钮上被吞掉**，必须点两次。现在去掉 `disabled`，改在 handler 里校验
- 局限：自定义条目没有食材清单，不参与推荐器加权、不参与"确认扣减"后的自动消书；等正式录入菜谱库后需要手动把这条自定义的删掉、换成真菜谱那条

### 25. 缓存层与 widget 状态的两类静默陷阱（排查过一轮，别踩回去）
- **`st.number_input(value=..., key=固定key)` 的写回模式会静默回滚别处的写入**（`views/inventory.py` 份数编辑）。Streamlit 在 key 已存在时**忽略 `value=`**，控件会一直返回本 session 里它自己持有的旧值；配合 `if new_qty != qty: set_quantity(...)` 这种"发现不一致就写回"的模式，**旧值反而会覆盖数据库的新值**。实测：库存 3 份 → 今日规划扣成 2 份 → 库存页任意点击一下 → 数据库被写回 3 份，扣减凭空消失，而 `plan_deducted` 还记着"已扣过"所以不会再扣。修法是把数据库当前值编进 key（`key=f"edit_{id}_{qty}"`），值一变就重建控件。**任何"读 DB → 渲染 widget → 发现不同就写回 DB"的地方都要这样处理**
- **widget key 会被 Streamlit 回收**：某次 run 没渲染该 widget，它的 session_state 就被删掉。备餐控制台的 `fd_*` 系列因此存在静默丢数据——今日规划和营养分析都渲染它们所以互跳没事，但**途经任意第三个页面（库存/菜谱库…）就清零**，用户填的临时加菜消失且无提示，之后确认扣减时 `compute_fullday_silent()` 读到空值，当天营养记录就漏掉那道菜。现在用 `restore_fd_state()` / `remember_fd_state()`（`views/nutrition.py`）镜像到普通 key，渲染前恢复、渲染后记录
- 菜谱写操作（create/update/delete/mark_cooked）已统一收进 `utils/cache.py` 的自动失效包装，和库存写操作一致。**不要再从 `db.recipes` 直接导入这四个函数**——原来靠"每个调用点记得手动 `invalidate_recipes_cache()`"的约定，漏一个就是查不出来的脏读
- AI 批量录入同名食材：`existing` 快照在循环外只建一次，同名条目第二次会读到写入前的旧值 → 覆盖而非累加（3+3+2 得到 5 而不是 8），新建的同名条目还会被建成两条。现在写入后同步更新快照、新建后登记进 `existing`

### 24. iCloud 自动备份（`ICLOUD_BACKUP_PATH` 从死配置变成真功能）
`config.py` 里早就定义了 `ICLOUD_BACKUP_PATH`，但从未被任何代码实际使用——目标文件夹在这次之前根本不存在。补上：
- `scripts/backup_to_icloud.py`：备份 `data/diet.db` + `data/local_nutrition.json` 到 `~/Library/Mobile Documents/com~apple~CloudDocs/diet_backup/YYYY-MM-DD/`，按日期分目录，自动清理 14 天前的旧备份。**不备份** `data/chroma/`（语义搜索索引，属于可随时用 `build_recipe_embeddings.py` 重建的派生数据，不是数据源）
- **DB 必须用 `sqlite3` 在线备份 API（`Connection.backup()`），不能用 `shutil.copy2`**：数据库是 **WAL 模式**（`PRAGMA journal_mode=wal`），且备份跑的时候 Streamlit 常驻进程正开着连接，已提交的事务可能还躺在 `diet.db-wal` 里没合并进主文件。实测过：直接 copy 主文件会**静默丢掉已提交的数据**（备份看起来成功，等真要恢复才发现少数据）。backup API 会自己处理加锁和 WAL，产出一致快照，`PRAGMA integrity_check` 通过、六张表行数与源库完全一致
- `~/Library/LaunchAgents/com.dietmanager.backup.plist`：`StartCalendarInterval` 每天 03:00 自动跑一次（不是 `KeepAlive` 常驻服务，是定时任务）
- 数据量很小（DB ~1MB + 营养库 JSON ~0.3MB，14 天滚动备份总共几十 MB），iCloud 免费 5GB 额度完全覆盖，不产生费用

### 26. 营养目标个人化：热量 / 供能比例 / 补剂（`⚙️ 设置`）
排查「1680 kcal 却显示 84% 达标」时挖出一串相互纠缠的问题，逐层修：

**a. `target_kcal_per_day` 是个死设置**。它从项目初期就存在于 `user_settings`（值 1800），但**没有任何代码读过它**——所有 DRI 条都在跟 `_DRI` 里写死的 2000 比。现在 `get_kcal_target()` 读设置，`_dri()` 统一分发，营养面板/7日热力图/AI 建议三处共用。

**b. 只改热量会让各条 DRI 互相打架**。碳水 300g / 脂肪 65g 这些 FDA 标签值的前提就是 2000 kcal 饮食（300×4=1200 kcal 正好 60%）。目标降到 1700 后碳水还要求 300g，光碳水就占 71%，那条**永远不可能在不超热量的前提下达标**。

**c. 各自按固定比例缩放也不行**。标签假设蛋白质只占 10%，而按体重算出来是 19%，三项加起来 108%。最终方案：三大营养素**统一由供能比例推导**（`get_macro_split()`，存 `macro_pct_*`），构造上必然合计 100%。当前设定 蛋白 25% / 脂肪 30% / 碳水 45%（AMDR 范围内：蛋白 10-35%、脂肪 20-35%、碳水 45-65%）。设置页可调，合计不等于 100% 不让保存。

**d. 蛋白质有两套目标在打架**。`_results` 的蛋白质条用「体重×系数」，碳水/脂肪条用能量占比，同一页面上可能一个说达标一个说不足。现已全部走 `_dri()`。`views/plan.py` 的晚餐蛋白质目标也改为**全日目标的 35-45%**（原来是自己另算一套「两人体重×系数」）。

**e. 每日补剂**（`get_supplements()` / `daily_supplements`）：维D 这类靠食物极难达标，不记进来 DRI 条会一直是红的、失去预警意义。设置页可填维D/钙/铁，自动加进全日合计。

### 27. 脂肪细分（饱和 / 单不饱和 / 多不饱和）
DB migration **step 15** 加三列 `satfat/monofat/polyfat_per_100g`，贯通 USDA 抓取（营养素 ID 1258/1292/1293）、缓存读写、AI 录入、手动修正表单、缓存库表格、营养面板、历史热力图。

- **缺数据必须保持 NULL 而不是 0**：界面靠「是否为 NULL」判断该食材有没有细分数据。`MealNutrition.fat_detailed` 记录「有细分数据的那部分脂肪有多少克」，据此显示覆盖率——否则「饱和 0.3g」出现在一顿 22g 脂肪的饭旁边会被当成真实比例，实际只是大部分食材没数据
- 饱和脂肪按**上限**逻辑判色（像钠），上限 = 热量目标 × 10%（AHA 建议）
- 回填分两条路：`scripts/backfill_fat_detail.py` 按 USDA ID 回查（**只覆盖 37 条**——注意 `usda_food_id` 里有 614 条是 `local_` 前缀，不是真 FDC ID，我一开始误判成 664 条可回填）；`scripts/ai_fill_fat_detail.py` 按**脂肪贡献量排序**用 Gemini 补，因为脂肪高度集中——补前 46 种就把加权覆盖率从 16% 拉到 83%

### 28. 默认早午餐拆掉硬编码字典，改走营养引擎
`_BFST_BASE` / `_LUNCH_BASE` 是 PRD 时期的估算字典。宏量还算准（早餐 580 vs 实算 596），但**微量营养素错得离谱**：维A 60µg vs 实际 630µg（**低估 10.7 倍**）、镁 80mg vs 275mg（低估 3.4 倍）。原因是南瓜/红薯是 β-胡萝卜素大户（红薯 709 µg RAE/100g）、火麻仁镁密度 700mg/100g，当年估算没算进去。

**这是「维A/镁 常年 🔴」的主因**，比食材库数据缺口的影响大得多。现改为 `_BFST_INGS` / `_LUNCH_INGS` 真实食材清单，跟晚餐走同一套 `calc_nutrition_with_breakdown()`——以后食材数据改进它自动跟着准。同时补录了 9 种缺失配料（干杂豆/钢切燕麦/三色藜麦/红薯/奇亚籽/火麻仁/燕麦麸皮/黑咖啡/混合坚果）。

**教训**：任何"预先算好的营养数字"都会悄悄失真，能走引擎就别写死。

### 29. 营养数据体检 + 微量营养素回填
`🗄️ 食材库` 顶部新增「🩺 营养数据体检」，报告两类**静默失效**：
- **① 完全未收录**：缓存和 local json 里都没有 → 计算时整个食材被忽略
- **② 有记录但空壳**：缓存里有这条但热量/蛋白/脂肪/碳水全 0（且钠也为 0）→ 看着匹配上了，实际按 0 计入

判据细节：**必须带上"钠也为 0"**，否则盐、小苏打这类本身零卡但含钠的会被误报。另外**不能因为 local json 里有数据就不报**——缓存是 tier 1，会 shadow local json（「牛排」那个坑），排除掉恰好会掩盖最危险的情况。

按「被几道菜使用」排序，默认隐藏水类和 `步骤1`/`详见步骤` 这类导入噪音。

微量营养素同样存在大面积缺口（维A 只有 28% 条目有数据、维D 11%），`scripts/ai_fill_micronutrients.py` 按用量排序回填，实测维A/镁/锌加权覆盖率 ~50% → ~86%。

### 30. 其它交互改进
- **推荐区单菜添加**：每道菜右侧 ＋ 按钮，可跨两个套餐挑菜，不必整套采用。widget key 必须带**套餐序号**（同一道菜可能同时出现在两个套餐里，撞 key 会让整页停止渲染）
- **食材修正表单**（`ingredient_fix_form`）：食材明细下方直接改营养数据，下拉只列当前明细里的食材。因为缓存库那个 `data_editor` 是虚拟滚动的，**Chrome 的 Ctrl+F 搜不到**（只有可见行在 DOM 里），在 665 行里翻找很痛苦；缓存库也补了名称筛选框
- **水果种类可扩展**：`multiselect(accept_new_options=True)` + 存 `user_settings.custom_fruits` 持久化（光靠 `accept_new_options` 只能维持当前会话）
- **食用油统一为牛油果油**：改的是**数据层**不是菜谱——菜谱里的「油」本就是"随便什么食用油"的意思，改成「牛油果油」要动 34 道菜且以后换油还得再改。把 8 个泛称（油/食用油/烹饪油/热油/炸油/无味油/植物油/牛油果油）的营养值换成牛油果油即可。橄榄油/芝麻油/猪油/黄油等有特定风味用途的保持原样
- **历史 DRI 热力图 9 项 → 15 项**：`daily_logs` 一直存着 14 种营养素，热力图却只显示 9 种，钾/维D/维A/镁/锌白存了几个月。补全后立刻暴露出维D 长期 24%（后由补剂设置解决）。老记录缺的字段显示「—」而不是 0%，否则饱和脂肪会对所有历史记录显示 ✅

### 31. GitHub 私有仓库自动备份（`scripts/backup_to_git.py`）
iCloud 那条链路查下来是账号层问题（CloudKit `Code=20 ManagedAccountRestricted`，家庭共享账号查不到配额），治不好也不必治。改用私有仓库：
- 存**文本 SQL dump 而不是二进制 .db**：git 对二进制几乎无法增量压缩，每天提交 1MB 的 .db 一年会涨到 ~133MB；文本每天只增加变化的几 KB，一年约 5-10MB。附带好处是能在网页上直接看 diff
- dump 前用 sqlite3 在线备份 API 取一致性快照（同 24 条的 WAL 问题）
- **没有独立的 LaunchAgent**：macOS Ventura 的后台项目审批会拦新添加的 agent（`posix_spawn` → `Operation not permitted`，exit 78），而已获批的 `com.dietmanager.backup` 跑得好好的。所以并进那个任务里，一个任务干两件事
- 恢复方法见数据仓库的 README

### 32. Gemini 模型可用性变化（2026-08 实测）
`gemini-2.0-flash` 的免费额度已被 Google 收回，API 返回 `limit: 0`（不是超配额，是彻底不可用）。实测可用：`gemini-flash-lite-latest` ✅、`gemini-2.5-flash` ✅、`gemini-flash-latest` ✅；`gemini-2.5-flash-lite` 返回 404。

app 内的 AI 功能（AI 入库/AI 录入/库存解析/购物清单解析）用的都是 `gemini-flash-lite-latest`，**不受影响**。但 CLAUDE.md 旧版里「gemini-2.0-flash 1500 RPD」的说法已过时，新写脚本别再用它。

### 33. 冷审查修复：克重口径统一 + 三处静默算错（2026-08）

一次完整的 cold pass 审查后修掉的一批**不抛异常、只算错数**的问题。

**a. 「调料摄入比例」的三代实现，只留最后一代**

同一个概念在库里存在过三份，前两代被取代但从未清理：

| 代 | 载体 | 处置 |
|---|---|---|
| 一代 | `user_settings.condiment_intake_ratio`（全局 25% 滑块，PRD 原设计） | migration 16 删除该行，`_user_settings_seeds()` 不再种 |
| 二代 | `ingredients.intake_ratio`（per-食材，migration step 1） | 列保留（删列要重建表），migration 16 全量归一为 1.0，**代码全线不再读** |
| 三代 | `recipes.condiment_ratio`（per-菜，migration step 4） | 当前唯一在用 |

二代复活的经过值得记住：`views/recipes.py` 的 AI 入库 prompt 是照着 `ingredients` 表列清单写输出格式的，看到这个废弃列就把它抄进了 JSON schema，还编了段说明（"其余参考 condiment_ratio"——等于自己承认重复）。Gemini 于是把同一个 0.9 同时写进 `recipes.condiment_ratio` 和 8 个调料的 `intake_ratio`，而 `plan.py`/`tonight.py` 里早就存在的 `base_ratio * cond_r` 乘法立刻开始双重打折（0.81 而非 0.9），`nutrition.py` 却只乘一次 → **规划页看到的钠和存进 `daily_logs` 的钠差 10%**。

**教训：废弃一个字段时，删列/删乘法/记文档三件事要一起做。留着的列迟早会被下一个照表结构写代码的人（或 prompt）捡回来。**

**b. 唯一的克重口径入口 `recipe_ings_for_two()`（`views/nutrition.py`）**

原来 `plan.py` / `tonight.py` / `nutrition.py` 的两个 tab 各有一份"菜谱 → 食材列表"的转换，四份实现三种算法。现在统一为一个函数，其余三处 import 它：

```
每人摄入 = amount × serving_ratio × (condiment_ratio if 调料 else 1) ÷ 2
```

- `serving_ratio` — per 菜：这一顿吃掉锅里的多少（4 servings 的红烧肉吃两顿 → 0.5）
- `condiment_ratio` — per 菜：调料真正吃进去多少（红烧肉的酱油不会都喝了）
- `÷ 2` — 两个人分

顺带修掉「🍽️ 菜谱营养计算」tab **完全没乘 `serving_ratio`** 的问题（`serving_ratio=0.35` 的菜在那里比全日营养页高 2.9 倍）。**以后再有新页面要算营养，直接用这个函数，别自己拼食材列表。**

**c. 营养查询的 NULL 语义**

- **tier-2 曾静默丢弃 4 种微量营养素**：`lookup_ingredient()` 的 local json 分支逐字段手写，漏了 `vitd/vita/magnesium/zinc`，并把这份残缺数据写进 tier-1 缓存。因为缓存 shadow local json，丢失是**永久**的（「🔄 同步 local_nutrition.json」也救不回来，它跳过已缓存的名字）。现在两处共用 `_NUTRIENT_KEYS` 常量，新增营养素不可能只加一半。存量 70 条坏行用 `scripts/repair_local_cache_micros.py` 修复（209 个字段，只填 NULL 不覆盖已有值）。
- **USDA 命中但无热量数据不再写缓存**：原来只要搜索有结果就照单全收，写出一条全 0 的行；此后 tier-1 命中，食材"查到了"（不进 missing 警告）但对每道菜贡献 0。现在 `kcal is None` 直接降级到 tier 4，保持可见、可重试。

**d. 两处 AI 报错被 `st.rerun()` 吞掉**：`views/inventory.py`、`views/recipes.py` 的 AI 解析失败原本 `st.error()` 后紧跟无条件 `st.rerun()`，用户看到的是"按钮点了没反应"。改为存 session_state 下一轮渲染（与 `views/nutrition.py` 的 `ai_nutr_error` 同款），并让 expander 自动展开。

**e. 保存今日记录混用旧快照 + 实时控件**：`compute_fullday_silent()` 现在把 `fruits`/`fruit_g` 一并返回，保存时用返回值而非重读 widget——原来"算完营养后改水果再保存"会让存进去的水果清单和热量对不上。

### 34. 库存「每份克重」从无入口到逐项可设（2026-08）

`portion_weight_g` 存在已久，但**没有任何 UI 能改它**，值完全取决于是哪条代码路径创建的条目：

| 创建路径 | 叶菜 | 蛋白 |
|---|---|---|
| 手动「➕ 添加条目」 | 200 g（根本没传，吃 `add_item()` 的函数默认值） | 200 g |
| ⚡ AI 批量录入 / 购物清单入库 | 500 g | 300 g |
| 顶部「约 X 天份」metric | 按 500 g 算 | 按 300 g 算 |

结果：叶菜 36 项里 35 项是 200g，而 metric 按 500g 算——**天数估算系统性高估 2.5 倍**（蛋白 1.5 倍）。CLAUDE.md 第 8 条写的「叶菜默认 500g」只是文档意图，手动添加那条路从没实现过。

修复：
- **`_PORTION_DEFAULT_G` 单一常量** + `default_portion_g()`，三处调用点统一（原来三份拷贝各不相同）
- **添加表单新增「每份克重」输入**，按分类预填、可当场改
- **新增「⚖️ 每份克重」expander**（叶菜/蛋白 tab 各一个），逐项调整。widget key 带当前值（`pw_{id}_{值}`），遵循第 25 条那个写回陷阱的解法
- **metric 改为 `Σ(份数 × 该项 portion_weight_g)`**，不再乘一个统一常数
- 存量数据**不做批量归一**——一份到底多重因食材而异（一根黄瓜 vs 一斤青菜），逐项设才对

同批次的其它小修：
- `_bar()` 的 🚨 分支原本不可达（`pct>0.65` 先匹配），钠严重超标和轻微偏高图标一样。改为先判 `>1.0`
- `add_item()` 给按份条目写的 `unit` 从 `'g'` 改回 `'份'`（份数模型下 quantity 存的是份，不是克）
- `_fetch_usda()` 的 except 补上 `ValueError`：USDA 返回 HTML 错误页时 `.json()` 会抛异常冲出去打崩当前页
- 购物清单入库对 AI 返回的 `category` 做白名单校验：越界的值会写进 DB 但没有任何 tab 渲染它、可做菜过滤也看不到，等于买了东西却查无此物
- 删掉 `db/daily_log.py` 的 `_BFST_DEFAULTS`/`_LUNCH_DEFAULTS` 和 `get/update_default_preset()`（105 行死代码）——正是第 28 条判定"维A 低估 10.7 倍"的那组原始估算值，无调用方但留着就是隐患。`meal_presets` 表本身未动

### 35. 第二轮冷审查：推荐器 / 食愿之书 / 购物清单（2026-08）

第一轮按"数值正确性"挑模块读，这三个没覆盖到。补审 1285 行后的发现：

**a. 添加超市会抛异常（唯一一个会弹红框的）**

`views/shopping.py` 原来在 `text_input` 建好之后又给同一个 key 赋值来清空输入框：

```python
new_store = ac1.text_input("新店名", key="shop_new_store")   # 建 widget
...
st.session_state["shop_new_store"] = ""                      # ← 抛异常
```

Streamlit 的 `SessionState.__setitem__`（`session_state.py:533`）有守卫：本次 run 已实例化的 widget，key 不能再被赋值。**用 `streamlit.testing.v1.AppTest` 复现确认。** 而 `_save(data)` 在抛异常之前已经执行，所以表现是"红色报错但店其实建好了"，`st.rerun()` 则没跑。

改用 version counter（`shop_new_store__v{ver}`，和 textarea 那套一致），bump 的是普通 key 不是 widget key。**注意 `__delitem__` 没有这个守卫**——`views/wishlist.py` 用 `st.session_state.pop()` 清输入框是安全的，别照着改成赋值。

**b. `utils/inventory_state.py`：库存可用性判断收敛成一份**

"这个食材现在有货吗"曾有三份实现，推荐器那份是错的：

```python
is_available = (q is None) or (q > 0)     # 旧的推荐器逻辑
```

叶菜/蛋白 tab 里的「🛒 常备免记量区」是 `item_type='boolean'`，`quantity` 恒为 NULL → **不管 `in_stock` 是 0 还是 1 一律判为有货**。实测把「猪肉末」标成 ⬜ 缺货：推荐器认为有货、今日规划的「仅显示可做的菜」认为没货，两个界面对同一道菜给出相反答案。

现在 `is_available()` / `available_names()` / `HIGH_STOCK_PORTIONS` 都在 `utils/inventory_state.py`，推荐器、今日规划、食愿之书、库存页四处共用。**和第 33 条的 `recipe_ings_for_two()` 是同一个教训**：规则复制到多个调用点就会悄悄漂移。

顺带：囤货阈值原本推荐器写 5、库存页写 4，现在统一为常量 4。`_score` 的 `high_stock` 加权补上 +3.0 封顶（原来无上限，5 个囤货食材 +5.0 会盖过易坏偏好）。

**c. 炖菜不再算占锅**

`_recipe_flags()` 用的是 `cook_time_min`（实操+等待），而 `_validate()` 和 `views/plan.py:_wok_violations()` 用 `active_time_min`。**同一条规则的两半互相矛盾**：一道「实操 3min + 炖 40min」的菜，选菜时判为标准占锅（挤掉真正炒菜的名额），复核时判为轻占锅。第 13 条记的"炖 40min 期间锅是空的"只改了一半。

现在统一走 `wok_minutes()`（只看实操时间），三处共用。实测炖菜 + 炒菜同桌 → 无冲突。

**d. 入库失败后重试会重复累加**

`_commit_to_inventory()` 原本先把已有食材累加进库，再调 AI 分类新食材。AI 失败时 `return False`，但**前半段已经写进去了**；而失败时清单不清空，重试一次那些已有食材就被累加两次。现在分类先做，成功了才开始写。

**e. 其它**：「🗑️ 清空全部」的二次确认标记原本永不过期（点一次看到警告 → 一周后再点一次直接清空，无警告），改为读取即清除、只对下一次 run 有效；采购入库的 `buys` 按名字建 dict，同店清单里写两遍的同名项只有最后一个的份数生效，改为累加；删除店铺原本在循环中途就 `_save`，会丢掉排在后面、尚未渲染的店的编辑，改为延后到全部读完；AI 解析建店按 casefold 归并（`hmart` / `Hmart` 不再变成两家店）。

**f. 文档偏差 D5**：第 13 条说炒锅冲突判断"改用 `active_time_min`"，实际 `_recipe_flags` 从未改过——文档描述的是意图，代码只改了一半。已随本次修复对齐。

### 36. 只扫「一级食材」的微量营养素回填（2026-08）

思路：不给 13 种营养素做覆盖率仪表盘，改为**只补主料（一级农产品）的数据缺口**。
先验证够不够——按菜谱累计克重加权：主料占 62%，把主料全补齐后总覆盖率可达
92–96%。所以够。

`scripts/ai_fill_micronutrients.py` 改了三处才能落地：

**a. prompt 把「确实不含」和「查不到」混为一谈（硬伤）**

```
- 确实查不到或该食材本身不含此营养素时填 null，不要猜 0    ← 旧
```

而缺口里绝大部分是**蔬菜的维生素D**——番茄的维D 真的就是 0 µg，不是查不到。
用旧 prompt 跑维D，模型对满屏蔬菜一律返回 null，**一条都补不进去**。现在明确
区分：确实不含 → 0（真实数据），无法确定 → null（未知）。UI 靠这个区分
"确实没吃到"和"没有数据"。

**b. `--mains-only`（默认开）**：跳过调料。调料占菜谱克重 38%，但经
condiment_ratio 折扣后实际摄入占比小得多，配额花在生抽老抽上不划算。

**c. 噪音过滤**：「配菜」「食材」「步骤1」这类导入残渣不再消耗配额。

实测（前值取自跑之前的 iCloud 备份快照，非估算）：

| | 之前 | 之后 |
|---|---|---|
| 维A | 86.3% | **92.1%** |
| 维D | 62.8% | **88.7%** |
| 镁 | 85.0% | **90.8%** |
| 锌 | 84.5% | **90.9%** |

维D 一项 +25.9 个百分点，全靠 prompt 那处修正。

**顺带挖出三条 USDA 错配**（比微量缺口严重得多，都是热量级别的错）：

| 食材 | 错配到 | 错值 | 正确值 |
|---|---|---|---|
| 胡萝卜 | Carrot, dehydrated | 341 kcal | 41（FDC 170393）|
| 红枣干 | Jujube, raw | 79 kcal | 281（FDC 168152）|
| 干辣椒 | 生辣椒 | 40 kcal | 281（FDC 169396）|

胡萝卜影响 11 道菜，「手抓饭」单道多算 1200 kcal（总量）。翻译词条一并修正
（`carrot` → `carrots raw`，否则 USDA 首条命中仍是脱水的）。
干裙带菜也可疑，但 USDA 只有 raw 条目、值与现值相同，**查不到更好的值就不动**。

**踩过的坑（重要，别重蹈）**：排查时我一度把 `force_refresh` 改成连
`local_nutrition.json` 一起跳过，理由是「强制刷新」的文案写着"重新查 USDA"。
一测试就把「番茄」刷成了 Tomato powder（302 kcal vs 真实 18）。
**`local_nutrition.json` 正是凌驾于 USDA 之上的人工修正层**（第 3 条：已修正
冬瓜、番茄的错误匹配），绕过它等于把所有人工修正作废。已回退，改的是文案不是
行为：force_refresh 只跳过 SQLite 缓存，要改人工校正过的食材请用「🗄️ 食材库」
的编辑表格。

### 37. 「近7日分析」名不副实 + 单日详情页（2026-08）

**a. `logs[:7]` 不是"近7日"，是"最近 7 条记录"**

实测：9 条记录里最近 7 条**跨度 89 天**（2026-05-10 ~ 08-07，中间有个 59 天的
空档）。受影响的是「每日平均食材」（分母是记录条数不是天数）、「食材种类合计」
（号称一周实际混了三个月）、以及 DRI 热力图（5月和8月的记录并排，看着像连续一周）。

改为**真实日历窗口**：只取落在最近 7 个自然日内的记录，标题下方注明
「2026-08-01 ~ 2026-08-07 这 7 天里，有 3 天留下了记录」。平均值的分母仍是
**有记录的天数**（否则算的是"你多久忘记保存一次"，不是饮食多样性），指标名
也改成「每次记录平均食材」并在 help 里写明分母。

顶部表格的「最近 N 天记录」同样改为「最近 N 次记录，跨 X 天」。

**b. 新增「📖 单日详情」**

选任意一天 → 展开当天的餐次构成、DRI 条、以及**某个营养素来自哪些食材**的
逐项拆解（`_log_meal_sources` + `_attribute_nutrient`）。

数据本来就存着（`breakfast`/`lunch`/`dinner_recipe_ids`/`dinner_staple`/
`dinner_placeholder`），但 **`get_recent_logs_full()` 没 SELECT 这几列**——
以前只画图表看不出来，一还原餐次就暴露了。更糟的是 `_log_meal_sources` 读到
空值时会 fallback 成 `mode: "default"`，**等于给跳过早餐的日子凭空补一顿默认
早餐**。两处都已修：SQL 补齐列，缺 blob 时跳过而不是当默认。

**还原口径校验**：2026-08-07 逐食材合计与记录里存的值——热量 1869.1 vs 1869.1、
钠 2162.1 vs 2162.1，**完全一致**，说明还原忠实。饱和脂肪 16.7 vs 13.8 差 21%，
是因为保存之后我又补了熟牛腱等食材的脂肪细分——UI 会在差异 >5% 时明确提示
「明细是用现在的食材库重算的，记录里存的是当天那一刻的值」，并说明**临时加菜
只留了名字没留克重、无法还原**。

**c. 副产品：饱和脂肪的来源**

拿这个功能查了当天：**牛奶占 51%**（早 150g + 午 300g = 8.55g，全天 16.7g）。
鸡蛋 11%、熟牛腱 10%、三道菜的油加起来只有 11%。上限 18.9g（1700 × 10% ÷ 9），
当天用到 88%。顺带用 USDA 补了熟牛腱/可可粉/三色藜麦/燕麦麸皮/芝麻的脂肪细分，
当天的细分覆盖率 77% → 94%。

### 38. 「牛奶」是全脂 + 脂肪细分自相矛盾（2026-08）

用「单日详情 → 某个营养素来自哪些食材」查当天饱和脂肪时，用户对着奶盒标签发现
库里的数不对。

**a. `牛奶` 存的是全脂，实际喝的是 2% 超滤**

`en_name` 是 `Milk, whole, 3.25% milkfat`。讽刺的是 `_BFST_DETAIL`/`_LUNCH_DETAIL`
的说明文字里早就写着「2% 超滤牛奶」，但食材名只是「牛奶」，USDA 就匹配到全脂了。

按产品标签（每 240ml：130kcal / 6g 脂肪 / 3g 饱和 / 13g 蛋白 / 429mg 钙 /
3µg 维D / 184mg 钾 / 187µg 维A / 65mg 钠 / 6g 碳水）重录。**超滤奶和普通 2%
差别很大**——蛋白质高约 50%、糖低约一半，所以不能只改脂肪：

| 每 100 | 全脂（旧） | 2%超滤（新） |
|---|---|---|
| 热量 | 61 | 54.2 |
| 蛋白质 | 3.15 | **5.42** |
| 总脂肪 | 3.27 | 2.50 |
| 饱和脂肪 | 1.90 | **1.25** |
| 钙 | 280 | 178.8 |
| 维D | 0.10 | **1.25** |

影响：每天 450g 牛奶（早 150 + 午 300）的饱和脂肪 8.55g → 5.62g，当日全天
16.7g → **13.7g**（占上限 18.9g 从 88% 降到 73%），蛋白质则多约 10g。

口径说明：标签是每 240 **ml**，系统里 ml 和 g 按 1:1 处理（`UNIT_TO_G`），菜谱
也写「牛奶 150 g」，所以直接 ÷2.4 当作每 100g。牛奶密度 1.03，这样差约 3%，
在噪音范围内且与录入口径自洽。**单/多不饱和标签没给 → 置 NULL 不猜**（旧的全脂
值配上新的总脂肪会让三者和超标）。`local_nutrition.json` 一并改，否则清一次缓存
就退回全脂。

**b. 顺着查出一类物理上不可能的值：脂肪细分之和 > 总脂肪**

| 食材 | 总脂肪 | 饱和+单+多（修前） |
|---|---|---|
| 淡奶油 | 19.0 | 饱和一项就 **23.5** |
| 五花肉/猪梅肉 | 18.2 | 31.5 |
| 猪颈肉 | 20.4 | 35.9 |
| 肥牛 | 8.4 | 19.5 |
| 牛肋排 | 19.0 | 33.8 |

成因是细分和总脂肪来自不同的 USDA 条目（`ai_fill_fat_detail.py` 按名字查细分，
不校验与已有总脂肪是否同源）。已按各自 `en_name` 整条重取，**热量/蛋白/脂肪同源**，
用 4-9-4 反推校验都对得上。全库现在零条这种值。

排查中我一度只更新脂肪字段，结果 `猪颈肉` 变成「259 kcal + 12.4g 脂肪」——又一个
自相矛盾。**改这类数据要整条覆盖，不要只改你关心的那几个字段。**

**c. `淀粉` 488 → 381 kcal**：碳水 91.4 本来就对，只有热量偏高 28%。根因是翻译
词条缺失，`corn start` 在 USDA 匹配到「无麸质面包卷」。已补 `淀粉/玉米淀粉 →
cornstarch`。

**d. 一个不可靠的判据，别再用**：我试过用「4×蛋白 + 9×脂肪 + 4×碳水 是否等于
记录热量」批量找错，扫出 69 条但绝大多数是误报——清酒（酒精 7kcal/g 不计入三大
宏量）、八角桂皮（膳食纤维实际约 2kcal/g 不是 4）、泡打粉（主要是矿物质）。
只用它做单条复核，不要拿来批量改数据。

**e. 单日详情补上脂肪细分**：`_ATTRIB_CHOICES` 加入单/多不饱和（`_BAR_SKIP`
让它们只参与归因、不画 DRI 条——它们没有"达标"一说，是看占总脂肪的比例）。

### 39. 上限型营养素的判色被当成目标型（2026-08）

用户问：「饱和脂肪 73%，颜色是黄色的，难道要吃到 100% 么？我以为少吃点更好？」

**是 UI 的错，不是理解的错。** 饱和脂肪和钠是**上限**，不是目标；73% 意味着离
上限还有富余，是好结果。但界面把它显示成 🟡，而 🟡 在同一张表的其它每一列都
表示"没吃够"。

三处成因：
1. **阈值来自另一个语境**：`_bar` 的 ⚠️ 卡在 65%，那是给「晚餐单顿钠」设的——
   一顿饭就吃掉全天钠上限的 65% 确实该警告。但整天的饱和脂肪在 65-99% 之间是
   正常的，热力图沿用了同一个 65% 判据。
2. **没有 ✅ 分支**：`_bar` 在上限型且低于阈值时返回空字符串，"安全"这个状态
   没有任何正向反馈，看着像漏了。
3. **图例只提了钠**：`（钠反向：...）`——饱和脂肪后来才加入 warn_over 行列，
   图例没同步，用户无从知道这一列是反向的。

修法：
- 上限型判色改为 **✅ <85% · 🟡 85–99%（接近上限）· 🔴 ≥100%（超标）**
- `_bar` 新增 `warn_at` 参数（默认 0.85 给全日口径；`_results` 的晚餐钠仍传
  0.65，因为那里"一顿占全天多少"才是重点），低于阈值时给 ✅ 而不是空白
- **列名和进度条标签加 `↓`**（`饱和脂肪 ↓`、`钠 ↓`），百分比文案从
  `% DRI/人` 改成 `% 上限`
- 图例改为两行，上限型那行的名单从 `_HISTORY_DRI` 自动生成，不会再漏

**教训**：同一个 `warn_over` 布尔量被用在"单顿 vs 全日"两种语境里，阈值却只有
一个。加参数的时候要问一句"这个默认值在所有调用点都成立吗"。

### 40. 单日详情：餐次标签 + 补记/修改任意一天（2026-08）

**a. 餐次标签毫无信息量**

原来单日详情顶部是一行「🌅 早餐　·　🕛 午餐　·　🍎 水果　·　🌙 糖醋藕片…」——
前三个等于什么都没说。现在改为每餐一行、列出实际吃的东西，并区分常规/自定义：

```
**🌅 常规早餐**　干杂豆、钢切燕麦、三色藜麦、南瓜、红薯、奇亚籽…
**🕛 常规午餐**　牛奶、可可粉、混合坚果
**🍎 水果**　苹果、香蕉、蓝莓
**🌙 糖醋藕片**　莲藕、水、淀粉、生抽…　_(整锅，每人 ÷2)_
```

**b. `compute_fullday()`：把算式从 session state 里剥出来**

`compute_fullday_silent()` 原本直接读 `fd_*` 控件和 `plan_rids`，所以**只能算
"此刻的今天"**。新增 `compute_fullday(**explicit)` 承载全部算术，
`compute_fullday_silent()` 退化成"读 session state → 调它"的适配器。

这样单日详情的编辑器不必再抄一份口径——正是本轮反复出现的病根（三份克重转换、
三份库存可用性判断）。回归验证：重构后重算 2026-08-07，蛋白 123.3g、饱和 13.7g，
与逐食材归因**完全一致**。

**c. 新增「✏️ 修改这天 / ➕ 补记某一天」**

原来 `save_daily_log` 的日期是硬编码 `datetime.now()`（`nutrition.py` 和
`plan.py` 各一处），只能存今天、没法补记。现在历史记录页可选任意日期编辑或补记。

**刻意不做进「🌅 全日营养」页**：那个页面本质是"今天的控制台"，输入全是实时
`fd_*` 控件。把日期这个维度塞进去，等于让一套已经出过多次问题的状态机再多担
一层——编辑 8月3日 时改主食可能把今天的也改掉。编辑器的每个控件 key 都带日期
（`ed_{iso}_bf_mode`），完全不碰 `fd_*` 和 `plan_rids`；切日期自动重建控件、
从那天的记录重新初始化。

**自定义早午餐预填常规清单**（不是给个空框），所以"常规早餐 + 多个鸡蛋"就是在
预填的 11 行后面加一行，不用新增数据结构。顺带解决了原「✏️ 特殊情况：自定义
食材」藏得太深、且只能整段替换的问题。

保存 = 用**当前**食材库重算 + 覆盖那天。数据变准了旧记录也该跟着准。

**e. 外食：晚餐多一个「每人份」入口**

单日详情能记三餐之后，第一个真实用例就是"今天在外面吃"。早餐/午餐的自定义本来
就是**每人份、不 ÷2**，直接填即可；但晚餐那个「临时加菜」栏的语境是"往两人共用
的锅里加"，会 ÷2 —— 外食时在那里填「牛肉面 500g」**只会记 250g**。

现在晚餐拆成两栏，语义写在标签上：
- ① 两人共用的临时加菜 —— 填总克数，会 ÷2
- ② 🍽️ 外食 / 只有我吃的 —— 填自己吃的量，不除以 2

`compute_fullday()` 新增 `solo_ings`（与 `staple_ings` 同级：每人份、不参与
÷2）。**「全日营养」控制台也加了同一栏**，否则今天外食走控制台还是同一个坑；
`fd_dinner_solo_txt` 已纳入 `_FD_KEYS` 跨页镜像。

验证：同样 500g 米饭，填①得 325 kcal、填②得 650 kcal，正好 2.00×。

**d. 确认扣减不再静默覆盖**：`plan.py` 的确认扣减也调 `save_daily_log(今天)`，
如果当天已被手工编辑过会被覆盖。现在检测到已有记录时提示「营养记录已更新
（覆盖今日原有记录）」而不是照旧显示「营养已记录」。

### 41. 已知但暂不处理

- **`utils/nutrition_lookup.py` 里 `calc_nutrition()` 的入参 key 也叫 `intake_ratio`**，和刚废弃的 DB 列同名。它指的是"这次计算对该食材打几折"，是计算层参数不是数据列。改名要动所有调用点，暂留——但读代码时注意区分。
- **微量营养素没有覆盖率指标**：只有脂肪细分有 `fat_detailed`（能提示"只有 32% 的脂肪有细分数据"）。其余 13 项缺数据时按 0 静默累加，"真的吃少了"和"没数据"在 DRI 条上分不出来。按实际用量加权测算，维A/镁/锌/钾/钙/铁 当前覆盖 75–100%（早餐 100%），影响可控；**只有维D 是 27%**，但它已由补剂设置兜底。继续补数据（`scripts/ai_fill_micronutrients.py`）比做仪表盘划算。

---

## 待优化方向（未实现，按优先级排序）

1. **购物清单生成**：对比计划菜谱的主料总需求 vs 当前库存，输出缺口清单，按叶菜/蛋白/干货分组。现有数据已齐全，只缺 UI 入口和汇总逻辑。

2. **营养持续低值预警**：在历史 tab 加汇总行，标出"连续 N 天低于 80% DRI"的营养素（如铁、维D），比每次盯热力图更直接。

3. **双语界面切换（备用）**：用 `st.session_state["lang"]` toggle + `STRINGS = {"zh": {...}, "en": {...}}` 字典管理所有 UI 文案，英文侧用机器翻译初稿。架构不难，主要工作量在提取全部 UI 字符串（4 个 view 文件），适合展示 demo 时启用。

4. **多样性降权 (Category Cooldown)**：引入品类冷却时间。如果近期连续食用"虾类"，即使库存充足，也自动调低相关菜系的推荐权重，强制进行物种多样性轮换。

---

## 已知问题 / 待决策

1. **部分菜谱食材名不规范**：来自原始菜谱的非标准名（如"各种海鲜"、"冷水"、"适量"）会出现在 seed 脚本的缺失列表中，查询无意义。可在 `scripts/seed_nutrition_ai.py` 的 `_collect_missing` 中加过滤词列表，或手动忽略。

2. **Gemini 模型**：`gemini-2.0-flash` 已不可用（免费额度归零，见第 32 条）。现役：app 内 AI 功能与批量脚本用 `gemini-flash-lite-latest`（高配额），营养顾问用 `gemini-2.5-flash`（25 RPD + 应用层 3次/天）。`scripts/seed_nutrition_ai.py` 里若仍写着 2.0-flash 需要改。

3. **菜谱数据质量**：DB 中仍有约 149 条 `needs_review` 菜谱（调料未结构化）。可用 `scripts/clean_recipes_ai.py` 补全，或在 UI 中手动编辑。

4. **en_name/en_desc 覆盖率**：`gen_recipe_descriptions.py` 已批量生成，但部分菜谱可能因 Gemini 配额耗尽未完成。可用 `--dry-run` 查看缺口，`--force` 强制重新生成。

5. **serving_ratio 存量菜谱**：DB migration 默认值为 1.0（100%），存量菜谱均为 100%。可在各菜谱编辑页按实际食用习惯调整。

6. **语义搜索偏离**：当前的语义索引在处理“清淡一些”等模糊口味描述时存在偏移（由于向量空间中负面词汇的关联）。短期方案：手动在 UI 过滤或通过 cuisine/tag 硬过滤。

7. **微量营养素仍有 ~14% 用量未覆盖**：维A/镁/锌回填到加权 86% 后停手（回填按用量排序，长尾收益递减）。想继续补：`python3.9 scripts/ai_fill_micronutrients.py --nutrient vita,magnesium,zinc --top 150`。脂肪细分同理，当前加权 83%。

8. **食愿之书「📖 卷宗」已存清单仍用 `st.columns`**：`views/wishlist.py` 的 `_list_view()`（展示已经写入书中的条目）还是分栏布局，在窄屏会被拆成多行——跟当初「➕ 录入新愿」浏览列表一样的问题，只是还没照 `st.container(horizontal=True)` 的模式重做。优先级不高（这里主要是查看，不是高频操作），但如果继续做手机端优化，这是下一个该动的地方。

---

## 关键文件速查

| 路径 | 说明 |
|------|------|
| `app.py` | 单页 Streamlit 入口，侧边栏导航，支持 `_nav_pending` 跨页跳转 |
| `db/init_db.py` | SQLite schema 初始化（幂等），含 migration 逻辑（步骤 1-16） |
| `db/recipes.py` | 菜谱/食材 CRUD，含 `condiment_ratio`、`serving_ratio`、`en_name`/`en_desc`/`zh_desc` |
| `db/daily_log.py` | 每日记录 CRUD，含 `dinner_staple`、`ingredients_snapshot`、`total_nutrients_json` |
| `utils/cache.py` | `st.cache_data`/`st.cache_resource` 缓存层，包 `get_all_recipes`/`get_all_inventory`/`get_all_ingredients_grouped` 及写操作自动失效 |
| `utils/nutrition_lookup.py` | 四级降级营养查询核心，`calc_nutrition_with_breakdown` 返回含 USDA 链接的明细 |
| `utils/recommender.py` | 加权随机推荐器，2-pass + 结构化 slot 填充（1凉拌+2热菜+1汤）；占锅判定见 `wok_minutes()` |
| `utils/inventory_state.py` | 库存可用性单一判据（`is_available`/`available_names`/`HIGH_STOCK_PORTIONS`），四处共用，见第 35 条 |
| `utils/semantic_search.py` | ChromaDB 语义搜索，`paraphrase-multilingual-MiniLM-L12-v2`，持久化于 `./data/chroma` |
| `utils/nutrition_advisor.py` | Gemini 营养顾问，3次/天限制存于 `user_settings` |
| `utils/pdf_generator.py` | reportlab PDF 生成，PingFang 中文字体，餐厅菜单风格正面 |
| `views/inventory.py` | 库存 UI，5分类标签页，购物模式，份数（直接键入）/常备布尔混合双模式 |
| `db/inventory.py` | 库存 CRUD，含 `portion_weight_g`、`is_frozen`、`is_perishable` 字段 |
| `views/recipes.py` | 菜谱库 UI，含 AI 入库（`_view_ai_onboard`）、语义搜索、CRUD 表单 |
| `views/plan.py` | 今日规划 UI，含「仅显示可做的菜」库存过滤 toggle（默认开启）、确认扣减后菜单保留 + 「📢 发布今日菜单」 |
| `views/tonight.py` | 「🍽️ 今夜のおすすめ」只读家人视图（默认首页），读取 `views/plan.py` 手动发布的 `today_menu` |
| `views/wishlist.py` | 食愿之书 UI，暂存区+浏览列表选菜（非 multiselect），支持未入库的自定义心愿 |
| `views/nutrition.py` | 全日营养 UI，含早午餐 skip/自定义、主食选项、7日 DRI 热力图 |
| `assets/mimi/` | 喵喵（吉祥物）照片素材，`.gitignore` 排除，见 CLAUDE.md 第 21 条 |
| `.streamlit/config.toml` | Streamlit 自定义主题（暖橘配色） |
| `~/.cloudflared/config.yml` | 命名 tunnel `diet-manager` 配置（不在项目目录内，Mac 本机全局路径） |
| `~/Library/LaunchAgents/com.dietmanager.*.plist` | launchd 常驻服务定义，见 CLAUDE.md 第 18 条 |
| `data/local_nutrition.json` | 手工/AI 维护的营养数据，优先于 USDA API |
| `data/ingredient_translations.json` | 中文→英文食材翻译，用于 USDA 查询 |
| `data/chroma/` | ChromaDB 持久化目录（recipes 集合，cosine 距离） |
| `scripts/seed_nutrition_ai.py` | Gemini 批量填充营养数据，支持 `--manual-update --data "..."` |
| `scripts/clean_recipes_ai.py` | Gemini 批量清洗菜谱结构（食材克重、步骤、分类） |
| `scripts/gen_recipe_descriptions.py` | Gemini 批量生成 en_name/en_desc/zh_desc，支持 `--recipe`/`--force`/`--dry-run` |
| `scripts/build_recipe_embeddings.py` | 一次性建立 ChromaDB 索引（全量菜谱） |
| `scripts/backup_to_icloud.py` | 每日备份到 iCloud，并顺带调用 backup_to_git（见第 24/31 条） |
| `scripts/backup_to_git.py` | 每日推送文本 SQL dump 到私有仓库 diet-manager-data（见第 31 条） |
| `scripts/backfill_fat_detail.py` | 按 USDA ID 回填脂肪细分（仅覆盖带真实 FDC ID 的条目） |
| `scripts/ai_fill_fat_detail.py` | Gemini 按脂肪贡献量排序补细分，`--top 46` 覆盖 80% 摄入 |
| `scripts/ai_fill_micronutrients.py` | Gemini 按用量排序补维A/镁/锌等，`--nutrient vita --top 40` |

## 运行环境

- Python 3.9（系统 `python` 是 2.7，始终用 `python3.9`）
- 启动：`python3.9 -m streamlit run app.py`
- DB：`data/diet.db`，通过 `python3.9 db/init_db.py` 初始化（幂等）
- 首次使用语义搜索：`python3.9 scripts/build_recipe_embeddings.py`（或在菜谱库 UI 点击初始化按钮）

## 生产环境（这台 Mac 24 小时常驻，见第 18 条）

- 线上地址：`https://diet.<your-domain>`（Cloudflare Access 邮箱验证码登录）
- **改完代码后不会自动生效**，必须重启 launchd 管理的进程：`launchctl kickstart -k gui/$(id -u)/com.dietmanager.streamlit`
- 查日志排障：`logs/streamlit.err.log`（应用报错）、`logs/cloudflared.err.log`（tunnel 连接问题）
- 确认服务状态：`launchctl list | grep dietmanager`（状态码非 0 说明进程刚崩溃过）
