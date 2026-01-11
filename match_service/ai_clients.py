"""
AI 客户端模块
- DeepSeek API 调用
- Gemini API 调用（支持图片）
"""
import json
import time
import requests
import config
from .blacklist import build_blacklist_prompt
from .state import log_error

# 全局会话对象，用于复用 TCP/SSL 连接
session = requests.Session()

# 全局 Gemini 客户端，用于复用连接池
gemini_client = None

def get_gemini_client():
    """获取或初始化全局 Gemini 客户端"""
    global gemini_client
    if gemini_client is None and config.GEMINI_API_KEY:
        try:
            from google import genai
            gemini_client = genai.Client(api_key=config.GEMINI_API_KEY)
        except ImportError:
            pass
    return gemini_client

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
        resp = session.get(
            f"{config.get_service_url('tracker')}/best_practices",
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            practices = resp.json()
            if practices:
                print(f"[AI] 获取到 {len(practices)} 条最佳实践样例", flush=True)
            return practices
    except Exception as e:
        print(f"[AI] 获取最佳实践失败: {e}", flush=True)
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


def parse_json_response(content, source="AI"):
    """解析 AI 返回的 JSON 数组"""
    keywords = None
    try:
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
            result = [k.lower() for k in keywords if isinstance(k, str)][:3]
            return result
    except json.JSONDecodeError as e:
        print(f"[{source}] JSON解析失败: {e}, 原始内容: {_truncate(content, 100)}", flush=True)
    except Exception as e:
        print(f"[{source}] 解析异常: {e}", flush=True)
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

    start = time.time()
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
        resp = session.post(config.DEEPSEEK_API_URL, headers=headers, json=payload, timeout=30)
        if resp.status_code == 200:
            result = resp.json()
            content = result['choices'][0]['message']['content'].strip()
            keywords = parse_json_response(content, "DeepSeek")
            print(f"[DeepSeek] OK {time.time()-start:.1f}s -> {keywords}", flush=True)
            return keywords
        print(f"[DeepSeek] HTTP {resp.status_code}", flush=True)
    except Exception as e:
        log_error(f"DeepSeek API: {e}")
        print(f"[DeepSeek] 失败: {e}", flush=True)
    return []


def call_gemini(news_content, image_paths=None):
    """调用 Gemini API 提取关键词（支持图片）"""
    client = get_gemini_client()
    if not client:
        return []

    start = time.time()
    img_count = len(image_paths) if image_paths else 0

    try:
        from google.genai import types

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
                    print(f"[Gemini] 图片加载失败: {e}", flush=True)

        contents = [types.Content(role="user", parts=parts)]

        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=contents
        )

        keywords = parse_json_response(response.text.strip(), "Gemini")
        print(f"[Gemini] OK {time.time()-start:.1f}s img={img_count} -> {keywords}", flush=True)
        return keywords

    except Exception as e:
        log_error(f"Gemini API: {e}")
        print(f"[Gemini] 失败: {e}", flush=True)
    return []


def call_gemini_judge(tweet_text, tokens, image_paths=None):
    """调用 Gemini 判断推文与代币的关联（用于老币匹配）
    Returns: 匹配的代币索引（0-based），无匹配返回 -1
    """
    client = get_gemini_client()
    if not client:
        return -1

    try:
        from google.genai import types

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

        start = time.time()
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=contents
        )

        result = response.text.strip().lower()

        if result == 'none' or not result:
            print(f"[Gemini Judge] OK {time.time()-start:.1f}s -> 无匹配", flush=True)
            return -1

        try:
            idx = int(result.replace('.', '').strip()) - 1
            if 0 <= idx < len(tokens):
                matched = tokens[idx]
                print(f"[Gemini Judge] OK {time.time()-start:.1f}s -> {matched.get('symbol', '')}", flush=True)
                return idx
        except ValueError:
            pass

        print(f"[Gemini Judge] OK {time.time()-start:.1f}s -> 解析失败: {result}", flush=True)
        return -1

    except Exception as e:
        log_error(f"Gemini Judge: {e}")
        print(f"[Gemini Judge] 失败: {e}", flush=True)
    return -1


def call_cerebras_fast_judge(tweet_text, tokens):
    """调用 Cerebras 快速判断推文与代币的关联

    特点：
    - 使用 gpt-oss-120b，推理速度极快
    - 针对中英文翻译、语义匹配优化
    """
    if not hasattr(config, 'CEREBRAS_API_KEY') or not config.CEREBRAS_API_KEY or not tokens:
        return []

    token_list_str = [f"{i+1}. symbol:{t['symbol']} name:{t['name']}" for i, t in enumerate(tokens)]
    token_str = "\n".join(token_list_str)

    prompt = f"""判断以下推文是否在提及代币列表中的某个代币。

推文内容: {tweet_text}

代币列表:
{token_str}

规则:
- 推文的单词需要与代币的 symbol 或 name 有关联（包含、谐音、缩写、翻译等）
- 如果有匹配，返回代币序号
- 如果多个匹配，返回所有匹配的序号，用逗号分隔
- 如果没有任何匹配，返回 none

只返回序号或 "none"，不要其他内容："""

    start = time.time()
    try:
        headers = {
            "Authorization": f"Bearer {config.CEREBRAS_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "gpt-oss-120b",
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0
        }
        resp = session.post(config.CEREBRAS_API_URL, headers=headers, json=payload, timeout=30)
        if resp.status_code == 200:
            result = resp.json()['choices'][0]['message']['content'].strip().lower()

            if result == 'none' or not result:
                print(f"[Cerebras] OK {time.time()-start:.1f}s -> 无匹配", flush=True)
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

            matched_symbols = [tokens[i].get('symbol', '') for i in matched_indices]
            print(f"[Cerebras] OK {time.time()-start:.1f}s -> {matched_symbols}", flush=True)
            return matched_indices
        print(f"[Cerebras] HTTP {resp.status_code}", flush=True)
    except Exception as e:
        log_error(f"Cerebras Fast Judge: {e}")
        print(f"[Cerebras] 失败: {e}", flush=True)
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

        keywords = call_gemini(content, image_paths if image_paths else None)
        if keywords:
            return keywords, 'gemini'

    keywords = call_deepseek(content)
    return keywords, 'deepseek'


def warm_up_ai_clients():
    """预热 AI 客户端，预先建立 TCP/SSL 连接池"""
    print("[AI Warm-up] 开始预热 AI 引擎...", flush=True)
    
    # 1. 预热 Gemini
    try:
        get_gemini_client()
        print("[AI Warm-up] Gemini 客户端已初始化", flush=True)
    except Exception:
        pass

    # 2. 预热 Cerebras (发送一个极小的请求)
    if hasattr(config, 'CEREBRAS_API_KEY') and config.CEREBRAS_API_KEY:
        try:
            call_cerebras_fast_judge("warmup", [{"symbol": "warmup", "name": "warmup"}])
            print("[AI Warm-up] Cerebras 连接已建立", flush=True)
        except Exception:
            pass

    # 3. 预热 DeepSeek
    if hasattr(config, 'DEEPSEEK_API_KEY') and config.DEEPSEEK_API_KEY:
        try:
            call_deepseek("warmup")
            print("[AI Warm-up] DeepSeek 连接已建立", flush=True)
        except Exception:
            pass

    print("[AI Warm-up] 预热完成", flush=True)
