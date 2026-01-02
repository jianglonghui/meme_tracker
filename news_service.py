"""
推文发现服务 (端口 5050)
- 监听 Binance 推文事件
- 提供 SSE 流
"""
import requests
import time
import json
import threading
from flask import Flask, Response, jsonify
import config

app = Flask(__name__)

# 状态统计
stats = {
    'total_news': 0,
    'running': True,
    'last_fetch': None,
    'last_success': None,
    'errors': 0
}

# 去重和新闻列表
seen_ids = set()
news_list = []
news_lock = threading.Lock()

# 错误日志
error_log = []
error_lock = threading.Lock()
MAX_ERRORS = 20


def log_error(msg):
    """记录错误"""
    with error_lock:
        error_log.append({'time': time.time(), 'msg': str(msg)[:200]})
        if len(error_log) > MAX_ERRORS:
            error_log.pop(0)
    stats['errors'] += 1

payload = {
    "translateOn": 0,
    "postEventTypeList": ["newTweet", "reply", "retweet", "quote"],
    "caFlag": False,
    "bioFlag": True,
    "followFlag": True,
    "groupIds": ["113213"]
}


def fetch_news():
    try:
        response = requests.post(
            config.BINANCE_NEWS_URL,
            headers=config.HEADERS,
            cookies=config.COOKIES,
            json=payload,
            proxies=config.PROXIES,
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            if data.get('code') not in ['000000', None] and data.get('message'):
                log_error(f"API: {data.get('message')}")
            else:
                stats['last_success'] = time.time()
            return data
        else:
            log_error(f"HTTP {response.status_code}")
    except Exception as e:
        log_error(f"请求: {e}")
        print(f"请求异常: {e}", flush=True)
    return None


def get_new_items(data):
    new_items = []
    if not data or 'data' not in data:
        return new_items

    items = data.get('data', [])
    for item in items:
        user = item.get('user', {})
        item_id = f"{item.get('eventTime', '')}_{user.get('handle', '')}_{item.get('eventType', '')}"
        if item_id not in seen_ids:
            seen_ids.add(item_id)
            new_items.append(item)

    new_items.sort(key=lambda x: x.get('eventTime', 0))
    return new_items


def news_fetcher():
    print("开始获取推文...", flush=True)
    while stats['running']:
        data = fetch_news()
        stats['last_fetch'] = time.time()
        new_items = get_new_items(data)
        if new_items:
            with news_lock:
                for item in new_items:
                    news_list.append(item)
                    stats['total_news'] += 1
                    user = item.get('user', {})
                    print(f"[推文] @{user.get('handle', 'Unknown')} - {item.get('eventType', '')}", flush=True)
        time.sleep(1)


@app.route('/stream')
def stream():
    def generate():
        last_idx = 0
        heartbeat_count = 0
        while True:
            with news_lock:
                if len(news_list) > last_idx:
                    for item in news_list[last_idx:]:
                        user = item.get('user') or {}
                        ref_user = item.get('referenceUser') or {}
                        event_type = item.get('eventType', '')

                        # 解析图片和视频
                        images = parse_json_field(item.get('fileUrls') or '')
                        videos = parse_json_field(item.get('videoUrls') or '')
                        ref_images = parse_json_field(item.get('referencedFiles') or '')

                        # 原推内容
                        ref_content = ''
                        if event_type in ('reply', 'retweet', 'quote'):
                            ref_content = item.get('contentOld') or ''

                        event_data = {
                            'id': item.get('eventTime'),
                            'type': event_type,
                            'time': item.get('eventTime', ''),
                            'author': user.get('handle', 'Unknown'),
                            'authorName': user.get('username', ''),
                            'avatar': user.get('profilePic', ''),
                            'content': item.get('contentNew') or '',
                            'images': images,
                            'videos': videos,
                            'refAuthor': ref_user.get('handle', ''),
                            'refAuthorName': ref_user.get('username', ''),
                            'refAvatar': ref_user.get('profilePic', ''),
                            'refContent': ref_content,
                            'refImages': ref_images,
                        }
                        yield f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"
                    last_idx = len(news_list)
                    heartbeat_count = 0
            # 每30次循环(约15秒)发送心跳
            heartbeat_count += 1
            if heartbeat_count >= 30:
                yield ": heartbeat\n\n"
                heartbeat_count = 0
            time.sleep(0.5)
    return Response(generate(), mimetype='text/event-stream')


@app.route('/status')
def status():
    return jsonify({
        'service': 'news_service',
        'port': config.NEWS_PORT,
        'running': stats['running'],
        'total_news': stats['total_news'],
        'last_fetch': stats['last_fetch'],
        'last_success': stats['last_success'],
        'errors': stats['errors']
    })


def parse_json_field(field):
    """解析可能是JSON字符串的字段"""
    if isinstance(field, list):
        return field
    if isinstance(field, str) and field.startswith('['):
        try:
            return json.loads(field)
        except:
            pass
    return []


@app.route('/recent')
def recent():
    """返回最近的推文和错误"""
    with news_lock:
        recent_items = news_list[-10:][::-1]
    with error_lock:
        recent_errors = list(error_log)[::-1]

    items = []
    for item in recent_items:
        user = item.get('user') or {}
        ref_user = item.get('referenceUser') or {}
        event_type = item.get('eventType', '')
        content = item.get('contentNew') or ''

        # 解析图片和视频
        images = parse_json_field(item.get('fileUrls') or '')
        videos = parse_json_field(item.get('videoUrls') or '')
        ref_images = parse_json_field(item.get('referencedFiles') or '')

        # 原推内容（reply/retweet/quote时）
        ref_content = ''
        if event_type in ('reply', 'retweet', 'quote'):
            ref_content = item.get('contentOld') or ''

        items.append({
            'author': user.get('handle', 'Unknown'),
            'authorName': user.get('username', ''),
            'avatar': user.get('profilePic', ''),
            'content': content,
            'type': event_type,
            'time': item.get('eventTime', 0),
            'images': images,
            'videos': videos,
            'refAuthor': ref_user.get('handle', ''),
            'refAuthorName': ref_user.get('username', ''),
            'refAvatar': ref_user.get('profilePic', ''),
            'refContent': ref_content,
            'refImages': ref_images
        })
    return jsonify({'items': items, 'errors': recent_errors})


@app.route('/inject', methods=['POST'])
def inject():
    """注入推文到流中（用于测试撮合）"""
    import base64
    import os
    import hashlib

    from flask import request
    data = request.json
    content = data.get('content', '')
    author = data.get('author', 'test_user')
    author_name = data.get('author_name', '测试用户')
    image_data = data.get('image', '')  # base64 图片

    if not content and not image_data:
        return jsonify({'success': False, 'error': '推文内容或图片至少需要一项'}), 400

    # 处理图片
    file_urls = []
    if image_data and image_data.startswith('data:image'):
        try:
            # 解析 base64
            header, encoded = image_data.split(',', 1)
            ext = '.png' if 'png' in header else '.jpg'
            img_bytes = base64.b64decode(encoded)

            # 保存到 image_cache 目录
            cache_dir = os.path.join(os.path.dirname(__file__), 'image_cache')
            os.makedirs(cache_dir, exist_ok=True)
            filename = hashlib.md5(img_bytes).hexdigest() + ext
            filepath = os.path.join(cache_dir, filename)
            with open(filepath, 'wb') as f:
                f.write(img_bytes)

            # 使用相对路径，通过 dashboard 的 /local_image 访问
            file_urls.append(f'/local_image/{filename}')
            print(f"[注入] 保存图片: {filename}", flush=True)
        except Exception as e:
            print(f"[注入] 图片处理失败: {e}", flush=True)

    # 构造推文数据（模拟API格式）
    item = {
        'eventTime': int(time.time()),
        'eventType': 'newTweet',
        'contentNew': content,
        'user': {
            'handle': author,
            'username': author_name,
            'profilePic': ''
        },
        'referenceUser': {},
        'fileUrls': json.dumps(file_urls) if file_urls else '[]',
        'videoUrls': '[]',
        'referencedFiles': '[]'
    }

    with news_lock:
        news_list.append(item)
        stats['total_news'] += 1

    print(f"[注入] @{author} - {content[:50]}..." + (f" (含{len(file_urls)}张图)" if file_urls else ""), flush=True)
    return jsonify({'success': True, 'time': item['eventTime'], 'images': len(file_urls)})


@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


if __name__ == "__main__":
    port = config.get_port('news')
    print(f"推文发现服务启动: http://127.0.0.1:{port}", flush=True)

    fetcher_thread = threading.Thread(target=news_fetcher, daemon=True)
    fetcher_thread.start()

    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
