"""
匹配逻辑模块
- 新币匹配（时间窗口内的代币）
- 老币匹配（优质代币/Binance搜索）
- 硬编码匹配 + AI匹配并行
"""
import time
import requests
from concurrent.futures import ThreadPoolExecutor
import config

from .state import (
    stats, token_list, token_lock,
    matched_token_names, matched_names_lock,
    tweet_matched_cache, tweet_cache_lock,
    exclusive_tokens_cache, log_error
)
from .blacklist import load_exclusive_blacklist
from .utils import match_name_in_tweet, get_cached_image
from .ai_clients import call_gemini_judge, call_deepseek_fast_judge

MIN_MATCH_SCORE = 2.0


def refresh_exclusive_tokens():
    """刷新优质代币缓存（包含优质代币 + Alpha代币）"""
    global exclusive_tokens_cache
    headers = {
        'accept': '*/*',
        'content-type': 'application/json',
        'clienttype': 'web',
        'lang': 'en',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    }
    result = []
    seen_addresses = set()

    # 1. 获取优质代币
    try:
        resp = requests.get(
            'https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/pulse/exclusive/rank/list?chainId=56',
            headers=headers,
            proxies=config.PROXIES,
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            tokens = data.get('data', {}).get('tokens', []) or []
            for t in tokens:
                addr = t.get('contractAddress', '').lower()
                if addr and addr not in seen_addresses:
                    seen_addresses.add(addr)
                    meta = t.get('metaInfo', {}) or {}
                    result.append({
                        'tokenAddress': t.get('contractAddress', ''),
                        'tokenSymbol': t.get('symbol', ''),
                        'tokenName': meta.get('name', '') or t.get('symbol', ''),
                        'chain': 'BSC',
                        'marketCap': float(t.get('marketCap', 0) or 0),
                        'holders': int(t.get('holders', 0) or 0),
                        'liquidity': float(t.get('liquidity', 0) or 0),
                        'price': t.get('price', 0),
                        'source': 'exclusive'
                    })
            print(f"[优质代币] 获取 {len(result)} 个", flush=True)
    except Exception as e:
        print(f"[优质代币] 获取失败: {e}", flush=True)

    # 2. 获取 Alpha 代币
    alpha_count = 0
    try:
        resp = requests.get(
            'https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/pulse/exclusive/in/alpha/token/list',
            headers=headers,
            proxies=config.PROXIES,
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            # API 返回格式: data 可能是列表或字典
            raw_data = data.get('data', [])
            if isinstance(raw_data, list):
                tokens = raw_data
            elif isinstance(raw_data, dict):
                tokens = raw_data.get('tokens', []) or []
            else:
                tokens = []
            for t in tokens:
                addr = t.get('contractAddress', '').lower()
                if addr and addr not in seen_addresses:
                    seen_addresses.add(addr)
                    meta = t.get('metaInfo', {}) or {}
                    result.append({
                        'tokenAddress': t.get('contractAddress', ''),
                        'tokenSymbol': t.get('symbol', ''),
                        'tokenName': meta.get('name', '') or t.get('symbol', ''),
                        'chain': 'BSC',
                        'marketCap': float(t.get('marketCap', 0) or 0),
                        'holders': int(t.get('holders', 0) or 0),
                        'liquidity': float(t.get('liquidity', 0) or 0),
                        'price': t.get('price', 0),
                        'source': 'alpha'
                    })
                    alpha_count += 1
            print(f"[Alpha代币] 获取 {alpha_count} 个", flush=True)
    except Exception as e:
        print(f"[Alpha代币] 获取失败: {e}", flush=True)

    from . import state
    state.exclusive_tokens_cache = result
    print(f"[优质+Alpha] 缓存总计 {len(result)} 个代币", flush=True)


def get_exclusive_tokens():
    """获取优质代币列表"""
    from . import state
    return state.exclusive_tokens_cache


def search_binance_tokens(keyword):
    """使用 Binance API 搜索代币"""
    try:
        url = f"{config.BINANCE_SEARCH_URL}?keyword={requests.utils.quote(keyword)}&chainIds={config.BINANCE_SEARCH_CHAINS}"
        resp = requests.get(
            url,
            headers=config.HEADERS,
            cookies=config.COOKIES,
            proxies=config.PROXIES,
            timeout=10
        )
        if resp.status_code != 200:
            return []

        data = resp.json()
        if data.get('code') != '000000':
            return []

        tokens = data.get('data', []) or []
        quality_tokens = []
        now = time.time() * 1000

        for token in tokens:
            mcap = float(token.get('marketCap', 0) or 0)
            liquidity = float(token.get('liquidity', 0) or 0)
            create_time = token.get('createTime', 0) or 0
            age_seconds = (now - create_time) / 1000 if create_time else 0

            chain_id = token.get('chainId', '')
            chain_name = 'SOL' if chain_id == 'CT_501' else 'BSC' if chain_id == '56' else 'BASE' if chain_id == '8453' else chain_id
            min_mcap = config.SEARCH_MIN_MCAP_SOL if chain_name == 'SOL' else config.SEARCH_MIN_MCAP

            if (mcap >= min_mcap and
                liquidity >= config.SEARCH_MIN_LIQUIDITY and
                age_seconds >= config.SEARCH_MIN_AGE_SECONDS):
                quality_tokens.append({
                    'tokenAddress': token.get('contractAddress', ''),
                    'tokenSymbol': token.get('symbol', ''),
                    'tokenName': token.get('name', ''),
                    'chain': chain_name,
                    'marketCap': mcap,
                    'liquidity': liquidity,
                    'price': token.get('price', 0),
                    'source': 'binance_search'
                })

        return quality_tokens
    except Exception as e:
        print(f"[搜索] 异常: {e}", flush=True)
        return []


def run_hardcoded_engine(tweet_text, tokens, local_cache=None, source='new'):
    """仅执行硬编码匹配逻辑 (无IO，无并发)"""
    matched = []
    tweet_lower = tweet_text.lower()
    
    for token in tokens:
        symbol = (token.get('tokenSymbol') or token.get('symbol') or '').lower()
        name = (token.get('tokenName') or token.get('name') or '').lower()
        score, match_type, matched_word = 0, None, None

        # 缓存命中（同一条推文内）
        if local_cache is not None and symbol and symbol in local_cache:
            score, match_type, matched_word = 5.0, "缓存命中", symbol

        # symbol 在推文中
        if score == 0 and symbol and len(symbol) >= 2 and symbol in tweet_lower:
            score, match_type, matched_word = 5.0, "推文包含symbol", symbol
        # name 匹配
        elif score == 0:
            m, word, mtype, sc = match_name_in_tweet(name, tweet_lower)
            if m:
                score, match_type, matched_word = sc, mtype, word

        if score >= (MIN_MATCH_SCORE if source == 'new' else 1.5):
            token_copy = token.copy()
            token_copy['_match_score'] = score
            token_copy['_matched_keyword'] = matched_word
            token_copy['_match_type'] = match_type
            token_copy['_match_method'] = 'hardcoded'
            token_copy['_token_source'] = source
            
            # 时间成本计算（如果是新币，基于其创建时间；如果是老币，基于当前时间起点）
            if source == 'new':
                create_time = token.get('createTime', 0)
                token_copy['_match_time_cost'] = int(time.time() * 1000) - create_time if create_time else 0
            else:
                token_copy['_match_time_cost'] = 0 # 老币暂不计延迟
                
            matched.append(token_copy)
            if local_cache is not None and symbol:
                local_cache.add(symbol)

    matched.sort(key=lambda x: x.get('_match_score', 0), reverse=True)
    return matched


def run_ai_engine(tweet_text, tokens, image_urls=None, local_cache=None, source='new'):
    """执行 AI 匹配逻辑"""
    if not tokens or not config.GEMINI_API_KEY:
        return []

    # 准备图片
    image_paths = []
    if image_urls:
        for url in image_urls[:3]:
            path = get_cached_image(url)
            if path:
                image_paths.append(path)

    # 转换格式供 AI 使用
    tokens_for_ai = [
        {'symbol': t.get('tokenSymbol') or t.get('symbol', ''),
         'name': t.get('tokenName') or t.get('name', '')}
        for t in tokens
    ]

    try:
        idx = call_gemini_judge(tweet_text, tokens_for_ai, image_paths)
        if 0 <= idx < len(tokens):
            token_copy = tokens[idx].copy()
            token_copy['_match_score'] = 5.0
            token_copy['_matched_keyword'] = token_copy.get('tokenSymbol') or token_copy.get('symbol', '')
            token_copy['_match_type'] = 'ai_match'
            token_copy['_match_method'] = 'ai'
            token_copy['_token_source'] = source
            
            if source == 'new':
                create_time = token_copy.get('createTime', 0)
                token_copy['_match_time_cost'] = int(time.time() * 1000) - create_time if create_time else 0
            else:
                token_copy['_match_time_cost'] = 0
                
            # 加入缓存
            if local_cache is not None:
                symbol = (token_copy.get('tokenSymbol') or token_copy.get('symbol') or '').lower()
                if symbol:
                    local_cache.add(symbol)

            return [token_copy]
    except Exception as e:
        print(f"[AI Engine] 异常: {e}", flush=True)
        
    return []


def run_ai_fast_engine(tweet_text, tokens, local_cache=None, source='new'):
    """执行 AI 快速匹配（DeepSeek chat，无图片，中英文语义匹配）"""
    if not tokens or not config.DEEPSEEK_API_KEY:
        return []

    # 转换格式供 AI 使用
    tokens_for_ai = [
        {'symbol': t.get('tokenSymbol') or t.get('symbol', ''),
         'name': t.get('tokenName') or t.get('name', '')}
        for t in tokens
    ]

    try:
        matched_indices = call_deepseek_fast_judge(tweet_text, tokens_for_ai)
        if not matched_indices:
            return []

        matched = []
        for idx in matched_indices:
            if 0 <= idx < len(tokens):
                token_copy = tokens[idx].copy()
                token_copy['_match_score'] = 4.5  # 略低于 Gemini
                token_copy['_matched_keyword'] = token_copy.get('tokenSymbol') or token_copy.get('symbol', '')
                token_copy['_match_type'] = 'ai_fast_match'
                token_copy['_match_method'] = 'ai_fast'
                token_copy['_token_source'] = source

                if source == 'new':
                    create_time = token_copy.get('createTime', 0)
                    token_copy['_match_time_cost'] = int(time.time() * 1000) - create_time if create_time else 0
                else:
                    token_copy['_match_time_cost'] = 0

                # 加入缓存
                if local_cache is not None:
                    symbol = (token_copy.get('tokenSymbol') or token_copy.get('symbol') or '').lower()
                    if symbol:
                        local_cache.add(symbol)

                matched.append(token_copy)

        return matched
    except Exception as e:
        print(f"[AI Fast Engine] 异常: {e}", flush=True)

    return []


def match_new_tokens(news_time, tweet_text, image_urls=None, tweet_id=None):
    """匹配时间窗口内的新币 (保留原接口兼容性)"""
    if not news_time or not tweet_text:
        return [], 0, []

    if tweet_id:
        with tweet_cache_lock:
            if tweet_id not in tweet_matched_cache:
                tweet_matched_cache[tweet_id] = set()
            local_cache = tweet_matched_cache[tweet_id]
    else:
        local_cache = set()

    news_time_ms = news_time * 1000
    window_tokens = []
    window_token_names = []

    with token_lock:
        for token in token_list:
            create_time = token.get('createTime', 0)
            if not create_time:
                continue
            if abs(create_time - news_time_ms) <= config.TIME_WINDOW_MS:
                window_tokens.append(token)
                window_token_names.append(token.get('tokenSymbol') or token.get('tokenName') or 'Unknown')

    if not window_tokens:
        return [], 0, []

    # 使用重构后的 Engine
    hardcoded_result = []
    if stats['enable_hardcoded_match']:
        hardcoded_result = run_hardcoded_engine(tweet_text, window_tokens, local_cache, source='new')
        if hardcoded_result:
            return hardcoded_result, len(window_tokens), window_token_names

    ai_result = run_ai_engine(tweet_text, window_tokens, image_urls, local_cache, source='new')
    return ai_result, len(window_tokens), window_token_names


def match_exclusive_tokens(tweet_text, image_urls=None):
    """匹配优质代币（老币） (保留原接口兼容性)"""
    if not tweet_text:
        return []

    all_tokens = get_exclusive_tokens()
    if not all_tokens:
        return []

    # 过滤黑名单
    blacklist = load_exclusive_blacklist()
    blacklist_lower = [b.lower() for b in blacklist]
    tokens = [t for t in all_tokens if t.get('tokenAddress', '').lower() not in blacklist_lower]

    if not tokens:
        return []

    # 使用重构后的 Engine
    local_cache = set() # 老币匹配目前不强依赖同一条推文内的 local_cache，但可以传入
    
    hardcoded_result = []
    if stats['enable_hardcoded_match']:
        hardcoded_result = run_hardcoded_engine(tweet_text, tokens, local_cache, source='exclusive')
        if hardcoded_result:
            # 兼容格式转换
            return hardcoded_result

    ai_result = run_ai_engine(tweet_text, tokens, image_urls, local_cache, source='exclusive')
    return ai_result
