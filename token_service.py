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

# 代币字典 (key: tokenAddress, value: token data)
token_dict = {}
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


def process_tokens(data):
    """处理代币数据，返回 (新代币列表, 更新数量)"""
    new_items = []
    updated_count = 0

    if not data or 'data' not in data:
        return new_items, updated_count

    items = data.get('data', [])
    if isinstance(items, dict):
        items = items.get('list', []) or items.get('tokens', []) or []

    with token_lock:
        for item in items:
            token_id = item.get('contractAddress', '') or item.get('tokenAddress', '')
            if not token_id:
                continue

            if token_id in token_dict:
                # 更新已有代币数据
                token_dict[token_id].update(item)
                updated_count += 1
            else:
                # 新代币
                token_dict[token_id] = item
                new_items.append(item)

    return new_items, updated_count


def token_fetcher():
    print("开始获取新币...", flush=True)
    while stats['running']:
        data = fetch_tokens()
        stats['last_fetch'] = time.time()
        new_items, updated_count = process_tokens(data)
        if new_items:
            stats['total_tokens'] += len(new_items)
            for item in new_items:
                symbol = item.get('symbol', '') or item.get('tokenSymbol', 'Unknown')
                print(f"[新币] {symbol}", flush=True)
        time.sleep(1)


@app.route('/stream')
def stream():
    def generate():
        sent_ids = set()
        heartbeat_count = 0
        while True:
            with token_lock:
                for token_id, item in token_dict.items():
                    if token_id not in sent_ids:
                        sent_ids.add(token_id)
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
        # 按创建时间排序，取最新10个
        sorted_tokens = sorted(token_dict.values(), key=lambda x: x.get('createTime', 0), reverse=True)
        recent_items = sorted_tokens[:10]
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
