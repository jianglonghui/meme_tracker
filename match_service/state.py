"""
状态管理模块
- 全局统计
- 日志记录
- 缓存数据
"""
import threading
import time

# ==================== 状态统计 ====================
stats = {
    'total_matches': 0,
    'total_news': 0,
    'running': True,
    'last_match': None,
    'errors': 0,
    'enable_hardcoded_match': True
}

# ==================== 代币列表缓存 ====================
token_list = []
token_lock = threading.Lock()
MAX_TOKENS = 500

# ==================== 日志记录 ====================
recent_matches = []
recent_attempts = []
recent_errors = []
recent_filtered = []
log_lock = threading.Lock()
MAX_LOG_SIZE = 20

# ==================== 待检测队列 ====================
pending_news = []
pending_lock = threading.Lock()

# ==================== 已匹配代币名称缓存 ====================
matched_token_names = set()
matched_names_lock = threading.Lock()

# ==================== 每条推文专属缓存 ====================
tweet_matched_cache = {}  # key: tweet_id, value: set of matched symbols
tweet_cache_lock = threading.Lock()

# ==================== 优质代币缓存 ====================
exclusive_tokens_cache = []


def log_error(msg):
    """记录错误"""
    with log_lock:
        recent_errors.append({'time': time.time(), 'msg': msg})
        if len(recent_errors) > MAX_LOG_SIZE:
            recent_errors.pop(0)
    stats['errors'] += 1


def log_filtered(author, content, reason, news_time):
    """记录被过滤的推文"""
    with log_lock:
        recent_filtered.append({
            'time': time.time(),
            'news_time': news_time,
            'author': author,
            'content': content[:80],
            'reason': reason
        })
        if len(recent_filtered) > MAX_LOG_SIZE:
            recent_filtered.pop(0)


def log_attempt(author, content, keywords, tokens_in_window, matched_count, window_token_names):
    """记录撮合尝试"""
    with log_lock:
        recent_attempts.append({
            'time': time.time(),
            'author': author,
            'content': content[:100],
            'keywords': keywords[:5] if keywords else [],
            'tokens_in_window': tokens_in_window,
            'matched': matched_count,
            'window_tokens': window_token_names[:5] if window_token_names else [],
            'match_tasks': {
                'new_hardcoded': {'status': 'pending', 'result': None},
                'new_ai': {'status': 'pending', 'result': None},
                'exclusive_hardcoded': {'status': 'pending', 'result': None},
                'exclusive_ai': {'status': 'pending', 'result': None}
            },
            'matched_tokens': []
        })
        if len(recent_attempts) > MAX_LOG_SIZE:
            recent_attempts.pop(0)


def update_attempt(content, tokens_in_window, matched_count, window_token_names):
    """更新已有的撮合尝试记录"""
    with log_lock:
        for attempt in recent_attempts:
            if attempt['content'] == content[:100]:
                attempt['tokens_in_window'] = tokens_in_window
                attempt['matched'] = matched_count
                attempt['window_tokens'] = window_token_names[:5] if window_token_names else []
                break


def update_attempt_task(content, task_type, status, result=None, matched_token=None):
    """更新撮合尝试的匹配任务状态"""
    with log_lock:
        for attempt in recent_attempts:
            if attempt['content'] == content[:100]:
                if 'match_tasks' not in attempt:
                    attempt['match_tasks'] = {
                        'new_hardcoded': {'status': 'pending', 'result': None},
                        'new_ai': {'status': 'pending', 'result': None},
                        'exclusive_hardcoded': {'status': 'pending', 'result': None},
                        'exclusive_ai': {'status': 'pending', 'result': None}
                    }
                if 'matched_tokens' not in attempt:
                    attempt['matched_tokens'] = []

                attempt['match_tasks'][task_type] = {'status': status, 'result': result}

                if matched_token:
                    attempt['matched_tokens'].append(matched_token)
                    attempt['matched'] = len(attempt['matched_tokens'])
                break


def log_match(author, content, tokens):
    """记录匹配"""
    with log_lock:
        recent_matches.append({
            'time': time.time(),
            'author': author,
            'content': content[:80],
            'tokens': [{
                'symbol': t.get('tokenSymbol') or t.get('symbol', ''),
                'time_cost': t.get('_match_time_cost', 0),
                'system_latency': t.get('_system_latency', 0),
                'method': t.get('_match_method', 'hardcoded'),
                'source': t.get('_token_source') or t.get('source', 'new')
            } for t in tokens[:3]]
        })
        if len(recent_matches) > MAX_LOG_SIZE:
            recent_matches.pop(0)
