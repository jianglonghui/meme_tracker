"""
控制面板服务 (端口 5000)
- 显示所有服务状态
- 显示匹配数据
"""
import requests
import time
import hashlib
import os
from collections import deque
from flask import Flask, render_template_string, jsonify, request, Response, send_file
import config

# 图片/视频本地缓存目录
CACHE_DIR = os.path.join(os.path.dirname(__file__), 'media_cache')
os.makedirs(CACHE_DIR, exist_ok=True)

app = Flask(__name__)

# 服务状态历史记录 (最近60个点，每5秒一个点 = 5分钟)
MAX_HISTORY = 60
status_history = {
    'news_service': deque(maxlen=MAX_HISTORY),
    'token_service': deque(maxlen=MAX_HISTORY),
    'tracker_service': deque(maxlen=MAX_HISTORY),
    'match_service': deque(maxlen=MAX_HISTORY),
}
# 上一次的 errors 计数
last_errors = {
    'news_service': 0,
    'token_service': 0,
    'tracker_service': 0,
    'match_service': 0,
}

def get_services():
    """动态获取服务列表，确保使用正确的端口"""
    return [
        {'name': 'news_service', 'url': config.get_service_url('news'), 'desc': '推文发现', 'port': config.get_port('news')},
        {'name': 'token_service', 'url': config.get_service_url('token'), 'desc': '代币发现', 'port': config.get_port('token')},
        {'name': 'tracker_service', 'url': config.get_service_url('tracker'), 'desc': '代币跟踪', 'port': config.get_port('tracker')},
        {'name': 'match_service', 'url': config.get_service_url('match'), 'desc': '代币撮合', 'port': config.get_port('match')},
    ]

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Meme Tracker Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0b0e11; color: #eaecef; padding: 20px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        h1 { text-align: center; margin-bottom: 30px; color: #f0b90b; font-size: 28px; }
        h2 { color: #f0b90b; margin-bottom: 15px; font-size: 18px; border-bottom: 1px solid #2b3139; padding-bottom: 10px; }

        .services { display: grid; grid-template-columns: repeat(2, 1fr); gap: 15px; margin-bottom: 30px; }
        .service-card {
            background: #1e2329; border-radius: 8px; padding: 15px;
            border-left: 4px solid #848e9c;
        }
        .service-card.online { border-left-color: #0ecb81; }
        .service-card.offline { border-left-color: #f6465d; }
        .service-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        .service-name { font-weight: bold; font-size: 16px; }
        .service-port { color: #848e9c; font-size: 12px; }
        .service-status { display: flex; align-items: center; gap: 6px; font-size: 13px; }
        .status-dot { width: 8px; height: 8px; border-radius: 50%; }
        .status-dot.online { background: #0ecb81; }
        .status-dot.offline { background: #f6465d; }
        .service-stats { display: flex; gap: 15px; font-size: 12px; color: #848e9c; margin-bottom: 10px; padding: 8px; background: #2b3139; border-radius: 4px; }
        .stat-item { display: flex; gap: 5px; }
        .stat-value { color: #eaecef; font-weight: bold; }
        .stat-value.error { color: #f6465d; }

        .timeline { display: flex; gap: 2px; margin-bottom: 10px; align-items: center; }
        .timeline-label { font-size: 10px; color: #848e9c; margin-right: 8px; white-space: nowrap; }
        .timeline-bars { display: flex; gap: 1px; flex: 1; }
        .timeline-bar { width: 4px; height: 16px; border-radius: 1px; background: #2b3139; }
        .timeline-bar.online { background: #0ecb81; }
        .timeline-bar.offline { background: #f6465d; }
        .timeline-bar:hover { opacity: 0.7; }

        .data-section { margin-top: 10px; }
        .data-title { font-size: 12px; color: #f0b90b; margin-bottom: 5px; cursor: pointer; }
        .data-title:hover { text-decoration: underline; }
        .data-list { max-height: 400px; overflow-y: auto; font-size: 11px; background: #0b0e11; border-radius: 4px; padding: 8px; }
        .data-item { padding: 8px 0; border-bottom: 1px solid #2b3139; }
        .data-item:last-child { border-bottom: none; }
        .data-item .author { color: #f0b90b; font-weight: bold; }
        .data-item .author-name { color: #848e9c; font-size: 10px; margin-left: 4px; }
        .data-item .symbol { color: #0ecb81; font-weight: bold; }
        .data-item .content { color: #b7bdc6; margin: 6px 0; line-height: 1.5; white-space: pre-wrap; }
        .data-item .time { color: #848e9c; font-size: 10px; }
        .data-item.error { color: #f6465d; }
        .data-item .header { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
        .data-item .avatar { width: 32px; height: 32px; border-radius: 50%; flex-shrink: 0; }
        .data-item .type { background: #2b3139; padding: 1px 6px; border-radius: 4px; font-size: 10px; margin-left: 6px; }
        .data-item .type.newTweet { background: #1e3d2c; color: #0ecb81; }
        .data-item .type.reply { background: #1e2c3d; color: #5bc0de; }
        .data-item .type.retweet { background: #2c1e3d; color: #b05bde; }
        .data-item .type.quote { background: #3d1e2c; color: #de5b8a; }
        .data-item .images { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0; }
        .data-item .images img { max-width: 120px; max-height: 120px; border-radius: 6px; cursor: pointer; object-fit: cover; }
        .data-item .images img:hover { opacity: 0.8; }
        .data-item .videos video { max-width: 200px; border-radius: 6px; margin: 6px 0; }
        .data-item .ref-box { background: #2b3139; border-radius: 6px; padding: 8px; margin: 6px 0; border-left: 2px solid #848e9c; }
        .data-item .ref-header { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
        .data-item .ref-avatar { width: 20px; height: 20px; border-radius: 50%; }
        .data-item .ref-author { color: #f0b90b; font-size: 11px; }
        .data-item .ref-content { color: #b7bdc6; font-size: 11px; line-height: 1.4; }
        .data-item .ref-images img { max-width: 80px; max-height: 80px; }

        .error-section .error-header { display: flex; align-items: center; gap: 8px; cursor: pointer; }
        .error-section .error-toggle { background: #2b3139; border: none; color: #f6465d; padding: 2px 8px; border-radius: 4px; font-size: 11px; cursor: pointer; }
        .error-section .error-toggle:hover { background: #3d2c2c; }
        .error-section .error-list { display: none; margin-top: 8px; }
        .error-section .error-list.show { display: block; }

        .matches { background: #1e2329; border-radius: 8px; padding: 20px; }
        .match-item {
            background: #2b3139; border-radius: 8px; padding: 12px; margin-bottom: 8px;
            border-left: 3px solid #f0b90b;
        }
        .match-author { color: #f0b90b; font-weight: bold; font-size: 13px; }
        .match-content { margin: 8px 0; line-height: 1.4; font-size: 13px; color: #b7bdc6; }
        .match-tokens { display: flex; gap: 8px; flex-wrap: wrap; }
        .token-badge {
            background: #0ecb81; color: #fff; padding: 3px 10px;
            border-radius: 12px; font-size: 12px; font-weight: bold;
        }
        .no-data { color: #848e9c; text-align: center; padding: 20px; font-size: 13px; }

        .refresh-info { text-align: center; color: #848e9c; font-size: 12px; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🚀 Meme Tracker Dashboard</h1>

        <h2>服务状态</h2>
        <div class="services" id="services">
            <div class="service-card"><div class="service-name">加载中...</div></div>
        </div>

        <h2 style="display:flex;justify-content:space-between;align-items:center">
            数据库匹配记录
            <button onclick="openImportModal()" style="background:#F0B90B;color:#000;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;font-size:12px">+ 导入推文</button>
        </h2>
        <div class="matches" id="matches">
            <div class="no-data">加载中...</div>
        </div>

        <!-- 导入推文弹窗 -->
        <div id="importModal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:1000;justify-content:center;align-items:center">
            <div style="background:#1e2329;padding:24px;border-radius:8px;width:500px;max-width:90%">
                <h3 style="margin:0 0 16px 0;color:#eaecef">导入推文</h3>
                <div style="margin-bottom:12px">
                    <label style="display:block;color:#848e9c;margin-bottom:4px;font-size:12px">推文内容</label>
                    <textarea id="importContent" rows="4" style="width:100%;background:#2b3139;border:1px solid #363c45;border-radius:4px;padding:8px;color:#eaecef;resize:vertical" placeholder="输入推文内容..."></textarea>
                </div>
                <div style="margin-bottom:12px">
                    <label style="display:block;color:#848e9c;margin-bottom:4px;font-size:12px">关键词（逗号分隔）</label>
                    <input id="importKeywords" type="text" style="width:100%;background:#2b3139;border:1px solid #363c45;border-radius:4px;padding:8px;color:#eaecef" placeholder="关键词1, 关键词2, 关键词3">
                </div>
                <div style="margin-bottom:16px">
                    <label style="display:block;color:#848e9c;margin-bottom:4px;font-size:12px">最佳代币</label>
                    <input id="importToken" type="text" style="width:100%;background:#2b3139;border:1px solid #363c45;border-radius:4px;padding:8px;color:#eaecef" placeholder="代币名称">
                </div>
                <div style="display:flex;gap:12px;justify-content:flex-end">
                    <button onclick="closeImportModal()" style="background:#363c45;color:#eaecef;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">取消</button>
                    <button onclick="submitImport()" style="background:#F0B90B;color:#000;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">导入</button>
                </div>
            </div>
        </div>

        <!-- 注入推文弹窗 -->
        <div id="injectModal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:1000;justify-content:center;align-items:center">
            <div style="background:#1e2329;padding:24px;border-radius:8px;width:500px;max-width:90%">
                <h3 style="margin:0 0 16px 0;color:#eaecef">注入推文</h3>
                <div style="margin-bottom:12px">
                    <label style="display:block;color:#848e9c;margin-bottom:4px;font-size:12px">推文内容</label>
                    <textarea id="injectContent" rows="4" style="width:100%;background:#2b3139;border:1px solid #363c45;border-radius:4px;padding:8px;color:#eaecef;resize:vertical" placeholder="输入推文内容..."></textarea>
                </div>
                <div style="margin-bottom:12px">
                    <label style="display:block;color:#848e9c;margin-bottom:4px;font-size:12px">图片（可选）</label>
                    <input type="file" id="injectImage" accept="image/*" style="display:none" onchange="previewInjectImage(this)">
                    <div id="injectImagePreview" style="display:none;margin-bottom:8px;position:relative">
                        <img id="injectImageImg" style="max-width:200px;max-height:150px;border-radius:4px">
                        <button onclick="clearInjectImage()" style="position:absolute;top:4px;right:4px;background:#f6465d;color:#fff;border:none;width:20px;height:20px;border-radius:50%;cursor:pointer;font-size:12px">×</button>
                    </div>
                    <button onclick="document.getElementById('injectImage').click()" style="background:#2b3139;color:#848e9c;border:1px solid #363c45;padding:6px 12px;border-radius:4px;cursor:pointer;font-size:12px">+ 添加图片</button>
                </div>
                <div id="injectResult" style="display:none;margin-bottom:12px;padding:12px;background:#2b3139;border-radius:4px">
                    <div id="injectMsg" style="color:#eaecef"></div>
                </div>
                <div style="display:flex;gap:12px;justify-content:flex-end">
                    <button onclick="closeInjectModal()" style="background:#363c45;color:#eaecef;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">关闭</button>
                    <button id="injectBtn" onclick="submitInject()" style="background:#F0B90B;color:#000;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">注入</button>
                </div>
            </div>
        </div>

        <!-- 测试撮合弹窗 -->
        <div id="testMatchModal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:1000;justify-content:center;align-items:center">
            <div style="background:#1e2329;padding:24px;border-radius:8px;width:500px;max-width:90%">
                <h3 style="margin:0 0 16px 0;color:#eaecef">测试撮合</h3>
                <div style="margin-bottom:12px">
                    <label style="display:block;color:#848e9c;margin-bottom:4px;font-size:12px">推文内容</label>
                    <textarea id="testMatchContent" rows="4" style="width:100%;background:#2b3139;border:1px solid #363c45;border-radius:4px;padding:8px;color:#eaecef;resize:vertical" placeholder="输入推文内容..."></textarea>
                </div>
                <div id="testMatchResult" style="display:none;margin-bottom:12px;padding:12px;background:#2b3139;border-radius:4px">
                    <div id="testMatchKeywords" style="color:#eaecef"></div>
                </div>
                <div style="display:flex;gap:12px;justify-content:flex-end">
                    <button onclick="closeTestMatchModal()" style="background:#363c45;color:#eaecef;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">关闭</button>
                    <button id="testMatchBtn" onclick="submitTestMatch()" style="background:#F0B90B;color:#000;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">提取关键词</button>
                </div>
            </div>
        </div>

        <!-- 注入代币弹窗 -->
        <div id="injectTokenModal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:1000;justify-content:center;align-items:center">
            <div style="background:#1e2329;padding:24px;border-radius:8px;width:400px;max-width:90%">
                <h3 style="margin:0 0 16px 0;color:#eaecef">注入代币</h3>
                <div style="margin-bottom:12px">
                    <label style="display:block;color:#848e9c;margin-bottom:4px;font-size:12px">代币符号 *</label>
                    <input id="injectTokenSymbol" type="text" style="width:100%;background:#2b3139;border:1px solid #363c45;border-radius:4px;padding:8px;color:#eaecef" placeholder="如: DOGE, PEPE">
                </div>
                <div style="margin-bottom:12px">
                    <label style="display:block;color:#848e9c;margin-bottom:4px;font-size:12px">代币名称</label>
                    <input id="injectTokenName" type="text" style="width:100%;background:#2b3139;border:1px solid #363c45;border-radius:4px;padding:8px;color:#eaecef" placeholder="如: Dogecoin (可选)">
                </div>
                <div style="margin-bottom:12px">
                    <label style="display:block;color:#848e9c;margin-bottom:4px;font-size:12px">合约地址 (CA)</label>
                    <input id="injectTokenCA" type="text" style="width:100%;background:#2b3139;border:1px solid #363c45;border-radius:4px;padding:8px;color:#eaecef;font-family:monospace;font-size:11px" placeholder="如: 0x... 或 pump地址 (可选)">
                </div>
                <div id="injectTokenResult" style="display:none;margin-bottom:12px;padding:12px;background:#2b3139;border-radius:4px">
                    <div id="injectTokenMsg" style="color:#eaecef"></div>
                </div>
                <div style="display:flex;gap:12px;justify-content:flex-end">
                    <button onclick="closeInjectTokenModal()" style="background:#363c45;color:#eaecef;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">关闭</button>
                    <button id="injectTokenBtn" onclick="submitInjectToken()" style="background:#F0B90B;color:#000;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">注入</button>
                </div>
            </div>
        </div>

        <div class="refresh-info">每 5 秒自动刷新 | <span id="last-update">-</span></div>
    </div>

    <script>
        function formatTime(ts) {
            if (!ts) return '';
            const date = new Date(ts * 1000);
            const h = date.getHours().toString().padStart(2,'0');
            const m = date.getMinutes().toString().padStart(2,'0');
            const s = date.getSeconds().toString().padStart(2,'0');
            return `${h}:${m}:${s}`;
        }
        function escapeHtml(str) {
            if (!str) return '';
            return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
        }

        function copyText(text) {
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            document.body.removeChild(textarea);
            // 提示
            const toast = document.createElement('div');
            toast.textContent = '已复制: ' + (text.length > 20 ? text.slice(0,10) + '...' : text);
            toast.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#0ecb81;color:#fff;padding:8px 16px;border-radius:4px;font-size:12px;z-index:9999';
            document.body.appendChild(toast);
            setTimeout(() => toast.remove(), 1500);
        }

        let lastItemsJson = '';
        let tokenChainFilter = 'ALL';
        function setTokenChainFilter(chain) {
            tokenChainFilter = chain;
            lastItemsJson = '';  // 强制刷新
            refresh();
        }

        // 单独更新时间戳显示（不重新渲染DOM）
        function updateTimestamps(services) {
            services.forEach(s => {
                const d = s.data || {};
                if (s.name === 'news_service' || s.name === 'token_service') {
                    const lastFetchEl = document.getElementById(`${s.name}-last-fetch`);
                    const lastSuccessEl = document.getElementById(`${s.name}-last-success`);
                    if (lastFetchEl) lastFetchEl.textContent = formatTime(d.last_fetch);
                    if (lastSuccessEl) lastSuccessEl.textContent = formatTime(d.last_success);
                }
            });
        }

        // 提取稳定的列表数据用于比较（只比较id，忽略时间戳等动态字段）
        function getStableItems(services) {
            return services.map(s => {
                if (!s.recent) return null;
                const r = s.recent;
                // 只提取 id 列表，忽略动态字段
                if (s.name === 'news_service') {
                    return { ids: (r.items || []).map(i => i.id), errCount: (r.errors || []).length };
                } else if (s.name === 'token_service') {
                    return { ids: (r.items || []).map(i => `${i.chain}:${i.address}`), errCount: (r.errors || []).length };
                } else if (s.name === 'match_service') {
                    // 只取 id/content 标识
                    return {
                        attemptIds: (r.attempts || []).map(a => `${a.author}:${a.time}`),
                        matchIds: (r.matches || []).map(m => `${m.author}:${m.time}`),
                        pendingIds: (r.pending || []).map(p => p.content),
                        errCount: (r.errors || []).length
                    };
                } else if (s.name === 'tracker_service') {
                    return { ids: (r.records || []).map(rec => rec.id || `${rec.author}:${rec.time}`) };
                }
                return null;
            });
        }

        function renderServices(services) {
            // 比较稳定的列表数据
            const stableItems = getStableItems(services);
            const newItemsJson = JSON.stringify(stableItems);
            const needRenderLists = newItemsJson !== lastItemsJson;
            if (needRenderLists) {
                lastItemsJson = newItemsJson;
            }

            // 即使列表没变，也需要更新时间戳显示
            updateTimestamps(services);

            // 如果列表没变，不重新渲染DOM
            if (!needRenderLists) return;

            const container = document.getElementById('services');
            container.innerHTML = services.map(s => {
                const isOnline = s.status === 'online';
                const statusClass = isOnline ? 'online' : 'offline';
                const statusText = isOnline ? '运行中' : '离线';
                const d = s.data || {};
                const hasErrors = (d.errors || 0) > 0;

                // 统计栏
                let statsHtml = '';
                if (s.name === 'news_service') {
                    statsHtml = `<div class="stat-item">推文: <span class="stat-value">${d.total_news || 0}</span></div>
                                <div class="stat-item">最后获取: <span class="stat-value" id="news_service-last-fetch">${formatTime(d.last_fetch)}</span></div>
                                <div class="stat-item">最后成功: <span class="stat-value" id="news_service-last-success">${formatTime(d.last_success)}</span></div>
                                <div class="stat-item">错误: <span class="stat-value ${hasErrors?'error':''}">${d.errors || 0}</span></div>`;
                } else if (s.name === 'token_service') {
                    statsHtml = `<div class="stat-item">代币: <span class="stat-value">${d.total_tokens || 0}</span></div>
                                <div class="stat-item">最后获取: <span class="stat-value" id="token_service-last-fetch">${formatTime(d.last_fetch)}</span></div>
                                <div class="stat-item">最后成功: <span class="stat-value" id="token_service-last-success">${formatTime(d.last_success)}</span></div>
                                <div class="stat-item">错误: <span class="stat-value ${hasErrors?'error':''}">${d.errors || 0}</span></div>`;
                } else if (s.name === 'match_service') {
                    statsHtml = `<div class="stat-item">匹配: <span class="stat-value">${d.total_matches || 0}</span></div>
                                <div class="stat-item">缓存: <span class="stat-value">${d.tokens_cached || 0}</span></div>
                                <div class="stat-item">错误: <span class="stat-value ${hasErrors?'error':''}">${d.errors || 0}</span></div>`;
                } else if (s.name === 'tracker_service') {
                    statsHtml = `<div class="stat-item">记录: <span class="stat-value">${d.total_matches || 0}</span></div>
                                <div class="stat-item">追踪: <span class="stat-value">${d.total_tracked || 0}</span></div>
                                <div class="stat-item">待处理: <span class="stat-value">${d.pending_tasks || 0}</span></div>`;
                }

                // 数据列表
                let dataHtml = '';

                // tracker_service 显示匹配记录
                if (s.name === 'tracker_service') {
                    let records = s.recent?.records || [];
                    dataHtml += `<div class="data-section">
                        <div class="data-title">📊 匹配记录</div>`;
                    if (records.length > 0) {
                        dataHtml += `<div class="data-list">${records.map(r => {
                            // 追踪状态
                            let statusBadge;
                            const errMsgs = {'-1': '无交易对', '-2': 'HTTP错误', '-3': '网络异常'};
                            if (r.error_code) {
                                const errMsg = errMsgs[r.error_code] || '未知错误';
                                statusBadge = `<span style="background:#f6465d;color:#fff;padding:2px 6px;border-radius:4px;font-size:10px">${errMsg}</span>`;
                            } else if (r.track_count >= 3) {
                                statusBadge = '<span style="background:#02c076;color:#fff;padding:2px 6px;border-radius:4px;font-size:10px">已完成</span>';
                            } else if (r.track_count > 0) {
                                statusBadge = '<span style="background:#F0B90B;color:#000;padding:2px 6px;border-radius:4px;font-size:10px">追踪中</span>';
                            } else {
                                statusBadge = '<span style="background:#848e9c;color:#fff;padding:2px 6px;border-radius:4px;font-size:10px">等待</span>';
                            }
                            // 代币
                            let tokensHtml = r.tokens && r.tokens.length > 0
                                ? r.tokens.map(t => `<span class="symbol">${t.symbol}</span>`).join(', ')
                                : '<span style="color:#848e9c">无</span>';
                            return `<div class="data-item">
                                <div><span class="author">@${r.author}</span> ${statusBadge} <span class="time">${formatTime(r.time)}</span></div>
                                <div class="content">${r.content || '(无内容)'}</div>
                                <div style="color:#848e9c;font-size:10px">关键词: ${(r.keywords || []).join(', ') || '无'}</div>
                                <div style="font-size:10px">匹配代币: ${tokensHtml}</div>
                            </div>`;
                        }).join('')}</div>`;
                    } else {
                        dataHtml += `<div class="no-data" style="padding:10px;color:#848e9c">暂无记录</div>`;
                    }
                    dataHtml += `</div>`;
                }
                if (s.recent) {
                    if (s.name === 'news_service') {
                        let items = s.recent.items || [];
                        let errors = s.recent.errors || [];
                        // 始终显示标题和注入按钮
                        dataHtml += `<div class="data-section">
                            <div class="data-title" style="display:flex;justify-content:space-between;align-items:center">
                                <span>📰 最近推文</span>
                                <button onclick="openInjectModal()" style="background:#F0B90B;color:#000;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:10px">注入推文</button>
                            </div>`;
                        if (items.length > 0) {
                            dataHtml += `<div class="data-list">${items.map(r => {
                                    const proxyUrl = (url) => {
                                        if (!url) return '';
                                        if (url.startsWith('/local_image/')) return url;
                                        return '/proxy?url=' + encodeURIComponent(url);
                                    };

                                    // 头像
                                    const avatarHtml = r.avatar
                                        ? `<img class="avatar" src="${proxyUrl(r.avatar)}" onerror="this.style.display='none'">`
                                        : '<div class="avatar" style="background:#2b3139"></div>';

                                    // 图片
                                    let imagesHtml = '';
                                    if (r.images && r.images.length > 0) {
                                        imagesHtml = '<div class="images">' +
                                            r.images.map(url => `<img src="${proxyUrl(url)}" onclick="window.open('${proxyUrl(url)}')" onerror="this.style.display='none'">`).join('') +
                                            '</div>';
                                    }

                                    // 视频
                                    let videosHtml = '';
                                    if (r.videos && r.videos.length > 0) {
                                        videosHtml = '<div class="videos">' +
                                            r.videos.map(v => {
                                                const url = typeof v === 'object' ? (v.variants?.[0]?.url || '') : v;
                                                return url ? `<video src="${proxyUrl(url)}" controls></video>` : '';
                                            }).join('') +
                                            '</div>';
                                    }

                                    // 引用内容
                                    let refHtml = '';
                                    if (r.refContent && (r.type === 'reply' || r.type === 'retweet' || r.type === 'quote')) {
                                        const refAvatarHtml = r.refAvatar
                                            ? `<img class="ref-avatar" src="${proxyUrl(r.refAvatar)}" onerror="this.style.display='none'">`
                                            : '';
                                        let refImagesHtml = '';
                                        if (r.refImages && r.refImages.length > 0) {
                                            refImagesHtml = '<div class="images ref-images">' +
                                                r.refImages.map(url => `<img src="${proxyUrl(url)}" onclick="window.open('${proxyUrl(url)}')" onerror="this.style.display='none'">`).join('') +
                                                '</div>';
                                        }
                                        refHtml = `<div class="ref-box">
                                            <div class="ref-header">
                                                ${refAvatarHtml}
                                                <span class="ref-author">@${r.refAuthor} ${r.refAuthorName ? '(' + r.refAuthorName + ')' : ''}</span>
                                            </div>
                                            <div class="ref-content">${r.refContent}</div>
                                            ${refImagesHtml}
                                        </div>`;
                                    }

                                    return `<div class="data-item">
                                        <div class="header">
                                            ${avatarHtml}
                                            <div>
                                                <span class="author">@${r.author}</span>
                                                <span class="author-name">${r.authorName || ''}</span>
                                                <span class="type ${r.type}">${r.type || ''}</span>
                                            </div>
                                            <span class="time">${formatTime(r.time)}</span>
                                        </div>
                                        <div class="content">${r.content || (r.type === 'follow' ? '关注了 @' + (r.refAuthor || '') + (r.refAuthorName ? ' (' + r.refAuthorName + ')' : '') : (r.images && r.images.length > 0 ? '' : '(无内容)'))}</div>
                                        ${imagesHtml}
                                        ${videosHtml}
                                        ${refHtml}
                                    </div>`;
                                }).join('')}</div>`;
                        } else {
                            dataHtml += `<div class="no-data" style="padding:10px;color:#848e9c">暂无推文</div>`;
                        }
                        dataHtml += `</div>`;
                        if (errors.length > 0) {
                            const errId = 'err-news-' + Date.now();
                            dataHtml += `<div class="data-section error-section">
                                <div class="error-header" onclick="document.getElementById('${errId}').classList.toggle('show')">
                                    <span class="data-title" style="margin:0">⚠️ 错误 (${errors.length})</span>
                                    <button class="error-toggle">展开</button>
                                </div>
                                <div id="${errId}" class="error-list data-list">${errors.map(r =>
                                    `<div class="data-item error">${r.msg} <span class="time">${formatTime(r.time)}</span></div>`
                                ).join('')}</div>
                            </div>`;
                        }
                    } else if (s.name === 'token_service') {
                        let items = s.recent.items || [];
                        let errors = s.recent.errors || [];
                        // 根据选中的链过滤
                        const filteredItems = tokenChainFilter === 'ALL' ? items : items.filter(r => r.chain === tokenChainFilter);
                        dataHtml += `<div class="data-section">
                            <div class="data-title" style="display:flex;justify-content:space-between;align-items:center">
                                <div style="display:flex;align-items:center;gap:8px">
                                    <span>🪙 最近代币</span>
                                    <div style="display:flex;gap:2px">
                                        <button onclick="setTokenChainFilter('ALL')" style="background:${tokenChainFilter==='ALL'?'#F0B90B':'#363c45'};color:${tokenChainFilter==='ALL'?'#000':'#eaecef'};border:none;padding:2px 6px;border-radius:3px;cursor:pointer;font-size:9px">全部</button>
                                        <button onclick="setTokenChainFilter('BSC')" style="background:${tokenChainFilter==='BSC'?'#F0B90B':'#363c45'};color:${tokenChainFilter==='BSC'?'#000':'#eaecef'};border:none;padding:2px 6px;border-radius:3px;cursor:pointer;font-size:9px">BSC</button>
                                        <button onclick="setTokenChainFilter('SOL')" style="background:${tokenChainFilter==='SOL'?'#9945FF':'#363c45'};color:#fff;border:none;padding:2px 6px;border-radius:3px;cursor:pointer;font-size:9px">SOL</button>
                                    </div>
                                </div>
                                <button onclick="openInjectTokenModal()" style="background:#F0B90B;color:#000;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:10px">注入代币</button>
                            </div>`;
                        if (filteredItems.length > 0) {
                            dataHtml += `<div class="data-list">${filteredItems.map(r => {
                                    const chainBadge = r.chain === 'SOL' ? '<span style="background:#9945FF;color:#fff;padding:1px 4px;border-radius:3px;font-size:9px;margin-right:4px">SOL</span>' : (r.chain === 'TEST' ? '<span style="background:#848e9c;color:#fff;padding:1px 4px;border-radius:3px;font-size:9px;margin-right:4px">TEST</span>' : '<span style="background:#F0B90B;color:#000;padding:1px 4px;border-radius:3px;font-size:9px;margin-right:4px">BSC</span>');
                                    const shortCa = r.address ? (r.address.length > 16 ? r.address.slice(0,8) + '...' + r.address.slice(-6) : r.address) : '';
                                    const caHtml = shortCa ? `<span style="color:#848e9c;font-size:9px;font-family:monospace;margin-left:6px;cursor:pointer" title="点击复制: ${r.address}" onclick="copyText('${r.address}')">${shortCa}</span>` : '';
                                    return `<div class="data-item">${chainBadge}<span class="symbol" style="cursor:pointer" title="点击复制" onclick="copyText('${r.symbol}')">${r.symbol}</span> ${r.name}${caHtml} <span class="time">${formatTime(r.time/1000)} | MC:${r.marketCap} H:${r.holders}</span></div>`;
                                }).join('')}</div>`;
                        } else {
                            dataHtml += `<div class="no-data" style="padding:10px;color:#848e9c">暂无代币</div>`;
                        }
                        dataHtml += `</div>`;
                        if (errors.length > 0) {
                            const errId = 'err-token-' + Date.now();
                            dataHtml += `<div class="data-section error-section">
                                <div class="error-header" onclick="document.getElementById('${errId}').classList.toggle('show')">
                                    <span class="data-title" style="margin:0">⚠️ 错误 (${errors.length})</span>
                                    <button class="error-toggle">展开</button>
                                </div>
                                <div id="${errId}" class="error-list data-list">${errors.map(r =>
                                    `<div class="data-item error">${r.msg} <span class="time">${formatTime(r.time)}</span></div>`
                                ).join('')}</div>
                            </div>`;
                        }
                    } else if (s.name === 'match_service') {
                        let attemptList = s.recent.attempts || [];
                        let matchList = s.recent.matches || [];
                        let errorList = s.recent.errors || [];
                        let pendingList = s.recent.pending || [];
                        // 构建 pending 查找表
                        const pendingMap = {};
                        pendingList.forEach(p => { pendingMap[p.content] = p; });
                        // 测试撮合按钮
                        dataHtml += `<div class="data-section">
                            <div class="data-title" style="display:flex;justify-content:space-between;align-items:center">
                                <span>🔍 撮合尝试</span>
                                <button onclick="openTestMatchModal()" style="background:#F0B90B;color:#000;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:10px">测试撮合</button>
                            </div>`;
                        if (attemptList.length > 0) {
                            dataHtml += `<div class="data-list">${attemptList.map(r => {
                                // 检测状态
                                const pendingInfo = pendingMap[r.content];
                                let statusBadge;
                                if (pendingInfo) {
                                    const now = Math.floor(Date.now() / 1000);
                                    const remaining = Math.max(0, pendingInfo.expire_time - now);
                                    const mins = Math.floor(remaining / 60);
                                    const secs = remaining % 60;
                                    statusBadge = `<span style="background:#F0B90B;color:#000;padding:2px 6px;border-radius:4px;font-size:10px;margin-left:6px">检测中 ${mins}:${secs.toString().padStart(2,'0')}</span>`;
                                } else {
                                    statusBadge = `<span style="background:#02c076;color:#fff;padding:2px 6px;border-radius:4px;font-size:10px;margin-left:6px">已完成</span>`;
                                }
                                let matchStatus = r.matched > 0 ? `<span class="symbol">✓ ${r.matched}个匹配</span>` : '<span style="color:#848e9c">无匹配</span>';
                                let keywordsStr = r.keywords && r.keywords.length > 0 ? r.keywords.join(', ') : '(无关键词)';
                                let windowTokensStr = r.window_tokens && r.window_tokens.length > 0 ? r.window_tokens.join(', ') : '(无)';
                                return `<div class="data-item">
                                    <div><span class="author">@${r.author}</span> ${matchStatus} ${statusBadge} <span class="time">${formatTime(r.time)}</span></div>
                                    <div class="content">${escapeHtml(r.content)}</div>
                                    <div style="color:#848e9c;font-size:10px">关键词: ${escapeHtml(keywordsStr)}</div>
                                    <div style="color:#848e9c;font-size:10px">窗口代币(${r.tokens_in_window}): ${escapeHtml(windowTokensStr)}</div>
                                </div>`;
                            }).join('')}</div>`;
                        } else {
                            dataHtml += `<div class="no-data" style="padding:10px;color:#848e9c">暂无撮合尝试</div>`;
                        }
                        dataHtml += `</div>`;
                        if (matchList.length > 0) {
                            dataHtml += `<div class="data-section">
                                <div class="data-title">🎯 成功匹配</div>
                                <div class="data-list">${matchList.map(r =>
                                    `<div class="data-item"><span class="author">@${r.author}</span> → <span class="symbol">${r.tokens.join(', ')}</span> <span class="time">${formatTime(r.time)}</span></div>`
                                ).join('')}</div>
                            </div>`;
                        }
                        if (errorList.length > 0) {
                            const errId = 'err-match-' + Date.now();
                            dataHtml += `<div class="data-section error-section">
                                <div class="error-header" onclick="document.getElementById('${errId}').classList.toggle('show')">
                                    <span class="data-title" style="margin:0">⚠️ 错误 (${errorList.length})</span>
                                    <button class="error-toggle">展开</button>
                                </div>
                                <div id="${errId}" class="error-list data-list">${errorList.map(r =>
                                    `<div class="data-item error">${r.msg} <span class="time">${formatTime(r.time)}</span></div>`
                                ).join('')}</div>
                            </div>`;
                        }
                    }
                }

                // 时间线
                let timelineHtml = '';
                if (s.history && s.history.length > 0) {
                    const bars = s.history.map(h =>
                        `<div class="timeline-bar ${h ? 'online' : 'offline'}" title="${h ? '正常' : '异常'}"></div>`
                    ).join('');
                    timelineHtml = `<div class="timeline">
                        <span class="timeline-label">5分钟</span>
                        <div class="timeline-bars">${bars}</div>
                        <span class="timeline-label">现在</span>
                    </div>`;
                }

                // 离线时显示启动按钮
                const startBtn = !isOnline
                    ? `<button onclick="startService('${s.name}')" style="background:#0ecb81;color:#fff;border:none;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:11px;margin-left:8px">启动</button>`
                    : '';

                return `<div class="service-card ${statusClass}">
                    <div class="service-header">
                        <div>
                            <span class="service-name">${s.desc}</span>
                            <span class="service-port">:${s.port}</span>
                        </div>
                        <div class="service-status">
                            <div class="status-dot ${statusClass}"></div>
                            <span>${statusText}</span>
                            ${startBtn}
                        </div>
                    </div>
                    ${timelineHtml}
                    <div class="service-stats">${statsHtml}</div>
                    ${dataHtml}
                </div>`;
            }).join('');
        }

        function renderMatches(data) {
            const container = document.getElementById('matches');
            if (!data || data.length === 0) {
                container.innerHTML = '<div class="no-data">暂无匹配数据 (等待撮合服务产生匹配)</div>';
                return;
            }

            const proxyUrl = (url) => {
                if (!url) return '';
                if (url.startsWith('/local_image/')) return url;
                return '/proxy?url=' + encodeURIComponent(url);
            };

            container.innerHTML = data.map(m => {
                // 最佳代币
                const bestTokensHtml = m.best_tokens && m.best_tokens.length > 0
                    ? m.best_tokens.map(t => `<span class="token-badge">${t.token_symbol}</span>`).join('')
                    : (m.matched_tokens && m.matched_tokens.length > 0
                        ? m.matched_tokens.map(t => `<span class="token-badge" style="background:#848e9c">${t.token_symbol}</span>`).join('')
                        : '<span style="color:#848e9c">等待追踪...</span>');

                // 头像
                const avatarHtml = m.avatar
                    ? `<img class="avatar" src="${proxyUrl(m.avatar)}" style="width:40px;height:40px;border-radius:50%;margin-right:10px" onerror="this.style.display='none'">`
                    : '<div style="width:40px;height:40px;border-radius:50%;background:#2b3139;margin-right:10px"></div>';

                // 图片
                let imagesHtml = '';
                if (m.images && m.images.length > 0) {
                    imagesHtml = '<div style="display:flex;flex-wrap:wrap;gap:6px;margin:8px 0">' +
                        m.images.map(url => `<img src="${proxyUrl(url)}" style="max-width:150px;max-height:150px;border-radius:6px;cursor:pointer" onclick="window.open('${proxyUrl(url)}')" onerror="this.style.display='none'">`).join('') +
                        '</div>';
                }

                // 关键词
                const keywordsHtml = m.keywords && m.keywords.length > 0
                    ? `<div style="font-size:11px;color:#848e9c;margin-top:6px">关键词: ${m.keywords.join(', ')}</div>`
                    : '';

                return `<div class="match-item">
                    <div style="display:flex;align-items:flex-start">
                        ${avatarHtml}
                        <div style="flex:1">
                            <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
                                <span class="match-author">@${m.author || 'Unknown'}</span>
                                <span style="color:#848e9c;font-size:12px">${m.authorName || ''}</span>
                                <span style="color:#848e9c;font-size:11px">${formatTime(m.time)}</span>
                            </div>
                            <div class="match-content">${m.content || ''}</div>
                            ${imagesHtml}
                            ${keywordsHtml}
                        </div>
                    </div>
                    <div style="margin-top:10px;padding-top:10px;border-top:1px solid #2b3139">
                        <span style="color:#f0b90b;font-size:12px;margin-right:8px">🎯 最佳代币:</span>
                        <div class="match-tokens" style="display:inline">${bestTokensHtml}</div>
                    </div>
                </div>`;
            }).join('');
        }

        async function refresh() {
            try {
                // 保存所有 data-list 的滚动位置
                const scrollPositions = {};
                document.querySelectorAll('.data-list').forEach((el, i) => {
                    scrollPositions[i] = el.scrollTop;
                });

                const statusResp = await fetch('api/status');
                const statusData = await statusResp.json();
                renderServices(statusData);

                // 恢复滚动位置
                document.querySelectorAll('.data-list').forEach((el, i) => {
                    if (scrollPositions[i]) el.scrollTop = scrollPositions[i];
                });

                const matchResp = await fetch('api/matches');
                const matchData = await matchResp.json();
                renderMatches(matchData);

                document.getElementById('last-update').textContent = new Date().toLocaleTimeString('zh-CN');
            } catch (e) {
                console.error('Refresh error:', e);
            }
        }

        // 导入推文弹窗
        function openImportModal() {
            document.getElementById('importModal').style.display = 'flex';
            document.getElementById('importContent').value = '';
            document.getElementById('importKeywords').value = '';
            document.getElementById('importToken').value = '';
        }

        function closeImportModal() {
            document.getElementById('importModal').style.display = 'none';
        }

        async function submitImport() {
            const content = document.getElementById('importContent').value.trim();
            const keywordsStr = document.getElementById('importKeywords').value.trim();
            const token = document.getElementById('importToken').value.trim();

            if (!content || !keywordsStr || !token) {
                alert('请填写所有字段');
                return;
            }

            const keywords = keywordsStr.split(',').map(k => k.trim()).filter(k => k);

            try {
                const resp = await fetch('api/import', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        tweet_content: content,
                        keywords: keywords,
                        best_token: token
                    })
                });
                const data = await resp.json();
                if (data.success) {
                    closeImportModal();
                    refresh();
                } else {
                    alert('导入失败: ' + (data.error || '未知错误'));
                }
            } catch (e) {
                alert('导入失败: ' + e.message);
            }
        }

        // 点击弹窗外部关闭
        document.getElementById('importModal').addEventListener('click', function(e) {
            if (e.target === this) closeImportModal();
        });

        // 注入推文弹窗
        let injectImageData = null;

        function openInjectModal() {
            document.getElementById('injectModal').style.display = 'flex';
            document.getElementById('injectContent').value = '';
            document.getElementById('injectResult').style.display = 'none';
            document.getElementById('injectBtn').textContent = '注入';
            clearInjectImage();
        }

        function closeInjectModal() {
            document.getElementById('injectModal').style.display = 'none';
        }

        function previewInjectImage(input) {
            if (input.files && input.files[0]) {
                const reader = new FileReader();
                reader.onload = function(e) {
                    injectImageData = e.target.result;
                    document.getElementById('injectImageImg').src = injectImageData;
                    document.getElementById('injectImagePreview').style.display = 'block';
                };
                reader.readAsDataURL(input.files[0]);
            }
        }

        function clearInjectImage() {
            injectImageData = null;
            document.getElementById('injectImage').value = '';
            document.getElementById('injectImagePreview').style.display = 'none';
        }

        async function submitInject() {
            const content = document.getElementById('injectContent').value.trim();
            if (!content && !injectImageData) {
                alert('请输入推文内容或上传图片');
                return;
            }

            const btn = document.getElementById('injectBtn');
            btn.textContent = '注入中...';
            btn.disabled = true;

            try {
                const payload = { content: content };
                if (injectImageData) {
                    payload.image = injectImageData;
                }
                const resp = await fetch('api/inject', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const data = await resp.json();

                document.getElementById('injectResult').style.display = 'block';
                if (data.success) {
                    document.getElementById('injectMsg').innerHTML =
                        '<span style="color:#02c076">已注入推文流，等待撮合...</span>' +
                        '<br><span style="color:#848e9c;font-size:11px;margin-top:8px;display:block">查看 match_service 状态获取结果</span>';
                    setTimeout(() => { refresh(); }, 2000);
                } else {
                    document.getElementById('injectMsg').innerHTML = '<span style="color:#f6465d">注入失败: ' + (data.error || '未知错误') + '</span>';
                }
            } catch (e) {
                document.getElementById('injectResult').style.display = 'block';
                document.getElementById('injectMsg').innerHTML = '<span style="color:#f6465d">错误: ' + e.message + '</span>';
            }

            btn.textContent = '再次注入';
            btn.disabled = false;
        }

        document.getElementById('injectModal').addEventListener('click', function(e) {
            if (e.target === this) closeInjectModal();
        });

        // 测试撮合弹窗
        function openTestMatchModal() {
            document.getElementById('testMatchModal').style.display = 'flex';
            document.getElementById('testMatchContent').value = '';
            document.getElementById('testMatchResult').style.display = 'none';
            document.getElementById('testMatchBtn').textContent = '提取关键词';
        }

        function closeTestMatchModal() {
            document.getElementById('testMatchModal').style.display = 'none';
        }

        async function submitTestMatch() {
            const content = document.getElementById('testMatchContent').value.trim();
            if (!content) {
                alert('请输入推文内容');
                return;
            }

            const btn = document.getElementById('testMatchBtn');
            btn.textContent = '提取中...';
            btn.disabled = true;

            try {
                const resp = await fetch('api/extract', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ text: content })
                });
                const data = await resp.json();

                document.getElementById('testMatchResult').style.display = 'block';
                if (data.keywords && data.keywords.length > 0) {
                    document.getElementById('testMatchKeywords').innerHTML =
                        '<div style="color:#848e9c;margin-bottom:8px">提取关键词:</div>' +
                        data.keywords.map(k => `<span style="background:#0ecb81;color:#fff;padding:4px 12px;border-radius:12px;margin-right:8px;font-weight:bold">${k}</span>`).join('') +
                        `<div style="color:#848e9c;font-size:10px;margin-top:12px">使用API: ${data.api || 'unknown'}</div>`;
                } else {
                    document.getElementById('testMatchKeywords').innerHTML = '<span style="color:#848e9c">未提取到关键词</span>';
                }
            } catch (e) {
                document.getElementById('testMatchResult').style.display = 'block';
                document.getElementById('testMatchKeywords').innerHTML = '<span style="color:#f6465d">错误: ' + e.message + '</span>';
            }

            btn.textContent = '再次提取';
            btn.disabled = false;
        }

        document.getElementById('testMatchModal').addEventListener('click', function(e) {
            if (e.target === this) closeTestMatchModal();
        });

        // 注入代币弹窗
        function openInjectTokenModal() {
            document.getElementById('injectTokenModal').style.display = 'flex';
            document.getElementById('injectTokenSymbol').value = '';
            document.getElementById('injectTokenName').value = '';
            document.getElementById('injectTokenCA').value = '';
            document.getElementById('injectTokenResult').style.display = 'none';
            document.getElementById('injectTokenBtn').textContent = '注入';
        }

        function closeInjectTokenModal() {
            document.getElementById('injectTokenModal').style.display = 'none';
        }

        async function submitInjectToken() {
            const symbol = document.getElementById('injectTokenSymbol').value.trim();
            const name = document.getElementById('injectTokenName').value.trim();
            const ca = document.getElementById('injectTokenCA').value.trim();

            if (!symbol) {
                alert('请输入代币符号');
                return;
            }

            const btn = document.getElementById('injectTokenBtn');
            btn.textContent = '注入中...';
            btn.disabled = true;

            try {
                const resp = await fetch('api/inject_token', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ symbol: symbol, name: name, ca: ca })
                });
                const data = await resp.json();

                document.getElementById('injectTokenResult').style.display = 'block';
                if (data.success) {
                    let msg = '<span style="color:#02c076">代币已注入</span>' +
                        '<br><span style="color:#848e9c;font-size:11px;margin-top:4px;display:block">符号: ' + data.token.tokenSymbol + '</span>';
                    if (data.token.tokenAddress) {
                        msg += '<br><span style="color:#848e9c;font-size:10px;font-family:monospace;word-break:break-all">CA: ' + data.token.tokenAddress + '</span>';
                    }
                    document.getElementById('injectTokenMsg').innerHTML = msg;
                    setTimeout(() => { refresh(); }, 1000);
                } else {
                    document.getElementById('injectTokenMsg').innerHTML = '<span style="color:#f6465d">注入失败: ' + (data.error || '未知错误') + '</span>';
                }
            } catch (e) {
                document.getElementById('injectTokenResult').style.display = 'block';
                document.getElementById('injectTokenMsg').innerHTML = '<span style="color:#f6465d">错误: ' + e.message + '</span>';
            }

            btn.textContent = '再次注入';
            btn.disabled = false;
        }

        document.getElementById('injectTokenModal').addEventListener('click', function(e) {
            if (e.target === this) closeInjectTokenModal();
        });

        // 启动服务
        async function startService(serviceName) {
            try {
                const resp = await fetch('api/start_service', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ service: serviceName })
                });
                const data = await resp.json();
                if (data.success) {
                    // 2秒后刷新状态
                    setTimeout(() => { refresh(); }, 2000);
                } else {
                    alert('启动失败: ' + (data.error || '未知错误'));
                }
            } catch (e) {
                alert('启动失败: ' + e.message);
            }
        }

        refresh();
        setInterval(refresh, 5000);
    </script>
</body>
</html>
"""


def get_service_status(service):
    """获取服务状态"""
    try:
        resp = requests.get(f"{service['url']}/status", timeout=2, proxies={'http': None, 'https': None})
        if resp.status_code == 200:
            return {'status': 'online', 'data': resp.json()}
    except:
        pass
    return {'status': 'offline', 'data': None}


@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


def get_recent_data(service):
    """获取服务的最近数据"""
    try:
        resp = requests.get(f"{service['url']}/recent", timeout=2, proxies={'http': None, 'https': None})
        if resp.status_code == 200:
            return resp.json()
    except:
        pass
    return None


@app.route('/api/status')
def api_status():
    results = []
    for service in get_services():
        status = get_service_status(service)
        recent = get_recent_data(service)
        name = service['name']

        # 记录状态历史：比较 errors 计数
        current_errors = status['data'].get('errors', 0) if status['data'] else 0
        has_new_error = current_errors > last_errors[name]
        last_errors[name] = current_errors

        # True = 正常(绿), False = 有新错误(红)
        status_history[name].append(not has_new_error)
        history = list(status_history[name])

        results.append({
            'name': name,
            'desc': service['desc'],
            'port': service['port'],
            'status': status['status'],
            'data': status['data'],
            'recent': recent,
            'history': history
        })
    return jsonify(results)


@app.route('/api/matches')
def api_matches():
    try:
        resp = requests.get(f'{config.get_service_url("tracker")}/query?limit=10', timeout=5, proxies={'http': None, 'https': None})
        if resp.status_code == 200:
            return jsonify(resp.json())
    except:
        pass
    return jsonify([])


@app.route('/api/import', methods=['POST'])
def api_import():
    """导入推文到数据库"""
    from flask import request
    try:
        data = request.json
        resp = requests.post(
            f'{config.get_service_url("tracker")}/best_practices',
            json=data,
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'success': False, 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/inject', methods=['POST'])
def api_inject():
    """注入推文到流中测试撮合"""
    from flask import request
    try:
        data = request.json
        resp = requests.post(
            f'{config.get_service_url("news")}/inject',
            json=data,
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'success': False, 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/extract', methods=['POST'])
def api_extract():
    """测试关键词提取"""
    from flask import request
    try:
        data = request.json
        resp = requests.post(
            f'{config.get_service_url("match")}/extract_keywords',
            json=data,
            timeout=30,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'keywords': [], 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'keywords': [], 'error': str(e)}), 500


@app.route('/api/inject_token', methods=['POST'])
def api_inject_token():
    """注入代币到代币发现服务"""
    from flask import request
    try:
        data = request.json
        resp = requests.post(
            f'{config.get_service_url("token")}/inject',
            json=data,
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'success': False, 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/start_service', methods=['POST'])
def api_start_service():
    """启动服务"""
    import subprocess
    import os

    data = request.json
    service_name = data.get('service', '')

    service_map = {
        'news_service': 'news_service.py',
        'token_service': 'token_service.py',
        'tracker_service': 'tracker_service.py',
        'match_service': 'match_service.py'
    }

    if service_name not in service_map:
        return jsonify({'success': False, 'error': '未知服务'}), 400

    script = service_map[service_name]
    script_path = os.path.join(os.path.dirname(__file__), script)
    log_path = f'/tmp/{service_name}.log'

    try:
        # 启动服务
        subprocess.Popen(
            ['python3', script_path],
            stdout=open(log_path, 'w'),
            stderr=subprocess.STDOUT,
            cwd=os.path.dirname(__file__),
            start_new_session=True
        )
        return jsonify({'success': True, 'message': f'{service_name} 启动中...'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/local_image/<filename>')
def local_image(filename):
    """提供本地注入的图片"""
    import os
    image_dir = os.path.join(os.path.dirname(__file__), 'image_cache')
    filepath = os.path.join(image_dir, filename)
    if os.path.exists(filepath):
        return send_file(filepath)
    return '', 404


@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


def get_extension(content_type, url):
    """根据content-type或url获取文件扩展名"""
    if 'png' in content_type or url.endswith('.png'):
        return '.png'
    if 'gif' in content_type or url.endswith('.gif'):
        return '.gif'
    if 'webp' in content_type or url.endswith('.webp'):
        return '.webp'
    if 'mp4' in content_type or url.endswith('.mp4'):
        return '.mp4'
    if 'video' in content_type:
        return '.mp4'
    return '.jpg'


@app.route('/proxy')
def proxy_media():
    """代理获取图片/视频，下载到本地缓存"""
    media_url = request.args.get('url', '')
    if not media_url:
        return '', 404

    # 生成缓存文件名
    cache_key = hashlib.md5(media_url.encode()).hexdigest()

    # 查找已缓存的文件
    for ext in ['.jpg', '.png', '.gif', '.webp', '.mp4']:
        cache_path = os.path.join(CACHE_DIR, cache_key + ext)
        if os.path.exists(cache_path):
            return send_file(cache_path)

    try:
        media_headers = {
            'accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,video/*,*/*;q=0.8',
            'referer': 'https://web3.binance.com/',
            'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }
        resp = requests.get(media_url, headers=media_headers, proxies=config.PROXIES, timeout=30)
        if resp.status_code == 200:
            content_type = resp.headers.get('content-type', 'image/jpeg')
            ext = get_extension(content_type, media_url)
            cache_path = os.path.join(CACHE_DIR, cache_key + ext)
            # 保存到本地
            with open(cache_path, 'wb') as f:
                f.write(resp.content)
            return send_file(cache_path)
    except Exception as e:
        print(f"媒体下载失败: {e}", flush=True)
    return '', 404


if __name__ == "__main__":
    port = config.get_port('dashboard')
    print(f"控制面板启动: http://127.0.0.1:{port}", flush=True)
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
