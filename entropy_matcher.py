"""
基于信息熵的代币匹配系统

核心原理：
1. 信息熵 H = -Σ P(x) * log(P(x))
2. IDF (Inverse Document Frequency) = log(N / df)
   - N = 代币总数
   - df = 包含该词的代币数
3. 高IDF = 词只出现在少数代币中 = 高选择性 = 好的匹配词
4. 低IDF = 词出现在很多代币中 = 低选择性 = 应该过滤

解决问题：
- "币安人生" 和 "币安人士" 共享 "币安" 和 "安人"
- 这些共享词的IDF低，会被过滤
- "人生" 和 "人士" 是各自独有的，IDF高，用于匹配
"""

from collections import Counter, defaultdict
import math


class EntropyMatcher:
    """基于信息熵的代币匹配器"""

    # 静态低熵词表（无论如何都过滤）
    STATIC_LOW_ENTROPY = {
        # 英文 - 加密通用词
        'crypto', 'coin', 'token', 'nft', 'web3', 'defi', 'blockchain',
        'bitcoin', 'btc', 'eth', 'bnb', 'sol', 'usdt', 'usdc',
        # 英文 - 交易所
        'binance', 'coinbase', 'okx', 'bybit', 'kucoin', 'gate',
        # 英文 - 常见词
        'the', 'a', 'an', 'to', 'for', 'and', 'or', 'is', 'are', 'was', 'be',
        'in', 'on', 'at', 'of', 'with', 'by', 'from', 'this', 'that', 'it',
        'new', 'big', 'first', 'best', 'top', 'major', 'price', 'market',
        # 中文 - 交易相关高频词
        'k线', '分析', '市场', '交易', '入场', '出场', '趋势', '信号',
        '比特', '以太', '合约', '现货', '杠杆', '仓位', '止损', '止盈',
        # 中文 - 交易所
        '币安', '欧易', '火币',
    }

    # IDF阈值：低于此值的词被过滤
    # IDF=0.7 约等于词出现在50%的代币中
    # IDF=1.0 约等于词出现在37%的代币中
    # IDF=2.0 约等于词出现在13%的代币中
    IDF_THRESHOLD = 0.5

    def __init__(self):
        # 代币数据库: {token_id: {'name': str, 'words': list}}
        self.tokens = {}
        # 倒排索引: word -> set of token_ids
        self.word_index = defaultdict(set)

    def tokenize(self, text):
        """分词"""
        if not text:
            return []

        text_lower = text.lower()
        has_chinese = any('\u4e00' <= c <= '\u9fff' for c in text)

        if has_chinese:
            # 中文: 2字符滑动窗口
            tokens = []
            for i in range(len(text) - 1):
                substr = text_lower[i:i+2]
                if any('\u4e00' <= c <= '\u9fff' for c in substr):
                    tokens.append(substr)
            return tokens
        else:
            # 英文: 空格分词
            return [w for w in text_lower.split() if len(w) >= 2]

    def add_token(self, token_id, name):
        """添加代币到索引"""
        words = self.tokenize(name)
        self.tokens[token_id] = {
            'name': name,
            'name_lower': name.lower(),
            'words': words
        }

        # 更新倒排索引
        for word in set(words):  # 去重
            self.word_index[word].add(token_id)

    def remove_token(self, token_id):
        """移除代币"""
        if token_id not in self.tokens:
            return

        token = self.tokens[token_id]
        for word in set(token['words']):
            self.word_index[word].discard(token_id)
            if not self.word_index[word]:
                del self.word_index[word]

        del self.tokens[token_id]

    def get_idf(self, word):
        """
        计算词的IDF值

        IDF = log(N / df)
        - N = 代币总数
        - df = 包含该词的代币数

        返回：IDF值（越高表示词越稀有，选择性越好）
        """
        # 静态低熵词
        if word.lower() in self.STATIC_LOW_ENTROPY:
            return 0.0

        # 计算动态IDF
        doc_freq = len(self.word_index.get(word, set()))
        if doc_freq == 0:
            return 10.0  # 未知词给最高分

        total_docs = len(self.tokens)
        if total_docs == 0:
            return 10.0

        # IDF = log(N / df)
        # 加1平滑避免log(1)=0的情况
        idf = math.log((total_docs + 1) / doc_freq)
        return idf

    def get_word_stats(self, word, token_id=None):
        """获取词的统计信息"""
        idf = self.get_idf(word)
        doc_freq = len(self.word_index.get(word, set()))
        is_static_low = word.lower() in self.STATIC_LOW_ENTROPY
        is_unique = token_id and self.is_word_unique_to_token(word, token_id)

        return {
            'word': word,
            'idf': idf,
            'doc_freq': doc_freq,
            'total_docs': len(self.tokens),
            'is_static_low_entropy': is_static_low,
            'is_unique': is_unique,
            'will_filter': is_static_low or (not is_unique),
            'tokens_containing': list(self.word_index.get(word, set()))[:5]  # 最多显示5个
        }

    def analyze_token(self, token_id):
        """分析代币的所有分词"""
        if token_id not in self.tokens:
            return None

        token = self.tokens[token_id]
        analysis = {
            'token_id': token_id,
            'name': token['name'],
            'words': []
        }

        for word in token['words']:
            stats = self.get_word_stats(word, token_id)
            stats['usable'] = not stats['will_filter']
            analysis['words'].append(stats)

        # 找出可用于匹配的词（独有且非静态低熵）
        analysis['usable_words'] = [w for w in analysis['words'] if w['usable']]

        return analysis

    def is_word_unique_to_token(self, word, token_id):
        """
        检查词是否是该代币独有的（不被其他代币共享）

        核心思想：
        - 如果一个词只出现在当前代币中，它具有最高的区分度
        - 如果一个词被多个代币共享，用它匹配会产生歧义
        """
        tokens_with_word = self.word_index.get(word, set())
        # 只有当前代币包含这个词
        return len(tokens_with_word) == 1 and token_id in tokens_with_word

    def match(self, token_id, tweet):
        """
        匹配代币和推文

        策略：
        1. 完整匹配 - 最高优先级，直接匹配整个名称
        2. 独有分词匹配 - 只使用该代币独有的分词（不被其他代币共享）
        3. 如果没有独有分词，只能靠完整匹配

        返回: (is_match, matched_word, match_type, score)
        """
        if token_id not in self.tokens:
            return False, None, None, 0

        token = self.tokens[token_id]
        name_lower = token['name_lower']
        tweet_lower = tweet.lower()

        # 1. 完整匹配（最高优先级，不受任何限制）
        if name_lower in tweet_lower:
            return True, token['name'], "完整匹配", 10.0

        # 2. 独有分词匹配
        # 只使用该代币独有的分词（不被其他代币共享，且不是静态低熵词）
        for word in token['words']:
            # 跳过静态低熵词
            if word.lower() in self.STATIC_LOW_ENTROPY:
                continue

            # 只使用独有词（不被其他代币共享）
            if not self.is_word_unique_to_token(word, token_id):
                continue

            if word in tweet_lower:
                idf = self.get_idf(word)
                return True, word, f"独有分词匹配(IDF={idf:.2f})", idf

        return False, None, None, 0

    def match_all(self, tweet):
        """
        匹配推文和所有代币

        返回: [(token_id, matched_word, match_type, score), ...]
        """
        results = []

        for token_id in self.tokens:
            is_match, word, match_type, score = self.match(token_id, tweet)
            if is_match:
                results.append({
                    'token_id': token_id,
                    'token_name': self.tokens[token_id]['name'],
                    'matched_word': word,
                    'match_type': match_type,
                    'score': score
                })

        # 按分数排序
        results.sort(key=lambda x: x['score'], reverse=True)
        return results


def test_entropy_system():
    """测试信息熵匹配系统"""
    print("=" * 80)
    print("信息熵匹配系统测试")
    print("=" * 80)

    matcher = EntropyMatcher()

    # 添加测试代币
    test_tokens = [
        ("token1", "币安人生"),
        ("token2", "币安人士"),
        ("token3", "人生k线"),
        ("token4", "crypto winter"),
        ("token5", "doge"),
        ("token6", "我踏马"),
    ]

    print("\n1. 添加代币到索引:")
    for token_id, name in test_tokens:
        matcher.add_token(token_id, name)
        print(f"   {token_id}: {name}")

    print(f"\n   代币总数: {len(matcher.tokens)}")

    # 分析每个代币
    print("\n2. 代币分词分析:")
    for token_id, name in test_tokens:
        analysis = matcher.analyze_token(token_id)
        print(f"\n   【{name}】")
        for w in analysis['words']:
            status = "✓可用" if w['usable'] else "✗过滤"
            reason = ""
            if w['is_static_low_entropy']:
                reason = "(静态低熵词)"
            elif not w['is_unique']:
                shared_with = [t for t in w['tokens_containing'] if t != token_id]
                reason = f"(共享于: {shared_with})"
            else:
                reason = "(独有词)"
            print(f"      {w['word']}: {status} {reason}")

        usable = [w['word'] for w in analysis['usable_words']]
        print(f"      → 可用词: {usable if usable else '无（只能完整匹配）'}")

    # 测试匹配
    print("\n3. 匹配测试:")
    test_cases = [
        # 币安人生 vs 币安人士
        ("币安人士真棒", "币安人生", False),  # 不应匹配（安人共享）
        ("币安人士真棒", "币安人士", True),   # 应该匹配（完整匹配）
        ("币安人生真好", "币安人生", True),   # 应该匹配（完整匹配）
        ("币安人生真好", "币安人士", False),  # 不应匹配（安人共享）
        ("我的人生很精彩", "币安人生", False), # 不匹配（人生被"人生k线"共享）
        ("我的人生很精彩", "币安人士", False),# 不应匹配
        ("他是人士代表", "币安人士", True),   # 应该匹配（人士独有）
        ("他是人士代表", "币安人生", False),  # 不应匹配
        # crypto winter
        ("crypto is the future", "crypto winter", False),  # 不应匹配（crypto是低熵词）
        ("this winter is cold", "crypto winter", True),    # 应该匹配（winter独有）
        # doge
        ("I love DOGE", "doge", True),  # 应该匹配（完整匹配）
        # 我踏马（用户提到的案例）
        ("我踏马怎么拿着麦克风", "我踏马", True),   # 应该匹配（完整匹配）
        ("踏马的怎么回事", "我踏马", True),         # 应该匹配（踏马独有）
    ]

    passed = 0
    failed = 0

    for tweet, token_name, expected in test_cases:
        # 找到token_id
        token_id = None
        for tid, name in test_tokens:
            if name == token_name:
                token_id = tid
                break

        is_match, word, match_type, score = matcher.match(token_id, tweet)

        ok = is_match == expected
        if ok:
            passed += 1
        else:
            failed += 1

        status = "✅" if ok else "❌"
        print(f"\n   {status} 推文: \"{tweet}\"")
        print(f"      代币: {token_name}")
        print(f"      期望: {'匹配' if expected else '不匹配'}")
        print(f"      实际: {'匹配' if is_match else '不匹配'} | 词={word} | {match_type}")

    print("\n" + "=" * 80)
    print(f"测试结果: 通过 {passed}/{len(test_cases)}, 失败 {failed}/{len(test_cases)}")
    print("=" * 80)

    return failed == 0


if __name__ == "__main__":
    test_entropy_system()
