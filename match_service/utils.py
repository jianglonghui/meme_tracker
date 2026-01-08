"""
工具函数模块
- 图片缓存
- 分词匹配
- 事件缓存
"""
import os
import json
import time
import hashlib
import requests
import config

# 图片缓存目录
MEDIA_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'media_cache')
os.makedirs(MEDIA_CACHE_DIR, exist_ok=True)

# 已处理推文缓存文件
SEEN_EVENTS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'seen_events.json')

# 低信息熵词表（分词匹配时过滤）
LOW_ENTROPY_WORDS = {
    # 英文 - 加密通用词
    'crypto', 'coin', 'token', 'nft', 'web3', 'defi', 'blockchain',
    'bitcoin', 'btc', 'eth', 'bnb', 'sol', 'usdt', 'usdc',
    # 英文 - 交易所
    'binance', 'coinbase', 'okx', 'bybit', 'kucoin', 'gate',
    # 英文 - 常见词
    'the', 'a', 'an', 'to', 'for', 'and', 'or', 'is', 'are', 'was', 'be',
    'in', 'on', 'at', 'of', 'with', 'by', 'from', 'this', 'that', 'it',
    'new', 'big', 'first', 'best', 'top', 'major', 'price', 'market',
    # 中文 - 交易相关
    'k线', '分析', '市场', '交易', '入场', '出场', '趋势', '信号',
    '比特', '以太', '合约', '现货', '杠杆', '仓位', '止损', '止盈',
    # 中文 - 交易所
    '币安', '欧易', '火币',
}


def get_cached_image(url):
    """获取缓存的图片路径，如果不存在则下载"""
    try:
        # 处理本地注入的图片
        if url.startswith('/local_image/'):
            filename = url.split('/')[-1]
            local_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'image_cache', filename)
            if os.path.exists(local_path):
                return local_path
            return None

        # 使用 URL 的 MD5 作为文件名
        cache_key = hashlib.md5(url.encode()).hexdigest()

        # 检查各种扩展名
        for ext in ['.jpg', '.png', '.gif', '.webp', '']:
            cache_path = os.path.join(MEDIA_CACHE_DIR, f"{cache_key}{ext}")
            if os.path.exists(cache_path) and os.path.getsize(cache_path) > 1000:
                return cache_path

        # 缓存不存在，下载图片
        ext = '.jpg'
        if '.png' in url.lower():
            ext = '.png'
        elif '.gif' in url.lower():
            ext = '.gif'
        cache_path = os.path.join(MEDIA_CACHE_DIR, f"{cache_key}{ext}")

        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }
        resp = requests.get(url, timeout=10, proxies=config.PROXIES, headers=headers)
        if resp.status_code == 200 and len(resp.content) > 1000:
            with open(cache_path, 'wb') as f:
                f.write(resp.content)
            return cache_path
    except Exception as e:
        print(f"[图片] 获取失败: {e}", flush=True)
    return None


def load_seen_events():
    """从文件加载已处理的推文 ID"""
    try:
        if os.path.exists(SEEN_EVENTS_FILE):
            with open(SEEN_EVENTS_FILE, 'r') as f:
                data = json.load(f)
                # 只保留最近24小时的记录
                cutoff = time.time() - 86400
                return {k: v for k, v in data.items() if v > cutoff}
    except Exception as e:
        print(f"加载 seen_events 失败: {e}", flush=True)
    return {}


def save_seen_events(seen_events):
    """保存已处理的推文 ID 到文件"""
    try:
        with open(SEEN_EVENTS_FILE, 'w') as f:
            json.dump(seen_events, f)
    except Exception as e:
        print(f"保存 seen_events 失败: {e}", flush=True)


def tokenize_name(name):
    """
    对代币名称进行分词
    - 英文：按空格分词
    - 中文：提取连续2字符的子串
    """
    if not name:
        return []

    tokens = []
    has_chinese = any('\u4e00' <= c <= '\u9fff' for c in name)

    if has_chinese:
        for i in range(len(name) - 1):
            substr = name[i:i+2]
            if len(substr) >= 2 and any('\u4e00' <= c <= '\u9fff' for c in substr):
                tokens.append(substr)
    else:
        tokens = [w for w in name.split() if len(w) >= 2]

    return tokens


def match_name_in_tweet(name, tweet_lower):
    """
    检查代币名称是否在推文中匹配
    返回 (是否匹配, 匹配的词, 匹配类型, 分数)
    """
    if not name or len(name) < 2:
        return False, None, None, 0

    # 1. 完整匹配
    if name in tweet_lower:
        return True, name, "推文包含name", 4.0

    # 2. 分词匹配（过滤低信息熵词）
    tokens = tokenize_name(name)
    for token in tokens:
        if token.lower() in LOW_ENTROPY_WORDS:
            continue
        if token in tweet_lower:
            return True, token, "推文包含name分词", 3.0

    return False, None, None, 0


def calculate_match_score(keywords, symbol, name):
    """计算关键词与代币的匹配分数"""
    max_score = 0
    matched_keyword = None
    match_type = None

    symbol_lower = symbol.lower() if symbol else ''
    name_lower = name.lower() if name else ''

    for kw in keywords:
        if not kw:
            continue
        kw_lower = kw.lower()

        score = 0
        mtype = None

        # 完全匹配 symbol
        if kw_lower == symbol_lower and len(symbol_lower) >= 2:
            score = 5.0
            mtype = "关键词=symbol"
        # 完全匹配 name
        elif kw_lower == name_lower and len(name_lower) >= 2:
            score = 4.0
            mtype = "关键词=name"
        # symbol 包含关键词
        elif len(kw_lower) >= 2 and kw_lower in symbol_lower:
            score = 3.0
            mtype = "symbol包含关键词"
        # name 包含关键词
        elif len(kw_lower) >= 2 and kw_lower in name_lower:
            score = 2.5
            mtype = "name包含关键词"
        # 关键词包含 symbol
        elif len(symbol_lower) >= 2 and symbol_lower in kw_lower:
            score = 2.0
            mtype = "关键词包含symbol"
        # 关键词包含 name
        elif len(name_lower) >= 2 and name_lower in kw_lower:
            score = 1.5
            mtype = "关键词包含name"

        if score > max_score:
            max_score = score
            matched_keyword = kw
            match_type = mtype

    return max_score, matched_keyword, match_type
