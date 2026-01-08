import time
import threading
from concurrent.futures import ThreadPoolExecutor
import config
from .matchers import run_hardcoded_engine, run_ai_engine
from .state import log_match, log_error, stats

class NewsSession:
    """代表一条推文的匹配会话，负责在时间窗口内监控新代币"""
    def __init__(self, news_data, full_content, all_images, orchestrator):
        self.news_data = news_data
        self.content = full_content
        self.images = all_images
        self.orchestrator = orchestrator
        
        self.news_time = news_data.get('time', 0)
        self.news_time_ms = self.news_time * 1000
        self.expire_time = self.news_time + (config.TIME_WINDOW_MS / 1000)
        
        self.matched_token_ids = set()
        self.local_cache = set() # 同一推文内的 symbol 缓存
        self.lock = threading.Lock()
        
        self.author = news_data.get('author', '')
        self.tweet_id = f"{self.news_time}_{self.author}"

    def get_remaining_seconds(self, current_time):
        return max(0, self.expire_time - current_time)

    def is_active(self, current_time):
        return current_time < self.expire_time

    def is_in_window(self, token_time_ms):
        return abs(token_time_ms - self.news_time_ms) <= config.TIME_WINDOW_MS

    def match_token_list(self, tokens, source='new'):
        """初始横扫：匹配已有的代币列表"""
        if not tokens: return []
        
        # 过滤处于窗口内的代币
        window_tokens = [t for t in tokens if self.is_in_window(t.get('createTime', 0))]
        if not window_tokens: return []
        
        return self._execute_engines(window_tokens, source)

    def match_single_token(self, token, source='new'):
        """增量匹配：匹配新产生的单个代币"""
        token_id = token.get('tokenAddress')
        with self.lock:
            if token_id in self.matched_token_ids:
                return []
        
        if not self.is_in_window(token.get('createTime', 0)):
            return []
            
        return self._execute_engines([token], source)

    def _execute_engines(self, tokens, source):
        """执行硬编码和 AI 引擎"""
        matched_results = []
        
        # 1. 硬编码引擎 (同步执行)
        if stats.get('enable_hardcoded_match', True):
            hard_matches = run_hardcoded_engine(self.content, tokens, self.local_cache, source)
            if hard_matches:
                current_time_ms = int(time.time() * 1000)
                with self.lock:
                    for m in hard_matches:
                        # 指标：从推文发布到【完成匹配】的系统总延迟
                        m['_system_latency'] = current_time_ms - (self.news_time * 1000)
                        self.matched_token_ids.add(m.get('tokenAddress'))
                matched_results.extend(hard_matches)
        
        return matched_results

    def execute_ai_engine_async(self, tokens, source='new'):
        """后台执行 AI 引擎"""
        # 过滤掉已经匹配过的
        with self.lock:
            remaining = [t for t in tokens if t.get('tokenAddress') not in self.matched_token_ids]
        
        if not remaining: return []
        
        ai_matches = run_ai_engine(self.content, remaining, self.images, self.local_cache, source)
        if ai_matches:
            current_time_ms = int(time.time() * 1000) # AI 运行完后再取时间
            with self.lock:
                for m in ai_matches:
                    m['_system_latency'] = current_time_ms - (self.news_time * 1000)
                    self.matched_token_ids.add(m.get('tokenAddress'))
            return ai_matches
        return []

class MatchOrchestrator:
    """全局撮合调度器"""
    def __init__(self, send_callback):
        self.sessions = {} # tweet_id -> NewsSession
        self.sessions_lock = threading.Lock()
        self.send_callback = send_callback
        self.executor = ThreadPoolExecutor(max_workers=20) # 用于异步 AI
        
        # 定时清理过期会话
        threading.Thread(target=self._cleanup_loop, daemon=True).start()

    def handle_news(self, news_data, full_content, all_images, existing_tokens):
        """处理新推文：创建会话并进行初始横扫"""
        session = NewsSession(news_data, full_content, all_images, self)
        tweet_id = session.tweet_id
        
        with self.sessions_lock:
            self.sessions[tweet_id] = session
            
        # 1. 初始硬编码横扫
        initial_matches = session.match_token_list(existing_tokens, source='new')
        if initial_matches:
            self.send_callback(news_data, [], initial_matches)
            
        # 2. 初始 AI 横扫 (异步)
        window_tokens = [t for t in existing_tokens if session.is_in_window(t.get('createTime', 0))]
        if window_tokens:
            self.executor.submit(self._run_ai_task, session, window_tokens, source='new')

    def handle_token(self, token_data):
        """处理新代币：推送到所有活跃的推文会话"""
        current_time = time.time()
        active_sessions = []
        
        with self.sessions_lock:
            for sid, session in list(self.sessions.items()):
                if session.is_active(current_time):
                    active_sessions.append(session)
        
        for session in active_sessions:
            # 1. 硬编码匹配
            matches = session.match_single_token(token_data, source='new')
            if matches:
                self.send_callback(session.news_data, [], matches)
            else:
                # 2. 如果硬编码没中，且符合窗口，可以尝试 AI (异步)
                if session.is_in_window(token_data.get('createTime', 0)):
                    self.executor.submit(self._run_ai_task, session, [token_data], source='new')

    def _run_ai_task(self, session, tokens, source='new'):
        """AI 任务执行逻辑"""
        try:
            ai_matches = session.execute_ai_engine_async(tokens, source)
            if ai_matches:
                self.send_callback(session.news_data, [], ai_matches)
        except Exception as e:
            log_error(f"Orchestrator AI Task: {e}")

    def get_active_sessions_info(self):
        """获取当前活跃会话的详细信息（符合用户要求的特定格式）"""
        now = time.time()
        info = []
        with self.sessions_lock:
            for sid, session in self.sessions.items():
                if session.is_active(now):
                    info.append({
                        'author': session.author,
                        'content': session.content[:100],
                        'keywords': [], # 兼容格式，Orchestrator 层面暂不存关键词
                        'matched_count': len(session.matched_token_ids),
                        'expire_time': session.expire_time,
                        'remaining_seconds': int(session.get_remaining_seconds(now)) # 额外保留倒计时
                    })
        return sorted(info, key=lambda x: x['expire_time'])

    def _cleanup_loop(self):
        """定期清理过期会话的后台线程"""
        while True:
            time.sleep(60)
            now = time.time()
            expired_ids = []
            with self.sessions_lock:
                for sid, session in self.sessions.items():
                    if not session.is_active(now):
                        expired_ids.append(sid)
                for sid in expired_ids:
                    del self.sessions[sid]
            if expired_ids:
                print(f"[Orchestrator] 清理过期会话: {len(expired_ids)} 个", flush=True)
