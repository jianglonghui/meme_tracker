"""
推文发现服务 (端口 5050)
- 监听 Binance 推文事件
- 提供 SSE 流
- 支持作者白名单过滤
"""
import requests
import time
import json
import threading
import os
from flask import Flask, Response, jsonify, request
import config

app = Flask(__name__)

# 状态统计
stats = {
    'total_news': 0,
    'running': True,
    'last_fetch': None,
    'last_success': None,
    'errors': 0,
    'filtered_by_whitelist': 0
}

# 去重和新闻列表
seen_ids = set()
news_list = []
news_lock = threading.Lock()

# 错误日志
error_log = []
error_lock = threading.Lock()
MAX_ERRORS = 20

# ==================== 作者白名单 ====================
WHITELIST_FILE = os.path.join(os.path.dirname(__file__), 'author_whitelist.json')
author_whitelist = set()
whitelist_lock = threading.Lock()
enable_whitelist = False  # 是否启用白名单过滤


def load_whitelist():
    """加载白名单"""
    global author_whitelist
    try:
        if os.path.exists(WHITELIST_FILE):
            with open(WHITELIST_FILE, 'r') as f:
                data = json.load(f)
                author_whitelist = set(a.lower() for a in data)
                print(f"[白名单] 加载 {len(author_whitelist)} 个作者", flush=True)
    except Exception as e:
        print(f"[白名单] 加载失败: {e}", flush=True)


def save_whitelist():
    """保存白名单"""
    try:
        with open(WHITELIST_FILE, 'w') as f:
            json.dump(list(author_whitelist), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[白名单] 保存失败: {e}", flush=True)


def is_author_allowed(author):
    """检查作者是否在白名单中（白名单关闭时允许所有）"""
    if not enable_whitelist:
        return True
    with whitelist_lock:
        return author.lower() in author_whitelist


def is_author_in_whitelist(author):
    """检查作者是否在白名单中（不考虑开关状态）"""
    with whitelist_lock:
        return author.lower() in author_whitelist


def trigger_token_boost(author):
    """触发 token_service 的高频模式"""
    try:
        resp = requests.post(
            f"{config.get_service_url('token')}/boost",
            json={'author': author},
            timeout=2,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            result = resp.json()
            print(f"[智能调频] 已触发高频模式 (作者: @{author})", flush=True)
            return True
    except Exception as e:
        print(f"[智能调频] 触发失败: {e}", flush=True)
    return False


# 启动时加载白名单
load_whitelist()


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
                    user = item.get('user', {})
                    author = user.get('handle', 'Unknown')

                    # 白名单过滤
                    if not is_author_allowed(author):
                        stats['filtered_by_whitelist'] += 1
                        continue

                    news_list.append(item)
                    stats['total_news'] += 1
                    print(f"[推文] @{author} - {item.get('eventType', '')}", flush=True)

                    # 智能调频：检测到白名单作者推文时触发高频模式
                    if is_author_in_whitelist(author):
                        trigger_token_boost(author)
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
    with whitelist_lock:
        whitelist_count = len(author_whitelist)
    return jsonify({
        'service': 'news_service',
        'port': config.NEWS_PORT,
        'running': stats['running'],
        'total_news': stats['total_news'],
        'last_fetch': stats['last_fetch'],
        'last_success': stats['last_success'],
        'errors': stats['errors'],
        'enable_whitelist': enable_whitelist,
        'whitelist_count': whitelist_count,
        'filtered_by_whitelist': stats['filtered_by_whitelist']
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
            'id': f"{item.get('eventTime', '')}_{user.get('handle', '')}_{event_type}",
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


# ==================== 白名单 API ====================

@app.route('/whitelist', methods=['GET'])
def get_whitelist():
    """获取白名单列表和状态"""
    with whitelist_lock:
        return jsonify({
            'enabled': enable_whitelist,
            'authors': sorted(list(author_whitelist)),
            'count': len(author_whitelist)
        })


@app.route('/whitelist/toggle', methods=['POST'])
def toggle_whitelist():
    """切换白名单开关"""
    global enable_whitelist
    data = request.json or {}
    if 'enabled' in data:
        enable_whitelist = bool(data['enabled'])
    else:
        enable_whitelist = not enable_whitelist
    status_str = "开启" if enable_whitelist else "关闭"
    print(f"[白名单] {status_str}", flush=True)
    return jsonify({'enabled': enable_whitelist})


@app.route('/whitelist/add', methods=['POST'])
def add_to_whitelist():
    """添加作者到白名单"""
    data = request.json or {}
    author = data.get('author', '').strip()
    if not author:
        return jsonify({'success': False, 'error': '作者名不能为空'}), 400

    with whitelist_lock:
        author_lower = author.lower()
        if author_lower in author_whitelist:
            return jsonify({'success': False, 'error': '作者已在白名单中'}), 400
        author_whitelist.add(author_lower)
        save_whitelist()

    print(f"[白名单] 添加: @{author}", flush=True)
    return jsonify({'success': True, 'author': author})


@app.route('/whitelist/remove', methods=['POST'])
def remove_from_whitelist():
    """从白名单移除作者"""
    data = request.json or {}
    author = data.get('author', '').strip()
    if not author:
        return jsonify({'success': False, 'error': '作者名不能为空'}), 400

    with whitelist_lock:
        author_lower = author.lower()
        if author_lower not in author_whitelist:
            return jsonify({'success': False, 'error': '作者不在白名单中'}), 400
        author_whitelist.discard(author_lower)
        save_whitelist()

    print(f"[白名单] 移除: @{author}", flush=True)
    return jsonify({'success': True, 'author': author})


@app.route('/whitelist/batch', methods=['POST'])
def batch_add_whitelist():
    """批量添加作者到白名单"""
    data = request.json or {}
    authors = data.get('authors', [])
    if not authors:
        return jsonify({'success': False, 'error': '作者列表为空'}), 400

    added = []
    with whitelist_lock:
        for author in authors:
            author = author.strip()
            if author:
                author_lower = author.lower()
                if author_lower not in author_whitelist:
                    author_whitelist.add(author_lower)
                    added.append(author)
        save_whitelist()

    print(f"[白名单] 批量添加: {len(added)} 个作者", flush=True)
    return jsonify({'success': True, 'added': added, 'count': len(added)})


@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


if __name__ == "__main__":
    port = config.get_port('news')
    print(f"推文发现服务启动: http://127.0.0.1:{port}", flush=True)

    fetcher_thread = threading.Thread(target=news_fetcher, daemon=True)
    fetcher_thread.start()

    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
