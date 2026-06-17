import os
import sys
import requests

# 1. 获取你的 API KEY
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("❌ 环境变量中没有找到 GEMINI_API_KEY")
    sys.exit(1)

print(f"🔍 正在使用 API Key (前缀 {api_key[:8]}...) 探测 Google 服务器...\n")

# 2. 直接调用最底层的 REST API 获取模型列表
url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
response = requests.get(url)

# 3. 打印结果
if response.status_code == 200:
    data = response.json()
    models = data.get("models", [])
    print("✅ 你的 API Key 目前有权限调用的模型列表如下：\n")
    for m in models:
        # 只打印出名字里带有 gemini 的核心模型
        if "gemini" in m["name"]:
            print(f"  - {m['name']}  (支持的操作: {', '.join(m.get('supportedGenerationMethods', []))})")
else:
    print(f"❌ 请求失败！HTTP 状态码: {response.status_code}")
    print(f"错误详情: {response.text}")