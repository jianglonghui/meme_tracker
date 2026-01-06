#!/bin/bash
# Meme Tracker 停止脚本

echo "正在停止 Meme Tracker 服务..."

# 通过进程名停止
services=("alpha_call_service.py","news_service.py" "token_service.py" "tracker_service.py" "match_service.py" "dashboard.py")

for service in "${services[@]}"; do
    pid=$(pgrep -f "$service" 2>/dev/null)
    if [ -n "$pid" ]; then
        echo "  停止 $service (PID: $pid)"
        pkill -9 -f "$service" 2>/dev/null
    fi
done

echo "所有服务已停止"
