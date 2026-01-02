"""
代币撮合服务 (端口 5053)
- 监听推文流和代币流
- 使用 DeepSeek/Gemini 提取关键词（Gemini 支持图片）
- 匹配代币并发送到跟踪服务
"""
import requests
import json
import threading
import time
import os
import hashlib
from flask import Flask, jsonify
import config

# 图片缓存目录（与 dashboard 共用）
MEDIA_CACHE_DIR = os.path.join(os.path.dirname(__file__), 'media_cache')
os.makedirs(MEDIA_CACHE_DIR, exist_ok=True)

# 已处理推文缓存文件
SEEN_EVENTS_FILE = os.path.join(os.path.dirname(__file__), 'seen_events.json')

app = Flask(__name__)


def load_seen_events():
    """从文件加载已处理的推文 ID"""
    try:
        if os.path.exists(SEEN_EVENTS_FILE):
            with open(SEEN_EVENTS_FILE, 'r') as f:
                data = json.load(f)
                # 只保留最近24小时的记录
                cutoff = time.time() - 86400
                return {k: v for k, v in data.items() if v > cutoff}
    except Exception as e:
        print(f"加载 seen_events 失败: {e}", flush=True)
    return {}


def save_seen_events(seen_events):
    """保存已处理的推文 ID 到文件"""
    try:
        with open(SEEN_EVENTS_FILE, 'w') as f:
            json.dump(seen_events, f)
    except Exception as e:
        print(f"保存 seen_events 失败: {e}", flush=True)

# 状态统计
stats = {
    'total_matches': 0,
    'total_news': 0,
    'running': True,
    'last_match': None,
    'errors': 0
}

# 代币列表
token_list = []
token_lock = threading.Lock()
MAX_TOKENS = 500  # 缓存上限

# 最近匹配、撮合尝试和错误日志
recent_matches = []
recent_attempts = []
recent_errors = []
recent_filtered = []  # 过滤的推文
log_lock = threading.Lock()
MAX_LOG_SIZE = 20

# 待检测队列（窗口期内持续检测）
pending_news = []  # [{news_data, keywords, expire_time, matched_tokens}]
pending_lock = threading.Lock()
MAX_NEWS_AGE = 3600 * 1000  # 1小时（毫秒）


def log_error(msg):
    """记录错误"""
    with log_lock:
        recent_errors.append({'time': time.time(), 'msg': msg})
        if len(recent_errors) > MAX_LOG_SIZE:
            recent_errors.pop(0)
    stats['errors'] += 1


def log_filtered(author, content, reason, news_time):
    """记录被过滤的推文"""
    with log_lock:
        recent_filtered.append({
            'time': time.time(),
            'news_time': news_time,
            'author': author,
            'content': content[:80],
            'reason': reason
        })
        if len(recent_filtered) > MAX_LOG_SIZE:
            recent_filtered.pop(0)


def log_attempt(author, content, keywords, tokens_in_window, matched_count, window_token_names):
    """记录撮合尝试"""
    with log_lock:
        recent_attempts.append({
            'time': time.time(),
            'author': author,
            'content': content[:100],
            'keywords': keywords[:5] if keywords else [],
            'tokens_in_window': tokens_in_window,
            'matched': matched_count,
            'window_tokens': window_token_names[:5] if window_token_names else []
        })
        if len(recent_attempts) > MAX_LOG_SIZE:
            recent_attempts.pop(0)


def log_match(author, content, tokens):
    """记录匹配"""
    with log_lock:
        recent_matches.append({
            'time': time.time(),
            'author': author,
            'content': content[:80],
            'tokens': [t.get('tokenSymbol', '') for t in tokens[:3]]
        })
        if len(recent_matches) > MAX_LOG_SIZE:
            recent_matches.pop(0)


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
        keywords_str = ', '.join(p['keywords'])
        examples += f"\n示例{i}: 推文「{p['tweet_content'][:50]}...」→ 关键词: [{keywords_str}] → 匹配代币: {p['best_token']}"

    return examples


def call_deepseek(news_content):
    """调用 DeepSeek API 提取关键词"""
    if not config.DEEPSEEK_API_KEY:
        return []

    examples = build_examples_prompt()

    prompt = f"""作为meme币分析师，从推文中提取最可能被用作代币名称的关键词。

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
{examples}

推文：{news_content}

返回JSON数组（最多3个，按潜力排序）："""

    try:
        headers = {
            "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 200
        }
        resp = requests.post(config.DEEPSEEK_API_URL, headers=headers, json=payload, timeout=10)
        if resp.status_code == 200:
            result = resp.json()
            content = result['choices'][0]['message']['content'].strip()
            # 解析 JSON 数组（支持多种格式）
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
                result = [k.lower() for k in keywords if isinstance(k, str)]
                return result[:3]  # 最多3个
    except Exception as e:
        log_error(f"DeepSeek API: {e}")
        print(f"[DeepSeek] 异常: {e}", flush=True)
    return []


def get_cached_image(url):
    """获取缓存的图片路径，如果不存在则下载"""
    try:
        # 处理本地注入的图片 (/local_image/xxx.jpg)
        if url.startswith('/local_image/'):
            filename = url.split('/')[-1]
            local_path = os.path.join(os.path.dirname(__file__), 'image_cache', filename)
            if os.path.exists(local_path):
                return local_path
            return None

        # 使用 URL 的 MD5 作为文件名（与 dashboard 一致）
        cache_key = hashlib.md5(url.encode()).hexdigest()

        # 检查各种扩展名
        for ext in ['.jpg', '.png', '.gif', '.webp', '']:
            cache_path = os.path.join(MEDIA_CACHE_DIR, f"{cache_key}{ext}")
            if os.path.exists(cache_path) and os.path.getsize(cache_path) > 1000:
                return cache_path

        # 缓存不存在，下载图片
        ext = '.jpg'
        if '.png' in url.lower():
            ext = '.png'
        elif '.gif' in url.lower():
            ext = '.gif'
        cache_path = os.path.join(MEDIA_CACHE_DIR, f"{cache_key}{ext}")

        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }
        resp = requests.get(url, timeout=10, proxies=config.PROXIES, headers=headers)
        if resp.status_code == 200 and len(resp.content) > 1000:
            with open(cache_path, 'wb') as f:
                f.write(resp.content)
            return cache_path
    except Exception as e:
        print(f"[图片] 获取失败: {e}", flush=True)
    return None


def call_gemini_vision(news_content, image_paths=None):
    """调用 Gemini API 提取关键词（支持图片）"""
    if not config.GEMINI_API_KEY:
        return []

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=config.GEMINI_API_KEY)

        examples = build_examples_prompt()

        content_section = f"推文内容：{news_content}" if news_content else "（纯图片推文，无文字内容）"

        prompt = f"""作为meme币分析师，从推文内容和图片中提取最可能被用作代币名称的关键词。

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
6. 年份数字（如2026）

排除：
- 已存在的主流币名（BTC、ETH、SOL等）
- 通用技术词汇（blockchain、gas、node等）
- 链接、@用户名、冠词介词
{examples}

{content_section}

返回JSON数组（最多3个，按潜力排序）："""

        # 构建 parts
        parts = [types.Part.from_text(text=prompt)]

        # 添加图片
        if image_paths:
            for img_path in image_paths[:3]:  # 最多3张图片
                try:
                    with open(img_path, 'rb') as f:
                        img_data = f.read()
                    # 判断 MIME 类型
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
            model="gemini-2.0-flash",
            contents=contents,
        )

        result_text = response.text.strip()

        # 解析 JSON 数组
        keywords = None
        if result_text.startswith('['):
            keywords = json.loads(result_text)
        elif '```json' in result_text:
            json_part = result_text.split('```json')[1].split('```')[0].strip()
            keywords = json.loads(json_part)
        elif '[' in result_text:
            start = result_text.index('[')
            end = result_text.rindex(']') + 1
            keywords = json.loads(result_text[start:end])

        if keywords:
            result = [k.lower() for k in keywords if isinstance(k, str)]
            return result[:3]  # 最多3个

    except ImportError:
        log_error("Gemini: 需要安装 google-genai")
        print("[Gemini] 需要安装 google-genai: pip install google-genai", flush=True)
    except Exception as e:
        log_error(f"Gemini API: {e}")
        print(f"[Gemini] 异常: {e}", flush=True)
    return []


def extract_keywords(content, image_urls=None):
    """提取关键词：有图片时使用 Gemini，否则使用 DeepSeek"""
    # 如果有图片且配置了 Gemini，优先使用 Gemini
    if image_urls and config.GEMINI_API_KEY:
        # 获取缓存的图片（优先使用 dashboard 已下载的）
        image_paths = []
        for url in image_urls[:3]:
            path = get_cached_image(url)
            if path:
                image_paths.append(path)

        if image_paths:
            print(f"[Gemini] 处理 {len(image_paths)} 张图片...", flush=True)
            keywords = call_gemini_vision(content, image_paths)
            if keywords:
                return keywords, 'gemini'

    # 回退到 DeepSeek（纯文本）
    keywords = call_deepseek(content)
    return keywords, 'deepseek'


def calculate_match_score(keywords, symbol, name):
    """计算匹配分数"""
    max_score = 0
    matched_keyword = None
    match_type = None

    for kw in keywords:
        if not kw:
            continue

        score = 0
        current_type = None

        if kw == symbol:
            score = 5.0
            current_type = "完全匹配symbol"
        elif kw == name:
            score = 4.0
            current_type = "完全匹配name"
        elif symbol and len(symbol) >= 2 and symbol in kw:
            score = 3.0
            current_type = "symbol在关键词中"
        elif name and len(name) >= 2 and name in kw:
            score = 2.5
            current_type = "name在关键词中"
        elif kw in symbol:
            score = 2.0
            current_type = "关键词在symbol中"
        elif kw in name:
            score = 1.5
            current_type = "关键词在name中"

        if score > max_score:
            max_score = score
            matched_keyword = kw
            match_type = current_type

    return max_score, matched_keyword, match_type


def match_tokens(news_time, keywords):
    """匹配时间窗口内的代币，返回 (匹配列表, 窗口内代币数, 窗口内代币名称列表)"""
    if not news_time:
        return [], 0, []

    news_time_ms = news_time * 1000
    matched = []
    tokens_in_window = 0
    window_token_names = []

    with token_lock:
        for token in token_list:
            create_time = token.get('createTime', 0)
            if not create_time:
                continue

            time_diff = abs(create_time - news_time_ms)
            if time_diff <= config.TIME_WINDOW_MS:
                tokens_in_window += 1
                symbol = (token.get('tokenSymbol') or '').lower()
                name = (token.get('tokenName') or '').lower()
                window_token_names.append(token.get('tokenSymbol') or token.get('tokenName') or 'Unknown')

                if keywords:
                    score, matched_kw, match_type = calculate_match_score(keywords, symbol, name)
                    if score > 0:
                        token_copy = token.copy()
                        token_copy['_match_score'] = score
                        token_copy['_matched_keyword'] = matched_kw
                        token_copy['_match_type'] = match_type
                        holders = float(token.get('holders', 0) or 0)
                        token_copy['_final_score'] = score * holders
                        matched.append(token_copy)

    matched.sort(key=lambda x: x.get('_final_score', 0), reverse=True)
    return matched, tokens_in_window, window_token_names


def send_to_tracker(news_data, keywords, matched_tokens):
    """发送到跟踪服务"""
    try:
        resp = requests.post(
            f"{config.get_service_url('tracker')}/track",
            json={
                'news': news_data,
                'keywords': keywords,
                'tokens': matched_tokens[:5]
            },
            timeout=5,
            proxies={'http': None, 'https': None}  # 本地连接不使用代理
        )
        if resp.status_code == 200:
            result = resp.json()
            print(f"[Tracker] 已提交 #{result.get('match_id')}", flush=True)
            # 推送到 Telegram
            push_to_telegram(news_data, keywords, matched_tokens)
            return True
    except Exception as e:
        log_error(f"Tracker连接: {e}")
        print(f"[Tracker] 连接失败: {e}", flush=True)
    return False


def push_to_telegram(news_data, keywords, matched_tokens):
    """推送撮合结果到 Telegram"""
    try:
        # 提取代币信息（symbol + CA）
        tokens_info = []
        for t in matched_tokens[:5]:
            symbol = t.get('tokenSymbol', '')
            ca = t.get('tokenAddress', '')
            if symbol and ca:
                tokens_info.append({'symbol': symbol, 'ca': ca})

        if not tokens_info:
            return

        resp = requests.post(
            'http://127.0.0.1:5060/news_token',
            json={
                'author': news_data.get('author', ''),
                'tweet': news_data.get('content', ''),
                'keywords': keywords,
                'tokens': tokens_info
            },
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            print(f"[Telegram] 已推送: {', '.join(token_symbols)}", flush=True)
        else:
            print(f"[Telegram] 推送失败: {resp.status_code}", flush=True)
    except Exception as e:
        # 连接失败不报错，tele_bot 可能没启动
        pass


def add_to_pending(news_data, keywords):
    """添加到待检测队列"""
    news_time = news_data.get('time', 0)
    expire_time = news_time + config.TIME_WINDOW_MS / 1000  # 窗口期结束时间（秒）

    with pending_lock:
        pending_news.append({
            'news_data': news_data,
            'keywords': keywords,
            'expire_time': expire_time,
            'matched_token_ids': set(),  # 已匹配的代币ID，避免重复
            'status': 'pending'
        })
        print(f"[待检测] 添加 @{news_data.get('author')} 到队列，窗口期至 {expire_time}", flush=True)


def check_pending_news():
    """持续检测待检测队列中的推文"""
    print("启动持续检测线程...", flush=True)

    while stats['running']:
        current_time = int(time.time())  # 秒级时间戳

        with pending_lock:
            # 移除过期的
            expired = [p for p in pending_news if current_time > p['expire_time']]
            for p in expired:
                pending_news.remove(p)
                author = p['news_data'].get('author', '')
                matched_count = len(p['matched_token_ids'])
                print(f"[待检测] @{author} 窗口期结束，共匹配 {matched_count} 个代币", flush=True)

            # 检查未过期的
            for pending in pending_news:
                news_data = pending['news_data']
                keywords = pending['keywords']
                news_time = news_data.get('time', 0)

                if not keywords:
                    continue

                # 检查新代币
                with token_lock:
                    for token in token_list:
                        token_id = token.get('tokenAddress', '')
                        if token_id in pending['matched_token_ids']:
                            continue  # 已匹配过

                        create_time = token.get('createTime', 0)
                        if not create_time:
                            continue

                        # 检查时间窗口
                        time_diff = abs(create_time - news_time * 1000)
                        if time_diff > config.TIME_WINDOW_MS:
                            continue

                        symbol = (token.get('tokenSymbol') or '').lower()
                        name = (token.get('tokenName') or '').lower()

                        # 调试：打印窗口内的代币
                        author = news_data.get('author', '')
                        print(f"[持续检测] @{author} 窗口内代币: {token.get('tokenSymbol')} (时差: {time_diff/1000:.1f}s)", flush=True)
                        print(f"[持续检测] 关键词: {keywords}, symbol: {symbol}, name: {name}", flush=True)

                        score, matched_kw, match_type = calculate_match_score(keywords, symbol, name)
                        print(f"[持续检测] 匹配分数: {score}, 匹配词: {matched_kw}", flush=True)
                        if score > 0:
                            pending['matched_token_ids'].add(token_id)

                            # 记录匹配
                            token_copy = token.copy()
                            token_copy['_match_score'] = score
                            token_copy['_matched_keyword'] = matched_kw
                            token_copy['_match_type'] = match_type

                            author = news_data.get('author', '')
                            print(f"[持续检测] @{author} 新匹配: {token.get('tokenSymbol')} (关键词: {matched_kw})", flush=True)

                            # 发送到 tracker
                            stats['total_matches'] += 1
                            stats['last_match'] = time.time()
                            log_match(author, news_data.get('content', ''), [token_copy])
                            send_to_tracker(news_data, keywords, [token_copy])

        time.sleep(2)  # 每2秒检查一次


def fetch_token_stream():
    """监听代币流"""
    print("监听代币流...", flush=True)
    while stats['running']:
        try:
            # 本地连接不使用代理
            resp = requests.get(f"{config.get_service_url('token')}/stream", stream=True, timeout=60, proxies={'http': None, 'https': None})
            for line in resp.iter_lines():
                if line:
                    line = line.decode('utf-8')
                    if line.startswith('data:'):
                        data = json.loads(line[5:].strip())
                        with token_lock:
                            exists = any(t.get('tokenAddress') == data.get('tokenAddress') for t in token_list)
                            if not exists:
                                token_list.append(data)
                                symbol = data.get('tokenSymbol', 'Unknown')
                                print(f"[代币流] 收到新代币: {symbol} (总计: {len(token_list)})", flush=True)
                                # 超过上限时移除最旧的
                                if len(token_list) > MAX_TOKENS:
                                    token_list.pop(0)
        except Exception as e:
            log_error(f"代币流: {e}")
            print(f"代币流异常: {e}", flush=True)
            time.sleep(2)


def fetch_news_stream():
    """监听推文流并匹配"""
    print("监听推文流...", flush=True)
    seen_events = load_seen_events()
    last_save_time = time.time()

    while stats['running']:
        try:
            # 本地连接不使用代理
            resp = requests.get(f"{config.get_service_url('news')}/stream", stream=True, timeout=60, proxies={'http': None, 'https': None})
            for line in resp.iter_lines():
                if line:
                    line = line.decode('utf-8')
                    if line.startswith('data:'):
                        data = json.loads(line[5:].strip())
                        stats['total_news'] += 1

                        event_id = f"{data.get('time')}_{data.get('author')}_{data.get('type')}"
                        if event_id in seen_events:
                            continue
                        seen_events[event_id] = time.time()

                        # 每30秒保存一次
                        if time.time() - last_save_time > 30:
                            save_seen_events(seen_events)
                            last_save_time = time.time()

                        content = data.get('content', '') or ''
                        author = data.get('author', '')
                        event_type = data.get('type', '')
                        news_time = data.get('time', 0)
                        images = data.get('images', []) or []

                        # 构建推文数据
                        news_data = {
                            'time': news_time,
                            'author': author,
                            'authorName': data.get('authorName', ''),
                            'avatar': data.get('avatar', ''),
                            'type': event_type,
                            'content': content,
                            'images': images,
                            'videos': data.get('videos', []) or [],
                            'refAuthor': data.get('refAuthor', ''),
                            'refAuthorName': data.get('refAuthorName', ''),
                            'refAvatar': data.get('refAvatar', ''),
                            'refContent': data.get('refContent', ''),
                            'refImages': data.get('refImages', []) or [],
                        }

                        # 检查推文时间，超过1小时的跳过
                        current_time_ms = int(time.time() * 1000)
                        news_age = current_time_ms - (news_time * 1000 if news_time < 10000000000 else news_time)

                        if news_age > MAX_NEWS_AGE:
                            age_hours = news_age / 3600000
                            log_filtered(author, content, f"推文过期({age_hours:.1f}小时前)", news_time)
                            print(f"[过滤] @{author} 推文过期 ({age_hours:.1f}小时前)", flush=True)
                            continue

                        # 使用 Gemini（有图片时）或 DeepSeek 提取关键词
                        keywords, api_used = extract_keywords(content, images if images else None)

                        if not keywords:
                            log_filtered(author, content, "无法提取关键词", news_time)
                            print(f"[过滤] @{author} 无法提取关键词", flush=True)
                            continue

                        # 立即匹配当前缓存的代币
                        matched_tokens, tokens_in_window, window_token_names = match_tokens(news_time, keywords)

                        # 记录撮合尝试
                        log_attempt(author, content, keywords, tokens_in_window, len(matched_tokens), window_token_names)

                        if matched_tokens:
                            stats['total_matches'] += 1
                            stats['last_match'] = time.time()
                            log_match(author, content, matched_tokens)

                            api_tag = "[Gemini+图片]" if api_used == 'gemini' else "[DeepSeek]"
                            print(f"\n[匹配] {api_tag} @{author}: {content[:50]}...", flush=True)
                            print(f"  关键词: {keywords}", flush=True)
                            print(f"  匹配代币: {len(matched_tokens)} 个", flush=True)

                            send_to_tracker(news_data, keywords, matched_tokens)

                        # 检查是否在窗口期内，如果是则加入持续检测队列
                        window_end = news_time * 1000 + config.TIME_WINDOW_MS
                        if current_time_ms < window_end:
                            add_to_pending(news_data, keywords)

        except Exception as e:
            log_error(f"推文流: {e}")
            print(f"推文流异常: {e}", flush=True)
            time.sleep(2)


@app.route('/status')
def status():
    with pending_lock:
        pending_count = len(pending_news)
    return jsonify({
        'service': 'match_service',
        'port': config.MATCH_PORT,
        'running': stats['running'],
        'total_matches': stats['total_matches'],
        'total_news': stats['total_news'],
        'tokens_cached': len(token_list),
        'pending_detection': pending_count,
        'last_match': stats['last_match'],
        'errors': stats['errors']
    })


@app.route('/recent')
def recent():
    """返回最近的匹配、撮合尝试、过滤和错误"""
    with log_lock:
        matches = list(recent_matches)[::-1]
        attempts = list(recent_attempts)[::-1]
        filtered = list(recent_filtered)[::-1]
        errors = list(recent_errors)[::-1]
    with pending_lock:
        pending = [{
            'author': p['news_data'].get('author', ''),
            'content': p['news_data'].get('content', '')[:100],
            'keywords': p['keywords'][:5] if p['keywords'] else [],
            'matched_count': len(p['matched_token_ids']),
            'expire_time': p['expire_time']
        } for p in pending_news]
    return jsonify({
        'matches': matches,
        'attempts': attempts,
        'filtered': filtered,
        'pending': pending,
        'errors': errors
    })


@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


@app.route('/extract_keywords', methods=['POST'])
def test_extract_keywords():
    """测试关键词提取"""
    from flask import request
    data = request.get_json()
    text = data.get('text', '')
    images = data.get('images', [])

    keywords, api_used = extract_keywords(text, images if images else None)
    return jsonify({
        'text': text,
        'keywords': keywords,
        'api': api_used
    })


if __name__ == "__main__":
    print(f"代币撮合服务启动: http://127.0.0.1:{config.get_port('match')}", flush=True)
    print(f"等待 news_service ({config.get_port('news')}) 和 token_service ({config.get_port('token')})...", flush=True)

    # 启动代币流监听
    token_thread = threading.Thread(target=fetch_token_stream, daemon=True)
    token_thread.start()

    time.sleep(2)

    # 启动推文流监听
    news_thread = threading.Thread(target=fetch_news_stream, daemon=True)
    news_thread.start()

    # 启动持续检测线程
    pending_thread = threading.Thread(target=check_pending_news, daemon=True)
    pending_thread.start()

    app.run(host='0.0.0.0', port=config.get_port('match'), debug=False, threaded=True)
