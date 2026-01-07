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

# 内存中的追踪数据（追踪完成后才决定是否写入数据库）
# key: match_id (临时ID), value: {news_data, keywords, tokens, tracking_data}
pending_records = {}
pending_lock = threading.Lock()
temp_match_id = 0

# 错误码: -1=无交易对, -2=HTTP错误, -3=网络异常


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
            source TEXT DEFAULT 'new',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (match_id) REFERENCES match_records(id)
        )
    ''')

    # 兼容旧表：添加 source 字段
    try:
        cursor.execute('ALTER TABLE matched_tokens ADD COLUMN source TEXT DEFAULT "new"')
    except:
        pass

    # 兼容旧表：添加 match_method 字段（硬编码/AI）
    try:
        cursor.execute('ALTER TABLE matched_tokens ADD COLUMN match_method TEXT DEFAULT "hardcoded"')
    except:
        pass

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

    # 最佳实践样例表（用于提示词）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS best_practices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tweet_content TEXT NOT NULL,
            keywords TEXT NOT NULL,
            best_token TEXT NOT NULL,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 全量推文记录表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS all_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_time INTEGER,
            news_author TEXT,
            news_author_name TEXT,
            news_avatar TEXT,
            news_type TEXT,
            news_content TEXT,
            news_images TEXT,
            news_videos TEXT,
            ref_author TEXT,
            ref_author_name TEXT,
            ref_avatar TEXT,
            ref_content TEXT,
            ref_images TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()
    print("[DB] 数据库初始化完成", flush=True)


def get_token_current_data(token_address):
    """从 DexScreener 获取代币数据，失败返回错误码"""
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
            else:
                print(f"[DexScreener] {token_address} - 无交易对数据", flush=True)
                return -1  # 无交易对
        else:
            print(f"[DexScreener] {token_address} - HTTP {resp.status_code}", flush=True)
            return -2  # HTTP错误
    except Exception as e:
        print(f"[DexScreener] {token_address} - 请求失败: {e}", flush=True)
        stats['errors'] += 1
        return -3  # 网络异常
    return -1


def save_match_record(news_data, keywords, matched_tokens):
    """保存匹配记录到内存，开始追踪（不写入数据库）"""
    global temp_match_id

    if not matched_tokens:
        return None

    # 生成临时 ID
    with pending_lock:
        temp_match_id += 1
        match_id = temp_match_id

    top5 = matched_tokens[:5]
    tokens_data = []

    for rank, token in enumerate(top5, 1):
        # 判断来源：有 source=binance_search 的是老币，否则是新币
        source = token.get('source', 'new')
        if source == 'binance_search':
            source = 'old'
        else:
            source = 'new'

        tokens_data.append({
            'rank': rank,
            'address': token.get('tokenAddress', ''),
            'symbol': token.get('tokenSymbol', ''),
            'name': token.get('tokenName', ''),
            'chain': token.get('chain', 'BSC'),
            'price': token.get('price', '0'),
            'initial_mc': float(token.get('marketCap', 0) or 0),
            'holders': int(token.get('holders', 0) or 0),
            'match_score': token.get('_match_score', 0),
            'match_keyword': token.get('_matched_keyword', ''),
            'match_type': token.get('_match_type', ''),
            'match_method': token.get('_match_method', 'hardcoded'),  # 匹配逻辑：hardcoded/ai
            'final_score': token.get('_final_score', 0),
            'source': source,  # 代币来源：new/exclusive
            'tracking': {}  # {60: {mc, change_pct, price}, 300: ..., 600: ...}
        })

    # 保存到内存
    with pending_lock:
        pending_records[match_id] = {
            'news_data': news_data,
            'keywords': keywords,
            'tokens': tokens_data
        }

    stats['total_matches'] += 1
    schedule_tracking(match_id)
    print(f"[追踪] 开始追踪 #{match_id} ({len(tokens_data)} 个代币)", flush=True)
    return match_id


def schedule_tracking(match_id):
    """安排追踪任务"""
    current_time = time.time()
    with tracking_lock:
        for offset in config.TRACK_INTERVALS:
            tracking_tasks.append({
                'match_id': match_id,
                'time_offset': offset,
                'execute_at': current_time + offset
            })


def track_market_cap(match_id, time_offset):
    """追踪市值（内存中）"""
    with pending_lock:
        record = pending_records.get(match_id)
        if not record:
            return

        for token in record['tokens']:
            address = token['address']
            symbol = token['symbol']
            initial_mc = token['initial_mc']

            result = get_token_current_data(address)

            if isinstance(result, dict):
                current_mc = result['market_cap']
                change_pct = ((current_mc - initial_mc) / initial_mc * 100) if initial_mc > 0 else 0
                token['tracking'][time_offset] = {
                    'mc': current_mc,
                    'change_pct': change_pct,
                    'price': result['price']
                }
                stats['total_tracked'] += 1
                print(f"[追踪] {symbol} ({time_offset}s) - MC: {current_mc:.0f} ({change_pct:+.1f}%)", flush=True)
            else:
                # 错误码
                token['tracking'][time_offset] = {
                    'mc': result,
                    'change_pct': 0,
                    'price': ''
                }
                stats['errors'] += 1


def calculate_performance_score(match_id):
    """
    追踪完成后判断是否达标，达标才写入数据库：
    - 新币：市值 >= 10万 → 写入数据库
    - 老币：涨幅 >= 10% → 写入数据库
    """
    with pending_lock:
        record = pending_records.pop(match_id, None)

    if not record:
        return

    news_data = record['news_data']
    keywords = record['keywords']
    tokens = record['tokens']

    # 筛选达标代币
    qualified_tokens = []
    for token in tokens:
        tracking = token.get('tracking', {})
        final_data = tracking.get(600, {})
        final_mc = final_data.get('mc', token['initial_mc'])
        change_pct = final_data.get('change_pct', 0)

        # 跳过错误数据
        if isinstance(final_mc, int) and final_mc < 0:
            continue

        # 计算表现分数
        c60 = tracking.get(60, {}).get('change_pct', 0)
        c300 = tracking.get(300, {}).get('change_pct', 0)
        c600 = change_pct
        score = c60 * 0.2 + c300 * 0.3 + c600 * 0.5

        token['final_mc'] = final_mc
        token['change_pct'] = change_pct
        token['score'] = score

        if token['source'] == 'old':
            # 老币：涨幅 >= 10%
            if change_pct >= config.MIN_CHANGE_TO_RECORD:
                qualified_tokens.append(token)
                print(f"[达标-老币] {token['symbol']} 涨幅 {change_pct:.1f}%", flush=True)
        else:
            # 新币：市值 >= 10万
            if final_mc >= config.MIN_MCAP_TO_KEEP:
                qualified_tokens.append(token)
                print(f"[达标-新币] {token['symbol']} 市值 {final_mc:.0f}", flush=True)

    # 没有达标代币，不写入数据库
    if not qualified_tokens:
        print(f"[追踪完成] #{match_id} 无达标代币，不写入数据库", flush=True)
        return

    # 写入数据库
    conn = sqlite3.connect(config.DB_PATH)
    cursor = conn.cursor()

    # 1. 写入 match_records
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
    db_match_id = cursor.lastrowid

    # 2. 写入 matched_tokens 和 market_cap_tracking
    for token in qualified_tokens:
        cursor.execute('''
            INSERT INTO matched_tokens
            (match_id, rank, token_address, token_symbol, token_name, chain,
             initial_price, initial_market_cap, initial_holders,
             match_score, match_keyword, match_type, final_score, source, match_method)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            db_match_id, token['rank'], token['address'], token['symbol'],
            token['name'], token['chain'], token['price'], token['initial_mc'],
            token['holders'], token['match_score'], token['match_keyword'],
            token['match_type'], token['final_score'], token['source'], token.get('match_method', 'hardcoded')
        ))
        token_id = cursor.lastrowid

        # 写入追踪数据
        for offset, data in token.get('tracking', {}).items():
            cursor.execute('''
                INSERT INTO market_cap_tracking
                (matched_token_id, time_offset, market_cap, market_cap_change_pct, price)
                VALUES (?, ?, ?, ?, ?)
            ''', (token_id, offset, data['mc'], data['change_pct'], data['price']))

    # 3. 写入 top_performers
    qualified_tokens.sort(key=lambda x: x['score'], reverse=True)
    for rank, token in enumerate(qualified_tokens[:2], 1):
        cursor.execute('''
            INSERT INTO top_performers
            (match_id, performance_rank, token_address, token_symbol, token_name, chain,
             initial_market_cap, final_market_cap, market_cap_change_pct, performance_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (db_match_id, rank, token['address'], token['symbol'], token['name'],
              token['chain'], token['initial_mc'], token['final_mc'],
              token['change_pct'], token['score']))

    conn.commit()
    conn.close()
    print(f"[写入数据库] #{db_match_id} 共 {len(qualified_tokens)} 个达标代币", flush=True)


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
            track_market_cap(task['match_id'], task['time_offset'])
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
    with pending_lock:
        tracking_records = len(pending_records)
    return jsonify({
        'service': 'tracker_service',
        'port': config.TRACKER_PORT,
        'running': stats['running'],
        'total_matches': stats['total_matches'],
        'total_tracked': stats['total_tracked'],
        'pending_tasks': pending_tasks,
        'tracking_records': tracking_records,
        'last_track': stats['last_track'],
        'errors': stats['errors']
    })


@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


@app.route('/recent')
def recent():
    """返回最近的匹配记录（数据库 + 内存追踪中）"""
    # 1. 内存中正在追踪的记录
    tracking = []
    with pending_lock:
        for mid, record in list(pending_records.items())[:10]:
            news = record['news_data']
            tokens = record['tokens']
            # 统计追踪进度
            track_progress = {}
            for t in tokens:
                for offset in t.get('tracking', {}).keys():
                    track_progress[offset] = track_progress.get(offset, 0) + 1

            tracking.append({
                'id': f"T{mid}",  # T 表示 tracking
                'time': news.get('time', 0),
                'author': news.get('author', ''),
                'content': (news.get('content', '') or '')[:50],
                'keywords': record['keywords'][:5],
                'tokens': [{
                    'symbol': t['symbol'],
                    'keyword': t['match_keyword'],
                    'match_type': t.get('match_type', ''),
                    'match_method': t.get('match_method', 'hardcoded'),
                    'source': t.get('source', 'new')
                } for t in tokens[:3]],
                'progress': track_progress,  # {60: n, 300: n, 600: n}
                'status': 'tracking'
            })

    # 2. 数据库中已完成的记录
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, news_time, news_author, news_content, keywords, created_at
        FROM match_records ORDER BY created_at DESC LIMIT 10
    ''')
    records = []
    for row in cursor.fetchall():
        mid = row['id']
        cursor.execute('''
            SELECT token_symbol, match_keyword, match_type, match_method, source FROM matched_tokens
            WHERE match_id = ? ORDER BY rank LIMIT 3
        ''', (mid,))
        tokens = [{
            'symbol': r['token_symbol'],
            'keyword': r['match_keyword'],
            'match_type': r['match_type'] or '',
            'match_method': r['match_method'] or 'hardcoded',
            'source': r['source'] or 'new'
        } for r in cursor.fetchall()]

        records.append({
            'id': mid,
            'time': row['news_time'],
            'author': row['news_author'],
            'content': row['news_content'][:50] if row['news_content'] else '',
            'keywords': json.loads(row['keywords']) if row['keywords'] else [],
            'tokens': tokens,
            'status': 'saved'
        })

    conn.close()

    # 待处理任务
    with tracking_lock:
        pending = [{
            'match_id': t['match_id'],
            'time_offset': t['time_offset'],
            'execute_at': t['execute_at']
        } for t in tracking_tasks[:10]]

    return jsonify({'tracking': tracking, 'records': records, 'pending': pending})


# ==================== 最佳实践 API ====================

@app.route('/best_practices', methods=['GET'])
def get_best_practices():
    """获取最佳实践样例（从匹配记录中获取）"""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 直接从匹配记录 + top_performers 获取
    cursor.execute('''
        SELECT m.id, m.news_content, m.keywords, t.token_symbol
        FROM match_records m
        JOIN top_performers t ON m.id = t.match_id
        WHERE t.performance_rank = 1
        ORDER BY m.created_at DESC
        LIMIT 20
    ''')

    results = []
    for r in cursor.fetchall():
        results.append({
            'id': r['id'],
            'tweet_content': r['news_content'],
            'keywords': json.loads(r['keywords']),
            'best_token': r['token_symbol']
        })

    conn.close()
    return jsonify(results)


@app.route('/best_practices', methods=['POST'])
def add_best_practice():
    """手动添加匹配记录（推文+代币）"""
    data = request.json
    tweet_content = data.get('tweet_content', '')
    keywords = data.get('keywords', [])
    best_token = data.get('best_token', '')

    if not tweet_content or not keywords or not best_token:
        return jsonify({'success': False, 'error': '缺少必要字段'}), 400

    conn = sqlite3.connect(config.DB_PATH)
    cursor = conn.cursor()

    # 1. 插入 match_records
    cursor.execute('''
        INSERT INTO match_records (news_time, news_author, news_author_name, news_avatar,
            news_type, news_content, news_images, news_videos,
            ref_author, ref_author_name, ref_avatar, ref_content, ref_images, keywords)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        int(time.time()), 'manual', '手动添加', '',
        'manual', tweet_content, '[]', '[]',
        '', '', '', '', '[]', json.dumps(keywords, ensure_ascii=False)
    ))
    match_id = cursor.lastrowid

    # 2. 插入 top_performers
    cursor.execute('''
        INSERT INTO top_performers (match_id, performance_rank, token_address, token_symbol,
            token_name, chain, initial_market_cap, final_market_cap, market_cap_change_pct, performance_score)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (match_id, 1, f'manual_{match_id}', best_token, best_token, 'MANUAL', 0, 0, 0, 0))

    conn.commit()
    conn.close()

    return jsonify({'success': True, 'id': match_id})


@app.route('/best_practices/<int:practice_id>', methods=['DELETE'])
def delete_best_practice(practice_id):
    """删除匹配记录"""
    conn = sqlite3.connect(config.DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM top_performers WHERE match_id = ?', (practice_id,))
    cursor.execute('DELETE FROM matched_tokens WHERE match_id = ?', (practice_id,))
    cursor.execute('DELETE FROM match_records WHERE id = ?', (practice_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/delete_records', methods=['POST'])
def delete_records():
    """批量删除匹配记录"""
    data = request.json
    ids = data.get('ids', [])

    if not ids:
        return jsonify({'success': False, 'error': '未选择记录'}), 400

    conn = sqlite3.connect(config.DB_PATH)
    cursor = conn.cursor()

    deleted = 0
    for record_id in ids:
        try:
            # 先删除关联的追踪数据
            cursor.execute('SELECT id FROM matched_tokens WHERE match_id = ?', (record_id,))
            token_ids = [row[0] for row in cursor.fetchall()]
            for tid in token_ids:
                cursor.execute('DELETE FROM market_cap_tracking WHERE matched_token_id = ?', (tid,))

            cursor.execute('DELETE FROM top_performers WHERE match_id = ?', (record_id,))
            cursor.execute('DELETE FROM matched_tokens WHERE match_id = ?', (record_id,))
            cursor.execute('DELETE FROM match_records WHERE id = ?', (record_id,))
            deleted += 1
        except Exception as e:
            print(f"[删除] 记录 {record_id} 失败: {e}", flush=True)

    conn.commit()
    conn.close()

    return jsonify({'success': True, 'deleted': deleted})


def insert_demo_data():
    """插入预制样例数据"""
    conn = sqlite3.connect(config.DB_PATH)
    cursor = conn.cursor()

    # 检查是否已有数据
    cursor.execute('SELECT COUNT(*) FROM match_records')
    if cursor.fetchone()[0] > 0:
        conn.close()
        return

    print("[DB] 插入预制样例数据...", flush=True)

    # 样例1: 币安一姐推文
    demo_news_1 = {
        'time': int(time.time()) - 300,
        'author': 'heyibinance',
        'authorName': 'Yi He',
        'avatar': '',
        'type': 'newTweet',
        'content': '唉呀呀呀呀，感谢我Jason总，你可太性情太通透了，简直就是币安思维，祝你持有BNB开币安汽车，住币安小区，享币安人生🙏',
        'images': [],
        'videos': [],
        'refAuthor': '',
        'refAuthorName': '',
        'refAvatar': '',
        'refContent': '',
        'refImages': []
    }
    keywords_1 = ['币安思维', 'bnb', '币安汽车', '币安小区', '币安人生']
    tokens_1 = [{
        'tokenAddress': '0xdemo1234567890',
        'tokenSymbol': '币安人生',
        'tokenName': '币安人生',
        'chain': 'BSC',
        'price': '0.00001',
        'marketCap': 5000,
        'holders': 50,
        '_match_score': 5.0,
        '_matched_keyword': '币安人生',
        '_match_type': '完全匹配symbol',
        '_final_score': 250
    }]

    # 样例2: Elon Musk
    demo_news_2 = {
        'time': int(time.time()) - 600,
        'author': 'elonmusk',
        'authorName': 'Elon Musk',
        'avatar': '',
        'type': 'newTweet',
        'content': 'DOGE to the moon! 🚀',
        'images': [],
        'videos': [],
        'refAuthor': '',
        'refAuthorName': '',
        'refAvatar': '',
        'refContent': '',
        'refImages': []
    }
    keywords_2 = ['doge', 'moon']
    tokens_2 = [{
        'tokenAddress': '0xdemo0987654321',
        'tokenSymbol': 'DOGE',
        'tokenName': 'Doge',
        'chain': 'BSC',
        'price': '0.00005',
        'marketCap': 15000,
        'holders': 120,
        '_match_score': 5.0,
        '_matched_keyword': 'doge',
        '_match_type': '完全匹配symbol',
        '_final_score': 600
    }]

    # 插入样例
    for news, kws, toks in [(demo_news_1, keywords_1, tokens_1), (demo_news_2, keywords_2, tokens_2)]:
        cursor.execute('''
            INSERT INTO match_records (
                news_time, news_author, news_author_name, news_avatar, news_type,
                news_content, news_images, news_videos,
                ref_author, ref_author_name, ref_avatar, ref_content, ref_images,
                keywords
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            news['time'], news['author'], news['authorName'], news['avatar'], news['type'],
            news['content'], json.dumps(news['images']), json.dumps(news['videos']),
            news['refAuthor'], news['refAuthorName'], news['refAvatar'],
            news['refContent'], json.dumps(news['refImages']),
            json.dumps(kws, ensure_ascii=False)
        ))
        match_id = cursor.lastrowid

        for rank, token in enumerate(toks, 1):
            cursor.execute('''
                INSERT INTO matched_tokens
                (match_id, rank, token_address, token_symbol, token_name, chain,
                 initial_price, initial_market_cap, initial_holders,
                 match_score, match_keyword, match_type, final_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                match_id, rank, token['tokenAddress'], token['tokenSymbol'],
                token['tokenName'], token['chain'], token['price'],
                token['marketCap'], token['holders'], token['_match_score'],
                token['_matched_keyword'], token['_match_type'], token['_final_score']
            ))

        # 插入最佳代币记录
        for rank, token in enumerate(toks, 1):
            cursor.execute('''
                INSERT INTO top_performers
                (match_id, performance_rank, token_address, token_symbol, token_name, chain,
                 initial_market_cap, final_market_cap, market_cap_change_pct, performance_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                match_id, rank, token['tokenAddress'], token['tokenSymbol'],
                token['tokenName'], token['chain'], token['marketCap'],
                token['marketCap'] * 1.5, 50.0, 25.0
            ))

    conn.commit()
    conn.close()
    print("[DB] 预制样例数据插入完成", flush=True)


if __name__ == "__main__":
    port = config.get_port('tracker')
    print(f"代币跟踪服务启动: http://127.0.0.1:{port}", flush=True)

    init_db()
    insert_demo_data()

    tracker_thread = threading.Thread(target=tracking_worker, daemon=True)
    tracker_thread.start()

    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
