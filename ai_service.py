from openai import OpenAI
import os

# 使用 OpenAI SDK 兼容各种模型 (包括 DeepSeek 等，只需替换 base_url)
# 默认留空，等待用户填入。此处代码逻辑预铺好。
API_KEY = os.getenv("AI_API_KEY", "your-api-key-here")
BASE_URL = os.getenv("AI_BASE_URL", "https://api.openai.com/v1")
MODEL_NAME = os.getenv("AI_MODEL_NAME", "gpt-3.5-turbo")


def is_configured():
    """API Key 是否已配置（非占位值）。"""
    return bool(API_KEY) and API_KEY != "your-api-key-here"

def analyze_query(query, context=""):
    """
    接收用户提问和当前行情上下文，发送给大模型进行解读
    """
    # 如果没配置真实 key，直接返回模拟回复，防止程序崩溃
    if API_KEY == "your-api-key-here" or not API_KEY:
        return (f"【AI模拟回复】我收到了你的复盘请求：'{query}'。\n\n"
                f"当前我还没有被配置真实的 API Key。请在系统的 ai_service.py 中填入有效的 Key，"
                f"或者使用本地环境变量 AI_API_KEY 来激活真正的 AI 分析能力。")

    try:
        client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
        
        system_prompt = (
            "你是一个专业的A股量化投研助手。请根据提供的行情数据和用户提问，"
            "给出客观、简练、一针见血的盘面分析。不要说废话，以专业术语为主。"
        )
        
        prompt = f"用户提问: {query}\n\n行情上下文: {context}"
        
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=600
        )
        
        return response.choices[0].message.content
    except Exception as e:
        return f"调用 AI 服务失败，原因: {str(e)}"
