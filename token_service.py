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

# BSC 链配置 (Binance API)
BSC_CHAIN = {"chainId": "56", "name": "BSC", "protocol": [2001]}

# DexScreener API
DEXSCREENER_LATEST_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens"


def fetch_solana_tokens():
    """从 DexScreener 获取 Solana 新代币"""
    try:
        response = requests.get(
            DEXSCREENER_LATEST_URL,
            headers={'accept': 'application/json', 'User-Agent': 'Mozilla/5.0'},
            proxies=config.PROXIES,
            timeout=10
        )
        if response.status_code != 200:
            log_error(f"DexScreener HTTP {response.status_code}")
            return []

        all_tokens = response.json()
        solana_tokens = [t for t in all_tokens if t.get('chainId') == 'solana']

        # 获取每个代币的详细信息
        detailed_tokens = []
        for token in solana_tokens[:20]:  # 限制数量避免请求过多
            token_address = token.get('tokenAddress')
            if not token_address:
                continue

            try:
                detail_resp = requests.get(
                    f"{DEXSCREENER_TOKEN_URL}/{token_address}",
                    headers={'accept': 'application/json', 'User-Agent': 'Mozilla/5.0'},
                    proxies=config.PROXIES,
                    timeout=5
                )
                if detail_resp.status_code == 200:
                    detail_data = detail_resp.json()
                    pairs = detail_data.get('pairs', [])
                    if pairs:
                        pair = pairs[0]
                        detailed_tokens.append({
                            'contractAddress': token_address,
                            'symbol': pair.get('baseToken', {}).get('symbol', ''),
                            'name': pair.get('baseToken', {}).get('name', ''),
                            'chain': 'SOL',
                            'price': pair.get('priceUsd', ''),
                            'marketCap': pair.get('marketCap', 0),
                            'liquidity': pair.get('liquidity', {}).get('usd', 0),
                            'volume': pair.get('volume', {}).get('h24', 0),
                            'createTime': pair.get('pairCreatedAt', 0),
                            'holders': 0,  # DexScreener 不提供 holders
                        })
            except Exception as e:
                pass  # 单个代币获取失败不影响整体

        return detailed_tokens
    except Exception as e:
        log_error(f"DexScreener 请求: {e}")
        print(f"DexScreener 请求异常: {e}", flush=True)
        return []


def fetch_tokens_for_chain(chain):
    """获取指定链的代币"""
    payload = {
        "chainId": chain["chainId"],
        "rankType": 10,
        "protocol": chain["protocol"],
        "holdersMin": 10
    }
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
                log_error(f"API [{chain['name']}]: {data.get('message')}")
                return None
            # 给每个代币添加链标识
            items = data.get('data', [])
            if isinstance(items, dict):
                items = items.get('list', []) or items.get('tokens', []) or []
            for item in items:
                item['chain'] = chain['name']
            return data
        else:
            log_error(f"HTTP [{chain['name']}] {response.status_code}")
    except Exception as e:
        log_error(f"请求 [{chain['name']}]: {e}")
        print(f"请求异常 [{chain['name']}]: {e}", flush=True)
    return None


def fetch_tokens():
    """获取所有链的代币"""
    all_success = True

    # 1. 获取 BSC 代币 (Binance API)
    bsc_data = fetch_tokens_for_chain(BSC_CHAIN)
    if bsc_data:
        new_items, _ = process_tokens(bsc_data, 'BSC')
        if new_items:
            stats['total_tokens'] += len(new_items)
            for item in new_items:
                symbol = item.get('symbol', '') or item.get('tokenSymbol', 'Unknown')
                print(f"[新币] [BSC] {symbol}", flush=True)
    else:
        all_success = False

    # 2. 获取 Solana 代币 (DexScreener API)
    sol_tokens = fetch_solana_tokens()
    if sol_tokens:
        new_items, _ = process_solana_tokens(sol_tokens)
        if new_items:
            stats['total_tokens'] += len(new_items)
            for item in new_items:
                symbol = item.get('symbol', 'Unknown')
                print(f"[新币] [SOL] {symbol}", flush=True)

    if all_success:
        stats['last_success'] = time.time()
    return all_success


def process_solana_tokens(tokens):
    """处理 Solana 代币数据，返回 (新代币列表, 更新数量)"""
    new_items = []
    updated_count = 0

    with token_lock:
        for item in tokens:
            token_id = item.get('contractAddress', '')
            if not token_id:
                continue

            unique_id = f"SOL:{token_id}"

            if unique_id in token_dict:
                token_dict[unique_id].update(item)
                updated_count += 1
            else:
                token_dict[unique_id] = item
                new_items.append(item)

    return new_items, updated_count


def process_tokens(data, chain_name='BSC'):
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

            # 用 chain:address 作为唯一标识
            unique_id = f"{chain_name}:{token_id}"
            item['chain'] = chain_name

            if unique_id in token_dict:
                # 更新已有代币数据
                token_dict[unique_id].update(item)
                updated_count += 1
            else:
                # 新代币
                token_dict[unique_id] = item
                new_items.append(item)

    return new_items, updated_count


def token_fetcher():
    print("开始获取新币...", flush=True)
    while stats['running']:
        fetch_tokens()
        stats['last_fetch'] = time.time()
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
                            'chain': item.get('chain', 'BSC'),
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
            'chain': item.get('chain', 'BSC'),
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
