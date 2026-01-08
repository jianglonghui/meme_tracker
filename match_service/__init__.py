"""
代币撮合服务 (端口 5053)
- 监听推文流和代币流
- 使用 DeepSeek/Gemini 提取关键词
- 匹配代币并发送到跟踪服务
"""
import json
import time
import threading
import sqlite3
import atexit
import requests
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, jsonify, request
import config

from .state import (
    stats, token_list, token_lock, MAX_TOKENS,
    pending_news, pending_lock,
    recent_matches, recent_attempts, recent_errors, recent_filtered, log_lock,
    matched_token_names, matched_names_lock,
    log_error, log_filtered, log_attempt, log_match,
    update_attempt, update_attempt_task
)
from .blacklist import (
    load_blacklist, add_to_blacklist, remove_from_blacklist,
    load_exclusive_blacklist, add_to_exclusive_blacklist, remove_from_exclusive_blacklist
)
from .matchers import (
    match_new_tokens, match_exclusive_tokens,
    refresh_exclusive_tokens, get_exclusive_tokens, search_binance_tokens
)
from .ai_clients import extract_keywords
from .utils import load_seen_events, save_seen_events, get_cached_image

app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=10)

# 推文缓存
news_buffer = []
NEWS_BUFFER_SIZE = 10
MAX_NEWS_AGE = 3600 * 1000  # 1小时


def buffer_news(news_data):
    """缓存推文，满10条批量写入数据库"""
    news_buffer.append(news_data)
    if len(news_buffer) >= NEWS_BUFFER_SIZE:
        flush_news_buffer()


def flush_news_buffer():
    """将缓存的推文写入数据库"""
    global news_buffer
    if not news_buffer:
        return
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        for news in news_buffer:
            cursor.execute('''
                INSERT INTO all_news (
                    news_time, news_author, news_author_name, news_avatar, news_type,
                    news_content, news_images, news_videos,
                    ref_author, ref_author_name, ref_avatar, ref_content, ref_images
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                news.get('time'), news.get('author'), news.get('authorName'),
                news.get('avatar'), news.get('type'), news.get('content'),
                json.dumps(news.get('images', [])), json.dumps(news.get('videos', [])),
                news.get('refAuthor'), news.get('refAuthorName'), news.get('refAvatar'),
                news.get('refContent'), json.dumps(news.get('refImages', []))
            ))
        conn.commit()
        conn.close()
        print(f"[全量记录] 写入 {len(news_buffer)} 条推文", flush=True)
        news_buffer = []
    except Exception as e:
        print(f"[全量记录] 写入失败: {e}", flush=True)


def send_to_tracker(news_data, keywords, matched_tokens):
    """发送到跟踪服务"""
    try:
        resp = requests.post(
            f"{config.get_service_url('tracker')}/track",
            json={'news': news_data, 'keywords': keywords, 'tokens': matched_tokens[:5]},
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            result = resp.json()
            print(f"[Tracker] 已提交 #{result.get('match_id')}", flush=True)
            push_to_telegram(news_data, keywords, matched_tokens)
            return True
    except Exception as e:
        log_error(f"Tracker连接: {e}")
    return False


def push_to_telegram(news_data, keywords, matched_tokens):
    """推送撮合结果到 Telegram"""
    try:
        tokens_info = []
        for t in matched_tokens[:5]:
            symbol = t.get('tokenSymbol', '')
            ca = t.get('tokenAddress', '')
            if symbol and ca:
                tokens_info.append({
                    'symbol': symbol, 'ca': ca,
                    'source': t.get('_token_source', ''),
                    'method': t.get('_match_method', '')
                })
        if not tokens_info:
            return

        payload = {
            'tweet': news_data.get('content', ''),
            'author': news_data.get('author', ''),
            'authorName': news_data.get('authorName', ''),
            'type': news_data.get('type', ''),
            'tokens': tokens_info,
            'keywords': keywords,
            'refAuthor': news_data.get('refAuthor', ''),
            'refAuthorName': news_data.get('refAuthorName', ''),
            'refContent': news_data.get('refContent', '')
        }
        requests.post('http://127.0.0.1:5060/news_token', json=payload, timeout=3)
    except:
        pass


def add_to_pending(news_data, tweet_text, matched_ids=None, image_urls=None):
    """添加到持续检测队列"""
    news_time = news_data.get('time', 0)
    expire_time = news_time + config.TIME_WINDOW_MS / 1000

    with pending_lock:
        pending_news.append({
            'news_data': news_data,
            'tweet_text': tweet_text,
            'keywords': [],
            'expire_time': expire_time,
            'matched_token_ids': set(matched_ids) if matched_ids else set(),
            'image_urls': image_urls or []
        })


def check_pending_news():
    """检查持续检测队列"""
    while stats['running']:
        time.sleep(5)
        current_time = time.time()

        with pending_lock:
            expired = [p for p in pending_news if current_time > p['expire_time']]
            for p in expired:
                pending_news.remove(p)

            for pending in pending_news:
                tweet_text = pending['tweet_text']
                matched_ids = pending['matched_token_ids']
                news_data = pending['news_data']
                image_urls = pending.get('image_urls', [])

                # 检查新代币
                with token_lock:
                    for token in token_list:
                        token_id = token.get('tokenAddress')
                        if token_id in matched_ids:
                            continue

                        symbol = (token.get('tokenSymbol') or '').lower()
                        name = (token.get('tokenName') or '').lower()
                        tweet_lower = tweet_text.lower()

                        # 简单硬编码匹配
                        if symbol and len(symbol) >= 2 and symbol in tweet_lower:
                            matched_ids.add(token_id)
                            token_copy = token.copy()
                            token_copy['_match_method'] = 'hardcoded'
                            token_copy['_token_source'] = 'new'
                            token_copy['_match_time_cost'] = int(time.time() * 1000) - token.get('createTime', 0)

                            stats['total_matches'] += 1
                            log_match(news_data.get('author', ''), news_data.get('content', ''), [token_copy])
                            send_to_tracker(news_data, [], [token_copy])

                            with matched_names_lock:
                                matched_token_names.add(symbol)


def process_news_item(news_data, full_content, all_images):
    """处理单条推文"""
    author = news_data.get('author', '')
    content = news_data.get('content', '')
    news_time = news_data.get('time', 0)
    current_time_ms = int(time.time() * 1000)

    tweet_text = full_content or content
    if not tweet_text:
        log_filtered(author, content, "推文内容为空", news_time)
        return

    print(f"[推文] @{author}: {tweet_text[:100]}...", flush=True)
    log_attempt(author, content, [], 0, 0, [])

    try:
        def match_new():
            matched, tokens_count, names = match_new_tokens(news_time, tweet_text, all_images)
            if matched:
                stats['total_matches'] += 1
                stats['last_match'] = time.time()
                log_match(author, content, matched)
                send_to_tracker(news_data, [], matched)
                for t in matched:
                    method = t.get('_match_method', 'hardcoded')
                    task_type = 'new_hardcoded' if method == 'hardcoded' else 'new_ai'
                    update_attempt_task(content, task_type, 'success', t.get('tokenSymbol'))

            # 加入持续检测
            window_end = news_time * 1000 + config.TIME_WINDOW_MS
            if current_time_ms < window_end:
                matched_ids = [t.get('tokenAddress') for t in matched] if matched else []
                add_to_pending(news_data, tweet_text, matched_ids, all_images)

        def match_old():
            matched = match_exclusive_tokens(tweet_text, all_images)
            if matched:
                stats['total_matches'] += 1
                stats['last_match'] = time.time()
                # 转换格式
                formatted = [{
                    'tokenAddress': t['address'],
                    'tokenSymbol': t['symbol'],
                    'tokenName': t['name'],
                    'chain': t['chain'],
                    'marketCap': t['marketCap'],
                    'source': 'exclusive',
                    '_match_method': t.get('_match_method', 'ai'),
                    '_token_source': t.get('_token_source', 'exclusive'),
                    '_match_time_cost': t.get('_match_time_cost', 0)
                } for t in matched]
                log_match(author, content, formatted)
                send_to_tracker(news_data, [], formatted)

        executor.submit(match_new)
        executor.submit(match_old)

    except Exception as e:
        log_error(f"处理推文异常: {e}")


def fetch_token_stream():
    """监听代币流"""
    print("监听代币流...", flush=True)
    while stats['running']:
        try:
            resp = requests.get(f"{config.get_service_url('token')}/stream", stream=True, timeout=(5, None), proxies={'http': None, 'https': None})
            for line in resp.iter_lines():
                if line:
                    line = line.decode('utf-8')
                    if line.startswith('data:'):
                        data = json.loads(line[5:].strip())
                        with token_lock:
                            exists = any(t.get('tokenAddress') == data.get('tokenAddress') for t in token_list)
                            if not exists:
                                token_list.append(data)
                                if len(token_list) > MAX_TOKENS:
                                    token_list.pop(0)
        except Exception as e:
            log_error(f"代币流: {e}")
            time.sleep(2)


def fetch_news_stream():
    """监听推文流"""
    print("监听推文流...", flush=True)
    seen_events = load_seen_events()
    last_save_time = time.time()

    while stats['running']:
        try:
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

                        if time.time() - last_save_time > 30:
                            save_seen_events(seen_events)
                            last_save_time = time.time()

                        content = data.get('content', '') or ''
                        author = data.get('author', '')
                        event_type = data.get('type', '')
                        news_time = data.get('time', 0)
                        images = data.get('images', []) or []

                        news_data = {
                            'time': news_time, 'author': author,
                            'authorName': data.get('authorName', ''),
                            'avatar': data.get('avatar', ''),
                            'type': event_type, 'content': content,
                            'images': images,
                            'videos': data.get('videos', []) or [],
                            'refAuthor': data.get('refAuthor', ''),
                            'refAuthorName': data.get('refAuthorName', ''),
                            'refAvatar': data.get('refAvatar', ''),
                            'refContent': data.get('refContent', ''),
                            'refImages': data.get('refImages', []) or [],
                        }

                        buffer_news(news_data)

                        current_time_ms = int(time.time() * 1000)
                        news_age = current_time_ms - (news_time * 1000 if news_time < 10000000000 else news_time)
                        if news_age > MAX_NEWS_AGE:
                            log_filtered(author, content, f"推文过期", news_time)
                            continue

                        ref_content = data.get('refContent', '') or ''
                        full_content = content
                        if event_type == 'follow':
                            ref_author = data.get('refAuthor', '')
                            full_content = f"关注了 @{ref_author}\n\n{content}" if content else f"关注了 @{ref_author}"
                        elif ref_content:
                            full_content = f"{content}\n\n引用推文: {ref_content}" if content else ref_content

                        ref_images = data.get('refImages', []) or []
                        all_images = images + ref_images

                        executor.submit(process_news_item, news_data, full_content, all_images)

        except Exception as e:
            log_error(f"推文流: {e}")
            time.sleep(2)


def exclusive_tokens_updater():
    """后台线程：每60秒刷新优质代币缓存"""
    while stats['running']:
        refresh_exclusive_tokens()
        time.sleep(60)


# ==================== Flask 路由 ====================

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
        'errors': stats['errors'],
        'enable_hardcoded_match': stats['enable_hardcoded_match']
    })


@app.route('/recent')
def recent():
    with log_lock:
        matches = list(recent_matches)[::-1]
        attempts = list(recent_attempts)[::-1]
        filtered = list(recent_filtered)[::-1]
        errors = list(recent_errors)[::-1]
    with pending_lock:
        pending = [{
            'author': p['news_data'].get('author', ''),
            'content': p['news_data'].get('content', '')[:100],
            'keywords': p.get('keywords', [])[:5] if p.get('keywords') else [],
            'matched_count': len(p['matched_token_ids']),
            'expire_time': p['expire_time']
        } for p in pending_news]
    return jsonify({
        'matches': matches, 'attempts': attempts,
        'filtered': filtered, 'pending': pending, 'errors': errors
    })


@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


@app.route('/hardcoded_match', methods=['GET', 'POST'])
def hardcoded_match_toggle():
    if request.method == 'POST':
        data = request.json or {}
        stats['enable_hardcoded_match'] = data.get('enabled', True)
    return jsonify({'enabled': stats['enable_hardcoded_match']})


@app.route('/extract_keywords', methods=['POST'])
def test_extract_keywords():
    data = request.json
    content = data.get('content', '')
    image_urls = data.get('image_urls', [])
    if not content and not image_urls:
        return jsonify({'error': '需要提供 content 或 image_urls'}), 400
    keywords, source = extract_keywords(content, image_urls)
    return jsonify({'keywords': keywords, 'source': source})


@app.route('/search', methods=['POST'])
def test_search():
    data = request.json
    keyword = data.get('keyword', '')
    if not keyword:
        return jsonify({'error': '需要提供 keyword'}), 400
    tokens = search_binance_tokens(keyword)
    return jsonify({'tokens': tokens, 'count': len(tokens)})


# ==================== 黑名单 API ====================

@app.route('/blacklist', methods=['GET'])
def get_blacklist():
    return jsonify(load_blacklist())


@app.route('/blacklist/add', methods=['POST'])
def api_add_blacklist():
    data = request.json
    token_name = data.get('token_name', '')
    if add_to_blacklist(token_name):
        return jsonify({'success': True, 'blacklist': load_blacklist()})
    return jsonify({'success': False, 'error': '添加失败或已存在'}), 400


@app.route('/blacklist/remove', methods=['POST'])
def api_remove_blacklist():
    data = request.json
    token_name = data.get('token_name', '')
    if remove_from_blacklist(token_name):
        return jsonify({'success': True, 'blacklist': load_blacklist()})
    return jsonify({'success': False, 'error': '移除失败或不存在'}), 400


# ==================== 优质代币合约黑名单 API ====================

@app.route('/exclusive_blacklist', methods=['GET'])
def get_exclusive_blacklist_api():
    return jsonify(load_exclusive_blacklist())


@app.route('/exclusive_blacklist/add', methods=['POST'])
def api_add_exclusive_blacklist():
    data = request.json
    address = data.get('address', '')
    if add_to_exclusive_blacklist(address):
        return jsonify({'success': True, 'blacklist': load_exclusive_blacklist()})
    return jsonify({'success': False, 'error': '添加失败或已存在'}), 400


@app.route('/exclusive_blacklist/remove', methods=['POST'])
def api_remove_exclusive_blacklist():
    data = request.json
    address = data.get('address', '')
    if remove_from_exclusive_blacklist(address):
        return jsonify({'success': True, 'blacklist': load_exclusive_blacklist()})
    return jsonify({'success': False, 'error': '移除失败或不存在'}), 400


@app.route('/exclusive_tokens', methods=['GET'])
def get_exclusive_tokens_api():
    return jsonify(get_exclusive_tokens())


# ==================== 启动 ====================

def run():
    """启动服务"""
    # 注册退出处理
    atexit.register(flush_news_buffer)

    # 启动后台线程
    threading.Thread(target=fetch_token_stream, daemon=True).start()
    threading.Thread(target=fetch_news_stream, daemon=True).start()
    threading.Thread(target=check_pending_news, daemon=True).start()
    threading.Thread(target=exclusive_tokens_updater, daemon=True).start()

    print(f"[Match Service] 启动在端口 {config.MATCH_PORT}", flush=True)
    app.run(host='0.0.0.0', port=config.MATCH_PORT, debug=False, use_reloader=False)


if __name__ == '__main__':
    run()
