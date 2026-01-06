"""
Alpha Group Call 服务 (端口 5054)
- 接收 Telegram 群聊中的合约信息
- 统计接收次数、时间、来源群聊
- 通过搜索 API 获取代币信息和市值
- 持久化记录
"""
import sqlite3
import time
import json
import requests
from flask import Flask, jsonify, request
import config

app = Flask(__name__)

# 数据库路径
DB_PATH = config.DB_PATH

# 状态统计
stats = {
    'total_calls': 0,
    'total_contracts': 0,
    'running': True,
    'last_call': None
}


def init_db():
    """初始化数据库表"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Alpha Call 记录表 (每次调用的详细记录)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alpha_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_address TEXT NOT NULL,
            symbol TEXT,
            name TEXT,
            chain TEXT DEFAULT 'Unknown',
            group_id TEXT NOT NULL,
            group_name TEXT,
            call_time INTEGER NOT NULL,
            market_cap REAL DEFAULT 0,
            extra_info TEXT
        )
    ''')

    # 合约统计表 (汇总)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alpha_contract_stats (
            contract_address TEXT PRIMARY KEY,
            symbol TEXT,
            name TEXT,
            chain TEXT DEFAULT 'Unknown',
            call_count INTEGER DEFAULT 1,
            first_call_time INTEGER,
            last_call_time INTEGER,
            group_ids TEXT,
            last_market_cap REAL DEFAULT 0
        )
    ''')

    # 尝试添加新字段 (兼容旧表)
    try:
        cursor.execute('ALTER TABLE alpha_calls ADD COLUMN market_cap REAL DEFAULT 0')
    except:
        pass
    try:
        cursor.execute('ALTER TABLE alpha_contract_stats ADD COLUMN last_market_cap REAL DEFAULT 0')
    except:
        pass

    conn.commit()
    conn.close()
    print("[Alpha Call] 数据库初始化完成", flush=True)


def fetch_token_info(contract_address):
    """通过搜索 API 获取代币信息和市值"""
    try:
        url = f"{config.BINANCE_SEARCH_URL}?keyword={requests.utils.quote(contract_address)}&chainIds={config.BINANCE_SEARCH_CHAINS}"
        resp = requests.get(
            url,
            headers=config.HEADERS,
            cookies=config.COOKIES,
            proxies=config.PROXIES,
            timeout=10
        )
        if resp.status_code != 200:
            return None

        data = resp.json()
        if data.get('code') != '000000':
            return None

        tokens = data.get('data', []) or []
        # 查找精确匹配的合约地址
        for token in tokens:
            if token.get('contractAddress', '').lower() == contract_address.lower():
                chain_id = token.get('chainId', '')
                chain_name = 'SOL' if chain_id == 'CT_501' else 'BSC' if chain_id == '56' else 'BASE' if chain_id == '8453' else chain_id
                return {
                    'symbol': token.get('symbol', ''),
                    'name': token.get('name', ''),
                    'chain': chain_name,
                    'market_cap': float(token.get('marketCap', 0) or 0),
                    'price': token.get('price', 0),
                    'liquidity': float(token.get('liquidity', 0) or 0)
                }
        return None
    except Exception as e:
        print(f"[Alpha Call] 获取代币信息失败: {e}", flush=True)
        return None


def record_call(contract_address, symbol, name, chain, group_id, group_name, extra_info=None):
    """记录一次 Alpha Call"""
    call_time = int(time.time())
    market_cap = 0

    # 获取代币信息和市值
    token_info = fetch_token_info(contract_address)
    if token_info:
        symbol = token_info.get('symbol') or symbol
        name = token_info.get('name') or name
        chain = token_info.get('chain') or chain
        market_cap = token_info.get('market_cap', 0)
        print(f"[Alpha Call] 获取到代币信息: {symbol} ${market_cap/1000:.1f}k", flush=True)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 插入详细记录 (包含市值)
    cursor.execute('''
        INSERT INTO alpha_calls (contract_address, symbol, name, chain, group_id, group_name, call_time, market_cap, extra_info)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (contract_address.lower(), symbol, name, chain, group_id, group_name, call_time, market_cap, json.dumps(extra_info) if extra_info else None))

    # 更新统计表
    cursor.execute('SELECT * FROM alpha_contract_stats WHERE contract_address = ?', (contract_address.lower(),))
    existing = cursor.fetchone()

    if existing:
        # 更新现有记录
        # 字段顺序: contract_address(0), symbol(1), name(2), chain(3), call_count(4), first_call_time(5), last_call_time(6), group_ids(7), last_market_cap(8)
        old_group_ids = json.loads(existing[7]) if existing[7] else []
        if group_id not in old_group_ids:
            old_group_ids.append(group_id)
        cursor.execute('''
            UPDATE alpha_contract_stats
            SET call_count = call_count + 1,
                last_call_time = ?,
                group_ids = ?,
                symbol = COALESCE(?, symbol),
                name = COALESCE(?, name),
                chain = COALESCE(?, chain),
                last_market_cap = ?
            WHERE contract_address = ?
        ''', (call_time, json.dumps(old_group_ids), symbol, name, chain, market_cap, contract_address.lower()))
    else:
        # 插入新记录
        cursor.execute('''
            INSERT INTO alpha_contract_stats (contract_address, symbol, name, chain, call_count, first_call_time, last_call_time, group_ids, last_market_cap)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
        ''', (contract_address.lower(), symbol, name, chain, call_time, call_time, json.dumps([group_id]), market_cap))
        stats['total_contracts'] += 1

    conn.commit()
    conn.close()

    stats['total_calls'] += 1
    stats['last_call'] = call_time

    mcap_str = f" ${market_cap/1000:.0f}k" if market_cap > 0 else ""
    print(f"[Alpha Call] {symbol or contract_address[:10]}{mcap_str} from {group_name or group_id} (总计: {stats['total_calls']})", flush=True)


@app.route('/call', methods=['POST'])
def api_call():
    """接收 Alpha Call (供 Telegram_Forwarder 调用)"""
    data = request.get_json()

    contract_address = data.get('contract_address') or data.get('ca') or data.get('address')
    if not contract_address:
        return jsonify({'success': False, 'error': '合约地址不能为空'}), 400

    symbol = data.get('symbol', '')
    name = data.get('name', '')
    chain = data.get('chain', 'Unknown')
    group_id = str(data.get('group_id', '') or data.get('chat_id', '') or 'unknown')
    group_name = data.get('group_name', '') or data.get('chat_name', '')
    extra_info = data.get('extra', None)

    record_call(contract_address, symbol, name, chain, group_id, group_name, extra_info)

    return jsonify({
        'success': True,
        'contract': contract_address,
        'total_calls': stats['total_calls']
    })


@app.route('/recent')
def api_recent():
    """获取最近的 Alpha Call 统计，每个合约包含调用历史"""
    limit = request.args.get('limit', 50, type=int)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 获取统计数据 (按 last_call_time 排序)
    cursor.execute('''
        SELECT * FROM alpha_contract_stats
        ORDER BY last_call_time DESC
        LIMIT ?
    ''', (limit,))
    stats_rows = cursor.fetchall()

    # 获取所有合约地址
    contract_addresses = [row['contract_address'] for row in stats_rows]

    # 获取这些合约的所有调用记录
    calls_by_contract = {}
    if contract_addresses:
        placeholders = ','.join(['?' for _ in contract_addresses])
        cursor.execute(f'''
            SELECT * FROM alpha_calls
            WHERE contract_address IN ({placeholders})
            ORDER BY call_time DESC
        ''', contract_addresses)
        for row in cursor.fetchall():
            addr = row['contract_address']
            if addr not in calls_by_contract:
                calls_by_contract[addr] = []
            # 获取市值 (兼容旧表)
            try:
                mcap = row['market_cap'] or 0
            except:
                mcap = 0
            calls_by_contract[addr].append({
                'time': row['call_time'],
                'market_cap': mcap,
                'group_id': row['group_id'],
                'group_name': row['group_name'] or ''
            })

    conn.close()

    # 格式化统计数据，包含调用历史
    contract_stats = []
    for row in stats_rows:
        addr = row['contract_address']
        # 获取市值 (兼容旧表)
        try:
            last_mcap = row['last_market_cap'] or 0
        except:
            last_mcap = 0
        contract_stats.append({
            'address': addr,
            'symbol': row['symbol'] or '',
            'name': row['name'] or '',
            'chain': row['chain'] or 'Unknown',
            'count': row['call_count'],
            'first_time': row['first_call_time'],
            'last_time': row['last_call_time'],
            'market_cap': last_mcap,
            'calls': calls_by_contract.get(addr, [])  # 每次调用的详情
        })

    return jsonify({
        'stats': contract_stats
    })


@app.route('/status')
def api_status():
    """服务状态"""
    return jsonify({
        'service': 'alpha_call_service',
        'port': config.get_port('alpha_call'),
        'running': stats['running'],
        'total_calls': stats['total_calls'],
        'total_contracts': stats['total_contracts'],
        'last_call': stats['last_call']
    })


@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


@app.route('/clear', methods=['POST'])
def api_clear():
    """清空记录 (慎用)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM alpha_calls')
    cursor.execute('DELETE FROM alpha_contract_stats')
    conn.commit()
    conn.close()

    stats['total_calls'] = 0
    stats['total_contracts'] = 0
    stats['last_call'] = None

    return jsonify({'success': True, 'message': '已清空所有记录'})


if __name__ == "__main__":
    init_db()

    # 加载已有统计
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM alpha_calls')
        stats['total_calls'] = cursor.fetchone()[0]
        cursor.execute('SELECT COUNT(*) FROM alpha_contract_stats')
        stats['total_contracts'] = cursor.fetchone()[0]
        conn.close()
    except:
        pass

    port = config.get_port('alpha_call')
    print(f"Alpha Call 服务启动: http://127.0.0.1:{port}", flush=True)
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
