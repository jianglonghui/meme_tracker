"""
控制面板服务 (端口 5000)
- 显示所有服务状态
- 显示匹配数据
"""
import requests
import time
from flask import Flask, render_template_string, jsonify
import config

app = Flask(__name__)

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

        .data-section { margin-top: 10px; }
        .data-title { font-size: 12px; color: #f0b90b; margin-bottom: 5px; cursor: pointer; }
        .data-title:hover { text-decoration: underline; }
        .data-list { max-height: 150px; overflow-y: auto; font-size: 11px; background: #0b0e11; border-radius: 4px; padding: 8px; }
        .data-item { padding: 4px 0; border-bottom: 1px solid #2b3139; }
        .data-item:last-child { border-bottom: none; }
        .data-item .author { color: #f0b90b; }
        .data-item .symbol { color: #0ecb81; font-weight: bold; }
        .data-item .content { color: #b7bdc6; }
        .data-item .time { color: #848e9c; font-size: 10px; }
        .data-item.error { color: #f6465d; }

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

        <h2>数据库匹配记录</h2>
        <div class="matches" id="matches">
            <div class="no-data">加载中...</div>
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

        function renderServices(services) {
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
                                <div class="stat-item">最后获取: <span class="stat-value">${formatTime(d.last_fetch)}</span></div>
                                <div class="stat-item">最后成功: <span class="stat-value">${formatTime(d.last_success)}</span></div>
                                <div class="stat-item">错误: <span class="stat-value ${hasErrors?'error':''}">${d.errors || 0}</span></div>`;
                } else if (s.name === 'token_service') {
                    statsHtml = `<div class="stat-item">代币: <span class="stat-value">${d.total_tokens || 0}</span></div>
                                <div class="stat-item">最后获取: <span class="stat-value">${formatTime(d.last_fetch)}</span></div>
                                <div class="stat-item">最后成功: <span class="stat-value">${formatTime(d.last_success)}</span></div>
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
                if (s.recent) {
                    if (s.name === 'news_service') {
                        let items = s.recent.items || [];
                        let errors = s.recent.errors || [];
                        if (items.length > 0) {
                            dataHtml += `<div class="data-section">
                                <div class="data-title">📰 最近推文</div>
                                <div class="data-list">${items.map(r =>
                                    `<div class="data-item"><span class="author">@${r.author}</span> <span class="content">${r.content}</span> <span class="time">${formatTime(r.time)}</span></div>`
                                ).join('')}</div>
                            </div>`;
                        }
                        if (errors.length > 0) {
                            dataHtml += `<div class="data-section">
                                <div class="data-title">⚠️ 错误日志</div>
                                <div class="data-list">${errors.map(r =>
                                    `<div class="data-item error">${r.msg} <span class="time">${formatTime(r.time)}</span></div>`
                                ).join('')}</div>
                            </div>`;
                        }
                    } else if (s.name === 'token_service') {
                        let items = s.recent.items || [];
                        let errors = s.recent.errors || [];
                        if (items.length > 0) {
                            dataHtml += `<div class="data-section">
                                <div class="data-title">🪙 最近代币</div>
                                <div class="data-list">${items.map(r =>
                                    `<div class="data-item"><span class="symbol">${r.symbol}</span> ${r.name} <span class="time">MC:${r.marketCap} H:${r.holders}</span></div>`
                                ).join('')}</div>
                            </div>`;
                        }
                        if (errors.length > 0) {
                            dataHtml += `<div class="data-section">
                                <div class="data-title">⚠️ 错误日志</div>
                                <div class="data-list">${errors.map(r =>
                                    `<div class="data-item error">${r.msg} <span class="time">${formatTime(r.time)}</span></div>`
                                ).join('')}</div>
                            </div>`;
                        }
                    } else if (s.name === 'match_service') {
                        let matchList = s.recent.matches || [];
                        let errorList = s.recent.errors || [];
                        if (matchList.length > 0) {
                            dataHtml += `<div class="data-section">
                                <div class="data-title">🎯 最近匹配</div>
                                <div class="data-list">${matchList.map(r =>
                                    `<div class="data-item"><span class="author">@${r.author}</span> → <span class="symbol">${r.tokens.join(', ')}</span> <span class="time">${formatTime(r.time)}</span></div>`
                                ).join('')}</div>
                            </div>`;
                        }
                        if (errorList.length > 0) {
                            dataHtml += `<div class="data-section">
                                <div class="data-title">⚠️ 错误日志</div>
                                <div class="data-list">${errorList.map(r =>
                                    `<div class="data-item error">${r.msg} <span class="time">${formatTime(r.time)}</span></div>`
                                ).join('')}</div>
                            </div>`;
                        }
                    }
                }

                return `<div class="service-card ${statusClass}">
                    <div class="service-header">
                        <div>
                            <span class="service-name">${s.desc}</span>
                            <span class="service-port">:${s.port}</span>
                        </div>
                        <div class="service-status">
                            <div class="status-dot ${statusClass}"></div>
                            <span>${statusText}</span>
                        </div>
                    </div>
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

            container.innerHTML = data.map(m => {
                const tokensHtml = m.best_tokens && m.best_tokens.length > 0
                    ? m.best_tokens.map(t => `<span class="token-badge">${t.symbol}</span>`).join('')
                    : '<span style="color:#848e9c">等待追踪...</span>';

                return `<div class="match-item">
                    <div class="match-author">@${m.author || 'Unknown'}</div>
                    <div class="match-content">${m.content || ''}</div>
                    <div class="match-tokens">${tokensHtml}</div>
                </div>`;
            }).join('');
        }

        async function refresh() {
            try {
                const statusResp = await fetch('/api/status');
                const statusData = await statusResp.json();
                renderServices(statusData);

                const matchResp = await fetch('/api/matches');
                const matchData = await matchResp.json();
                renderMatches(matchData);

                document.getElementById('last-update').textContent = new Date().toLocaleTimeString('zh-CN');
            } catch (e) {
                console.error('Refresh error:', e);
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
        results.append({
            'name': service['name'],
            'desc': service['desc'],
            'port': service['port'],
            'status': status['status'],
            'data': status['data'],
            'recent': recent
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


@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


if __name__ == "__main__":
    port = config.get_port('dashboard')
    print(f"控制面板启动: http://127.0.0.1:{port}", flush=True)
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
