"""
代币撮合服务 (端口 5053)
- 监听推文流和代币流
- 使用 DeepSeek 提取关键词
- 匹配代币并发送到跟踪服务
"""
import requests
import json
import threading
import time
from flask import Flask, jsonify
import config

app = Flask(__name__)

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

# 最近匹配和错误日志
recent_matches = []
recent_errors = []
log_lock = threading.Lock()
MAX_LOG_SIZE = 20


def log_error(msg):
    """记录错误"""
    with log_lock:
        recent_errors.append({'time': time.time(), 'msg': msg})
        if len(recent_errors) > MAX_LOG_SIZE:
            recent_errors.pop(0)
    stats['errors'] += 1


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


def call_deepseek(news_content):
    """调用 DeepSeek API 提取关键词"""
    if not config.DEEPSEEK_API_KEY:
        return []

    prompt = f"""作为meme币分析师，从推文中提取可能被用作代币名称的关键词。

提取原则：
- 只提取推文原文中的词，不翻译不推断
- 中文短语保持完整
- 包含：缩写、数字年份、名词短语、情绪词、人名地名
- 排除：链接、@用户名、冠词介词

推文：{news_content}

返回JSON数组："""

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
            if content.startswith('['):
                keywords = json.loads(content)
                return [k.lower() for k in keywords if isinstance(k, str)]
    except Exception as e:
        log_error(f"DeepSeek API: {e}")
        print(f"[DeepSeek] 异常: {e}", flush=True)
    return []


def calculate_match_score(keywords, symbol, name):
    """计算匹配分数"""
    max_score = 0
    matched_keyword = None
    match_type = None

    for kw in keywords:
        if not kw or len(kw) < 2:
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
    """匹配时间窗口内的代币"""
    if not news_time:
        return []

    news_time_ms = news_time * 1000
    matched = []

    with token_lock:
        for token in token_list:
            create_time = token.get('createTime', 0)
            if not create_time:
                continue

            time_diff = abs(create_time - news_time_ms)
            if time_diff <= config.TIME_WINDOW_MS:
                symbol = (token.get('tokenSymbol') or '').lower()
                name = (token.get('tokenName') or '').lower()

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
    return matched


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
            return True
    except Exception as e:
        log_error(f"Tracker连接: {e}")
        print(f"[Tracker] 连接失败: {e}", flush=True)
    return False


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
    seen_events = set()

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
                        seen_events.add(event_id)

                        content = data.get('content', '') or ''
                        author = data.get('author', '')
                        event_type = data.get('type', '')
                        news_time = data.get('time', 0)

                        keywords = call_deepseek(content)
                        matched_tokens = match_tokens(news_time, keywords)

                        if matched_tokens:
                            stats['total_matches'] += 1
                            stats['last_match'] = time.time()
                            log_match(author, content, matched_tokens)

                            print(f"\n[匹配] @{author}: {content[:50]}...", flush=True)
                            print(f"  关键词: {keywords}", flush=True)
                            print(f"  匹配代币: {len(matched_tokens)} 个", flush=True)

                            news_data = {
                                'time': news_time,
                                'author': author,
                                'type': event_type,
                                'content': content
                            }
                            send_to_tracker(news_data, keywords, matched_tokens)

        except Exception as e:
            log_error(f"推文流: {e}")
            print(f"推文流异常: {e}", flush=True)
            time.sleep(2)


@app.route('/status')
def status():
    return jsonify({
        'service': 'match_service',
        'port': config.MATCH_PORT,
        'running': stats['running'],
        'total_matches': stats['total_matches'],
        'total_news': stats['total_news'],
        'tokens_cached': len(token_list),
        'last_match': stats['last_match'],
        'errors': stats['errors']
    })


@app.route('/recent')
def recent():
    """返回最近的匹配和错误"""
    with log_lock:
        matches = list(recent_matches)[::-1]
        errors = list(recent_errors)[::-1]
    return jsonify({
        'matches': matches,
        'errors': errors
    })


@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


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

    app.run(host='0.0.0.0', port=config.get_port('match'), debug=False, threaded=True)
