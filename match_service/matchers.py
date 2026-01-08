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
    exclusive_tokens_cache, log_error
)
from .blacklist import load_exclusive_blacklist
from .utils import match_name_in_tweet, get_cached_image
from .ai_clients import call_gemini_judge

MIN_MATCH_SCORE = 2.0


def refresh_exclusive_tokens():
    """刷新优质代币缓存"""
    global exclusive_tokens_cache
    try:
        headers = {
            'accept': '*/*',
            'content-type': 'application/json',
            'clienttype': 'web',
            'lang': 'en',
            'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }
        resp = requests.get(
            'https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/pulse/exclusive/rank/list?chainId=56',
            headers=headers,
            proxies=config.PROXIES,
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            tokens = data.get('data', {}).get('tokens', []) or []
            result = []
            for t in tokens:
                meta = t.get('metaInfo', {}) or {}
                result.append({
                    'address': t.get('contractAddress', ''),
                    'symbol': t.get('symbol', ''),
                    'name': meta.get('name', '') or t.get('symbol', ''),
                    'chain': 'BSC',
                    'marketCap': float(t.get('marketCap', 0) or 0),
                    'holders': int(t.get('holders', 0) or 0),
                    'liquidity': float(t.get('liquidity', 0) or 0),
                    'price': t.get('price', 0),
                    'source': 'exclusive'
                })
            from . import state
            state.exclusive_tokens_cache = result
            print(f"[优质代币] 刷新缓存 {len(result)} 个", flush=True)
    except Exception as e:
        print(f"[优质代币] 刷新失败: {e}", flush=True)


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
                    'address': token.get('contractAddress', ''),
                    'symbol': token.get('symbol', ''),
                    'name': token.get('name', ''),
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


def match_new_tokens(news_time, tweet_text, image_urls=None):
    """匹配时间窗口内的新币

    Returns:
        (matched_tokens, tokens_in_window, window_token_names)
    """
    if not news_time or not tweet_text:
        return [], 0, []

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
        return [], len(window_tokens), window_token_names

    tweet_lower = tweet_text.lower()
    start_time_ms = int(time.time() * 1000)

    def do_hardcoded():
        matched = []
        for token in window_tokens:
            symbol = (token.get('tokenSymbol') or '').lower()
            name = (token.get('tokenName') or '').lower()
            score, match_type, matched_word = 0, None, None

            # 缓存命中
            with matched_names_lock:
                if symbol and symbol in matched_token_names:
                    score, match_type, matched_word = 5.0, "缓存命中", symbol

            # symbol 在推文中
            if score == 0 and symbol and len(symbol) >= 2 and symbol in tweet_lower:
                score, match_type, matched_word = 5.0, "推文包含symbol", symbol
            # name 匹配
            elif score == 0:
                m, word, mtype, sc = match_name_in_tweet(name, tweet_lower)
                if m:
                    score, match_type, matched_word = sc, mtype, word

            if score >= MIN_MATCH_SCORE:
                token_copy = token.copy()
                token_copy['_match_score'] = score
                token_copy['_matched_keyword'] = matched_word
                token_copy['_match_type'] = match_type
                token_copy['_match_method'] = 'hardcoded'
                token_copy['_token_source'] = 'new'
                create_time = token.get('createTime', 0)
                token_copy['_match_time_cost'] = int(time.time() * 1000) - create_time if create_time else 0
                matched.append(token_copy)

                if symbol:
                    with matched_names_lock:
                        matched_token_names.add(symbol)

        matched.sort(key=lambda x: x.get('_match_score', 0), reverse=True)
        return matched

    def do_ai():
        if not window_tokens or not config.GEMINI_API_KEY:
            return []

        # 准备图片
        image_paths = []
        if image_urls:
            for url in image_urls[:3]:
                path = get_cached_image(url)
                if path:
                    image_paths.append(path)

        # 转换格式
        tokens_for_ai = [{'symbol': t.get('tokenSymbol', ''), 'name': t.get('tokenName', '')} for t in window_tokens]
        idx = call_gemini_judge(tweet_text, tokens_for_ai, image_paths)

        if idx >= 0 and idx < len(window_tokens):
            token_copy = window_tokens[idx].copy()
            token_copy['_match_score'] = 5.0
            token_copy['_matched_keyword'] = token_copy.get('tokenSymbol', '')
            token_copy['_match_type'] = 'ai_match'
            token_copy['_match_method'] = 'ai'
            token_copy['_token_source'] = 'new'
            create_time = token_copy.get('createTime', 0)
            token_copy['_match_time_cost'] = int(time.time() * 1000) - create_time if create_time else 0

            symbol = (token_copy.get('tokenSymbol') or '').lower()
            if symbol:
                with matched_names_lock:
                    matched_token_names.add(symbol)
            return [token_copy]
        return []

    # 并行执行
    if stats['enable_hardcoded_match']:
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_hard = pool.submit(do_hardcoded)
            f_ai = pool.submit(do_ai)
            hardcoded_result = f_hard.result()
            ai_result = f_ai.result()

        if hardcoded_result:
            return hardcoded_result, len(window_tokens), window_token_names
    else:
        ai_result = do_ai()

    if ai_result:
        return ai_result, len(window_tokens), window_token_names

    return [], len(window_tokens), window_token_names


def match_exclusive_tokens(tweet_text, image_urls=None):
    """匹配优质代币（老币）

    Returns:
        matched_tokens list
    """
    if not tweet_text:
        return []

    all_tokens = get_exclusive_tokens()
    if not all_tokens:
        return []

    # 过滤黑名单
    blacklist = load_exclusive_blacklist()
    blacklist_lower = [b.lower() for b in blacklist]
    tokens = [t for t in all_tokens if t.get('address', '').lower() not in blacklist_lower]

    if not tokens:
        return []

    tweet_lower = tweet_text.lower()
    start_time_ms = int(time.time() * 1000)

    def do_hardcoded():
        best_match, best_score, best_keyword, best_type = None, 0, None, None

        for token in tokens:
            symbol = (token.get('symbol') or '').lower()
            name = (token.get('name') or '').lower()
            score, match_type, matched_word = 0, None, None

            if symbol and len(symbol) >= 2 and symbol in tweet_lower:
                score, match_type, matched_word = 5.0, "推文包含symbol", symbol
            else:
                m, word, mtype, sc = match_name_in_tweet(name, tweet_lower)
                if m:
                    score, match_type, matched_word = sc, mtype, word

            if score > best_score:
                best_score = score
                best_match = token
                best_keyword = matched_word
                best_type = match_type

        if best_match and best_score >= 1.5:
            matched = best_match.copy()
            matched['_match_score'] = best_score
            matched['_matched_keyword'] = best_keyword
            matched['_match_type'] = best_type
            matched['_match_method'] = 'hardcoded'
            matched['_token_source'] = 'exclusive'
            matched['_match_time_cost'] = int(time.time() * 1000) - start_time_ms

            symbol = (best_match.get('symbol') or '').lower()
            if symbol:
                with matched_names_lock:
                    matched_token_names.add(symbol)
            return [matched]
        return []

    def do_ai():
        if not config.GEMINI_API_KEY:
            return []

        image_paths = []
        if image_urls:
            for url in image_urls[:3]:
                path = get_cached_image(url)
                if path:
                    image_paths.append(path)

        tokens_for_ai = [{'symbol': t.get('symbol', ''), 'name': t.get('name', '')} for t in tokens]
        idx = call_gemini_judge(tweet_text, tokens_for_ai, image_paths)

        if idx >= 0 and idx < len(tokens):
            matched = tokens[idx].copy()
            matched['_match_score'] = 5.0
            matched['_matched_keyword'] = matched.get('symbol', '')
            matched['_match_type'] = 'ai_match'
            matched['_match_method'] = 'ai'
            matched['_token_source'] = 'exclusive'
            matched['_match_time_cost'] = int(time.time() * 1000) - start_time_ms

            symbol = (matched.get('symbol') or '').lower()
            if symbol:
                with matched_names_lock:
                    matched_token_names.add(symbol)
            return [matched]
        return []

    # 并行执行
    if stats['enable_hardcoded_match']:
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_hard = pool.submit(do_hardcoded)
            f_ai = pool.submit(do_ai)
            hardcoded_result = f_hard.result()
            ai_result = f_ai.result()

        if hardcoded_result:
            return hardcoded_result
    else:
        ai_result = do_ai()

    return ai_result if ai_result else []
