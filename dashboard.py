"""
控制面板服务 (端口 5000)
- 显示所有服务状态
- 显示匹配数据
"""
import requests
import time
import hashlib
import os
import json
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
    'alpha_call_service': deque(maxlen=MAX_HISTORY),
    'trade_service': deque(maxlen=MAX_HISTORY),
}
# 上一次的 errors 计数
last_errors = {
    'news_service': 0,
    'token_service': 0,
    'tracker_service': 0,
    'match_service': 0,
    'alpha_call_service': 0,
    'trade_service': 0,
}

def get_services():
    """动态获取服务列表，确保使用正确的端口"""
    return [
        {'name': 'news_service', 'url': config.get_service_url('news'), 'desc': '推文发现', 'port': config.get_port('news')},
        {'name': 'token_service', 'url': config.get_service_url('token'), 'desc': '代币发现', 'port': config.get_port('token')},
        {'name': 'tracker_service', 'url': config.get_service_url('tracker'), 'desc': '代币跟踪', 'port': config.get_port('tracker')},
        {'name': 'match_service', 'url': config.get_service_url('match'), 'desc': '代币撮合', 'port': config.get_port('match')},
        {'name': 'alpha_call_service', 'url': config.get_service_url('alpha_call'), 'desc': 'Alpha Call', 'port': config.get_port('alpha_call')},
        {'name': 'trade_service', 'url': config.get_service_url('trade'), 'desc': '自动交易', 'port': config.get_port('trade')},
    ]

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
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

        /* ==================== 移动端适配 ==================== */
        @media (max-width: 768px) {
            body { padding: 12px; }
            h1 { font-size: 20px; margin-bottom: 20px; }
            h2 { font-size: 16px; flex-direction: column; align-items: flex-start !important; gap: 12px; }
            h2 > div { width: 100%; flex-wrap: wrap; }
            .container { max-width: 100%; }

            /* 服务卡片单列 */
            .services {
                grid-template-columns: 1fr !important;
                gap: 12px;
            }
            .services > div[style*="grid-template-rows"] {
                display: flex !important;
                flex-direction: column;
                gap: 12px;
            }
            .service-card { padding: 12px; }
            .service-header { flex-wrap: wrap; gap: 8px; }
            .service-name { font-size: 14px; }
            .service-stats {
                flex-wrap: wrap;
                gap: 8px;
                padding: 6px;
            }
            .stat-item { font-size: 11px; }

            /* 时间线压缩 */
            .timeline { flex-wrap: wrap; }
            .timeline-bars { min-width: 0; overflow-x: auto; }
            .timeline-bar { width: 3px; height: 14px; flex-shrink: 0; }

            /* 数据列表 */
            .data-list { max-height: 250px; padding: 6px; }
            .data-item { padding: 6px 0; }
            .data-item .content { font-size: 12px; }
            .data-item .images img { max-width: 60px; max-height: 60px; }
            .data-item .avatar { width: 28px; height: 28px; }
            .data-item .header { flex-wrap: wrap; }

            /* 匹配区域 */
            .matches { padding: 12px; }
            .match-item { padding: 10px; }
            .match-content { font-size: 12px; }
            .token-badge { padding: 4px 8px; font-size: 11px; }

            /* 弹窗全宽 */
            #importModal > div,
            #injectModal > div,
            #testMatchModal > div,
            #injectTokenModal > div,
            #blacklistModal > div,
            #exclusiveBlacklistModal > div,
            #promptModal > div {
                width: calc(100% - 24px) !important;
                max-width: none !important;
                margin: 12px;
                padding: 16px;
                max-height: 85vh;
                overflow-y: auto;
            }
            #promptModal > div { width: calc(100% - 24px) !important; }

            /* 弹窗内元素 */
            textarea, input[type="text"] { font-size: 16px !important; } /* 防止iOS缩放 */

            /* 按钮触控优化 */
            button {
                min-height: 40px;
                padding: 10px 14px !important;
                font-size: 13px !important;
            }
            .data-title {
                padding: 8px 0;
                font-size: 13px;
            }

            /* 最佳实践按钮组 */
            h2[style*="justify-content"] > div {
                display: flex;
                flex-wrap: wrap;
                gap: 8px !important;
            }
            h2[style*="justify-content"] > div button {
                flex: 1;
                min-width: 80px;
            }
        }

        /* 超小屏幕 (< 400px) */
        @media (max-width: 400px) {
            body { padding: 8px; }
            h1 { font-size: 18px; }
            .service-stats { font-size: 10px; }
            .timeline-bar { width: 2px; height: 12px; }
            .data-item .images img { max-width: 50px; max-height: 50px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🚀 Meme Tracker Dashboard</h1>

        <h2>服务状态</h2>
        <div class="services">
            <div id="news_service_card"></div>
            <div id="token_service_card"></div>
            <div style="display:grid;grid-template-rows:auto 1fr;gap:15px">
                <div id="match_service_card"></div>
                <div id="trade_service_card"></div>
            </div>
            <div style="display:grid;grid-template-rows:auto 1fr;gap:15px">
                <div id="alpha_call_service_card" style="max-height:500px;overflow:hidden"></div>
                <div id="tracker_service_card"></div>
            </div>
        </div>

        <h2 style="display:flex;justify-content:space-between;align-items:center">
            最佳实践
            <div style="display:flex;gap:8px">
                <button onclick="exportRecords()" style="background:#0ecb81;color:#fff;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;font-size:12px">📥 导出CSV</button>
                <button onclick="exportAnalysis()" style="background:#1DA1F2;color:#fff;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;font-size:12px">📊 导出分析</button>
                <button id="deleteBtn" onclick="toggleDeleteMode()" style="background:#363c45;color:#eaecef;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;font-size:12px">移除</button>
                <button id="confirmDeleteBtn" onclick="confirmDelete()" style="display:none;background:#f6465d;color:#fff;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;font-size:12px">确认移除</button>
                <button id="cancelDeleteBtn" onclick="cancelDeleteMode()" style="display:none;background:#363c45;color:#eaecef;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;font-size:12px">取消</button>
                <button onclick="openImportModal()" style="background:#F0B90B;color:#000;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;font-size:12px">+ 导入推文</button>
            </div>
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

        <!-- 黑名单管理弹窗 -->
        <div id="blacklistModal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:1000;justify-content:center;align-items:center">
            <div style="background:#1e2329;padding:24px;border-radius:8px;width:450px;max-width:90%">
                <h3 style="margin:0 0 16px 0;color:#f6465d">🚫 代币黑名单</h3>
                <p style="color:#848e9c;font-size:12px;margin-bottom:12px">添加到黑名单的代币名称将不会被AI提取为关键词</p>
                <div style="margin-bottom:12px;display:flex;gap:8px">
                    <input id="blacklistInput" type="text" style="flex:1;background:#2b3139;border:1px solid #363c45;border-radius:4px;padding:8px;color:#eaecef" placeholder="输入代币名称，如: pepe, doge">
                    <button onclick="addToBlacklist()" style="background:#f6465d;color:#fff;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;white-space:nowrap">添加</button>
                </div>
                <div id="blacklistList" style="max-height:300px;overflow-y:auto;background:#0b0e11;border-radius:4px;padding:8px">
                    <div style="color:#848e9c;text-align:center;padding:20px">加载中...</div>
                </div>
                <div style="display:flex;gap:12px;justify-content:flex-end;margin-top:16px">
                    <button onclick="closeBlacklistModal()" style="background:#363c45;color:#eaecef;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">关闭</button>
                </div>
            </div>
        </div>

        <!-- 优质代币合约黑名单弹窗 -->
        <div id="exclusiveBlacklistModal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:1000;justify-content:center;align-items:center">
            <div style="background:#1e2329;padding:24px;border-radius:8px;width:550px;max-width:90%">
                <h3 style="margin:0 0 16px 0;color:#f6465d">🚫 优质代币黑名单</h3>
                <p style="color:#848e9c;font-size:12px;margin-bottom:12px">添加到黑名单的合约地址对应的代币将不参与AI匹配</p>
                <div style="margin-bottom:12px;display:flex;gap:8px">
                    <input id="exclusiveBlacklistInput" type="text" style="flex:1;background:#2b3139;border:1px solid #363c45;border-radius:4px;padding:8px;color:#eaecef;font-family:monospace;font-size:11px" placeholder="输入合约地址，如: 0x...">
                    <button onclick="addToExclusiveBlacklist()" style="background:#f6465d;color:#fff;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;white-space:nowrap">添加</button>
                </div>
                <div id="exclusiveBlacklistList" style="max-height:300px;overflow-y:auto;background:#0b0e11;border-radius:4px;padding:8px">
                    <div style="color:#848e9c;text-align:center;padding:20px">加载中...</div>
                </div>
                <div style="display:flex;gap:12px;justify-content:flex-end;margin-top:16px">
                    <button onclick="closeExclusiveBlacklistModal()" style="background:#363c45;color:#eaecef;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">关闭</button>
                </div>
            </div>
        </div>

        <!-- 作者白名单弹窗 -->
        <div id="authorWhitelistModal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:1000;justify-content:center;align-items:center">
            <div style="background:#1e2329;padding:24px;border-radius:8px;width:500px;max-width:90%">
                <h3 style="margin:0 0 16px 0;color:#0ecb81">✅ 作者白名单</h3>
                <p style="color:#848e9c;font-size:12px;margin-bottom:12px">启用后只接收白名单内作者的推文</p>
                <div style="margin-bottom:12px;display:flex;gap:8px;align-items:center">
                    <span style="color:#eaecef;font-size:13px">白名单过滤:</span>
                    <button id="whitelistToggleBtn" onclick="toggleAuthorWhitelist()" style="background:#363c45;color:#eaecef;border:none;padding:6px 16px;border-radius:4px;cursor:pointer;font-size:12px">关闭</button>
                </div>
                <div style="margin-bottom:12px;display:flex;gap:8px">
                    <input id="authorWhitelistInput" type="text" style="flex:1;background:#2b3139;border:1px solid #363c45;border-radius:4px;padding:8px;color:#eaecef" placeholder="输入作者 handle，如: elonmusk">
                    <button onclick="addToAuthorWhitelist()" style="background:#0ecb81;color:#fff;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;white-space:nowrap">添加</button>
                </div>
                <div style="margin-bottom:12px">
                    <textarea id="authorWhitelistBatch" style="width:100%;height:60px;background:#2b3139;border:1px solid #363c45;border-radius:4px;padding:8px;color:#eaecef;resize:vertical;font-size:12px" placeholder="批量添加（每行一个或逗号分隔）"></textarea>
                    <button onclick="batchAddAuthorWhitelist()" style="background:#363c45;color:#eaecef;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;font-size:11px;margin-top:4px">批量添加</button>
                </div>
                <div id="authorWhitelistList" style="max-height:250px;overflow-y:auto;background:#0b0e11;border-radius:4px;padding:8px">
                    <div style="color:#848e9c;text-align:center;padding:20px">加载中...</div>
                </div>
                <div style="display:flex;gap:12px;justify-content:flex-end;margin-top:16px">
                    <button onclick="openWhitelistNewsModal()" style="background:#F0B90B;color:#000;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">📜 查看历史推文</button>
                    <button onclick="closeAuthorWhitelistModal()" style="background:#363c45;color:#eaecef;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">关闭</button>
                </div>
            </div>
        </div>

        <!-- 白名单历史推文弹窗 -->
        <div id="whitelistNewsModal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:1001;justify-content:center;align-items:center">
            <div style="background:#1e2329;padding:24px;border-radius:8px;width:900px;max-width:95%;max-height:90vh;display:flex;flex-direction:column">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
                    <h3 style="margin:0;color:#F0B90B">📜 白名单作者历史推文</h3>
                    <div style="display:flex;gap:8px;align-items:center">
                        <select id="whitelistNewsAuthor" onchange="loadWhitelistNews()" style="background:#2b3139;border:1px solid #363c45;border-radius:4px;padding:6px 12px;color:#eaecef;font-size:12px">
                            <option value="">全部作者</option>
                        </select>
                        <input id="whitelistNewsLimit" type="number" value="50" min="10" max="500" style="width:60px;background:#2b3139;border:1px solid #363c45;border-radius:4px;padding:6px;color:#eaecef;font-size:12px">
                        <button onclick="loadWhitelistNews()" style="background:#0ecb81;color:#fff;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;font-size:12px">刷新</button>
                    </div>
                </div>
                <div id="whitelistNewsList" style="flex:1;overflow-y:auto;background:#0b0e11;border-radius:4px;padding:12px">
                    <div style="color:#848e9c;text-align:center;padding:40px">点击刷新加载推文...</div>
                </div>
                <div style="display:flex;justify-content:flex-end;margin-top:16px">
                    <button onclick="closeWhitelistNewsModal()" style="background:#363c45;color:#eaecef;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">关闭</button>
                </div>
            </div>
        </div>

        <!-- 提示词查看弹窗 -->
        <div id="promptModal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:1000;justify-content:center;align-items:center">
            <div style="background:#1e2329;padding:24px;border-radius:8px;width:700px;max-width:95%;max-height:90vh;overflow-y:auto">
                <h3 style="margin:0 0 16px 0;color:#eaecef">📝 当前提示词模版</h3>
                <div style="margin-bottom:16px">
                    <div style="display:flex;gap:8px;margin-bottom:8px">
                        <button id="promptTabDeepseek" onclick="switchPromptTab('deepseek')" style="background:#F0B90B;color:#000;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;font-size:12px">DeepSeek (纯文本)</button>
                        <button id="promptTabGemini" onclick="switchPromptTab('gemini')" style="background:#363c45;color:#eaecef;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;font-size:12px">Gemini (图片+文本)</button>
                    </div>
                    <pre id="promptContent" style="background:#0b0e11;border-radius:4px;padding:12px;color:#b7bdc6;font-size:11px;white-space:pre-wrap;word-break:break-all;max-height:400px;overflow-y:auto;line-height:1.5">加载中...</pre>
                </div>
                <div id="promptStats" style="color:#848e9c;font-size:11px;margin-bottom:12px"></div>
                <div style="display:flex;gap:12px;justify-content:flex-end">
                    <button onclick="closePromptModal()" style="background:#363c45;color:#eaecef;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">关闭</button>
                </div>
            </div>
        </div>

        <!-- 自动交易配置弹窗 -->
        <div id="tradeModal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:1000;justify-content:center;align-items:center">
            <div style="background:#1e2329;padding:24px;border-radius:8px;width:800px;max-width:95%;max-height:90vh;overflow-y:auto">
                <h3 style="margin:0 0 16px 0;color:#f0b90b">🤖 自动交易配置</h3>

                <!-- Tab 切换 -->
                <div style="display:flex;gap:8px;margin-bottom:16px;border-bottom:1px solid #2b3139;padding-bottom:8px">
                    <button id="tradeTabConfig" onclick="switchTradeTab('config')" style="background:#f0b90b;color:#000;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;font-size:12px">配置</button>
                    <button id="tradeTabPositions" onclick="switchTradeTab('positions')" style="background:#363c45;color:#eaecef;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;font-size:12px">持仓</button>
                    <button id="tradeTabHistory" onclick="switchTradeTab('history')" style="background:#363c45;color:#eaecef;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;font-size:12px">历史</button>
                    <button id="tradeTabAuthors" onclick="switchTradeTab('authors')" style="background:#363c45;color:#eaecef;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;font-size:12px">作者白名单</button>
                    <button id="tradeTabTokens" onclick="switchTradeTab('tokens')" style="background:#363c45;color:#eaecef;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;font-size:12px">代币白名单</button>
                </div>

                <!-- 配置面板 -->
                <div id="tradePanelConfig">
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">
                        <div>
                            <label style="color:#848e9c;font-size:11px">交易开关</label>
                            <div style="margin-top:4px">
                                <button id="tradeEnabledBtn" onclick="toggleTradeEnabled()" style="padding:8px 16px;border-radius:4px;border:none;cursor:pointer;font-size:12px">加载中...</button>
                            </div>
                        </div>
                        <div>
                            <label style="color:#848e9c;font-size:11px">白名单条件</label>
                            <select id="tradeWhitelistMode" style="width:100%;padding:8px;background:#0b0e11;border:1px solid #2b3139;border-radius:4px;color:#eaecef;margin-top:4px">
                                <option value="any">任一满足 (作者或代币)</option>
                                <option value="author">仅作者白名单</option>
                                <option value="token">仅代币白名单</option>
                                <option value="both">两者都要满足</option>
                            </select>
                        </div>
                        <div>
                            <label style="color:#848e9c;font-size:11px">新币买入金额 (BNB)</label>
                            <input type="number" id="tradeNewTokenAmount" step="0.1" min="0.01" style="width:100%;padding:8px;background:#0b0e11;border:1px solid #2b3139;border-radius:4px;color:#eaecef;margin-top:4px" />
                        </div>
                        <div>
                            <label style="color:#848e9c;font-size:11px">老币买入金额 (BNB)</label>
                            <input type="number" id="tradeOldTokenAmount" step="0.1" min="0.01" style="width:100%;padding:8px;background:#0b0e11;border:1px solid #2b3139;border-radius:4px;color:#eaecef;margin-top:4px" />
                        </div>
                        <div>
                            <label style="color:#848e9c;font-size:11px">卖出触发倍数</label>
                            <input type="number" id="tradeSellMultiple" step="0.5" min="1.5" style="width:100%;padding:8px;background:#0b0e11;border:1px solid #2b3139;border-radius:4px;color:#eaecef;margin-top:4px" />
                        </div>
                        <div>
                            <label style="color:#848e9c;font-size:11px">每次卖出比例</label>
                            <input type="number" id="tradeSellPct" step="0.1" min="0.1" max="1" style="width:100%;padding:8px;background:#0b0e11;border:1px solid #2b3139;border-radius:4px;color:#eaecef;margin-top:4px" />
                        </div>
                        <div>
                            <label style="color:#848e9c;font-size:11px">止损比例 (跌到X倍全卖)</label>
                            <input type="number" id="tradeStopLoss" step="0.1" min="0.1" max="0.9" style="width:100%;padding:8px;background:#0b0e11;border:1px solid #2b3139;border-radius:4px;color:#eaecef;margin-top:4px" />
                        </div>
                        <div>
                            <label style="color:#848e9c;font-size:11px">最大持仓数</label>
                            <input type="number" id="tradeMaxPositions" step="1" min="1" max="50" style="width:100%;padding:8px;background:#0b0e11;border:1px solid #2b3139;border-radius:4px;color:#eaecef;margin-top:4px" />
                        </div>
                         <div>
                            <label style="color:#848e9c;font-size:11px">无波动超时 (秒, 0=禁用)</label>
                            <input type="number" id="tradeNoChangeTimeout" step="1" min="0" max="300" style="width:100%;padding:8px;background:#0b0e11;border:1px solid #2b3139;border-radius:4px;color:#eaecef;margin-top:4px" />
                        </div>
                        <div style="grid-column: span 2">
                            <label style="display:flex;align-items:center;gap:8px;cursor:pointer;color:#eaecef;font-size:12px">
                                <input type="checkbox" id="tradeAllowNewTokenByAuthor" style="width:16px;height:16px;accent-color:#f0b90b" />
                                <span>新币特赦：若作者在白名单，即使代币不在，在新币模式下也买入 (针对“两者满足”模式)</span>
                            </label>
                        </div>
                    </div>
                    <div style="display:flex;gap:8px;justify-content:flex-end">
                        <button onclick="saveTradeConfig()" style="background:#0ecb81;color:#fff;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">保存配置</button>
                    </div>
                </div>

                <!-- 持仓面板 -->
                <div id="tradePanelPositions" style="display:none">
                    <div id="tradePositionsList" style="max-height:400px;overflow-y:auto"></div>
                </div>

                <!-- 历史面板 -->
                <div id="tradePanelHistory" style="display:none">
                    <div id="tradeHistoryList" style="max-height:400px;overflow-y:auto"></div>
                </div>

                <!-- 作者白名单面板 -->
                <div id="tradePanelAuthors" style="display:none">
                    <div style="display:flex;gap:8px;margin-bottom:12px">
                        <input type="text" id="tradeNewAuthor" placeholder="输入作者用户名" style="flex:1;padding:8px;background:#0b0e11;border:1px solid #2b3139;border-radius:4px;color:#eaecef" />
                        <button onclick="addTradeAuthor()" style="background:#0ecb81;color:#fff;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">添加</button>
                    </div>
                    <div id="tradeAuthorsList" style="max-height:300px;overflow-y:auto"></div>
                </div>

                <!-- 代币白名单面板 -->
                <div id="tradePanelTokens" style="display:none">
                    <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap">
                        <input type="text" id="tradeNewTokenAddr" placeholder="合约地址" style="flex:2;min-width:200px;padding:8px;background:#0b0e11;border:1px solid #2b3139;border-radius:4px;color:#eaecef" />
                        <input type="text" id="tradeNewTokenSymbol" placeholder="符号(可选)" style="flex:1;min-width:80px;padding:8px;background:#0b0e11;border:1px solid #2b3139;border-radius:4px;color:#eaecef" />
                        <button onclick="addTradeToken()" style="background:#0ecb81;color:#fff;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">添加</button>
                    </div>
                    <div id="tradeTokensList" style="max-height:300px;overflow-y:auto"></div>
                </div>

                <div style="display:flex;gap:12px;justify-content:flex-end;margin-top:16px;border-top:1px solid #2b3139;padding-top:16px">
                    <button onclick="closeTradeModal()" style="background:#363c45;color:#eaecef;border:none;padding:8px 16px;border-radius:4px;cursor:pointer">关闭</button>
                </div>
            </div>
        </div>

        <div class="refresh-info">🔴 实时更新 | <span id="last-update">-</span></div>
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
        function formatDateTime(ts) {
            if (!ts) return '';
            const date = new Date(ts * 1000);
            const y = date.getFullYear();
            const M = (date.getMonth() + 1).toString().padStart(2,'0');
            const d = date.getDate().toString().padStart(2,'0');
            const h = date.getHours().toString().padStart(2,'0');
            const m = date.getMinutes().toString().padStart(2,'0');
            return `${y}-${M}-${d} ${h}:${m}`;
        }
        function escapeHtml(str) {
            if (!str) return '';
            return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
        }

        // 更新所有倒计时元素
        function updateCountdowns() {
            const now = Math.floor(Date.now() / 1000);
            let needRefresh = false;
            document.querySelectorAll('.countdown').forEach(el => {
                const expire = parseInt(el.dataset.expire);
                if (expire) {
                    const remaining = Math.max(0, expire - now);
                    if (remaining <= 0) {
                        needRefresh = true;
                    } else {
                        const mins = Math.floor(remaining / 60);
                        const secs = remaining % 60;
                        el.textContent = `检测中 ${mins}:${secs.toString().padStart(2,'0')}`;
                    }
                }
            });
            if (needRefresh) refresh();
        }
        setInterval(updateCountdowns, 1000);

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

        let tokenChainFilter = 'ALL';
        // ==================== 状态机 ====================
        // 视图模式（互斥）
        const ViewMode = { NORMAL: 'normal', EXCLUSIVE: 'exclusive', ALPHA: 'alpha' };
        // 编辑模式（互斥）
        const EditMode = { NONE: 'none', BLACKLIST: 'blacklist', WHITELIST: 'whitelist' };

        // 统一状态对象
        const tokenState = {
            viewMode: ViewMode.NORMAL,
            editMode: EditMode.NONE,
            // 数据缓存
            exclusiveTokens: [],
            alphaTokens: [],
            blacklistSet: new Set(),      // 黑名单集合
            whitelistSet: new Set(),      // 交易白名单集合
            // 选择状态（添加/移除共用，根据原状态判断）
            selected: new Set(),
            // 加载状态
            exclusiveLoading: false,
            alphaLoading: false,
            exclusiveError: null,
            alphaError: null,
        };

        // 兼容旧变量（渐进重构，避免大面积改动）
        let exclusiveTokens = tokenState.exclusiveTokens;
        let alphaTokens = tokenState.alphaTokens;
        let exclusiveBlacklistSet = tokenState.blacklistSet;
        let tradeWhitelistSet = tokenState.whitelistSet;
        let lastServiceData = {};
        let deleteMode = false;
        let selectedIds = new Set();

        // 旧变量映射到新状态（getter）
        Object.defineProperty(window, 'showExclusive', { get: () => tokenState.viewMode === ViewMode.EXCLUSIVE });
        Object.defineProperty(window, 'showAlpha', { get: () => tokenState.viewMode === ViewMode.ALPHA });
        Object.defineProperty(window, 'exclusiveBlacklistMode', { get: () => tokenState.editMode === EditMode.BLACKLIST });
        Object.defineProperty(window, 'tradeWhitelistMode', { get: () => tokenState.editMode === EditMode.WHITELIST });

        // 状态切换函数
        function setViewMode(mode) {
            tokenState.viewMode = mode;
            setEditMode(EditMode.NONE);  // 切换视图时退出编辑模式
            lastServiceData['token_service'] = '';
        }

        function setEditMode(mode) {
            tokenState.editMode = mode;
            tokenState.selected.clear();  // 切换编辑模式时清空选择
            lastServiceData['token_service'] = '';
        }

        // 选择操作（统一处理添加/移除）
        function toggleSelection(addr, isInList) {
            const key = addr + (isInList ? ':remove' : ':add');
            if (tokenState.selected.has(key)) {
                tokenState.selected.delete(key);
            } else {
                tokenState.selected.add(key);
            }
            updateEditBtnText();
        }

        function getSelectionCounts() {
            let toAdd = 0, toRemove = 0;
            tokenState.selected.forEach(k => {
                if (k.endsWith(':add')) toAdd++;
                else if (k.endsWith(':remove')) toRemove++;
            });
            return { toAdd, toRemove };
        }

        function updateEditBtnText() {
            const { toAdd, toRemove } = getSelectionCounts();
            const btnId = tokenState.editMode === EditMode.BLACKLIST ? 'confirmBlacklistBtn' : 'confirmTradeWhitelistBtn';
            const btn = document.getElementById(btnId);
            if (!btn) return;

            const actionAdd = tokenState.editMode === EditMode.BLACKLIST ? '加黑' : '加入';
            const actionRemove = '移除';

            if (toAdd > 0 && toRemove > 0) {
                btn.textContent = `确认 (+${toAdd} -${toRemove})`;
            } else if (toAdd > 0) {
                btn.textContent = `确认${actionAdd} (${toAdd})`;
            } else if (toRemove > 0) {
                btn.textContent = `确认${actionRemove} (${toRemove})`;
            } else {
                btn.textContent = '确认';
            }
        }

        function isSelected(addr, isInList) {
            const key = addr + (isInList ? ':remove' : ':add');
            return tokenState.selected.has(key);
        }

        function shouldBeChecked(addr, isInList) {
            // 已在列表中：默认勾选，如果选中移除则不勾选
            // 不在列表中：默认不勾选，如果选中添加则勾选
            if (isInList) {
                return !isSelected(addr, true);
            } else {
                return isSelected(addr, false);
            }
        }

        function setTokenChainFilter(chain) {
            tokenChainFilter = chain;
            lastServiceData['token_service'] = '';  // 强制刷新
            refresh();
        }

        async function toggleExclusiveMode() {
            if (tokenState.viewMode === ViewMode.EXCLUSIVE) {
                setViewMode(ViewMode.NORMAL);
            } else {
                setViewMode(ViewMode.EXCLUSIVE);
                // 总是重新加载优质代币（确保数据最新）
                await loadExclusiveTokens();
            }
            refresh();
        }

        async function toggleAlphaMode() {
            if (tokenState.viewMode === ViewMode.ALPHA) {
                setViewMode(ViewMode.NORMAL);
            } else {
                setViewMode(ViewMode.ALPHA);
                if (tokenState.alphaTokens.length === 0) {
                    await loadAlphaTokens();
                }
            }
            refresh();
        }

        async function loadExclusiveTokens() {
            tokenState.exclusiveLoading = true;
            tokenState.exclusiveError = null;
            // 强制清除缓存，触发重新渲染显示加载状态
            lastServiceData['token_service'] = '';
            try {
                const [tokenResp, blacklistResp, tradeWhitelistResp] = await Promise.all([
                    fetch('api/exclusive'),
                    fetch('api/exclusive_blacklist'),
                    fetch('api/trade/whitelist/tokens')
                ]);
                const tokenData = await tokenResp.json();
                const blacklistData = await blacklistResp.json();
                const tradeWlData = await tradeWhitelistResp.json();

                // 检查 API 返回的错误
                if (tokenData.error) {
                    tokenState.exclusiveError = tokenData.error;
                    tokenState.exclusiveTokens = [];
                } else {
                    tokenState.exclusiveTokens = tokenData.items || [];
                }
                tokenState.blacklistSet = new Set((blacklistData.blacklist || []).map(a => a.toLowerCase()));
                tokenState.whitelistSet = new Set((tradeWlData.tokens || []).map(t => (t.address || t).toLowerCase()));
                // 兼容旧引用
                exclusiveTokens = tokenState.exclusiveTokens;
                exclusiveBlacklistSet = tokenState.blacklistSet;
                tradeWhitelistSet = tokenState.whitelistSet;
            } catch (e) {
                console.error('加载优质代币失败:', e);
                tokenState.exclusiveError = e.message || '网络错误';
                tokenState.exclusiveTokens = [];
                exclusiveTokens = [];
            } finally {
                tokenState.exclusiveLoading = false;
                // 强制清除缓存，触发重新渲染
                lastServiceData['token_service'] = '';
            }
        }

        async function loadAlphaTokens() {
            tokenState.alphaLoading = true;
            tokenState.alphaError = null;
            lastServiceData['token_service'] = '';
            try {
                const [tokenResp, tradeWhitelistResp] = await Promise.all([
                    fetch('api/alpha'),
                    fetch('api/trade/whitelist/tokens')
                ]);
                const tokenData = await tokenResp.json();
                const tradeWlData = await tradeWhitelistResp.json();

                if (tokenData.error) {
                    tokenState.alphaError = tokenData.error;
                    tokenState.alphaTokens = [];
                } else {
                    tokenState.alphaTokens = tokenData.items || [];
                }
                tokenState.whitelistSet = new Set((tradeWlData.tokens || []).map(t => (t.address || t).toLowerCase()));
                // 兼容旧引用
                alphaTokens = tokenState.alphaTokens;
                tradeWhitelistSet = tokenState.whitelistSet;
            } catch (e) {
                console.error('加载Alpha代币失败:', e);
                tokenState.alphaError = e.message || '网络错误';
                tokenState.alphaTokens = [];
                alphaTokens = [];
            } finally {
                tokenState.alphaLoading = false;
                lastServiceData['token_service'] = '';
            }
        }

        // ==================== 编辑模式切换 ====================
        function toggleTradeWhitelistMode() {
            if (tokenState.editMode === EditMode.WHITELIST) {
                setEditMode(EditMode.NONE);
            } else {
                setEditMode(EditMode.WHITELIST);
            }
            refresh();
        }

        function toggleExclusiveBlacklistMode() {
            if (tokenState.editMode === EditMode.BLACKLIST) {
                setEditMode(EditMode.NONE);
            } else {
                setEditMode(EditMode.BLACKLIST);
            }
            refresh();
        }

        function cancelTradeWhitelistMode() {
            setEditMode(EditMode.NONE);
            refresh();
        }

        function cancelExclusiveBlacklistMode() {
            setEditMode(EditMode.NONE);
            refresh();
        }

        // ==================== 选择操作（统一） ====================
        function toggleSelectTradeWhitelistAddr(addr, isInList) {
            toggleSelection(addr, isInList);
            // 更新复选框状态
            const checkbox = document.getElementById('tw-check-' + addr.slice(0,8));
            if (checkbox) checkbox.checked = shouldBeChecked(addr, isInList);
        }

        function toggleSelectBlacklistAddr(addr, isInList) {
            toggleSelection(addr, isInList);
            // 更新复选框状态
            const checkbox = document.getElementById('bl-check-' + addr.slice(0,8));
            if (checkbox) checkbox.checked = shouldBeChecked(addr, isInList);
        }

        // ==================== 快速移除（非编辑模式下点击图标） ====================
        async function removeFromTradeWhitelistQuick(addr) {
            try {
                const resp = await fetch('api/trade/whitelist/tokens', {
                    method: 'DELETE',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ address: addr })
                });
                if (resp.ok) {
                    tokenState.whitelistSet.delete(addr.toLowerCase());
                    tradeWhitelistSet = tokenState.whitelistSet;
                    lastServiceData['token_service'] = '';
                    refresh();
                }
            } catch (e) {
                alert('移除失败: ' + e.message);
            }
        }

        async function removeFromBlacklistQuick(addr) {
            try {
                const resp = await fetch('api/exclusive_blacklist', {
                    method: 'DELETE',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ address: addr })
                });
                if (resp.ok) {
                    tokenState.blacklistSet.delete(addr.toLowerCase());
                    exclusiveBlacklistSet = tokenState.blacklistSet;
                    lastServiceData['token_service'] = '';
                    refresh();
                }
            } catch (e) {
                alert('移除失败: ' + e.message);
            }
        }

        // ==================== 批量确认操作 ====================
        async function confirmAddToTradeWhitelist() {
            const { toAdd, toRemove } = getSelectionCounts();
            if (toAdd === 0 && toRemove === 0) {
                alert('请选择要操作的代币');
                return;
            }
            try {
                const currentTokens = tokenState.viewMode === ViewMode.ALPHA ? tokenState.alphaTokens : tokenState.exclusiveTokens;

                // 批量添加
                for (const key of tokenState.selected) {
                    if (key.endsWith(':add')) {
                        const addr = key.slice(0, -4);
                        const token = currentTokens.find(t => t.address === addr);
                        await fetch('api/trade/whitelist/tokens', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({
                                address: addr,
                                symbol: token ? token.symbol : '',
                                note: tokenState.viewMode === ViewMode.ALPHA ? 'Alpha代币' : '优质代币'
                            })
                        });
                    }
                }
                // 批量移除
                for (const key of tokenState.selected) {
                    if (key.endsWith(':remove')) {
                        const addr = key.slice(0, -7);
                        await fetch('api/trade/whitelist/tokens', {
                            method: 'DELETE',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({ address: addr })
                        });
                    }
                }

                // 刷新白名单数据
                const resp = await fetch('api/trade/whitelist/tokens');
                const data = await resp.json();
                tokenState.whitelistSet = new Set((data.tokens || []).map(t => (t.address || t).toLowerCase()));
                tradeWhitelistSet = tokenState.whitelistSet;

                setEditMode(EditMode.NONE);
                refresh();
                alert('操作成功');
            } catch (e) {
                alert('操作失败: ' + e.message);
            }
        }

        async function confirmAddToBlacklist() {
            const { toAdd, toRemove } = getSelectionCounts();
            if (toAdd === 0 && toRemove === 0) {
                alert('请选择要操作的代币');
                return;
            }
            try {
                // 批量添加
                for (const key of tokenState.selected) {
                    if (key.endsWith(':add')) {
                        const addr = key.slice(0, -4);
                        await fetch('api/exclusive_blacklist', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({ address: addr })
                        });
                    }
                }
                // 批量移除
                for (const key of tokenState.selected) {
                    if (key.endsWith(':remove')) {
                        const addr = key.slice(0, -7);
                        await fetch('api/exclusive_blacklist', {
                            method: 'DELETE',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({ address: addr })
                        });
                    }
                }

                // 刷新黑名单数据
                const resp = await fetch('api/exclusive_blacklist');
                const data = await resp.json();
                tokenState.blacklistSet = new Set((data.blacklist || []).map(a => a.toLowerCase()));
                exclusiveBlacklistSet = tokenState.blacklistSet;

                setEditMode(EditMode.NONE);
                refresh();
                alert('操作成功');
            } catch (e) {
                alert('操作失败: ' + e.message);
            }
        }

        function exportRecords() {
            // 直接下载 CSV 文件
            window.location.href = 'api/export_records';
        }

        function exportAnalysis() {
            // 下载分析导出 CSV
            window.location.href = 'api/export_analysis';
        }

        function toggleDeleteMode() {
            deleteMode = true;
            selectedIds.clear();
            document.getElementById('deleteBtn').style.display = 'none';
            document.getElementById('confirmDeleteBtn').style.display = 'inline-block';
            document.getElementById('cancelDeleteBtn').style.display = 'inline-block';
            updateDeleteBtnText();
            refresh();
        }

        function cancelDeleteMode() {
            deleteMode = false;
            selectedIds.clear();
            document.getElementById('deleteBtn').style.display = 'inline-block';
            document.getElementById('confirmDeleteBtn').style.display = 'none';
            document.getElementById('cancelDeleteBtn').style.display = 'none';
            refresh();
        }

        function toggleSelectRecord(id) {
            if (selectedIds.has(id)) {
                selectedIds.delete(id);
            } else {
                selectedIds.add(id);
            }
            updateDeleteBtnText();
            // 更新复选框状态
            const checkbox = document.getElementById('check-' + id);
            if (checkbox) checkbox.checked = selectedIds.has(id);
        }

        function updateDeleteBtnText() {
            const btn = document.getElementById('confirmDeleteBtn');
            btn.textContent = selectedIds.size > 0 ? `确认移除 (${selectedIds.size})` : '确认移除';
        }

        async function confirmDelete() {
            if (selectedIds.size === 0) {
                alert('请选择要移除的记录');
                return;
            }
            if (!confirm(`确定从最佳实践中移除 ${selectedIds.size} 条记录吗？`)) {
                return;
            }
            try {
                const resp = await fetch('api/delete_records', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ ids: Array.from(selectedIds) })
                });
                const data = await resp.json();
                if (data.success) {
                    cancelDeleteMode();
                    refresh();
                } else {
                    alert('移除失败: ' + (data.error || '未知错误'));
                }
            } catch (e) {
                alert('移除失败: ' + e.message);
            }
        }

        // 单独更新时间戳和时间线（不重新渲染DOM）
        function updateTimestamps(services) {
            services.forEach(s => {
                const d = s.data || {};
                // 更新时间戳
                if (s.name === 'news_service' || s.name === 'token_service') {
                    const lastFetchEl = document.getElementById(`${s.name}-last-fetch`);
                    const lastSuccessEl = document.getElementById(`${s.name}-last-success`);
                    if (lastFetchEl) lastFetchEl.textContent = formatTime(d.last_fetch);
                    if (lastSuccessEl) lastSuccessEl.textContent = formatTime(d.last_success);
                }
                // 更新时间线
                const timelineEl = document.getElementById(`${s.name}-timeline`);
                if (timelineEl && s.history) {
                    timelineEl.innerHTML = s.history.map(h =>
                        `<div class="timeline-bar ${h ? 'online' : 'offline'}" title="${h ? '正常' : '异常'}"></div>`
                    ).join('');
                }
            });
        }

        // 获取服务的稳定数据（用于比较）
        function getServiceStableData(s) {
            if (!s.recent) return null;
            const r = s.recent;
            if (s.name === 'news_service') {
                return { ids: (r.items || []).map(i => i.id), errCount: (r.errors || []).length };
            } else if (s.name === 'token_service') {
                return { ids: (r.items || []).map(i => `${i.chain}:${i.address}`), errCount: (r.errors || []).length };
            }
            // match_service 和 tracker_service 每次都渲染
            return Math.random();
        }

        function renderServices(services, monitoringData) {
            // 确保 monitoringData 有默认值
            monitoringData = monitoringData || {count: 0, contracts: []};

            // 时间戳和时间线始终更新
            updateTimestamps(services);

            // 分别渲染每个服务
            services.forEach(s => {
                const container = document.getElementById(`${s.name}_card`);
                if (!container) return;

                // 只对 news_service 和 token_service 做优化
                if (s.name === 'news_service' || s.name === 'token_service') {
                    const stableData = JSON.stringify(getServiceStableData(s));
                    if (lastServiceData[s.name] === stableData) return;
                    lastServiceData[s.name] = stableData;
                }

                container.innerHTML = renderServiceCard(s, monitoringData);
            });
        }

        function renderServiceCard(s, monitoringData) {
                monitoringData = monitoringData || {count: 0, contracts: []};
                const isOnline = s.status === 'online';
                const statusClass = isOnline ? 'online' : 'offline';
                const statusText = isOnline ? '运行中' : '离线';
                const d = s.data || {};
                const hasErrors = (d.errors || 0) > 0;

                // 统计栏
                let statsHtml = '';
                if (s.name === 'news_service') {
                    const whitelistEnabled = d.enable_whitelist;
                    const whitelistBtnStyle = whitelistEnabled
                        ? 'background:#0ecb81;color:#fff'
                        : 'background:#363c45;color:#eaecef';
                    const whitelistStatus = whitelistEnabled
                        ? `<span style="color:#0ecb81">开启(${d.whitelist_count || 0}人)</span>`
                        : '<span style="color:#848e9c">关闭</span>';
                    statsHtml = `<div class="stat-item">推文: <span class="stat-value">${d.total_news || 0}</span></div>
                                <div class="stat-item">白名单: ${whitelistStatus}</div>
                                <div class="stat-item">过滤: <span class="stat-value">${d.filtered_by_whitelist || 0}</span></div>
                                <div class="stat-item">错误: <span class="stat-value ${hasErrors?'error':''}">${d.errors || 0}</span></div>
                                <div class="stat-item"><button onclick="openAuthorWhitelistModal()" style="${whitelistBtnStyle};border:none;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:11px">管理白名单</button></div>`;
                } else if (s.name === 'token_service') {
                    const boostActive = d.boost_active;
                    const boostStyle = boostActive ? 'color:#f0b90b;font-weight:bold' : 'color:#848e9c';
                    const boostText = boostActive ? `⚡高频 (${Math.ceil(d.boost_remaining || 0)}s)` : '普通';
                    const boostBtnStyle = boostActive ? 'background:#f0b90b;color:#000' : 'background:#363c45;color:#eaecef';
                    const freqText = `${d.fetch_count_60s || 0}次/分`;
                    statsHtml = `<div class="stat-item">代币: <span class="stat-value">${d.total_tokens || 0}</span></div>
                                <div class="stat-item">模式: <span class="stat-value" style="${boostStyle}">${boostText}</span></div>
                                <div class="stat-item">频率: <span class="stat-value">${freqText}</span></div>
                                <div class="stat-item">最后成功: <span class="stat-value" id="token_service-last-success">${formatTime(d.last_success)}</span></div>
                                <div class="stat-item">错误: <span class="stat-value ${hasErrors?'error':''}">${d.errors || 0}</span></div>
                                <div class="stat-item"><button onclick="triggerBoostMode()" style="${boostBtnStyle};border:none;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:11px">⚡高频</button></div>`;
                } else if (s.name === 'match_service') {
                    const hardcodedEnabled = d.enable_hardcoded_match !== false;
                    const toggleColor = hardcodedEnabled ? '#0ecb81' : '#848e9c';
                    const toggleText = hardcodedEnabled ? '硬编码:开' : '硬编码:关';
                    statsHtml = `<div class="stat-item">匹配: <span class="stat-value">${d.total_matches || 0}</span></div>
                                <div class="stat-item">缓存: <span class="stat-value">${d.tokens_cached || 0}</span></div>
                                <div class="stat-item">错误: <span class="stat-value ${hasErrors?'error':''}">${d.errors || 0}</span></div>
                                <div class="stat-item">
                                    <button onclick="toggleHardcodedMatch()" id="hardcodedToggleBtn" style="background:${toggleColor};color:#fff;border:none;padding:2px 8px;border-radius:4px;cursor:pointer;font-size:10px">${toggleText}</button>
                                </div>`;
                } else if (s.name === 'tracker_service') {
                    statsHtml = `<div class="stat-item">记录: <span class="stat-value">${d.total_matches || 0}</span></div>
                                <div class="stat-item">追踪: <span class="stat-value">${d.total_tracked || 0}</span></div>
                                <div class="stat-item">待处理: <span class="stat-value">${d.pending_tasks || 0}</span></div>`;
                } else if (s.name === 'alpha_call_service') {
                    const monitorCount = (monitoringData && monitoringData.count) || 0;
                    statsHtml = `<div class="stat-item">Call: <span class="stat-value">${d.total_calls || 0}</span></div>
                                <div class="stat-item">合约: <span class="stat-value">${d.total_contracts || 0}</span></div>
                                <div class="stat-item">监测: <span class="stat-value" style="color:#F0B90B">${monitorCount}</span></div>
                                <div class="stat-item">翻倍: <span class="stat-value" style="color:#02c076">${d.doubled || 0}</span></div>`;
                } else if (s.name === 'trade_service') {
                    const tradeEnabled = d.enabled !== false;
                    const toggleColor = tradeEnabled ? '#0ecb81' : '#f6465d';
                    const toggleText = tradeEnabled ? '已启用' : '已禁用';
                    const apiFreq = d.api_call_count_60s || 0;
                    const freqStyle = apiFreq > 0 ? 'color:#f0b90b' : 'color:#848e9c';
                    statsHtml = `<div class="stat-item">信号: <span class="stat-value">${d.total_signals || 0}</span></div>
                                <div class="stat-item">买入: <span class="stat-value" style="color:#0ecb81">${d.total_buys || 0}</span></div>
                                <div class="stat-item">卖出: <span class="stat-value" style="color:#f6465d">${d.total_sells || 0}</span></div>
                                <div class="stat-item">持仓: <span class="stat-value" style="color:#F0B90B">${d.active_positions || 0}</span></div>
                                <div class="stat-item">频率: <span class="stat-value" style="${freqStyle}">${apiFreq}次/分</span></div>
                                <div class="stat-item">
                                    <button onclick="openTradeModal()" style="background:${toggleColor};color:#fff;border:none;padding:2px 8px;border-radius:4px;cursor:pointer;font-size:10px">${toggleText}</button>
                                </div>`;
                }

                // 数据列表
                let dataHtml = '';

                // tracker_service 显示匹配记录
                if (s.name === 'tracker_service') {
                    let records = s.recent?.records || [];

                    // 格式化市值
                    const fmtMcap = (mcap) => {
                        if (!mcap || mcap <= 0) return '-';
                        if (mcap >= 1000000) return (mcap/1000000).toFixed(1) + 'M';
                        if (mcap >= 1000) return (mcap/1000).toFixed(0) + 'k';
                        return mcap.toFixed(0);
                    };
                    const changeColor = (v) => v > 0 ? '#0ecb81' : (v < 0 ? '#f6465d' : '#848e9c');

                    dataHtml += `<div class="data-section">
                        <div class="data-title">📊 匹配记录</div>`;
                    if (records.length > 0) {
                        dataHtml += `<div class="data-list" style="max-height:300px">${records.map(r => {
                            // 代币表格
                            let tokensTableHtml = '';
                            if (r.tokens && r.tokens.length > 0) {
                                tokensTableHtml = `<table style="width:100%;font-size:10px;border-collapse:collapse;margin-top:4px">
                                    <tr style="color:#848e9c">
                                        <th style="padding:2px;text-align:left">代币</th>
                                        <th style="padding:2px">来源</th>
                                        <th style="padding:2px">匹配</th>
                                        <th style="padding:2px">初始</th>
                                        <th style="padding:2px">1min</th>
                                        <th style="padding:2px">5min</th>
                                        <th style="padding:2px">10min</th>
                                        <th style="padding:2px">得分</th>
                                    </tr>
                                    ${r.tokens.map(t => {
                                        const isBest = t.is_best === 1;
                                        const rowStyle = isBest ? 'background:#1a3d2e;' : '';
                                        const symbolStyle = isBest ? 'color:#0ecb81;font-weight:bold' : '';
                                        const sourceLabel = t.source === 'old' ? '📦' : '🆕';
                                        const methodLabel = t.match_method === 'ai' ? '🤖' : '⚙️';
                                        const c1 = t.change_1min || 0;
                                        const c5 = t.change_5min || 0;
                                        const c10 = t.change_10min || 0;
                                        return '<tr style="' + rowStyle + '">' +
                                            '<td style="padding:2px;' + symbolStyle + '">' + (isBest ? '⭐' : '') + t.symbol + '</td>' +
                                            '<td style="padding:2px;text-align:center">' + sourceLabel + '</td>' +
                                            '<td style="padding:2px;text-align:center">' + methodLabel + '</td>' +
                                            '<td style="padding:2px;text-align:center">' + fmtMcap(t.initial_mcap) + '</td>' +
                                            '<td style="padding:2px;text-align:center;color:' + changeColor(c1) + '">' + fmtMcap(t.mcap_1min) + '</td>' +
                                            '<td style="padding:2px;text-align:center;color:' + changeColor(c5) + '">' + fmtMcap(t.mcap_5min) + '</td>' +
                                            '<td style="padding:2px;text-align:center;color:' + changeColor(c10) + '">' + fmtMcap(t.mcap_10min) + '</td>' +
                                            '<td style="padding:2px;text-align:center">' + (t.final_score || 0).toFixed(1) + '</td>' +
                                        '</tr>';
                                    }).join('')}
                                </table>`;
                            } else {
                                tokensTableHtml = '<div style="color:#848e9c;font-size:10px">无匹配代币</div>';
                            }
                            return `<div class="data-item" style="padding:6px">
                                <div><span class="author">@${r.author}</span> <span class="time">${formatTime(r.time)}</span></div>
                                <div class="content" style="font-size:11px;margin:2px 0">${r.content || '(无内容)'}</div>
                                ${tokensTableHtml}
                            </div>`;
                        }).join('')}</div>`;
                    } else {
                        dataHtml += `<div class="no-data" style="padding:10px;color:#848e9c">暂无记录</div>`;
                    }
                    dataHtml += `</div>`;

                    // 错误日志
                    let trackerErrors = s.recent?.errors || [];
                    if (trackerErrors.length > 0) {
                        const errId = 'err-tracker-' + Date.now();
                        dataHtml += '<div class="data-section error-section">' +
                            '<div class="error-header" onclick="document.getElementById(\\'' + errId + '\\').classList.toggle(\\'show\\')">' +
                                '<span class="data-title" style="margin:0">⚠️ 错误 (' + trackerErrors.length + ')</span>' +
                                '<button class="error-toggle">展开</button>' +
                            '</div>' +
                            '<div id="' + errId + '" class="error-list data-list">' + trackerErrors.map(r =>
                                '<div class="data-item error">' + r.msg + ' <span class="time">' + formatTime(r.time) + '</span></div>'
                            ).join('') + '</div>' +
                        '</div>';
                    }
                }

                // alpha_call_service 显示合约及调用历史
                if (s.name === 'alpha_call_service') {
                    let contractStats = s.recent?.stats || [];

                    // 格式化市值
                    const formatMcap = (mcap) => {
                        if (!mcap || mcap <= 0) return '-';
                        if (mcap >= 1000000) return '$' + (mcap/1000000).toFixed(1) + 'M';
                        if (mcap >= 1000) return '$' + (mcap/1000).toFixed(0) + 'k';
                        return '$' + mcap.toFixed(0);
                    };

                    // 格式化时间（短格式）
                    const formatShortTime = (ts) => {
                        if (!ts) return '-';
                        const d = new Date(ts * 1000);
                        return (d.getMonth()+1) + '/' + d.getDate() + ' ' +
                               String(d.getHours()).padStart(2,'0') + ':' + String(d.getMinutes()).padStart(2,'0');
                    };

                    dataHtml += `<div class="data-section">
                        <div class="data-title">📢 Alpha Call (${contractStats.length})</div>`;
                    if (contractStats.length > 0) {
                        dataHtml += `<div class="data-list" style="max-height:320px">${contractStats.slice(0, 20).map(c => {
                            const chainBadge = c.chain === 'SOL' ? '<span style="background:#9945FF;color:#fff;padding:1px 4px;border-radius:3px;font-size:9px;margin-right:4px">SOL</span>' : '<span style="background:#F0B90B;color:#000;padding:1px 4px;border-radius:3px;font-size:9px;margin-right:4px">BSC</span>';
                            const mcapStr = formatMcap(c.market_cap);

                            // 调用历史列表
                            const callsHtml = (c.calls || []).map(call => {
                                const callMcap = formatMcap(call.market_cap);
                                const senderInfo = call.sender ? `<span style="color:#F0B90B" title="${call.sender}">${call.sender.length > 12 ? call.sender.slice(0,12)+'...' : call.sender}</span> · ` : '';
                                return `<div style="display:flex;justify-content:space-between;padding:2px 0;font-size:10px;color:#848e9c;border-top:1px dashed #2b3139">
                                    <span>${formatShortTime(call.time)}</span>
                                    <span style="color:#02c076">${callMcap}</span>
                                    <span style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${call.sender ? call.sender + ' @ ' : ''}${call.group_name || call.group_id}">${senderInfo}${call.group_name || call.group_id}</span>
                                </div>`;
                            }).join('');

                            // 最后检查数据（含涨跌幅）
                            let lastCheckHtml = '';
                            if (c.last_check_elapsed > 0) {
                                const firstMcap = c.first_market_cap || 0;
                                const lastMcap = c.last_check_mcap || 0;
                                let changeStr = '';
                                if (firstMcap > 0 && lastMcap > 0) {
                                    const changeRatio = ((lastMcap - firstMcap) / firstMcap * 100);
                                    const changeColor = changeRatio >= 0 ? '#02c076' : '#f6465d';
                                    const sign = changeRatio >= 0 ? '+' : '';
                                    changeStr = ` <span style="color:${changeColor}">(${sign}${changeRatio.toFixed(1)}%)</span>`;
                                }
                                lastCheckHtml = `<div style="font-size:10px;color:#848e9c;margin-top:3px">📊 最后检查: <span style="color:#F0B90B">${c.last_check_elapsed}s</span> · <span style="color:#02c076">${formatMcap(lastMcap)}</span>${changeStr}</div>`;
                            }

                            return `<div class="data-item" style="padding:6px 0;border-bottom:1px solid #2b3139">
                                <div style="display:flex;justify-content:space-between;align-items:center">
                                    <div>
                                        ${chainBadge}
                                        <span class="symbol">${c.symbol || 'Unknown'}</span>
                                        ${c.name ? `<span style="color:#848e9c;font-size:9px;margin-left:3px">${c.name}</span>` : ''}
                                    </div>
                                    <div>
                                        <span style="color:#02c076;font-size:10px;margin-right:6px">${mcapStr}</span>
                                        <span style="background:#02c076;color:#fff;padding:2px 6px;border-radius:10px;font-size:10px;font-weight:bold">${c.count}次</span>
                                    </div>
                                </div>
                                <div style="color:#F0B90B;font-size:10px;margin-top:3px;cursor:pointer;word-break:break-all" onclick="copyText('${c.address}')" title="点击复制">
                                    📋 ${c.address}
                                </div>
                                ${lastCheckHtml}
                                <div style="margin-top:4px;padding-left:8px">
                                    ${callsHtml}
                                </div>
                            </div>`;
                        }).join('')}</div>`;
                    } else {
                        dataHtml += `<div class="no-data" style="padding:10px;color:#848e9c">暂无 Alpha Call</div>`;
                    }
                    dataHtml += `</div>`;

                    // 监测中的合约
                    const monitorContracts = (monitoringData && monitoringData.contracts) || [];
                    dataHtml += `<div class="data-section" style="margin-top:10px">
                        <div class="data-title">🔍 监测中 (${monitorContracts.length})</div>`;
                    if (monitorContracts.length > 0) {
                        dataHtml += `<div class="data-list" style="max-height:200px">${monitorContracts.map(m => {
                            const chainBadge = m.chain === 'SOL' ? '<span style="background:#9945FF;color:#fff;padding:1px 4px;border-radius:3px;font-size:9px;margin-right:4px">SOL</span>' : '<span style="background:#F0B90B;color:#000;padding:1px 4px;border-radius:3px;font-size:9px;margin-right:4px">BSC</span>';
                            const startMcapStr = formatMcap(m.start_mcap);

                            // 计算当前涨幅
                            const latestMcap = (m.history && m.history.length > 0) ? m.history[m.history.length - 1].mcap : m.start_mcap;
                            const gainRatio = m.start_mcap > 0 ? (latestMcap / m.start_mcap) : 1;
                            const gainColor = gainRatio >= 2 ? '#02c076' : gainRatio >= 1.5 ? '#F0B90B' : '#848e9c';
                            const gainStr = gainRatio.toFixed(2) + 'x';

                            // 市值历史（每条记录：相对时间 + 市值）
                            const historyHtml = (m.history || []).map(h => {
                                const hMcap = formatMcap(h.mcap);
                                const hRatio = m.start_mcap > 0 ? (h.mcap / m.start_mcap) : 1;
                                const hColor = hRatio >= 2 ? '#02c076' : hRatio >= 1.5 ? '#F0B90B' : '#848e9c';
                                return `<span style="display:inline-block;margin-right:8px;font-size:10px"><span style="color:#848e9c">${h.time}s</span>:<span style="color:${hColor}">${hMcap}</span></span>`;
                            }).join('');

                            return `<div class="data-item" style="padding:6px 0;border-bottom:1px solid #2b3139">
                                <div style="display:flex;justify-content:space-between;align-items:center">
                                    <div>
                                        ${chainBadge}
                                        <span class="symbol">${m.symbol || 'Unknown'}</span>
                                    </div>
                                    <div>
                                        <span style="color:#848e9c;font-size:10px;margin-right:4px">${m.elapsed}s</span>
                                        <span style="color:${gainColor};font-size:11px;font-weight:bold">${gainStr}</span>
                                    </div>
                                </div>
                                <div style="color:#848e9c;font-size:9px;margin-top:2px;word-break:break-all">${m.address.slice(0,8)}...${m.address.slice(-6)}</div>
                                <div style="margin-top:4px;line-height:1.6">${historyHtml || '<span style="color:#848e9c;font-size:10px">暂无数据</span>'}</div>
                            </div>`;
                        }).join('')}</div>`;
                    } else {
                        dataHtml += `<div class="no-data" style="padding:10px;color:#848e9c">暂无监测</div>`;
                    }
                    dataHtml += `</div>`;
                }

                // trade_service 显示持仓
                if (s.name === 'trade_service') {
                    let positions = s.recent?.positions || [];
                    let trades = s.recent?.trades || [];

                    // 格式化市值
                    const formatMcap = (mcap) => {
                        if (!mcap || mcap <= 0) return '-';
                        if (mcap >= 1000000) return '$' + (mcap/1000000).toFixed(1) + 'M';
                        if (mcap >= 1000) return '$' + (mcap/1000).toFixed(0) + 'k';
                        return '$' + mcap.toFixed(0);
                    };

                    dataHtml += `<div class="data-section">
                        <div class="data-title" style="display:flex;justify-content:space-between;align-items:center">
                            <span>📊 当前持仓 (${positions.length})</span>
                            <button onclick="openTradeModal()" style="background:#0ecb81;color:#fff;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:10px">配置</button>
                        </div>`;
                    if (positions.length > 0) {
                        dataHtml += `<div class="data-list" style="max-height:300px">${positions.map(p => {
                            const buyMcap = p.buy_mcap || 0;
                            const curMcap = p.current_mcap || 0;
                            const changePct = buyMcap > 0 ? ((curMcap - buyMcap) / buyMcap * 100) : 0;
                            const changeColor = changePct >= 0 ? '#0ecb81' : '#f6465d';
                            const changeSign = changePct >= 0 ? '+' : '';
                            const soldPct = (p.sold_ratio || 0) * 100;

                            // 生成市值曲线图
                            const history = p.mcap_history || [];
                            let chartHtml = '';
                            if (history.length > 1) {
                                const mcaps = history.map(pt => pt.mcap);
                                const minMcap = Math.min(...mcaps);
                                const maxMcap = Math.max(...mcaps);
                                const range = maxMcap - minMcap || 1;
                                const chartW = 120, chartH = 30;
                                const points = history.map((pt, i) => {
                                    const x = (i / (history.length - 1)) * chartW;
                                    const y = chartH - 2 - ((pt.mcap - minMcap) / range) * (chartH - 4);
                                    return x.toFixed(1) + ',' + y.toFixed(1);
                                }).join(' ');
                                const lineColor = curMcap >= buyMcap ? '#0ecb81' : '#f6465d';
                                chartHtml = '<svg width="' + chartW + '" height="' + chartH + '" style="margin-top:4px"><polyline points="' + points + '" fill="none" stroke="' + lineColor + '" stroke-width="1.5"/></svg>';
                            }

                            return `<div class="data-item" style="padding:6px 0;border-bottom:1px solid #2b3139">
                                <div style="display:flex;justify-content:space-between;align-items:center">
                                    <span class="symbol">${p.symbol || 'Unknown'}</span>
                                    <span style="color:${changeColor};font-weight:bold">${changeSign}${changePct.toFixed(1)}%</span>
                                </div>
                                <div style="font-size:10px;color:#848e9c;margin-top:3px">
                                    买入: ${formatMcap(buyMcap)} → 当前: ${formatMcap(curMcap)}
                                </div>
                                ${chartHtml}
                                <div style="font-size:10px;color:#848e9c;margin-top:2px">
                                    已卖: ${soldPct.toFixed(0)}% | 下次: ${p.next_sell_multiple || 2}x | @${p.author || '-'}
                                </div>
                            </div>`;
                        }).join('')}</div>`;
                    } else {
                        dataHtml += `<div class="no-data" style="padding:10px;color:#848e9c">暂无持仓</div>`;
                    }
                    dataHtml += `</div>`;

                    // 最近交易
                    dataHtml += `<div class="data-section" style="margin-top:8px">
                        <div class="data-title">📜 最近交易</div>`;
                    if (trades.length > 0) {
                        dataHtml += `<div class="data-list" style="max-height:120px">${trades.slice(0,10).map(t => {
                            const actionColor = t.action === 'buy' ? '#0ecb81' : '#f6465d';
                            const actionText = t.action === 'buy' ? '买入' : '卖出';
                            return `<div style="display:flex;justify-content:space-between;padding:4px 0;font-size:10px;border-bottom:1px solid #2b3139">
                                <span><span style="color:${actionColor};font-weight:bold">${actionText}</span> ${t.symbol || '-'}</span>
                                <span style="color:#848e9c">${formatTime(t.time)}</span>
                            </div>`;
                        }).join('')}</div>`;
                    } else {
                        dataHtml += `<div class="no-data" style="padding:10px;color:#848e9c">暂无交易</div>`;
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
                        let items = showAlpha ? alphaTokens : (showExclusive ? exclusiveTokens : (s.recent.items || []));
                        let errors = s.recent.errors || [];
                        // 根据选中的链过滤（仅在非优质/Alpha模式下）
                        const isSpecialMode = showExclusive || showAlpha;
                        const filteredItems = isSpecialMode ? items : (tokenChainFilter === 'ALL' ? items : items.filter(r => r.chain === tokenChainFilter));
                        const titleText = showAlpha ? '🅰️ Alpha代币' : (showExclusive ? '⭐ 优质代币' : '🪙 最近代币');
                        dataHtml += `<div class="data-section">
                            <div class="data-title" style="display:flex;justify-content:space-between;align-items:center">
                                <div style="display:flex;align-items:center;gap:8px">
                                    <span>${titleText}</span>
                                    ${!isSpecialMode ? `<div style="display:flex;gap:2px">
                                        <button onclick="setTokenChainFilter('ALL')" style="background:${tokenChainFilter==='ALL'?'#F0B90B':'#363c45'};color:${tokenChainFilter==='ALL'?'#000':'#eaecef'};border:none;padding:2px 6px;border-radius:3px;cursor:pointer;font-size:9px">全部</button>
                                        <button onclick="setTokenChainFilter('BSC')" style="background:${tokenChainFilter==='BSC'?'#F0B90B':'#363c45'};color:${tokenChainFilter==='BSC'?'#000':'#eaecef'};border:none;padding:2px 6px;border-radius:3px;cursor:pointer;font-size:9px">BSC</button>
                                        <button onclick="setTokenChainFilter('SOL')" style="background:${tokenChainFilter==='SOL'?'#9945FF':'#363c45'};color:#fff;border:none;padding:2px 6px;border-radius:3px;cursor:pointer;font-size:9px">SOL</button>
                                    </div>` : ''}
                                </div>
                                <div style="display:flex;gap:4px">
                                    ${(showExclusive || showAlpha) && tradeWhitelistMode ? `
                                        <button id="confirmTradeWhitelistBtn" onclick="confirmAddToTradeWhitelist()" style="background:#0ecb81;color:#fff;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:10px">确认加入</button>
                                        <button onclick="cancelTradeWhitelistMode()" style="background:#363c45;color:#eaecef;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:10px">取消</button>
                                    ` : showExclusive && exclusiveBlacklistMode ? `
                                        <button id="confirmBlacklistBtn" onclick="confirmAddToBlacklist()" style="background:#f6465d;color:#fff;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:10px">确认加黑</button>
                                        <button onclick="cancelExclusiveBlacklistMode()" style="background:#363c45;color:#eaecef;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:10px">取消</button>
                                    ` : `
                                        <button onclick="toggleAlphaMode()" style="background:${showAlpha?'#9945FF':'#363c45'};color:#fff;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:10px">${showAlpha?'返回':'Alpha'}</button>
                                        <button onclick="toggleExclusiveMode()" style="background:${showExclusive?'#02c076':'#363c45'};color:#fff;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:10px">${showExclusive?'返回':'优质'}</button>
                                        ${isSpecialMode ? `
                                            <button onclick="toggleTradeWhitelistMode()" style="background:#0ecb81;color:#fff;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:10px">加入白名单</button>
                                            ${showExclusive ? `
                                                <button onclick="toggleExclusiveBlacklistMode()" style="background:#848e9c;color:#fff;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:10px">一键加黑</button>
                                                <button onclick="openExclusiveBlacklistModal()" style="background:#f6465d;color:#fff;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:10px">黑名单</button>
                                            ` : ''}
                                        ` : `<button onclick="openInjectTokenModal()" style="background:#F0B90B;color:#000;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:10px">注入代币</button>`}
                                    `}
                                </div>
                            </div>`;
                        if (filteredItems.length > 0) {
                            dataHtml += `<div class="data-list">${filteredItems.map(r => {
                                    const chainBadge = r.chain === 'SOL' ? '<span style="background:#9945FF;color:#fff;padding:1px 4px;border-radius:3px;font-size:9px;margin-right:4px">SOL</span>' : (r.chain === 'TEST' ? '<span style="background:#848e9c;color:#fff;padding:1px 4px;border-radius:3px;font-size:9px;margin-right:4px">TEST</span>' : '<span style="background:#F0B90B;color:#000;padding:1px 4px;border-radius:3px;font-size:9px;margin-right:4px">BSC</span>');
                                    const shortCa = r.address ? (r.address.length > 16 ? r.address.slice(0,8) + '...' + r.address.slice(-6) : r.address) : '';
                                    const caHtml = shortCa ? `<span style="color:#848e9c;font-size:9px;font-family:monospace;margin-left:6px;cursor:pointer" title="点击复制: ${r.address}" onclick="copyText('${r.address}')">${shortCa}</span>` : '';
                                    const extraInfo = isSpecialMode && r.priceChange24h ? ` <span style="color:${r.priceChange24h>=0?'#02c076':'#f6465d'}">${r.priceChange24h>=0?'+':''}${(r.priceChange24h*100).toFixed(1)}%</span>` : '';

                                    // 优质代币/Alpha代币模式下的前缀标识
                                    let prefixHtml = '';
                                    if (isSpecialMode && r.address) {
                                        const isInWhitelist = tokenState.whitelistSet.has(r.address.toLowerCase());
                                        const isInBlacklist = tokenState.viewMode === ViewMode.EXCLUSIVE && tokenState.blacklistSet.has(r.address.toLowerCase());

                                        if (tokenState.editMode === EditMode.BLACKLIST && tokenState.viewMode === ViewMode.EXCLUSIVE) {
                                            // 黑名单编辑模式
                                            prefixHtml = `<input type="checkbox" id="bl-check-${r.address.slice(0,8)}" ${shouldBeChecked(r.address, isInBlacklist) ? 'checked' : ''} onclick="toggleSelectBlacklistAddr('${r.address}', ${isInBlacklist})" style="margin-right:6px;cursor:pointer;accent-color:#f6465d">`;
                                        } else if (tokenState.editMode === EditMode.WHITELIST) {
                                            // 白名单编辑模式
                                            prefixHtml = `<input type="checkbox" id="tw-check-${r.address.slice(0,8)}" ${shouldBeChecked(r.address, isInWhitelist) ? 'checked' : ''} onclick="toggleSelectTradeWhitelistAddr('${r.address}', ${isInWhitelist})" style="margin-right:6px;cursor:pointer;accent-color:#0ecb81">`;
                                        } else if (isInBlacklist) {
                                            // 已在黑名单中，点击可解除
                                            prefixHtml = `<span onclick="removeFromBlacklistQuick('${r.address}')" style="cursor:pointer;margin-right:6px;font-size:14px" title="点击解除黑名单">🚫</span>`;
                                        } else if (isInWhitelist) {
                                            // 已在白名单中，点击可移除
                                            prefixHtml = `<span onclick="removeFromTradeWhitelistQuick('${r.address}')" style="cursor:pointer;margin-right:6px;font-size:12px" title="点击移除白名单">✅</span>`;
                                        }
                                    }

                                    const timeStr = isSpecialMode ? formatDateTime(r.time/1000) : formatTime(r.time/1000);
                                    return `<div class="data-item">${prefixHtml}${chainBadge}<span class="symbol" style="cursor:pointer" title="点击复制" onclick="copyText('${r.symbol}')">${r.symbol}</span> ${r.name}${caHtml} <span class="time">${timeStr} | MC:${r.marketCap} H:${r.holders}${extraInfo}</span></div>`;
                                }).join('')}</div>`;
                        } else {
                            // 根据加载状态显示不同消息
                            let noDataMsg = '暂无代币';
                            if (isSpecialMode) {
                                if (tokenState.viewMode === ViewMode.EXCLUSIVE) {
                                    if (tokenState.exclusiveLoading) {
                                        noDataMsg = '加载中...';
                                    } else if (tokenState.exclusiveError) {
                                        noDataMsg = '加载失败: ' + tokenState.exclusiveError;
                                    } else {
                                        noDataMsg = '暂无优质代币';
                                    }
                                } else if (tokenState.viewMode === ViewMode.ALPHA) {
                                    if (tokenState.alphaLoading) {
                                        noDataMsg = '加载中...';
                                    } else if (tokenState.alphaError) {
                                        noDataMsg = '加载失败: ' + tokenState.alphaError;
                                    } else {
                                        noDataMsg = '暂无Alpha代币';
                                    }
                                }
                            }
                            dataHtml += `<div class="no-data" style="padding:10px;color:#848e9c">${noDataMsg}</div>`;
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
                        // 测试撮合按钮 + 黑名单 + 提示词按钮 + 自动交易
                        dataHtml += `<div class="data-section">
                            <div class="data-title" style="display:flex;justify-content:space-between;align-items:center">
                                <span>🔍 撮合尝试</span>
                                <div style="display:flex;gap:4px">
                                    <button onclick="openTradeModal()" style="background:#0ecb81;color:#fff;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:10px">自动交易</button>
                                    <button onclick="openBlacklistModal()" style="background:#f6465d;color:#fff;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:10px">黑名单</button>
                                    <button onclick="openPromptModal()" style="background:#848e9c;color:#fff;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:10px">提示词</button>
                                    <button onclick="openTestMatchModal()" style="background:#F0B90B;color:#000;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:10px">测试撮合</button>
                                </div>
                            </div>`;
                        if (attemptList.length > 0) {
                            dataHtml += `<div class="data-list">${attemptList.map(r => {
                                // 检测状态
                                const pendingInfo = pendingMap[r.content];
                                let statusBadge;
                                if (pendingInfo) {
                                    statusBadge = `<span class="countdown" data-expire="${pendingInfo.expire_time}" style="background:#F0B90B;color:#000;padding:2px 6px;border-radius:4px;font-size:10px;margin-left:6px">检测中 --:--</span>`;
                                } else {
                                    statusBadge = `<span style="background:#02c076;color:#fff;padding:2px 6px;border-radius:4px;font-size:10px;margin-left:6px">已完成</span>`;
                                }
                                let matchStatus = r.matched > 0 ? `<span class="symbol">✓ ${r.matched}个匹配</span>` : '<span style="color:#848e9c">无匹配</span>';

                                // 匹配任务状态显示
                                const tasks = r.match_tasks || {};
                                const taskStatusIcon = (status) => {
                                    if (status === 'success') return '✅';
                                    if (status === 'no_match') return '❌';
                                    if (status === 'skipped') return '⏭️';
                                    if (status === 'running') return '🔄';
                                    if (status === 'error') return '⚠️';
                                    return '⏳';  // pending
                                };
                                const taskNames = {
                                    'new_hardcoded': '新币⚡',
                                    'new_ai': '新币🤖',
                                    'exclusive_hardcoded': '优质⚡',
                                    'exclusive_ai': '优质🤖'
                                };
                                let tasksHtml = Object.entries(tasks).map(([key, val]) => {
                                    const icon = taskStatusIcon(val.status);
                                    const name = taskNames[key] || key;
                                    const resultStr = val.result ? ` (${val.result})` : '';
                                    return `<span style="margin-right:6px;font-size:10px" title="${key}: ${val.status}${resultStr}">${icon}${name}</span>`;
                                }).join('');

                                // 匹配到的代币列表
                                const matchedTokens = r.matched_tokens || [];
                                let tokensHtml = '';
                                if (matchedTokens.length > 0) {
                                    tokensHtml = `<div style="margin-top:4px;font-size:10px">🎯 匹配: ${matchedTokens.map(t => {
                                        const methodIcon = t.method === 'ai' ? '🤖' : '⚡';
                                        const sourceIcon = t.source === 'exclusive' ? '📦' : '🆕';
                                        return `<span style="color:#0ecb81;margin-right:6px">${t.symbol} ${methodIcon}${sourceIcon} M:${t.time_cost || 0}ms S:${t.system_latency || 0}ms</span>`;
                                    }).join('')}</div>`;
                                }

                                // 处理 follow 类型事件，拼接 refAuthorName
                                const displayContent = r.type === 'follow'
                                    ? '关注了 @' + (r.refAuthor || '') + (r.refAuthorName ? ' (' + r.refAuthorName + ')' : '')
                                    : r.content;

                                return `<div class="data-item">
                                    <div><span class="author">@${r.author}</span> ${matchStatus} ${statusBadge} <span class="time">${formatTime(r.time)}</span></div>
                                    <div class="content">${escapeHtml(displayContent || '')}</div>
                                    <div style="color:#848e9c;font-size:10px;margin-top:4px">任务: ${tasksHtml}</div>
                                    ${tokensHtml}
                                    <div style="color:#848e9c;font-size:10px">窗口代币(${r.tokens_in_window}): ${escapeHtml(r.window_tokens && r.window_tokens.length > 0 ? r.window_tokens.join(', ') : '(无)')}</div>
                                </div>`;
                            }).join('')}</div>`;
                        } else {
                            dataHtml += `<div class="no-data" style="padding:10px;color:#848e9c">暂无撮合尝试</div>`;
                        }
                        dataHtml += `</div>`;
                        if (matchList.length > 0) {
                            dataHtml += `<div class="data-section">
                                <div class="data-title">🎯 成功匹配</div>
                                <div class="data-list">${matchList.map(r => {
                                    // 兼容新旧格式：tokens 可能是 [{symbol, time_cost, method, source}] 或 ['symbol']
                                    const tokenInfo = r.tokens.map(t => {
                                        if (typeof t === 'string') return t;
                                        const method = t.method === 'ai' ? '🤖' : '⚡';
                                        const source = t.source === 'exclusive' ? '📦' : '🆕';
                                        return `${t.symbol} <span style="color:#848e9c;font-size:10px">${method} M:${t.time_cost}ms S:${t.system_latency || 0}ms ${source}</span>`;
                                    }).join(', ');
                                    return `<div class="data-item"><span class="author">@${r.author}</span> → <span class="symbol">${tokenInfo}</span> <span class="time">${formatTime(r.time)}</span></div>`;
                                }).join('')}</div>
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
                        <div class="timeline-bars" id="${s.name}-timeline">${bars}</div>
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
                // 复选框（删除模式下显示）
                const checkboxHtml = deleteMode
                    ? `<div style="margin-right:12px;display:flex;align-items:center">
                        <input type="checkbox" id="check-${m.id}" ${selectedIds.has(m.id) ? 'checked' : ''}
                            onclick="toggleSelectRecord(${m.id})"
                            style="width:18px;height:18px;cursor:pointer;accent-color:#f6465d">
                       </div>`
                    : '';

                // 最佳代币
                const bestTokensHtml = m.best_tokens && m.best_tokens.length > 0
                    ? m.best_tokens.map(t => `<span class="token-badge">${t.token_symbol}</span>`).join('')
                    : '<span style="color:#848e9c">无</span>';

                return `<div class="match-item" style="${deleteMode ? 'display:flex;align-items:flex-start' : ''}">
                    ${checkboxHtml}
                    <div style="flex:1">
                        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
                            <span class="match-author">@${m.author || 'Unknown'}</span>
                            <span style="color:#848e9c;font-size:11px">${formatTime(m.time)}</span>
                        </div>
                        <div class="match-content" style="margin-bottom:8px">${m.content || ''}</div>
                        <div>
                            <span style="color:#f0b90b;font-size:12px">🎯 最佳代币:</span>
                            <span class="match-tokens">${bestTokensHtml}</span>
                        </div>
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

                // 获取 Alpha Call 监测数据
                let monitoringData = {count: 0, contracts: []};
                try {
                    const monitorResp = await fetch('api/monitoring');
                    monitoringData = await monitorResp.json();
                } catch (e) {
                    console.warn('Failed to fetch monitoring data:', e);
                }

                renderServices(statusData, monitoringData);

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

        // 黑名单弹窗
        let currentBlacklist = [];

        // 硬编码匹配开关
        async function toggleHardcodedMatch() {
            try {
                // 先获取当前状态
                const getResp = await fetch('api/hardcoded_match');
                const getData = await getResp.json();
                const currentEnabled = getData.enabled;

                // 切换状态
                const resp = await fetch('api/hardcoded_match', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({enabled: !currentEnabled})
                });
                const data = await resp.json();

                // 更新按钮显示
                const btn = document.getElementById('hardcodedToggleBtn');
                if (btn) {
                    btn.style.background = data.enabled ? '#0ecb81' : '#848e9c';
                    btn.textContent = data.enabled ? '硬编码:开' : '硬编码:关';
                }
            } catch (e) {
                console.error('切换硬编码匹配失败:', e);
            }
        }

        // 手动触发高频模式
        async function triggerBoostMode() {
            try {
                const resp = await fetch('api/token/boost', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({author: 'manual'})
                });
                const data = await resp.json();
                if (data.success) {
                    console.log('高频模式已激活');
                }
            } catch (e) {
                console.error('触发高频模式失败:', e);
            }
        }

        function openBlacklistModal() {
            document.getElementById('blacklistModal').style.display = 'flex';
            document.getElementById('blacklistInput').value = '';
            loadBlacklist();
        }

        function closeBlacklistModal() {
            document.getElementById('blacklistModal').style.display = 'none';
        }

        async function loadBlacklist() {
            try {
                const resp = await fetch('api/blacklist');
                const data = await resp.json();
                currentBlacklist = data.blacklist || [];
                renderBlacklist();
            } catch (e) {
                document.getElementById('blacklistList').innerHTML =
                    '<div style="color:#f6465d;text-align:center;padding:20px">加载失败: ' + e.message + '</div>';
            }
        }

        function renderBlacklist() {
            const container = document.getElementById('blacklistList');
            if (currentBlacklist.length === 0) {
                container.innerHTML = '<div style="color:#848e9c;text-align:center;padding:20px">暂无黑名单</div>';
                return;
            }
            container.innerHTML = currentBlacklist.map(name =>
                `<div style="display:flex;justify-content:space-between;align-items:center;padding:8px;border-bottom:1px solid #2b3139">
                    <span style="color:#eaecef">${name}</span>
                    <button onclick="removeFromBlacklist('${name}')" style="background:#f6465d;color:#fff;border:none;padding:2px 8px;border-radius:4px;cursor:pointer;font-size:10px">删除</button>
                </div>`
            ).join('');
        }

        async function addToBlacklist() {
            const input = document.getElementById('blacklistInput');
            const tokenName = input.value.trim();
            if (!tokenName) {
                alert('请输入代币名称');
                return;
            }

            try {
                const resp = await fetch('api/blacklist', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ token_name: tokenName })
                });
                const data = await resp.json();
                if (data.success) {
                    currentBlacklist = data.blacklist || [];
                    renderBlacklist();
                    input.value = '';
                } else {
                    alert('添加失败: ' + (data.error || '未知错误'));
                }
            } catch (e) {
                alert('添加失败: ' + e.message);
            }
        }

        async function removeFromBlacklist(tokenName) {
            try {
                const resp = await fetch('api/blacklist', {
                    method: 'DELETE',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ token_name: tokenName })
                });
                const data = await resp.json();
                if (data.success) {
                    currentBlacklist = data.blacklist || [];
                    renderBlacklist();
                } else {
                    alert('删除失败: ' + (data.error || '未知错误'));
                }
            } catch (e) {
                alert('删除失败: ' + e.message);
            }
        }

        document.getElementById('blacklistModal').addEventListener('click', function(e) {
            if (e.target === this) closeBlacklistModal();
        });

        // 优质代币合约黑名单弹窗
        let currentExclusiveBlacklist = [];

        function openExclusiveBlacklistModal() {
            document.getElementById('exclusiveBlacklistModal').style.display = 'flex';
            document.getElementById('exclusiveBlacklistInput').value = '';
            loadExclusiveBlacklist();
        }

        function closeExclusiveBlacklistModal() {
            document.getElementById('exclusiveBlacklistModal').style.display = 'none';
        }

        async function loadExclusiveBlacklist() {
            try {
                const resp = await fetch('api/exclusive_blacklist');
                const data = await resp.json();
                currentExclusiveBlacklist = data.blacklist || [];
                renderExclusiveBlacklist();
            } catch (e) {
                document.getElementById('exclusiveBlacklistList').innerHTML =
                    '<div style="color:#f6465d;text-align:center;padding:20px">加载失败: ' + e.message + '</div>';
            }
        }

        function renderExclusiveBlacklist() {
            const container = document.getElementById('exclusiveBlacklistList');
            if (currentExclusiveBlacklist.length === 0) {
                container.innerHTML = '<div style="color:#848e9c;text-align:center;padding:20px">暂无黑名单</div>';
                return;
            }
            container.innerHTML = currentExclusiveBlacklist.map(addr => {
                const shortAddr = addr.length > 20 ? addr.slice(0,10) + '...' + addr.slice(-8) : addr;
                return `<div style="display:flex;justify-content:space-between;align-items:center;padding:8px;border-bottom:1px solid #2b3139">
                    <span style="color:#eaecef;font-family:monospace;font-size:11px;cursor:pointer" title="${addr}" onclick="copyText('${addr}')">${shortAddr}</span>
                    <button onclick="removeFromExclusiveBlacklist('${addr}')" style="background:#f6465d;color:#fff;border:none;padding:2px 8px;border-radius:4px;cursor:pointer;font-size:10px">删除</button>
                </div>`;
            }).join('');
        }

        async function addToExclusiveBlacklist() {
            const input = document.getElementById('exclusiveBlacklistInput');
            const address = input.value.trim();
            if (!address) {
                alert('请输入合约地址');
                return;
            }

            try {
                const resp = await fetch('api/exclusive_blacklist', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ address: address })
                });
                const data = await resp.json();
                if (data.success) {
                    currentExclusiveBlacklist = data.blacklist || [];
                    renderExclusiveBlacklist();
                    input.value = '';
                } else {
                    alert('添加失败: ' + (data.error || '未知错误'));
                }
            } catch (e) {
                alert('添加失败: ' + e.message);
            }
        }

        async function removeFromExclusiveBlacklist(address) {
            try {
                const resp = await fetch('api/exclusive_blacklist', {
                    method: 'DELETE',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ address: address })
                });
                const data = await resp.json();
                if (data.success) {
                    currentExclusiveBlacklist = data.blacklist || [];
                    renderExclusiveBlacklist();
                } else {
                    alert('删除失败: ' + (data.error || '未知错误'));
                }
            } catch (e) {
                alert('删除失败: ' + e.message);
            }
        }

        document.getElementById('exclusiveBlacklistModal').addEventListener('click', function(e) {
            if (e.target === this) closeExclusiveBlacklistModal();
        });

        // 作者白名单弹窗
        let currentAuthorWhitelist = [];
        let authorWhitelistEnabled = false;

        function openAuthorWhitelistModal() {
            document.getElementById('authorWhitelistModal').style.display = 'flex';
            document.getElementById('authorWhitelistInput').value = '';
            document.getElementById('authorWhitelistBatch').value = '';
            loadAuthorWhitelist();
        }

        function closeAuthorWhitelistModal() {
            document.getElementById('authorWhitelistModal').style.display = 'none';
        }

        async function loadAuthorWhitelist() {
            try {
                const resp = await fetch('api/author_whitelist');
                const data = await resp.json();
                currentAuthorWhitelist = data.authors || [];
                authorWhitelistEnabled = data.enabled || false;
                renderAuthorWhitelist();
                updateWhitelistToggleBtn();
            } catch (e) {
                document.getElementById('authorWhitelistList').innerHTML =
                    '<div style="color:#f6465d;text-align:center;padding:20px">加载失败: ' + e.message + '</div>';
            }
        }

        function updateWhitelistToggleBtn() {
            const btn = document.getElementById('whitelistToggleBtn');
            if (authorWhitelistEnabled) {
                btn.textContent = '开启中';
                btn.style.background = '#0ecb81';
                btn.style.color = '#fff';
            } else {
                btn.textContent = '已关闭';
                btn.style.background = '#363c45';
                btn.style.color = '#eaecef';
            }
        }

        function renderAuthorWhitelist() {
            const container = document.getElementById('authorWhitelistList');
            if (currentAuthorWhitelist.length === 0) {
                container.innerHTML = '<div style="color:#848e9c;text-align:center;padding:20px">暂无白名单作者</div>';
                return;
            }
            container.innerHTML = currentAuthorWhitelist.map(author =>
                `<div style="display:flex;justify-content:space-between;align-items:center;padding:8px;border-bottom:1px solid #2b3139">
                    <span style="color:#0ecb81">@${author}</span>
                    <button onclick="removeFromAuthorWhitelist('${author}')" style="background:#f6465d;color:#fff;border:none;padding:2px 8px;border-radius:4px;cursor:pointer;font-size:10px">删除</button>
                </div>`
            ).join('');
        }

        async function toggleAuthorWhitelist() {
            try {
                const resp = await fetch('api/author_whitelist/toggle', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({})
                });
                const data = await resp.json();
                authorWhitelistEnabled = data.enabled;
                updateWhitelistToggleBtn();
            } catch (e) {
                alert('切换失败: ' + e.message);
            }
        }

        async function addToAuthorWhitelist() {
            const input = document.getElementById('authorWhitelistInput');
            const author = input.value.trim().replace(/^@/, '');
            if (!author) {
                alert('请输入作者 handle');
                return;
            }

            try {
                const resp = await fetch('api/author_whitelist/add', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ author: author })
                });
                const data = await resp.json();
                if (data.success) {
                    input.value = '';
                    loadAuthorWhitelist();
                } else {
                    alert('添加失败: ' + (data.error || '未知错误'));
                }
            } catch (e) {
                alert('添加失败: ' + e.message);
            }
        }

        async function batchAddAuthorWhitelist() {
            const textarea = document.getElementById('authorWhitelistBatch');
            const text = textarea.value.trim();
            if (!text) {
                alert('请输入作者列表');
                return;
            }

            // 支持换行或逗号分隔
            const authors = text.split(/[,\\n]/).map(a => a.trim().replace(/^@/, '')).filter(a => a);
            if (authors.length === 0) {
                alert('未识别到有效作者');
                return;
            }

            try {
                const resp = await fetch('api/author_whitelist/batch', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ authors: authors })
                });
                const data = await resp.json();
                if (data.success) {
                    textarea.value = '';
                    alert(`成功添加 ${data.count} 个作者`);
                    loadAuthorWhitelist();
                } else {
                    alert('添加失败: ' + (data.error || '未知错误'));
                }
            } catch (e) {
                alert('添加失败: ' + e.message);
            }
        }

        async function removeFromAuthorWhitelist(author) {
            try {
                const resp = await fetch('api/author_whitelist/remove', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ author: author })
                });
                const data = await resp.json();
                if (data.success) {
                    loadAuthorWhitelist();
                } else {
                    alert('删除失败: ' + (data.error || '未知错误'));
                }
            } catch (e) {
                alert('删除失败: ' + e.message);
            }
        }

        document.getElementById('authorWhitelistModal').addEventListener('click', function(e) {
            if (e.target === this) closeAuthorWhitelistModal();
        });

        // 白名单历史推文弹窗
        function openWhitelistNewsModal() {
            document.getElementById('whitelistNewsModal').style.display = 'flex';
            // 填充作者下拉框
            const select = document.getElementById('whitelistNewsAuthor');
            select.innerHTML = '<option value="">全部作者</option>';
            currentAuthorWhitelist.forEach(author => {
                select.innerHTML += `<option value="${author}">@${author}</option>`;
            });
            loadWhitelistNews();
        }

        function closeWhitelistNewsModal() {
            document.getElementById('whitelistNewsModal').style.display = 'none';
        }

        async function loadWhitelistNews() {
            const container = document.getElementById('whitelistNewsList');
            const author = document.getElementById('whitelistNewsAuthor').value;
            const limit = document.getElementById('whitelistNewsLimit').value || 50;

            container.innerHTML = '<div style="color:#848e9c;text-align:center;padding:40px">加载中...</div>';

            try {
                const url = `api/whitelist_news?limit=${limit}` + (author ? `&author=${encodeURIComponent(author)}` : '');
                const resp = await fetch(url);
                const data = await resp.json();

                if (!data.news || data.news.length === 0) {
                    container.innerHTML = '<div style="color:#848e9c;text-align:center;padding:40px">暂无推文记录</div>';
                    return;
                }

                let html = `<div style="color:#848e9c;font-size:11px;margin-bottom:12px">共 ${data.total} 条记录，显示最近 ${data.news.length} 条</div>`;

                data.news.forEach(news => {
                    const time = news.news_time ? new Date(news.news_time * 1000).toLocaleString() : '';
                    const content = (news.news_content || '').substring(0, 300);
                    const typeColors = {
                        'newTweet': '#0ecb81',
                        'reply': '#F0B90B',
                        'retweet': '#1DA1F2',
                        'quote': '#9B59B6'
                    };
                    const typeColor = typeColors[news.news_type] || '#848e9c';

                    html += `
                        <div style="background:#181a20;border-radius:6px;padding:12px;margin-bottom:8px;border-left:3px solid ${typeColor}">
                            <div style="display:flex;justify-content:space-between;margin-bottom:8px">
                                <div>
                                    <span style="color:#F0B90B;font-weight:bold">@${news.news_author || ''}</span>
                                    <span style="color:#848e9c;margin-left:8px;font-size:11px">${news.news_author_name || ''}</span>
                                    <span style="color:${typeColor};margin-left:8px;font-size:10px;padding:2px 6px;background:${typeColor}22;border-radius:3px">${news.news_type || ''}</span>
                                </div>
                                <span style="color:#848e9c;font-size:11px">${time}</span>
                            </div>
                            <div style="color:#eaecef;font-size:13px;line-height:1.5;word-break:break-all">${content}${content.length >= 300 ? '...' : ''}</div>
                            ${news.ref_content ? `<div style="margin-top:8px;padding:8px;background:#0b0e11;border-radius:4px;border-left:2px solid #363c45"><span style="color:#848e9c;font-size:11px">引用 @${news.ref_author || ''}:</span><div style="color:#b7bdc6;font-size:12px;margin-top:4px">${(news.ref_content || '').substring(0, 150)}...</div></div>` : ''}
                        </div>
                    `;
                });

                container.innerHTML = html;
            } catch (e) {
                container.innerHTML = `<div style="color:#f6465d;text-align:center;padding:40px">加载失败: ${e.message}</div>`;
            }
        }

        document.getElementById('whitelistNewsModal').addEventListener('click', function(e) {
            if (e.target === this) closeWhitelistNewsModal();
        });

        // 提示词弹窗
        let promptData = null;
        let currentPromptTab = 'deepseek';

        function openPromptModal() {
            document.getElementById('promptModal').style.display = 'flex';
            loadPromptTemplate();
        }

        function closePromptModal() {
            document.getElementById('promptModal').style.display = 'none';
        }

        async function loadPromptTemplate() {
            try {
                const resp = await fetch('api/prompt_template');
                promptData = await resp.json();
                renderPromptContent();
            } catch (e) {
                document.getElementById('promptContent').textContent = '加载失败: ' + e.message;
            }
        }

        function switchPromptTab(tab) {
            currentPromptTab = tab;
            document.getElementById('promptTabDeepseek').style.background = tab === 'deepseek' ? '#F0B90B' : '#363c45';
            document.getElementById('promptTabDeepseek').style.color = tab === 'deepseek' ? '#000' : '#eaecef';
            document.getElementById('promptTabGemini').style.background = tab === 'gemini' ? '#F0B90B' : '#363c45';
            document.getElementById('promptTabGemini').style.color = tab === 'gemini' ? '#000' : '#eaecef';
            renderPromptContent();
        }

        function renderPromptContent() {
            if (!promptData) return;
            const content = currentPromptTab === 'deepseek' ? promptData.deepseek : promptData.gemini;
            document.getElementById('promptContent').textContent = content;

            const blacklistCount = promptData.blacklist ? promptData.blacklist.length : 0;
            const examplesCount = promptData.examples_count || 0;
            document.getElementById('promptStats').innerHTML =
                `最佳实践样例: <span style="color:#0ecb81">${examplesCount}</span> 条 | 黑名单: <span style="color:#f6465d">${blacklistCount}</span> 个`;
        }

        document.getElementById('promptModal').addEventListener('click', function(e) {
            if (e.target === this) closePromptModal();
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

        // ==================== 自动交易功能 ====================
        let tradeConfig = {};
        let currentTradeTab = 'config';

        function openTradeModal() {
            document.getElementById('tradeModal').style.display = 'flex';
            loadTradeConfig();
            switchTradeTab('config');
        }

        function closeTradeModal() {
            document.getElementById('tradeModal').style.display = 'none';
        }

        document.getElementById('tradeModal').addEventListener('click', function(e) {
            if (e.target === this) closeTradeModal();
        });

        function switchTradeTab(tab) {
            currentTradeTab = tab;
            const tabs = ['config', 'positions', 'history', 'authors', 'tokens'];
            tabs.forEach(t => {
                document.getElementById('tradeTab' + t.charAt(0).toUpperCase() + t.slice(1)).style.background = t === tab ? '#f0b90b' : '#363c45';
                document.getElementById('tradeTab' + t.charAt(0).toUpperCase() + t.slice(1)).style.color = t === tab ? '#000' : '#eaecef';
                document.getElementById('tradePanel' + t.charAt(0).toUpperCase() + t.slice(1)).style.display = t === tab ? 'block' : 'none';
            });

            if (tab === 'positions') loadTradePositions();
            else if (tab === 'history') loadTradeHistory();
            else if (tab === 'authors') loadTradeAuthors();
            else if (tab === 'tokens') loadTradeTokens();
        }

        async function loadTradeConfig() {
            try {
                const resp = await fetch('api/trade/config');
                tradeConfig = await resp.json();
                document.getElementById('tradeNewTokenAmount').value = tradeConfig.new_token_buy_amount || tradeConfig.default_buy_amount || 0.5;
                document.getElementById('tradeOldTokenAmount').value = tradeConfig.old_token_buy_amount || 0.3;
                document.getElementById('tradeSellMultiple').value = tradeConfig.sell_trigger_multiple || 2.0;
                document.getElementById('tradeSellPct').value = tradeConfig.sell_percentage || 0.5;
                document.getElementById('tradeStopLoss').value = tradeConfig.stop_loss_ratio || 0.5;
                document.getElementById('tradeMaxPositions').value = tradeConfig.max_positions || 10;
                document.getElementById('tradeWhitelistMode').value = tradeConfig.whitelist_mode || 'any';
                document.getElementById('tradeNoChangeTimeout').value = tradeConfig.no_change_timeout ?? 20;
                document.getElementById('tradeAllowNewTokenByAuthor').checked = tradeConfig.allow_new_token_by_author ?? true;
                updateTradeEnabledBtn(tradeConfig.enabled);
            } catch (e) {
                console.error('加载交易配置失败:', e);
            }
        }

        function updateTradeEnabledBtn(enabled) {
            const btn = document.getElementById('tradeEnabledBtn');
            if (enabled) {
                btn.textContent = '已启用';
                btn.style.background = '#0ecb81';
                btn.style.color = '#fff';
            } else {
                btn.textContent = '已禁用';
                btn.style.background = '#f6465d';
                btn.style.color = '#fff';
            }
            tradeConfig.enabled = enabled;
        }

        async function toggleTradeEnabled() {
            const newEnabled = !tradeConfig.enabled;
            try {
                const resp = await fetch('api/trade/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ enabled: newEnabled })
                });
                if (resp.ok) {
                    updateTradeEnabledBtn(newEnabled);
                }
            } catch (e) {
                alert('切换失败: ' + e.message);
            }
        }

        async function saveTradeConfig() {
            const config = {
                new_token_buy_amount: parseFloat(document.getElementById('tradeNewTokenAmount').value) || 0.5,
                old_token_buy_amount: parseFloat(document.getElementById('tradeOldTokenAmount').value) || 0.3,
                sell_trigger_multiple: parseFloat(document.getElementById('tradeSellMultiple').value) || 2.0,
                sell_percentage: parseFloat(document.getElementById('tradeSellPct').value) || 0.5,
                stop_loss_ratio: parseFloat(document.getElementById('tradeStopLoss').value) || 0.5,
                max_positions: parseInt(document.getElementById('tradeMaxPositions').value) || 10,
                whitelist_mode: document.getElementById('tradeWhitelistMode').value || 'any',
                no_change_timeout: parseInt(document.getElementById('tradeNoChangeTimeout').value) ?? 20,
                allow_new_token_by_author: document.getElementById('tradeAllowNewTokenByAuthor').checked
            };
            try {
                const resp = await fetch('api/trade/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(config)
                });
                if (resp.ok) {
                    alert('配置已保存');
                } else {
                    alert('保存失败');
                }
            } catch (e) {
                alert('保存失败: ' + e.message);
            }
        }

        async function loadTradePositions() {
            try {
                const resp = await fetch('api/trade/positions');
                const data = await resp.json();
                const positions = data.positions || [];
                const container = document.getElementById('tradePositionsList');

                if (positions.length === 0) {
                    container.innerHTML = '<div style="color:#848e9c;text-align:center;padding:20px">暂无持仓</div>';
                    return;
                }

                // 按地址合并持仓
                const mergedPositions = {};
                positions.forEach(p => {
                    const addr = p.address.toLowerCase();
                    if (!mergedPositions[addr]) {
                        mergedPositions[addr] = {
                            symbol: p.symbol,
                            address: p.address,
                            positions: [],
                            total_buy_amount: 0,
                            total_buy_mcap: 0,
                            current_mcap: p.current_mcap,
                            trigger_types: new Set(),
                            api_call_count_60s: p.api_call_count_60s || 0
                        };
                    }
                    mergedPositions[addr].positions.push(p);
                    mergedPositions[addr].total_buy_amount += p.buy_amount || 0;
                    mergedPositions[addr].total_buy_mcap += (p.buy_mcap || 0) * (p.buy_amount || 1);
                    mergedPositions[addr].trigger_types.add(p.trigger_type || '');
                    mergedPositions[addr].api_call_count_60s = p.api_call_count_60s || 0;
                });

                container.innerHTML = Object.values(mergedPositions).map(m => {
                    const avgBuyMcap = m.total_buy_amount > 0 ? m.total_buy_mcap / m.total_buy_amount : m.positions[0].buy_mcap;
                    const changePct = avgBuyMcap > 0 ? ((m.current_mcap - avgBuyMcap) / avgBuyMcap * 100) : 0;
                    const changeColor = changePct >= 0 ? '#0ecb81' : '#f6465d';
                    const changeSign = changePct >= 0 ? '+' : '';
                    const triggers = Array.from(m.trigger_types).filter(t => t).join(', ') || '-';
                    const posCount = m.positions.length;
                    const apiFreq = m.api_call_count_60s || 0;
                    const freqStyle = apiFreq > 0 ? 'color:#f0b90b' : 'color:#848e9c';

                    return `
                        <div style="background:#0b0e11;padding:12px;border-radius:4px;margin-bottom:8px">
                            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                                <span style="color:#f0b90b;font-weight:bold">${escapeHtml(m.symbol)} ${posCount > 1 ? '<span style="color:#848e9c;font-size:11px">(×' + posCount + ')</span>' : ''}</span>
                                <span style="color:${changeColor}">${changeSign}${changePct.toFixed(1)}%</span>
                            </div>
                            <div style="font-size:11px;color:#848e9c;margin-bottom:4px">
                                买入: $${(avgBuyMcap/1e6).toFixed(2)}M → 当前: $${(m.current_mcap/1e6).toFixed(2)}M
                            </div>
                            <div style="font-size:11px;color:#848e9c;margin-bottom:8px">
                                投入: ${m.total_buy_amount.toFixed(2)} BNB | 触发: ${triggers} | <span style="${freqStyle}">频率: ${apiFreq}次/分</span>
                            </div>
                            <div style="display:flex;gap:8px">
                                ${m.positions.map(p => `<button onclick="closePosition('${p.id}')" style="background:#f6465d;color:#fff;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:10px">平仓${posCount > 1 ? '#' + (m.positions.indexOf(p) + 1) : ''}</button>`).join('')}
                                <button onclick="copyText('${m.address}')" style="background:#363c45;color:#eaecef;border:none;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:11px">复制CA</button>
                            </div>
                        </div>
                    `;
                }).join('');
            } catch (e) {
                document.getElementById('tradePositionsList').innerHTML = '<div style="color:#f6465d">加载失败: ' + e.message + '</div>';
            }
        }

        async function closePosition(positionId) {
            if (!confirm('确定要平仓吗?')) return;
            try {
                const resp = await fetch('api/trade/positions/' + positionId, { method: 'DELETE' });
                if (resp.ok) {
                    loadTradePositions();
                } else {
                    alert('平仓失败');
                }
            } catch (e) {
                alert('平仓失败: ' + e.message);
            }
        }

        async function loadTradeHistory() {
            try {
                const resp = await fetch('api/trade/history?limit=30');
                const data = await resp.json();
                const history = data.history || [];
                const container = document.getElementById('tradeHistoryList');

                if (history.length === 0) {
                    container.innerHTML = '<div style="color:#848e9c;text-align:center;padding:20px">暂无交易记录</div>';
                    return;
                }

                // 原因翻译
                const reasonMap = {
                    'author_whitelist': '作者白名单',
                    'token_whitelist': '代币白名单',
                    'both_whitelist': '双重白名单',
                    'take_profit': '止盈',
                    'stop_loss': '止损',
                    'no_change': '无波动',
                    'manual': '手动'
                };

                container.innerHTML = history.map(h => {
                    let actionColor = '#f6465d'; // Default red for sell/error
                    let actionText = '卖出';
                    if (h.action === 'buy') {
                        actionColor = '#0ecb81';
                        actionText = '买入';
                    } else if (h.action === 'filter') {
                        actionColor = '#848e9c';
                        actionText = '过滤';
                    }
                    const reasonText = reasonMap[h.reason] || h.reason || '-';
                    return `
                        <div style="background:#0b0e11;padding:8px 12px;border-radius:4px;margin-bottom:4px">
                            <div style="display:flex;justify-content:space-between;align-items:center">
                                <div>
                                    <span style="color:${actionColor};font-weight:bold">${actionText}</span>
                                    <span style="color:#eaecef;margin-left:8px">${escapeHtml(h.symbol)}</span>
                                    <span style="color:#848e9c;margin-left:8px;font-size:11px">${h.amount}</span>
                                </div>
                                <span style="color:#848e9c;font-size:11px">${formatTime(h.time)}</span>
                            </div>
                            <div style="font-size:10px;color:#5c6370;margin-top:4px">
                                原因: ${reasonText} | 市值: $${((h.mcap || 0)/1e6).toFixed(2)}M
                            </div>
                        </div>
                    `;
                }).join('');
            } catch (e) {
                document.getElementById('tradeHistoryList').innerHTML = '<div style="color:#f6465d">加载失败: ' + e.message + '</div>';
            }
        }

        async function loadTradeAuthors() {
            try {
                const resp = await fetch('api/trade/whitelist/authors');
                const data = await resp.json();
                const authors = data.authors || [];
                const container = document.getElementById('tradeAuthorsList');

                if (authors.length === 0) {
                    container.innerHTML = '<div style="color:#848e9c;text-align:center;padding:20px">白名单为空</div>';
                    return;
                }

                container.innerHTML = authors.map(a => `
                    <div style="background:#0b0e11;padding:8px 12px;border-radius:4px;margin-bottom:4px;display:flex;justify-content:space-between;align-items:center">
                        <span style="color:#f0b90b">@${escapeHtml(a)}</span>
                        <button onclick="removeTradeAuthor('${escapeHtml(a)}')" style="background:#f6465d;color:#fff;border:none;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:11px">移除</button>
                    </div>
                `).join('');
            } catch (e) {
                document.getElementById('tradeAuthorsList').innerHTML = '<div style="color:#f6465d">加载失败: ' + e.message + '</div>';
            }
        }

        async function addTradeAuthor() {
            const author = document.getElementById('tradeNewAuthor').value.trim();
            if (!author) return;
            try {
                const resp = await fetch('api/trade/whitelist/authors', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ author })
                });
                if (resp.ok) {
                    document.getElementById('tradeNewAuthor').value = '';
                    loadTradeAuthors();
                }
            } catch (e) {
                alert('添加失败: ' + e.message);
            }
        }

        async function removeTradeAuthor(author) {
            try {
                const resp = await fetch('api/trade/whitelist/authors', {
                    method: 'DELETE',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ author })
                });
                if (resp.ok) {
                    loadTradeAuthors();
                }
            } catch (e) {
                alert('移除失败: ' + e.message);
            }
        }

        async function loadTradeTokens() {
            try {
                const resp = await fetch('api/trade/whitelist/tokens');
                const data = await resp.json();
                const tokens = data.tokens || [];
                const container = document.getElementById('tradeTokensList');

                if (tokens.length === 0) {
                    container.innerHTML = '<div style="color:#848e9c;text-align:center;padding:20px">白名单为空</div>';
                    return;
                }

                container.innerHTML = tokens.map(t => {
                    const symbol = t.symbol || '';
                    const addr = t.address || t;
                    const shortAddr = addr.slice(0, 10) + '...' + addr.slice(-6);
                    return `
                        <div style="background:#0b0e11;padding:8px 12px;border-radius:4px;margin-bottom:4px;display:flex;justify-content:space-between;align-items:center">
                            <div>
                                ${symbol ? `<span style="color:#0ecb81;font-weight:bold">${escapeHtml(symbol)}</span>` : ''}
                                <span style="color:#848e9c;font-size:11px;margin-left:8px">${shortAddr}</span>
                            </div>
                            <button onclick="removeTradeToken('${addr}')" style="background:#f6465d;color:#fff;border:none;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:11px">移除</button>
                        </div>
                    `;
                }).join('');
            } catch (e) {
                document.getElementById('tradeTokensList').innerHTML = '<div style="color:#f6465d">加载失败: ' + e.message + '</div>';
            }
        }

        async function addTradeToken() {
            const address = document.getElementById('tradeNewTokenAddr').value.trim();
            const symbol = document.getElementById('tradeNewTokenSymbol').value.trim();
            if (!address) return;
            try {
                const resp = await fetch('api/trade/whitelist/tokens', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ address, symbol })
                });
                if (resp.ok) {
                    document.getElementById('tradeNewTokenAddr').value = '';
                    document.getElementById('tradeNewTokenSymbol').value = '';
                    loadTradeTokens();
                }
            } catch (e) {
                alert('添加失败: ' + e.message);
            }
        }

        async function removeTradeToken(address) {
            try {
                const resp = await fetch('api/trade/whitelist/tokens', {
                    method: 'DELETE',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ address })
                });
                if (resp.ok) {
                    loadTradeTokens();
                }
            } catch (e) {
                alert('移除失败: ' + e.message);
            }
        }

        // 初始加载
        refresh();

        // SSE 实时更新
        let eventSource = null;
        function connectSSE() {
            if (eventSource) {
                eventSource.close();
            }
            eventSource = new EventSource('api/sse');
            eventSource.onmessage = function(e) {
                try {
                    const data = JSON.parse(e.data);
                    if (data.services) {
                        // 保存滚动位置
                        const scrollPositions = {};
                        document.querySelectorAll('.data-list').forEach((el, i) => {
                            scrollPositions[i] = el.scrollTop;
                        });

                        renderServices(data.services, data.monitoring || {count: 0, contracts: []});

                        // 恢复滚动位置
                        document.querySelectorAll('.data-list').forEach((el, i) => {
                            if (scrollPositions[i]) el.scrollTop = scrollPositions[i];
                        });

                        document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
                    }
                } catch (err) {
                    console.error('SSE parse error:', err);
                }
            };
            eventSource.onerror = function() {
                console.warn('SSE connection error, reconnecting in 3s...');
                eventSource.close();
                setTimeout(connectSSE, 3000);
            };
        }
        connectSSE();

        // 匹配数据仍用轮询（更新较少）
        setInterval(async () => {
            try {
                const matchResp = await fetch('api/matches');
                const matchData = await matchResp.json();
                renderMatches(matchData);
            } catch (e) {}
        }, 5000);
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
        # match_service 可能需要更长时间（AI处理）
        timeout = 5 if service['name'] == 'match_service' else 2
        resp = requests.get(f"{service['url']}/recent", timeout=timeout, proxies={'http': None, 'https': None})
        if resp.status_code == 200:
            return resp.json()
    except:
        pass
    return None


@app.route('/api/sse')
def api_sse():
    """SSE 实时推送服务状态"""
    def generate():
        while True:
            try:
                # 获取服务状态
                results = []
                for service in get_services():
                    status = get_service_status(service)
                    recent = get_recent_data(service)
                    name = service['name']
                    current_errors = status['data'].get('errors', 0) if status['data'] else 0
                    has_new_error = current_errors > last_errors[name]
                    last_errors[name] = current_errors
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

                # 获取监测数据
                monitoring_data = {'count': 0, 'contracts': []}
                try:
                    resp = requests.get(f"{config.get_service_url('alpha')}/monitoring", timeout=2, proxies={'http': None, 'https': None})
                    if resp.status_code == 200:
                        monitoring_data = resp.json()
                except:
                    pass

                data = json.dumps({'services': results, 'monitoring': monitoring_data})
                yield f"data: {data}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            time.sleep(1)  # 每秒推送一次

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'X-Accel-Buffering': 'no'
    })


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


@app.route('/api/exclusive')
def api_exclusive():
    """获取优质代币列表"""
    try:
        resp = requests.get(
            f'{config.get_service_url("token")}/exclusive',
            timeout=10,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'items': [], 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'items': [], 'error': str(e)}), 500


@app.route('/api/alpha')
def api_alpha():
    """获取 Alpha 代币列表"""
    try:
        resp = requests.get(
            f'{config.get_service_url("token")}/alpha',
            timeout=10,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'items': [], 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'items': [], 'error': str(e)}), 500


@app.route('/api/monitoring')
def api_monitoring():
    """获取 Alpha Call 监测中的合约"""
    try:
        resp = requests.get(
            f'{config.get_service_url("alpha_call")}/monitoring',
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'count': 0, 'contracts': [], 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'count': 0, 'contracts': [], 'error': str(e)}), 500


@app.route('/api/export_records', methods=['GET'])
def api_export_records():
    """导出匹配记录为 CSV"""
    try:
        resp = requests.get(
            f'{config.get_service_url("tracker")}/export_records',
            timeout=30,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            # 透传 CSV 文件
            return Response(
                resp.content,
                mimetype='text/csv',
                headers=dict(resp.headers)
            )
        return jsonify({'success': False, 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/export_analysis', methods=['GET'])
def api_export_analysis():
    """导出分析推文和匹配代币"""
    try:
        resp = requests.get(
            f'{config.get_service_url("tracker")}/export_analysis',
            timeout=30,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return Response(
                resp.content,
                mimetype='text/csv',
                headers=dict(resp.headers)
            )
        return jsonify({'success': False, 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/delete_records', methods=['POST'])
def api_delete_records():
    """批量删除匹配记录"""
    try:
        data = request.json
        resp = requests.post(
            f'{config.get_service_url("tracker")}/delete_records',
            json=data,
            timeout=10,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'success': False, 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/blacklist', methods=['GET'])
def api_get_blacklist():
    """获取黑名单"""
    try:
        resp = requests.get(
            f'{config.get_service_url("match")}/blacklist',
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'blacklist': [], 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'blacklist': [], 'error': str(e)}), 500


@app.route('/api/blacklist', methods=['POST'])
def api_add_blacklist():
    """添加到黑名单"""
    try:
        data = request.json
        resp = requests.post(
            f'{config.get_service_url("match")}/blacklist',
            json=data,
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'success': False, 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/blacklist', methods=['DELETE'])
def api_remove_blacklist():
    """从黑名单移除"""
    try:
        data = request.json
        resp = requests.delete(
            f'{config.get_service_url("match")}/blacklist',
            json=data,
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'success': False, 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hardcoded_match', methods=['GET', 'POST'])
def api_hardcoded_match():
    """获取或设置硬编码匹配开关"""
    try:
        if request.method == 'GET':
            resp = requests.get(
                f'{config.get_service_url("match")}/hardcoded_match',
                timeout=5,
                proxies={'http': None, 'https': None}
            )
        else:
            resp = requests.post(
                f'{config.get_service_url("match")}/hardcoded_match',
                json=request.json,
                timeout=5,
                proxies={'http': None, 'https': None}
            )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'enabled': True, 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'enabled': True, 'error': str(e)}), 500


@app.route('/api/exclusive_blacklist', methods=['GET'])
def api_get_exclusive_blacklist():
    """获取优质代币合约黑名单"""
    try:
        resp = requests.get(
            f'{config.get_service_url("match")}/exclusive_blacklist',
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            data = resp.json()
            # match_service 返回数组，前端期望 {blacklist: [...]}
            if isinstance(data, list):
                return jsonify({'blacklist': data})
            return jsonify(data)
        return jsonify({'blacklist': [], 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'blacklist': [], 'error': str(e)}), 500


@app.route('/api/exclusive_blacklist', methods=['POST'])
def api_add_exclusive_blacklist():
    """添加合约到黑名单"""
    try:
        data = request.json
        resp = requests.post(
            f'{config.get_service_url("match")}/exclusive_blacklist/add',
            json=data,
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'success': False, 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/exclusive_blacklist', methods=['DELETE'])
def api_remove_exclusive_blacklist():
    """从黑名单移除合约"""
    try:
        data = request.json
        resp = requests.post(
            f'{config.get_service_url("match")}/exclusive_blacklist/remove',
            json=data,
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'success': False, 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/prompt_template', methods=['GET'])
def api_prompt_template():
    """获取提示词模版"""
    try:
        resp = requests.get(
            f'{config.get_service_url("match")}/prompt_template',
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'error': resp.text}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== 作者白名单 API ====================

@app.route('/api/author_whitelist', methods=['GET'])
def api_get_author_whitelist():
    """获取作者白名单"""
    try:
        resp = requests.get(
            f'{config.get_service_url("news")}/whitelist',
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'enabled': False, 'authors': [], 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'enabled': False, 'authors': [], 'error': str(e)}), 500


@app.route('/api/author_whitelist/toggle', methods=['POST'])
def api_toggle_author_whitelist():
    """切换作者白名单开关"""
    try:
        resp = requests.post(
            f'{config.get_service_url("news")}/whitelist/toggle',
            json=request.json,
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'success': False, 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/author_whitelist/add', methods=['POST'])
def api_add_author_whitelist():
    """添加作者到白名单"""
    try:
        resp = requests.post(
            f'{config.get_service_url("news")}/whitelist/add',
            json=request.json,
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'success': False, 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/author_whitelist/remove', methods=['POST'])
def api_remove_author_whitelist():
    """从白名单移除作者"""
    try:
        resp = requests.post(
            f'{config.get_service_url("news")}/whitelist/remove',
            json=request.json,
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'success': False, 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/author_whitelist/batch', methods=['POST'])
def api_batch_author_whitelist():
    """批量添加作者到白名单"""
    try:
        resp = requests.post(
            f'{config.get_service_url("news")}/whitelist/batch',
            json=request.json,
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'success': False, 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/whitelist_news', methods=['GET'])
def api_whitelist_news():
    """查询白名单作者的历史推文"""
    import sqlite3

    limit = request.args.get('limit', 50, type=int)
    author_filter = request.args.get('author', '').strip().lower()

    try:
        # 获取白名单
        resp = requests.get(
            f'{config.get_service_url("news")}/whitelist',
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code != 200:
            return jsonify({'success': False, 'error': '无法获取白名单'}), 500

        whitelist_data = resp.json()
        authors = whitelist_data.get('authors', [])

        if not authors:
            return jsonify({'news': [], 'total': 0, 'message': '白名单为空'})

        # 如果指定了作者过滤
        if author_filter:
            if author_filter not in [a.lower() for a in authors]:
                return jsonify({'news': [], 'total': 0, 'message': '该作者不在白名单中'})
            authors = [author_filter]

        # 查询数据库
        conn = sqlite3.connect(config.DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # 构建 IN 子句
        placeholders = ','.join(['?' for _ in authors])
        query = f'''
            SELECT news_time, news_author, news_author_name, news_avatar, news_type,
                   news_content, news_images, ref_author, ref_content
            FROM all_news
            WHERE LOWER(news_author) IN ({placeholders})
            ORDER BY news_time DESC
            LIMIT ?
        '''
        cursor.execute(query, [a.lower() for a in authors] + [limit])
        rows = cursor.fetchall()

        # 获取总数
        count_query = f'SELECT COUNT(*) FROM all_news WHERE LOWER(news_author) IN ({placeholders})'
        cursor.execute(count_query, [a.lower() for a in authors])
        total = cursor.fetchone()[0]

        conn.close()

        news_list = [dict(row) for row in rows]
        return jsonify({'news': news_list, 'total': total})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== Token 服务 API ====================

@app.route('/api/token/boost', methods=['POST'])
def api_token_boost():
    """手动触发高频模式"""
    try:
        resp = requests.post(
            f'{config.get_service_url("token")}/boost',
            json=request.json or {'author': 'manual'},
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'error': 'Service unavailable'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== 交易服务 API ====================

@app.route('/api/trade/status', methods=['GET'])
def api_trade_status():
    """获取交易服务状态"""
    try:
        resp = requests.get(
            f'{config.get_service_url("trade")}/status',
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'error': 'Service unavailable'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/trade/config', methods=['GET', 'POST'])
def api_trade_config():
    """获取/更新交易配置"""
    try:
        if request.method == 'GET':
            resp = requests.get(
                f'{config.get_service_url("trade")}/config',
                timeout=5,
                proxies={'http': None, 'https': None}
            )
        else:
            resp = requests.post(
                f'{config.get_service_url("trade")}/config',
                json=request.json,
                timeout=5,
                proxies={'http': None, 'https': None}
            )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'success': False, 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/trade/positions', methods=['GET'])
def api_trade_positions():
    """获取当前持仓"""
    try:
        resp = requests.get(
            f'{config.get_service_url("trade")}/positions',
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'positions': []}), 500
    except Exception as e:
        return jsonify({'positions': [], 'error': str(e)}), 500


@app.route('/api/trade/positions/<position_id>', methods=['DELETE'])
def api_trade_close_position(position_id):
    """手动平仓"""
    try:
        resp = requests.delete(
            f'{config.get_service_url("trade")}/positions/{position_id}',
            timeout=15,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'success': False, 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/trade/history', methods=['GET'])
def api_trade_history():
    """获取交易历史"""
    try:
        limit = request.args.get('limit', 50, type=int)
        resp = requests.get(
            f'{config.get_service_url("trade")}/history?limit={limit}',
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'history': []}), 500
    except Exception as e:
        return jsonify({'history': [], 'error': str(e)}), 500


@app.route('/api/trade/whitelist/authors', methods=['GET'])
def api_trade_author_whitelist():
    """获取交易作者白名单"""
    try:
        resp = requests.get(
            f'{config.get_service_url("trade")}/whitelist/authors',
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'authors': []}), 500
    except Exception as e:
        return jsonify({'authors': [], 'error': str(e)}), 500


@app.route('/api/trade/whitelist/authors', methods=['POST'])
def api_trade_add_author():
    """添加交易作者白名单"""
    try:
        resp = requests.post(
            f'{config.get_service_url("trade")}/whitelist/authors',
            json=request.json,
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'success': False, 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/trade/whitelist/authors', methods=['DELETE'])
def api_trade_remove_author():
    """移除交易作者白名单"""
    try:
        resp = requests.delete(
            f'{config.get_service_url("trade")}/whitelist/authors',
            json=request.json,
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'success': False, 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/trade/whitelist/tokens', methods=['GET'])
def api_trade_token_whitelist():
    """获取交易代币白名单"""
    try:
        resp = requests.get(
            f'{config.get_service_url("trade")}/whitelist/tokens',
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'tokens': []}), 500
    except Exception as e:
        return jsonify({'tokens': [], 'error': str(e)}), 500


@app.route('/api/trade/whitelist/tokens', methods=['POST'])
def api_trade_add_token():
    """添加交易代币白名单"""
    try:
        resp = requests.post(
            f'{config.get_service_url("trade")}/whitelist/tokens',
            json=request.json,
            timeout=5,
            proxies={'http': None, 'https': None}
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({'success': False, 'error': resp.text}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/trade/whitelist/tokens', methods=['DELETE'])
def api_trade_remove_token():
    """移除交易代币白名单"""
    try:
        resp = requests.delete(
            f'{config.get_service_url("trade")}/whitelist/tokens',
            json=request.json,
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
        'match_service': 'match_service.py',
        'trade_service': 'trade_service.py'
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
