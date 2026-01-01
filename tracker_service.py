"""
代币跟踪服务 (端口 5052)
- 保存匹配记录
- 追踪 1/5/10 分钟市值变化
- 评分并记录最佳代币
"""
import sqlite3
import threading
import time
import json
import requests
from flask import Flask, request, jsonify
import config

app = Flask(__name__)

# 状态统计
stats = {
    'total_matches': 0,
    'total_tracked': 0,
    'running': True,
    'last_track': None,
    'errors': 0
}

# 追踪任务队列
tracking_tasks = []
tracking_lock = threading.Lock()


def init_db():
    """初始化数据库"""
    conn = sqlite3.connect(config.DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS match_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_time INTEGER, news_author TEXT, news_author_name TEXT,
            news_avatar TEXT, news_type TEXT, news_content TEXT,
            news_images TEXT, news_videos TEXT,
            ref_author TEXT, ref_author_name TEXT, ref_avatar TEXT,
            ref_content TEXT, ref_images TEXT,
            keywords TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 兼容旧表：添加新字段
    new_columns = [
        ('news_author_name', 'TEXT'), ('news_avatar', 'TEXT'),
        ('news_images', 'TEXT'), ('news_videos', 'TEXT'),
        ('ref_author', 'TEXT'), ('ref_author_name', 'TEXT'),
        ('ref_avatar', 'TEXT'), ('ref_content', 'TEXT'), ('ref_images', 'TEXT')
    ]
    for col, col_type in new_columns:
        try:
            cursor.execute(f'ALTER TABLE match_records ADD COLUMN {col} {col_type}')
        except:
            pass

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS matched_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER, rank INTEGER,
            token_address TEXT, token_symbol TEXT, token_name TEXT, chain TEXT,
            initial_price TEXT, initial_market_cap REAL, initial_holders INTEGER,
            match_score REAL, match_keyword TEXT, match_type TEXT, final_score REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (match_id) REFERENCES match_records(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS market_cap_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            matched_token_id INTEGER, time_offset INTEGER,
            market_cap REAL, market_cap_change_pct REAL, price TEXT,
            tracked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (matched_token_id) REFERENCES matched_tokens(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS top_performers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER, performance_rank INTEGER,
            token_address TEXT, token_symbol TEXT, token_name TEXT, chain TEXT,
            initial_market_cap REAL, final_market_cap REAL,
            market_cap_change_pct REAL, performance_score REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (match_id) REFERENCES match_records(id)
        )
    ''')

    conn.commit()
    conn.close()
    print("[DB] 数据库初始化完成", flush=True)


def get_token_current_data(token_address):
    """从 DexScreener 获取代币数据"""
    try:
        url = f"{config.DEXSCREENER_API}/{token_address}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            pairs = data.get('pairs', [])
            if pairs:
                pair = pairs[0]
                return {
                    'market_cap': float(pair.get('marketCap') or pair.get('fdv') or 0),
                    'price': str(pair.get('priceUsd', '0')),
                }
    except Exception as e:
        stats['errors'] += 1
    return None


def save_match_record(news_data, keywords, matched_tokens):
    """保存匹配记录"""
    if not matched_tokens:
        return None

    conn = sqlite3.connect(config.DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO match_records (
            news_time, news_author, news_author_name, news_avatar, news_type,
            news_content, news_images, news_videos,
            ref_author, ref_author_name, ref_avatar, ref_content, ref_images,
            keywords
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        news_data.get('time', 0),
        news_data.get('author', ''),
        news_data.get('authorName', ''),
        news_data.get('avatar', ''),
        news_data.get('type', ''),
        news_data.get('content', ''),
        json.dumps(news_data.get('images', []), ensure_ascii=False),
        json.dumps(news_data.get('videos', []), ensure_ascii=False),
        news_data.get('refAuthor', ''),
        news_data.get('refAuthorName', ''),
        news_data.get('refAvatar', ''),
        news_data.get('refContent', ''),
        json.dumps(news_data.get('refImages', []), ensure_ascii=False),
        json.dumps(keywords, ensure_ascii=False)
    ))
    match_id = cursor.lastrowid

    top5 = matched_tokens[:5]
    matched_token_ids = []

    for rank, token in enumerate(top5, 1):
        cursor.execute('''
            INSERT INTO matched_tokens
            (match_id, rank, token_address, token_symbol, token_name, chain,
             initial_price, initial_market_cap, initial_holders,
             match_score, match_keyword, match_type, final_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            match_id, rank,
            token.get('tokenAddress', ''),
            token.get('tokenSymbol', ''),
            token.get('tokenName', ''),
            token.get('chain', 'BSC'),
            token.get('price', '0'),
            float(token.get('marketCap', 0) or 0),
            int(token.get('holders', 0) or 0),
            token.get('_match_score', 0),
            token.get('_matched_keyword', ''),
            token.get('_match_type', ''),
            token.get('_final_score', 0)
        ))
        matched_token_ids.append(cursor.lastrowid)

    conn.commit()
    conn.close()

    stats['total_matches'] += 1
    schedule_tracking(match_id, matched_token_ids)
    return match_id


def schedule_tracking(match_id, matched_token_ids):
    """安排追踪任务"""
    current_time = time.time()
    with tracking_lock:
        for offset in config.TRACK_INTERVALS:
            tracking_tasks.append({
                'match_id': match_id,
                'token_ids': matched_token_ids,
                'time_offset': offset,
                'execute_at': current_time + offset
            })


def track_market_cap(matched_token_id, time_offset):
    """追踪市值"""
    conn = sqlite3.connect(config.DB_PATH)
    cursor = conn.cursor()

    cursor.execute('SELECT token_address, initial_market_cap FROM matched_tokens WHERE id = ?', (matched_token_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return

    token_address, initial_mc = row
    current_data = get_token_current_data(token_address)

    if current_data:
        current_mc = current_data['market_cap']
        change_pct = ((current_mc - initial_mc) / initial_mc * 100) if initial_mc > 0 else 0

        cursor.execute('''
            INSERT INTO market_cap_tracking (matched_token_id, time_offset, market_cap, market_cap_change_pct, price)
            VALUES (?, ?, ?, ?, ?)
        ''', (matched_token_id, time_offset, current_mc, change_pct, current_data['price']))
        conn.commit()
        stats['total_tracked'] += 1

    conn.close()


def calculate_performance_score(match_id):
    """计算表现分数"""
    conn = sqlite3.connect(config.DB_PATH)
    cursor = conn.cursor()

    cursor.execute('SELECT id, token_address, token_symbol, token_name, chain, initial_market_cap FROM matched_tokens WHERE match_id = ?', (match_id,))
    tokens = cursor.fetchall()

    performers = []
    for token_id, address, symbol, name, chain, initial_mc in tokens:
        cursor.execute('SELECT time_offset, market_cap, market_cap_change_pct FROM market_cap_tracking WHERE matched_token_id = ? ORDER BY time_offset', (token_id,))
        tracking_data = cursor.fetchall()

        if not tracking_data:
            continue

        changes = {60: 0, 300: 0, 600: 0}
        final_mc = initial_mc
        for offset, mc, change_pct in tracking_data:
            changes[offset] = change_pct or 0
            if offset == 600:
                final_mc = mc

        base_score = changes.get(60, 0) * 0.2 + changes.get(300, 0) * 0.3 + changes.get(600, 0) * 0.5
        performers.append({
            'token_id': token_id, 'address': address, 'symbol': symbol,
            'name': name, 'chain': chain, 'initial_mc': initial_mc,
            'final_mc': final_mc, 'change_pct': changes.get(600, 0), 'score': base_score
        })

    performers.sort(key=lambda x: x['score'], reverse=True)
    for rank, p in enumerate(performers[:2], 1):
        cursor.execute('''
            INSERT INTO top_performers
            (match_id, performance_rank, token_address, token_symbol, token_name, chain,
             initial_market_cap, final_market_cap, market_cap_change_pct, performance_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (match_id, rank, p['address'], p['symbol'], p['name'], p['chain'],
              p['initial_mc'], p['final_mc'], p['change_pct'], p['score']))

    conn.commit()
    conn.close()


def tracking_worker():
    """追踪工作线程"""
    print("[Tracker] 追踪线程启动", flush=True)
    while stats['running']:
        current_time = time.time()
        tasks_to_execute = []

        with tracking_lock:
            remaining = []
            for task in tracking_tasks:
                if task['execute_at'] <= current_time:
                    tasks_to_execute.append(task)
                else:
                    remaining.append(task)
            tracking_tasks[:] = remaining

        for task in tasks_to_execute:
            stats['last_track'] = time.time()
            for token_id in task['token_ids']:
                track_market_cap(token_id, task['time_offset'])
            if task['time_offset'] == 600:
                calculate_performance_score(task['match_id'])

        time.sleep(5)


def query_best_tokens(limit=10):
    """查询推文及最佳代币"""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, news_time, news_author, news_author_name, news_avatar, news_type,
               news_content, news_images, news_videos,
               ref_author, ref_author_name, ref_avatar, ref_content, ref_images,
               keywords, created_at
        FROM match_records ORDER BY created_at DESC LIMIT ?
    ''', (limit,))
    records = cursor.fetchall()

    results = []
    for row in records:
        mid = row['id']

        # 查询匹配的代币
        cursor.execute('''
            SELECT token_symbol, token_name, token_address, chain,
                   initial_market_cap, initial_holders, match_score, match_keyword
            FROM matched_tokens WHERE match_id = ? ORDER BY rank
        ''', (mid,))
        matched = [dict(r) for r in cursor.fetchall()]

        # 查询最佳表现代币
        cursor.execute('''
            SELECT token_symbol, token_name, market_cap_change_pct, performance_score
            FROM top_performers WHERE match_id = ? ORDER BY performance_rank
        ''', (mid,))
        top = [dict(r) for r in cursor.fetchall()]

        # 解析 JSON 字段
        def parse_json(val):
            if not val:
                return []
            try:
                return json.loads(val)
            except:
                return []

        results.append({
            'id': mid,
            'time': row['news_time'],
            'author': row['news_author'],
            'authorName': row['news_author_name'] or '',
            'avatar': row['news_avatar'] or '',
            'type': row['news_type'],
            'content': row['news_content'],
            'images': parse_json(row['news_images']),
            'videos': parse_json(row['news_videos']),
            'refAuthor': row['ref_author'] or '',
            'refAuthorName': row['ref_author_name'] or '',
            'refAvatar': row['ref_avatar'] or '',
            'refContent': row['ref_content'] or '',
            'refImages': parse_json(row['ref_images']),
            'keywords': parse_json(row['keywords']),
            'matched_tokens': matched,
            'best_tokens': top,
            'created_at': row['created_at']
        })

    conn.close()
    return results


# ==================== Flask API ====================

@app.route('/track', methods=['POST'])
def api_track():
    data = request.json
    match_id = save_match_record(data.get('news', {}), data.get('keywords', []), data.get('tokens', []))
    if match_id:
        return jsonify({'success': True, 'match_id': match_id})
    return jsonify({'success': False}), 400


@app.route('/query', methods=['GET'])
def api_query():
    limit = request.args.get('limit', 10, type=int)
    return jsonify(query_best_tokens(limit))


@app.route('/status')
def status():
    with tracking_lock:
        pending_tasks = len(tracking_tasks)
    return jsonify({
        'service': 'tracker_service',
        'port': config.TRACKER_PORT,
        'running': stats['running'],
        'total_matches': stats['total_matches'],
        'total_tracked': stats['total_tracked'],
        'pending_tasks': pending_tasks,
        'last_track': stats['last_track'],
        'errors': stats['errors']
    })


@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


if __name__ == "__main__":
    port = config.get_port('tracker')
    print(f"代币跟踪服务启动: http://127.0.0.1:{port}", flush=True)

    init_db()

    tracker_thread = threading.Thread(target=tracking_worker, daemon=True)
    tracker_thread.start()

    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
