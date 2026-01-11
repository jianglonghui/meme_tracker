"""
AI 客户端模块
- DeepSeek API 调用
- Gemini API 调用（支持图片）
"""
import json
import requests
import config
from .blacklist import build_blacklist_prompt
from .state import log_error

# ==================== 提示词模板 ====================
DEEPSEEK_PROMPT_TEMPLATE = """作为meme币分析师，从推文中提取最可能被用作代币名称的关键词。

提取原则：
- 最多返回3个关键词，按meme币潜力从高到低排序
- 只提取推文原文中的词，不翻译不推断
- 中文短语保持完整

优先级排序（从高到低）：
1. 亚文化符号/梗词（如milady、pepe、doge等有社区认同的词）
2. 情绪词/口号（如"我踏马来了"、"LFG"、"WAGMI"）
3. 人名/昵称（推文中提到的人物）
4. 特殊名词/新造词

排除：
- 已存在的主流币名（BTC、ETH、SOL等）
- 通用技术词汇（blockchain、gas、node等）
- 年份数字（如2026）
- 链接、@用户名、冠词介词
- {blacklist_prompt}
{examples}

推文：{news_content}

返回JSON数组（最多3个，按潜力排序）："""

GEMINI_PROMPT_TEMPLATE = """作为meme币分析师，从推文内容和图片中提取最可能被用作代币名称的关键词。

提取原则：
- 最多返回3个关键词，按meme币潜力从高到低排序
- 从文字和图片中都提取关键词
- 如果图片中有文字/标语/符号，优先提取
- 中文短语保持完整

优先级排序（从高到低）：
1. 亚文化符号/梗词（如milady、pepe、doge等有社区认同的词）
2. 图片中的文字、标语、符号名称
3. 情绪词/口号（如"我踏马来了"、"LFG"、"WAGMI"）
4. 人名/昵称（推文中提到的人物）
5. 特殊名词/新造词

排除：
- 已存在的主流币名（BTC、ETH、SOL等）
- 通用技术词汇（blockchain、gas、node等）
- 链接、@用户名、冠词介词
- 年份（如2025，2026）
- {blacklist_prompt}
{examples}

{content_section}

返回JSON数组（最多3个，按潜力排序）："""


def get_best_practices():
    """从 tracker_service 获取最佳实践样例"""
    try:
        resp = requests.get(
            f"{config.get_service_url('tracker')}/best_practices",
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return resp.json()
    except:
        pass
    return []


def build_examples_prompt():
    """构建最佳实践示例提示词"""
    practices = get_best_practices()
    if not practices:
        return ""

    examples = "\n\n最佳实践示例："
    for i, p in enumerate(practices[:5], 1):
        examples += f"\n示例{i}: 推文「{p['tweet_content']}」→ 关键词: {p['best_token']}"

    return examples


def parse_json_response(content):
    """解析 AI 返回的 JSON 数组"""
    keywords = None
    if content.startswith('['):
        keywords = json.loads(content)
    elif '```json' in content:
        json_part = content.split('```json')[1].split('```')[0].strip()
        keywords = json.loads(json_part)
    elif '```' in content:
        json_part = content.split('```')[1].split('```')[0].strip()
        if json_part.startswith('['):
            keywords = json.loads(json_part)
    elif '[' in content:
        start = content.index('[')
        end = content.rindex(']') + 1
        keywords = json.loads(content[start:end])

    if keywords:
        return [k.lower() for k in keywords if isinstance(k, str)][:3]
    return []


def call_deepseek(news_content):
    """调用 DeepSeek API 提取关键词"""
    if not config.DEEPSEEK_API_KEY:
        return []

    examples = build_examples_prompt()
    blacklist_prompt = build_blacklist_prompt()

    prompt = DEEPSEEK_PROMPT_TEMPLATE.format(
        blacklist_prompt=blacklist_prompt,
        examples=examples,
        news_content=news_content
    )

    try:
        headers = {
            "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "deepseek-reasoner",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1024
        }
        resp = requests.post(config.DEEPSEEK_API_URL, headers=headers, json=payload, timeout=30)
        if resp.status_code == 200:
            result = resp.json()
            content = result['choices'][0]['message']['content'].strip()
            return parse_json_response(content)
    except Exception as e:
        log_error(f"DeepSeek API: {e}")
        print(f"[DeepSeek] 异常: {e}", flush=True)
    return []


def call_gemini(news_content, image_paths=None):
    """调用 Gemini API 提取关键词（支持图片）"""
    if not config.GEMINI_API_KEY:
        return []

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=config.GEMINI_API_KEY)

        examples = build_examples_prompt()
        blacklist_prompt = build_blacklist_prompt()
        content_section = f"推文内容：{news_content}" if news_content else "（纯图片推文，无文字内容）"

        prompt = GEMINI_PROMPT_TEMPLATE.format(
            blacklist_prompt=blacklist_prompt,
            examples=examples,
            content_section=content_section
        )

        # 构建 parts
        parts = [types.Part.from_text(text=prompt)]

        # 添加图片
        if image_paths:
            for img_path in image_paths[:3]:
                try:
                    with open(img_path, 'rb') as f:
                        img_data = f.read()
                    mime_type = "image/jpeg"
                    if img_path.lower().endswith('.png'):
                        mime_type = "image/png"
                    elif img_path.lower().endswith('.gif'):
                        mime_type = "image/gif"
                    parts.append(types.Part.from_bytes(data=img_data, mime_type=mime_type))
                except Exception as e:
                    print(f"[Gemini] 添加图片失败: {e}", flush=True)

        contents = [types.Content(role="user", parts=parts)]

        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=contents,
        )

        return parse_json_response(response.text.strip())

    except ImportError:
        log_error("Gemini: 需要安装 google-genai")
        print("[Gemini] 需要安装 google-genai: pip install google-genai", flush=True)
    except Exception as e:
        log_error(f"Gemini API: {e}")
        print(f"[Gemini] 异常: {e}", flush=True)
    return []


def call_gemini_judge(tweet_text, tokens, image_paths=None):
    """调用 Gemini 判断推文与代币的关联（用于老币匹配）

    Args:
        tweet_text: 推文内容
        tokens: 代币列表 [{'symbol': '', 'name': ''}, ...]
        image_paths: 图片路径列表

    Returns:
        匹配的代币索引（0-based），无匹配返回 -1
    """
    if not config.GEMINI_API_KEY:
        return -1

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=config.GEMINI_API_KEY)

        token_list_str = [f"{i+1}. symbol:{t['symbol']} name:{t['name']}" for i, t in enumerate(tokens)]
        token_str = "\n".join(token_list_str)

        has_images = image_paths and len(image_paths) > 0
        image_hint = "（注意：推文包含图片，请结合图片内容判断）" if has_images else ""

        prompt = f"""判断以下推文是否在提及代币列表中的某个代币。{image_hint}

推文内容: {tweet_text}

代币列表:
{token_str}

规则:
- 推文需要与代币的 symbol 或 name 有明确关联（包含、谐音、缩写、翻译等）
- 如果推文有图片，也要分析图片中是否包含代币相关信息
- 如果有匹配，返回代币序号（如 "1" 或 "3"）
- 如果多个匹配，返回最相关的一个序号
- 如果没有任何匹配，返回 "none"

只返回序号或 "none"，不要其他内容："""

        parts = [types.Part.from_text(text=prompt)]

        if image_paths:
            for img_path in image_paths[:3]:
                try:
                    with open(img_path, 'rb') as f:
                        img_data = f.read()
                    mime_type = "image/jpeg"
                    if img_path.lower().endswith('.png'):
                        mime_type = "image/png"
                    elif img_path.lower().endswith('.gif'):
                        mime_type = "image/gif"
                    parts.append(types.Part.from_bytes(data=img_data, mime_type=mime_type))
                except Exception as e:
                    print(f"[Gemini] 添加图片失败: {e}", flush=True)

        contents = [types.Content(role="user", parts=parts)]

        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=contents,
        )

        result = response.text.strip().lower()

        if result == 'none' or not result:
            return -1

        try:
            idx = int(result.replace('.', '').strip()) - 1
            if 0 <= idx < len(tokens):
                return idx
        except ValueError:
            pass

        return -1

    except Exception as e:
        log_error(f"Gemini Judge: {e}")
        print(f"[Gemini Judge] 异常: {e}", flush=True)
    return -1


def call_deepseek_fast_judge(tweet_text, tokens):
    """调用 DeepSeek chat 模型快速判断推文与代币的关联

    特点：
    - 使用 deepseek-chat（非 reason 模型），速度快
    - 强调中英文翻译、谐音、缩写匹配
    - 不支持图片，纯文本匹配

    Args:
        tweet_text: 推文内容
        tokens: 代币列表 [{'symbol': '', 'name': ''}, ...]

    Returns:
        匹配的代币索引列表（0-based），无匹配返回空列表
    """
    if not config.DEEPSEEK_API_KEY or not tokens:
        return []

    token_list_str = [f"{i+1}. symbol:{t['symbol']} name:{t['name']}" for i, t in enumerate(tokens)]
    token_str = "\n".join(token_list_str)

    prompt = f"""判断推文是否提及以下代币列表中的代币。

推文: {tweet_text}

代币列表:
{token_str}

匹配规则（重要）:
- 翻译匹配：中文词 ↔ 英文词（如 "狗狗" ↔ "DOGE"，"青蛙" ↔ "PEPE"，"牛" ↔ "BULL"）
- 谐音匹配：发音相似（如 "踏马" ↔ "TM"，"韭菜" ↔ "JC"）
- 缩写匹配：首字母或简写（如 "我踏马" ↔ "WTM"）
- 语义匹配：含义相关（如 "牛市" ↔ "BULL"，"起飞" ↔ "MOON"）
- 包含匹配：推文包含代币名或符号

返回格式：
- 如果有匹配，返回所有匹配的代币序号，用逗号分隔（如 "1,3,5"）
- 如果没有匹配，返回 "none"

只返回序号或 "none"："""

    try:
        headers = {
            "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "deepseek-chat",  # 非 reason 模型，速度快
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 32
        }
        resp = requests.post(config.DEEPSEEK_API_URL, headers=headers, json=payload, timeout=10)
        if resp.status_code == 200:
            result = resp.json()['choices'][0]['message']['content'].strip().lower()

            if result == 'none' or not result:
                return []

            # 解析返回的序号列表
            matched_indices = []
            for part in result.replace(' ', '').split(','):
                try:
                    idx = int(part.replace('.', '').strip()) - 1
                    if 0 <= idx < len(tokens) and idx not in matched_indices:
                        matched_indices.append(idx)
                except ValueError:
                    continue

            return matched_indices
    except Exception as e:
        log_error(f"DeepSeek Fast Judge: {e}")
        print(f"[DeepSeek Fast] 异常: {e}", flush=True)
    return []


def extract_keywords(content, image_urls=None):
    """提取关键词：默认使用 Gemini，失败时回退到 DeepSeek

    Returns:
        (keywords, source) - keywords 列表和来源 ('gemini' 或 'deepseek')
    """
    from .utils import get_cached_image

    if config.GEMINI_API_KEY:
        image_paths = []
        if image_urls:
            for url in image_urls[:3]:
                path = get_cached_image(url)
                if path:
                    image_paths.append(path)
            if image_paths:
                print(f"[Gemini] 处理 {len(image_paths)} 张图片...", flush=True)

        keywords = call_gemini(content, image_paths if image_paths else None)
        if keywords:
            return keywords, 'gemini'
        print("[Gemini] 未提取到关键词，回退到 DeepSeek", flush=True)

    keywords = call_deepseek(content)
    return keywords, 'deepseek'
