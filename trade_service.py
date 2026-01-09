"""
自动交易服务 (端口 5055)
- 接收匹配信号
- 白名单过滤
- 自动买入/卖出
- 市值监控
"""
import json
import os
import time
import threading
import uuid
import sqlite3
import requests
from flask import Flask, request, jsonify
import config

app = Flask(__name__)

# ==================== 配置 ====================
WHITELIST_AUTHORS_FILE = os.path.join(os.path.dirname(__file__), 'trade_author_whitelist.json')
WHITELIST_TOKENS_FILE = os.path.join(os.path.dirname(__file__), 'trade_token_whitelist.json')
DB_PATH = os.path.join(os.path.dirname(__file__), 'trade.db')

# 默认配置
DEFAULT_CONFIG = {
    'enabled': True,
    'default_buy_amount': 0.5,      # 默认买入 BNB
    'sell_trigger_multiple': 2.0,   # 翻倍触发卖出
    'sell_percentage': 0.5,         # 每次卖出比例
    'stop_loss_ratio': 0.5,         # 跌到买入价的50%止损
    'max_positions': 10,            # 最大持仓数
    'telegram_api_url': 'http://127.0.0.1:5060/trade',
    'monitor_interval': 1.0,        # 监控间隔（秒）
    'whitelist_mode': 'any',        # 白名单模式: 'any'=任一满足, 'author'=仅作者, 'token'=仅代币, 'both'=两者都要
    'no_change_timeout': 20,        # 无波动超时（秒），0=禁用
}

# 运行时配置
runtime_config = dict(DEFAULT_CONFIG)

# ==================== 状态 ====================
stats = {
    'running': True,
    'total_signals': 0,
    'total_buys': 0,
    'total_sells': 0,
    'errors': 0,
    'last_signal': None,
    'last_trade': None,
}

# 持仓数据: {position_id: {...}}
positions = {}
positions_lock = threading.Lock()

# 交易历史
trade_history = []
history_lock = threading.Lock()
MAX_HISTORY = 100

# 错误日志
recent_errors = []
error_lock = threading.Lock()
MAX_ERRORS = 50


def log_error(msg):
    """记录错误"""
    with error_lock:
        recent_errors.append({'time': time.time(), 'msg': msg})
        if len(recent_errors) > MAX_ERRORS:
            recent_errors.pop(0)
    stats['errors'] += 1
    print(f"[Trade] 错误: {msg}", flush=True)


# ==================== 数据库 ====================

def init_db():
    """初始化数据库"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 持仓表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS positions (
            id TEXT PRIMARY KEY,
            address TEXT NOT NULL,
            symbol TEXT,
            name TEXT,
            chain TEXT DEFAULT 'BSC',
            author TEXT,
            buy_price TEXT,
            buy_mcap REAL,
            buy_amount REAL,
            current_mcap REAL,
            current_price TEXT,
            sold_ratio REAL DEFAULT 0,
            next_sell_multiple REAL DEFAULT 2.0,
            status TEXT DEFAULT 'holding',
            trigger_type TEXT,
            created_at REAL,
            updated_at REAL,
            mcap_history TEXT DEFAULT '[]'
        )
    ''')

    # 交易历史表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trade_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time REAL,
            position_id TEXT,
            action TEXT,
            symbol TEXT,
            address TEXT,
            amount REAL,
            price TEXT,
            mcap REAL,
            response TEXT,
            reason TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 兼容旧表：添加 reason 字段
    try:
        cursor.execute('ALTER TABLE trade_history ADD COLUMN reason TEXT DEFAULT ""')
    except:
        pass

    conn.commit()
    conn.close()
    print("[Trade] 数据库初始化完成", flush=True)


def load_positions_from_db():
    """从数据库加载持仓"""
    global positions
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM positions WHERE status = "holding"')
        rows = cursor.fetchall()
        conn.close()

        for row in rows:
            pos = dict(row)
            pos['mcap_history'] = json.loads(pos.get('mcap_history', '[]'))
            positions[pos['id']] = pos

        print(f"[Trade] 从数据库加载 {len(positions)} 个持仓", flush=True)
    except Exception as e:
        print(f"[Trade] 加载持仓失败: {e}", flush=True)


def load_history_from_db():
    """从数据库加载交易历史"""
    global trade_history
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM trade_history ORDER BY time DESC LIMIT ?', (MAX_HISTORY,))
        rows = cursor.fetchall()
        conn.close()

        trade_history = [dict(row) for row in reversed(rows)]
        print(f"[Trade] 从数据库加载 {len(trade_history)} 条交易历史", flush=True)
    except Exception as e:
        print(f"[Trade] 加载交易历史失败: {e}", flush=True)


def save_position_to_db(pos):
    """保存持仓到数据库"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO positions
            (id, address, symbol, name, chain, author, buy_price, buy_mcap, buy_amount,
             current_mcap, current_price, sold_ratio, next_sell_multiple, status,
             trigger_type, created_at, updated_at, mcap_history)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            pos['id'], pos['address'], pos.get('symbol', ''), pos.get('name', ''),
            pos.get('chain', 'BSC'), pos.get('author', ''), pos.get('buy_price', ''),
            pos.get('buy_mcap', 0), pos.get('buy_amount', 0), pos.get('current_mcap', 0),
            pos.get('current_price', ''), pos.get('sold_ratio', 0),
            pos.get('next_sell_multiple', 2.0), pos.get('status', 'holding'),
            pos.get('trigger_type', ''), pos.get('created_at', 0), pos.get('updated_at', 0),
            json.dumps(pos.get('mcap_history', []))
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        log_error(f"保存持仓失败: {e}")


def save_trade_to_db(trade_record):
    """保存交易记录到数据库"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO trade_history (time, position_id, action, symbol, address, amount, price, mcap, response, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            trade_record['time'], trade_record.get('position_id', ''),
            trade_record['action'], trade_record.get('symbol', ''),
            trade_record.get('address', ''), trade_record.get('amount', 0),
            trade_record.get('price', ''), trade_record.get('mcap', 0),
            json.dumps(trade_record.get('response', {})),
            trade_record.get('reason', '')
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        log_error(f"保存交易记录失败: {e}")


def log_trade(position_id, action, token_symbol, address, amount, price, mcap, response, reason=''):
    """记录交易"""
    trade_record = {
        'time': time.time(),
        'position_id': position_id,
        'action': action,
        'symbol': token_symbol,
        'address': address,
        'amount': amount,
        'price': price,
        'mcap': mcap,
        'response': response,
        'reason': reason
    }

    with history_lock:
        trade_history.append(trade_record)
        if len(trade_history) > MAX_HISTORY:
            trade_history.pop(0)

    # 保存到数据库
    save_trade_to_db(trade_record)


# ==================== 白名单管理 ====================

def load_author_whitelist():
    """加载作者白名单"""
    if os.path.exists(WHITELIST_AUTHORS_FILE):
        try:
            with open(WHITELIST_AUTHORS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return []


def save_author_whitelist(authors):
    """保存作者白名单"""
    with open(WHITELIST_AUTHORS_FILE, 'w', encoding='utf-8') as f:
        json.dump(authors, f, ensure_ascii=False, indent=2)


def load_token_whitelist():
    """加载代币白名单"""
    if os.path.exists(WHITELIST_TOKENS_FILE):
        try:
            with open(WHITELIST_TOKENS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return []


def save_token_whitelist(tokens):
    """保存代币白名单"""
    with open(WHITELIST_TOKENS_FILE, 'w', encoding='utf-8') as f:
        json.dump(tokens, f, ensure_ascii=False, indent=2)


def is_author_whitelisted(author):
    """检查作者是否在白名单"""
    authors = load_author_whitelist()
    return author.lower() in [a.lower() for a in authors]


def is_token_whitelisted(address):
    """检查代币是否在白名单"""
    tokens = load_token_whitelist()
    addr_lower = address.lower()
    for t in tokens:
        if isinstance(t, dict):
            if t.get('address', '').lower() == addr_lower:
                return True
        elif isinstance(t, str):
            if t.lower() == addr_lower:
                return True
    return False


# ==================== 市值查询 ====================

def get_token_mcap_dex(address):
    """从 DexScreener 获取代币市值"""
    try:
        url = f"{config.DEXSCREENER_API}/{address}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            pairs = data.get('pairs', [])
            if pairs:
                pair = pairs[0]
                mcap = float(pair.get('marketCap') or pair.get('fdv') or 0)
                if mcap > 0:
                    return {
                        'market_cap': mcap,
                        'price': str(pair.get('priceUsd', '0')),
                        'source': 'dex'
                    }
    except Exception as e:
        print(f"[Trade] DexScreener 查询失败 {address[:10]}...: {e}", flush=True)
    return None


def get_token_mcap_binance(address):
    """从 Binance 搜索 API 获取代币市值（备用）"""
    try:
        url = f"{config.BINANCE_SEARCH_URL}?keyword={requests.utils.quote(address)}&chainIds={config.BINANCE_SEARCH_CHAINS}"
        resp = requests.get(
            url,
            headers=config.HEADERS,
            cookies=config.COOKIES,
            proxies=config.PROXIES,
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get('code') == '000000':
                tokens = data.get('data', []) or []
                for token in tokens:
                    if token.get('contractAddress', '').lower() == address.lower():
                        mcap = float(token.get('marketCap', 0) or 0)
                        if mcap > 0:
                            return {
                                'market_cap': mcap,
                                'price': str(token.get('price', '0')),
                                'source': 'binance'
                            }
    except Exception as e:
        print(f"[Trade] Binance 查询失败 {address[:10]}...: {e}", flush=True)
    return None


def get_token_mcap(address):
    """获取代币市值（DexScreener 优先，失败则用 Binance）"""
    # 优先使用 DexScreener
    result = get_token_mcap_dex(address)
    if result:
        return result

    # DexScreener 失败，尝试 Binance
    result = get_token_mcap_binance(address)
    if result:
        print(f"[Trade] 使用 Binance 备用接口获取 {address[:10]}... 市值", flush=True)
        return result

    log_error(f"查询市值失败 {address[:10]}...: DexScreener 和 Binance 均失败")
    return None


# ==================== 交易执行 ====================

def send_trade_command(action, address, amount, wait_reply=False):
    """发送交易指令到 Telegram"""
    try:
        url = runtime_config.get('telegram_api_url', DEFAULT_CONFIG['telegram_api_url'])
        resp = requests.post(url, json={
            'action': action,
            'address': address,
            'amount': amount,
            'wait_reply': wait_reply
        }, timeout=15)

        if resp.status_code == 200:
            result = resp.json()
            print(f"[Trade] {action.upper()} {address[:10]}... {amount} - 成功", flush=True)
            return result
        else:
            log_error(f"交易指令失败: HTTP {resp.status_code}")
            return {'success': False, 'error': f'HTTP {resp.status_code}'}
    except Exception as e:
        log_error(f"交易指令异常: {e}")
        return {'success': False, 'error': str(e)}


def execute_buy(token_data, trigger_type):
    """执行买入"""
    address = token_data.get('token_address', '')
    symbol = token_data.get('token_symbol', '')
    mcap = token_data.get('market_cap', 0)
    price = token_data.get('price', '0')
    author = token_data.get('author', '')

    # 检查持仓数量限制
    with positions_lock:
        active_count = sum(1 for p in positions.values() if p['status'] == 'holding')
        if active_count >= runtime_config.get('max_positions', 10):
            print(f"[Trade] 跳过买入 {symbol}: 持仓已满 ({active_count})", flush=True)
            return None

        # 检查是否已持有该代币 - 再次触发时加仓
        existing_pos = None
        for p in positions.values():
            if p['address'].lower() == address.lower() and p['status'] == 'holding':
                existing_pos = p
                break
        if existing_pos:
            print(f"[Trade] {symbol} 再次触发，加仓", flush=True)

    buy_amount = runtime_config.get('default_buy_amount', 0.5)

    # 发送买入指令
    result = send_trade_command('buy', address, buy_amount, wait_reply=True)

    if result.get('success'):
        position_id = f"pos_{uuid.uuid4().hex[:8]}"

        with positions_lock:
            positions[position_id] = {
                'id': position_id,
                'address': address,
                'symbol': symbol,
                'name': token_data.get('token_name', ''),
                'chain': token_data.get('chain', 'BSC'),
                'author': author,
                'buy_price': price,
                'buy_mcap': mcap,
                'buy_amount': buy_amount,
                'current_mcap': mcap,
                'current_price': price,
                'sold_ratio': 0,           # 已卖出比例
                'next_sell_multiple': runtime_config.get('sell_trigger_multiple', 2.0),
                'status': 'holding',
                'trigger_type': trigger_type,
                'created_at': time.time(),
                'updated_at': time.time(),
                'mcap_history': [{'time': 0, 'mcap': mcap}],  # 市值历史 [{time: 秒, mcap: 市值}]
            }

        stats['total_buys'] += 1
        stats['last_trade'] = time.time()
        log_trade(position_id, 'buy', symbol, address, buy_amount, price, mcap, result, reason=trigger_type)

        # 保存到数据库
        with positions_lock:
            if position_id in positions:
                save_position_to_db(positions[position_id])

        print(f"[Trade] 买入成功 {symbol} @ {mcap:.0f} mcap, 触发: {trigger_type}", flush=True)
        return position_id

    return None


def execute_sell(position, sell_ratio, reason):
    """执行卖出"""
    address = position['address']
    symbol = position['symbol']
    position_id = position['id']

    # 发送卖出指令
    result = send_trade_command('sell', address, sell_ratio, wait_reply=True)

    if result.get('success'):
        with positions_lock:
            if position_id in positions:
                pos = positions[position_id]
                # 更新已卖出比例
                remaining = 1 - pos['sold_ratio']
                sold_this_time = remaining * sell_ratio
                pos['sold_ratio'] += sold_this_time
                pos['updated_at'] = time.time()

                # 更新下次卖出倍数
                if reason == 'take_profit':
                    pos['next_sell_multiple'] *= 2

                # 检查是否全部卖出
                if pos['sold_ratio'] >= 0.99:
                    pos['status'] = 'closed'
                    print(f"[Trade] {symbol} 已全部卖出, 平仓", flush=True)
                else:
                    print(f"[Trade] {symbol} 卖出 {sell_ratio*100:.0f}%, 累计已卖 {pos['sold_ratio']*100:.0f}%", flush=True)

        stats['total_sells'] += 1
        stats['last_trade'] = time.time()
        current_mcap = position.get('current_mcap', 0)
        log_trade(position_id, 'sell', symbol, address, sell_ratio, position.get('current_price', '0'), current_mcap, result, reason=reason)

        # 保存到数据库
        with positions_lock:
            if position_id in positions:
                save_position_to_db(positions[position_id])

        return True

    return False


# ==================== 市值监控 ====================

def monitor_positions():
    """监控持仓市值，触发卖出"""
    print("[Trade] 市值监控线程启动", flush=True)
    last_db_save = 0

    while stats['running']:
        try:
            interval = runtime_config.get('monitor_interval', 1.0)
            time.sleep(interval)

            if not runtime_config.get('enabled', True):
                continue

            # 获取需要监控的持仓
            with positions_lock:
                active_positions = [p.copy() for p in positions.values() if p['status'] == 'holding']

            for pos in active_positions:
                try:
                    # 查询当前市值
                    data = get_token_mcap(pos['address'])
                    if not data:
                        continue

                    current_mcap = data['market_cap']
                    current_price = data['price']
                    buy_mcap = pos['buy_mcap']

                    no_change_timeout = runtime_config.get('no_change_timeout', 20)

                    # 更新持仓数据
                    with positions_lock:
                        if pos['id'] in positions:
                            p = positions[pos['id']]
                            p['current_mcap'] = current_mcap
                            p['current_price'] = current_price
                            p['updated_at'] = time.time()

                            # 记录市值用于方差计算（只保留 no_change_timeout 秒内的数据）
                            now = time.time()
                            recent_mcaps = p.get('recent_mcaps', [])
                            recent_mcaps.append(current_mcap)
                            # 每秒1个点，只保留最近 N 个点
                            max_points = max(no_change_timeout, 20) if no_change_timeout > 0 else 20
                            if len(recent_mcaps) > max_points:
                                recent_mcaps = recent_mcaps[-max_points:]
                            p['recent_mcaps'] = recent_mcaps

                            # 记录市值历史（每5秒记录一次，用于图表显示）
                            elapsed = int(now - p['created_at'])
                            history = p.get('mcap_history', [])
                            if not history or elapsed - history[-1]['time'] >= 5:
                                history.append({'time': elapsed, 'mcap': current_mcap})
                                if len(history) > 60:
                                    history.pop(0)
                                p['mcap_history'] = history

                    if buy_mcap <= 0:
                        continue

                    ratio = current_mcap / buy_mcap

                    # 无波动检查: 计算最近 N 秒市值的变异系数(CV)
                    should_sell_no_change = False
                    if no_change_timeout > 0:
                        with positions_lock:
                            if pos['id'] in positions:
                                recent_mcaps = positions[pos['id']].get('recent_mcaps', [])

                                # 需要足够的数据点（达到配置的秒数）
                                if len(recent_mcaps) >= no_change_timeout:
                                    # 计算变异系数 CV = std / mean
                                    mean_mcap = sum(recent_mcaps) / len(recent_mcaps)
                                    if mean_mcap > 0:
                                        variance = sum((m - mean_mcap) ** 2 for m in recent_mcaps) / len(recent_mcaps)
                                        std_dev = variance ** 0.5
                                        cv = std_dev / mean_mcap

                                        # CV < 0.1% 视为无波动
                                        if cv < 0.001:
                                            print(f"[Trade] {pos['symbol']} 触发无波动卖出 (CV={cv:.6f}, {len(recent_mcaps)}点)", flush=True)
                                            should_sell_no_change = True

                    if should_sell_no_change:
                        execute_sell(pos, 1.0, 'no_change')
                        continue

                    # 止损检查: 跌到 50% 以下全卖
                    stop_loss = runtime_config.get('stop_loss_ratio', 0.5)
                    if ratio <= stop_loss:
                        print(f"[Trade] {pos['symbol']} 触发止损 ({ratio:.2f}x)", flush=True)
                        execute_sell(pos, 1.0, 'stop_loss')
                        continue

                    # 止盈检查: 达到目标倍数卖出
                    next_multiple = pos.get('next_sell_multiple', 2.0)
                    sell_pct = runtime_config.get('sell_percentage', 0.5)

                    if ratio >= next_multiple:
                        print(f"[Trade] {pos['symbol']} 触发止盈 ({ratio:.2f}x >= {next_multiple}x)", flush=True)
                        execute_sell(pos, sell_pct, 'take_profit')

                except Exception as e:
                    log_error(f"监控 {pos.get('symbol', '?')} 异常: {e}")

            # 每30秒保存一次持仓到数据库
            now = time.time()
            if now - last_db_save >= 30:
                last_db_save = now
                with positions_lock:
                    for p in positions.values():
                        if p['status'] == 'holding':
                            save_position_to_db(p)

        except Exception as e:
            log_error(f"监控线程异常: {e}")
            time.sleep(5)


# ==================== Flask API ====================

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


@app.route('/status')
def status():
    with positions_lock:
        active_count = sum(1 for p in positions.values() if p['status'] == 'holding')

    return jsonify({
        'service': 'trade_service',
        'port': config.get_port('trade'),
        'enabled': runtime_config.get('enabled', True),
        'running': stats['running'],
        'total_signals': stats['total_signals'],
        'total_buys': stats['total_buys'],
        'total_sells': stats['total_sells'],
        'active_positions': active_count,
        'errors': stats['errors'],
        'last_signal': stats['last_signal'],
        'last_trade': stats['last_trade'],
    })


@app.route('/signal', methods=['POST'])
def receive_signal():
    """接收匹配信号"""
    if not runtime_config.get('enabled', True):
        return jsonify({'success': False, 'action': 'disabled', 'reason': '交易服务已禁用'})

    data = request.json
    if not data:
        return jsonify({'success': False, 'error': '无数据'}), 400

    stats['total_signals'] += 1
    stats['last_signal'] = time.time()

    author = data.get('author', '')
    tokens = data.get('tokens', [])

    if not tokens:
        return jsonify({'success': False, 'action': 'skip', 'reason': '无代币'})

    results = []

    for token in tokens[:3]:  # 最多处理前3个代币
        address = token.get('token_address', '')
        symbol = token.get('token_symbol', '')

        if not address:
            continue

        # 检查白名单
        trigger_type = None
        whitelist_mode = runtime_config.get('whitelist_mode', 'any')
        author_in_wl = is_author_whitelisted(author)
        token_in_wl = is_token_whitelisted(address)

        if whitelist_mode == 'author':
            # 仅检查作者白名单
            if author_in_wl:
                trigger_type = 'author_whitelist'
        elif whitelist_mode == 'token':
            # 仅检查代币白名单
            if token_in_wl:
                trigger_type = 'token_whitelist'
        elif whitelist_mode == 'both':
            # 两者都要满足
            if author_in_wl and token_in_wl:
                trigger_type = 'both_whitelist'
        else:  # 'any' - 任一满足
            if author_in_wl:
                trigger_type = 'author_whitelist'
            elif token_in_wl:
                trigger_type = 'token_whitelist'

        if not trigger_type:
            mode_desc = {'any': '任一白名单', 'author': '作者白名单', 'token': '代币白名单', 'both': '作者+代币白名单'}
            results.append({
                'symbol': symbol,
                'action': 'skip',
                'reason': f'不满足{mode_desc.get(whitelist_mode, "白名单")}条件'
            })
            continue

        # 准备代币数据
        token_data = {
            'token_address': address,
            'token_symbol': symbol,
            'token_name': token.get('token_name', ''),
            'chain': token.get('chain', 'BSC'),
            'market_cap': float(token.get('market_cap', 0) or token.get('marketCap', 0) or 0),
            'price': token.get('price', '0'),
            'author': author,
        }

        # 执行买入
        position_id = execute_buy(token_data, trigger_type)

        if position_id:
            results.append({
                'symbol': symbol,
                'action': 'buy',
                'position_id': position_id,
                'trigger': trigger_type
            })
        else:
            results.append({
                'symbol': symbol,
                'action': 'skip',
                'reason': '买入失败或已持有'
            })

    return jsonify({'success': True, 'results': results})


@app.route('/positions', methods=['GET'])
def get_positions():
    """获取当前持仓"""
    with positions_lock:
        pos_list = []
        for p in positions.values():
            buy_mcap = p.get('buy_mcap', 0)
            current_mcap = p.get('current_mcap', 0)
            change_pct = ((current_mcap - buy_mcap) / buy_mcap * 100) if buy_mcap > 0 else 0

            pos_list.append({
                'id': p['id'],
                'symbol': p['symbol'],
                'name': p.get('name', ''),
                'address': p['address'],
                'chain': p.get('chain', 'BSC'),
                'author': p.get('author', ''),
                'buy_mcap': buy_mcap,
                'current_mcap': current_mcap,
                'change_pct': round(change_pct, 2),
                'buy_amount': p.get('buy_amount', 0),
                'sold_ratio': p.get('sold_ratio', 0),
                'next_sell_multiple': p.get('next_sell_multiple', 2.0),
                'status': p['status'],
                'trigger_type': p.get('trigger_type', ''),
                'created_at': p.get('created_at', 0),
                'updated_at': p.get('updated_at', 0),
                'mcap_history': p.get('mcap_history', []),
            })

        # 按创建时间倒序
        pos_list.sort(key=lambda x: x['created_at'], reverse=True)

    return jsonify({'positions': pos_list})


@app.route('/positions/<position_id>', methods=['DELETE'])
def close_position(position_id):
    """手动平仓"""
    with positions_lock:
        if position_id not in positions:
            return jsonify({'success': False, 'error': '持仓不存在'}), 404

        pos = positions[position_id]
        if pos['status'] != 'holding':
            return jsonify({'success': False, 'error': '持仓已关闭'})

    # 全部卖出
    result = execute_sell(pos, 1.0, 'manual_close')

    if result:
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': '卖出失败'}), 500


@app.route('/history', methods=['GET'])
def get_history():
    """获取交易历史"""
    limit = request.args.get('limit', 50, type=int)
    with history_lock:
        return jsonify({'history': list(reversed(trade_history))[:limit]})


@app.route('/config', methods=['GET', 'POST'])
def handle_config():
    """获取/更新配置"""
    if request.method == 'GET':
        return jsonify(runtime_config)

    data = request.json
    if not data:
        return jsonify({'success': False, 'error': '无数据'}), 400

    # 更新配置
    for key in ['enabled', 'default_buy_amount', 'sell_trigger_multiple',
                'sell_percentage', 'stop_loss_ratio', 'max_positions',
                'telegram_api_url', 'monitor_interval', 'whitelist_mode',
                'no_change_timeout']:
        if key in data:
            runtime_config[key] = data[key]

    print(f"[Trade] 配置已更新: {data}", flush=True)
    return jsonify({'success': True, 'config': runtime_config})


# ==================== 白名单 API ====================

@app.route('/whitelist/authors', methods=['GET'])
def get_author_whitelist():
    return jsonify({'authors': load_author_whitelist()})


@app.route('/whitelist/authors', methods=['POST'])
def add_author_whitelist():
    data = request.json
    author = data.get('author', '').strip()
    if not author:
        return jsonify({'success': False, 'error': '作者名不能为空'}), 400

    authors = load_author_whitelist()
    if author.lower() not in [a.lower() for a in authors]:
        authors.append(author)
        save_author_whitelist(authors)
        print(f"[Trade] 添加作者白名单: {author}", flush=True)

    return jsonify({'success': True, 'authors': authors})


@app.route('/whitelist/authors', methods=['DELETE'])
def remove_author_whitelist():
    data = request.json
    author = data.get('author', '').strip()
    if not author:
        return jsonify({'success': False, 'error': '作者名不能为空'}), 400

    authors = load_author_whitelist()
    authors_lower = [a.lower() for a in authors]
    if author.lower() in authors_lower:
        idx = authors_lower.index(author.lower())
        authors.pop(idx)
        save_author_whitelist(authors)
        print(f"[Trade] 移除作者白名单: {author}", flush=True)

    return jsonify({'success': True, 'authors': authors})


@app.route('/whitelist/tokens', methods=['GET'])
def get_token_whitelist():
    return jsonify({'tokens': load_token_whitelist()})


@app.route('/whitelist/tokens', methods=['POST'])
def add_token_whitelist():
    data = request.json
    address = data.get('address', '').strip()
    symbol = data.get('symbol', '').strip()
    note = data.get('note', '').strip()

    if not address:
        return jsonify({'success': False, 'error': '地址不能为空'}), 400

    tokens = load_token_whitelist()
    # 检查是否已存在
    exists = any(
        (t.get('address', '').lower() if isinstance(t, dict) else t.lower()) == address.lower()
        for t in tokens
    )

    if not exists:
        tokens.append({'address': address, 'symbol': symbol, 'note': note})
        save_token_whitelist(tokens)
        print(f"[Trade] 添加代币白名单: {symbol or address[:10]}", flush=True)

    return jsonify({'success': True, 'tokens': tokens})


@app.route('/whitelist/tokens', methods=['DELETE'])
def remove_token_whitelist():
    data = request.json
    address = data.get('address', '').strip()

    if not address:
        return jsonify({'success': False, 'error': '地址不能为空'}), 400

    tokens = load_token_whitelist()
    new_tokens = [
        t for t in tokens
        if (t.get('address', '').lower() if isinstance(t, dict) else t.lower()) != address.lower()
    ]

    if len(new_tokens) < len(tokens):
        save_token_whitelist(new_tokens)
        print(f"[Trade] 移除代币白名单: {address[:10]}", flush=True)

    return jsonify({'success': True, 'tokens': new_tokens})


@app.route('/recent')
def recent():
    """返回最近数据"""
    with positions_lock:
        active_positions = [p.copy() for p in positions.values() if p['status'] == 'holding']

    with history_lock:
        recent_trades = list(reversed(trade_history))[:20]

    with error_lock:
        errors = list(reversed(recent_errors))[:10]

    return jsonify({
        'positions': active_positions,
        'trades': recent_trades,
        'errors': errors
    })


# ==================== 启动 ====================

def run():
    """启动服务"""
    port = config.get_port('trade')
    print(f"[Trade] 自动交易服务启动: http://127.0.0.1:{port}", flush=True)

    # 初始化数据库
    init_db()

    # 从数据库加载数据
    load_positions_from_db()
    load_history_from_db()

    # 启动市值监控线程
    monitor_thread = threading.Thread(target=monitor_positions, daemon=True)
    monitor_thread.start()

    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)


if __name__ == '__main__':
    run()
