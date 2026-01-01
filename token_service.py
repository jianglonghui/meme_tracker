"""
代币发现服务 (端口 5051)
- 监听 Binance 新币
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
    'total_tokens': 0,
    'running': True,
    'last_fetch': None,
    'last_success': None,
    'errors': 0
}

# 去重和代币列表
seen_ids = set()
token_list = []
token_lock = threading.Lock()

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
    "chainId": "56",
    "rankType": 10,
    "protocol": [2001],
    "holdersMin": 10
}


def fetch_tokens():
    try:
        response = requests.post(
            config.BINANCE_TOKEN_URL,
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
    if isinstance(items, dict):
        items = items.get('list', []) or items.get('tokens', []) or []

    for item in items:
        token_id = item.get('contractAddress', '') or item.get('tokenAddress', '')
        if token_id and token_id not in seen_ids:
            seen_ids.add(token_id)
            new_items.append(item)

    return new_items


def token_fetcher():
    print("开始获取新币...", flush=True)
    while stats['running']:
        data = fetch_tokens()
        stats['last_fetch'] = time.time()
        new_items = get_new_items(data)
        if new_items:
            with token_lock:
                for item in new_items:
                    token_list.append(item)
                    stats['total_tokens'] += 1
                    symbol = item.get('symbol', '') or item.get('tokenSymbol', 'Unknown')
                    print(f"[新币] {symbol}", flush=True)
        time.sleep(1)


@app.route('/stream')
def stream():
    def generate():
        last_idx = 0
        heartbeat_count = 0
        while True:
            with token_lock:
                if len(token_list) > last_idx:
                    for item in token_list[last_idx:]:
                        token_data = {
                            'tokenAddress': item.get('contractAddress', '') or item.get('tokenAddress', ''),
                            'tokenSymbol': item.get('symbol', '') or item.get('tokenSymbol', ''),
                            'tokenName': item.get('name', '') or item.get('tokenName', ''),
                            'chain': 'BSC',
                            'price': item.get('price', ''),
                            'marketCap': item.get('marketCap', ''),
                            'holders': item.get('holders', ''),
                            'liquidity': item.get('liquidity', ''),
                            'createTime': item.get('createTime', ''),
                        }
                        yield f"data: {json.dumps(token_data, ensure_ascii=False)}\n\n"
                    last_idx = len(token_list)
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
        'service': 'token_service',
        'port': config.TOKEN_PORT,
        'running': stats['running'],
        'total_tokens': stats['total_tokens'],
        'last_fetch': stats['last_fetch'],
        'last_success': stats['last_success'],
        'errors': stats['errors']
    })


@app.route('/recent')
def recent():
    """返回最近的代币和错误"""
    with token_lock:
        recent_items = token_list[-10:][::-1]
    with error_lock:
        recent_errors = list(error_log)[::-1]

    items = []
    for item in recent_items:
        items.append({
            'symbol': item.get('symbol') or item.get('tokenSymbol', 'Unknown'),
            'name': item.get('name') or item.get('tokenName', ''),
            'marketCap': item.get('marketCap', 0),
            'holders': item.get('holders', 0),
            'time': item.get('createTime', 0)
        })
    return jsonify({'items': items, 'errors': recent_errors})


@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


if __name__ == "__main__":
    port = config.get_port('token')
    print(f"代币发现服务启动: http://127.0.0.1:{port}", flush=True)

    fetcher_thread = threading.Thread(target=token_fetcher, daemon=True)
    fetcher_thread.start()

    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
