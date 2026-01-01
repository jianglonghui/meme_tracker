#!/usr/bin/env python3
"""
Meme Tracker 启动脚本
统一启动所有服务
"""
import subprocess
import sys
import time
import signal
import os
import socket
import shutil
import requests

# 确定正确的 Python 解释器
def get_python_executable():
    """获取能正常运行服务的 Python 解释器"""
    # 优先使用 /usr/bin/python3
    if os.path.exists('/usr/bin/python3'):
        return '/usr/bin/python3'
    # 其次使用 PATH 中的 python3
    python3_path = shutil.which('python3')
    if python3_path:
        return python3_path
    # 最后使用当前解释器
    return sys.executable

PYTHON_EXE = get_python_executable()

# 端口检测函数
def is_port_available(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.bind(('127.0.0.1', port))
            return True
    except OSError:
        return False

def find_available_port(start_port, assigned):
    for i in range(20):
        port = start_port + i
        if port not in assigned and is_port_available(port):
            return port
    return start_port

# 分配端口并设置环境变量
def allocate_ports():
    defaults = {
        'news': 5050,
        'token': 5051,
        'tracker': 5052,
        'match': 5053,
        'dashboard': 5080,  # 避免 5060 (浏览器会阻止 SIP 端口)
    }
    assigned = set()
    ports = {}

    for name, default in defaults.items():
        port = find_available_port(default, assigned)
        assigned.add(port)
        ports[name] = port
        os.environ[f'MEME_{name.upper()}_PORT'] = str(port)

    return ports

# 分配端口
PORTS = allocate_ports()

# 服务配置
SERVICES = [
    {'name': 'news_service', 'file': 'news_service.py', 'port': PORTS['news'], 'desc': '推文发现'},
    {'name': 'token_service', 'file': 'token_service.py', 'port': PORTS['token'], 'desc': '代币发现'},
    {'name': 'tracker_service', 'file': 'tracker_service.py', 'port': PORTS['tracker'], 'desc': '代币跟踪'},
    {'name': 'match_service', 'file': 'match_service.py', 'port': PORTS['match'], 'desc': '代币撮合'},
    {'name': 'dashboard', 'file': 'dashboard.py', 'port': PORTS['dashboard'], 'desc': '控制面板'},
]

processes = []


def print_banner():
    print("""
╔══════════════════════════════════════════════════════════╗
║                   Meme Tracker v1.0                      ║
║          推文发现 | 代币发现 | 撮合 | 跟踪               ║
╚══════════════════════════════════════════════════════════╝
""")
    print(f"Python: {PYTHON_EXE}")
    print("\n端口分配 (自动避开已占用端口):")
    for s in SERVICES:
        print(f"  {s['desc']:12} → :{s['port']}")
    print()


def start_service(service):
    """启动单个服务"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(script_dir, service['file'])

    if not os.path.exists(script_path):
        print(f"  ✗ {service['desc']} - 文件不存在: {service['file']}")
        return None

    proc = subprocess.Popen(
        [PYTHON_EXE, script_path],
        cwd=script_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=os.environ.copy()  # 传递环境变量
    )
    return proc


def check_service(port, timeout=5):
    """检查服务是否启动"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            resp = requests.get(f'http://127.0.0.1:{port}/health', timeout=1)
            if resp.status_code == 200:
                return True
        except:
            pass
        time.sleep(0.5)
    return False


def start_all():
    """启动所有服务"""
    print_banner()
    print("启动服务...\n")

    for service in SERVICES:
        print(f"  → 启动 {service['desc']} (:{service['port']})...", end=' ', flush=True)
        proc = start_service(service)
        if proc:
            processes.append({'service': service, 'process': proc})
            time.sleep(1)  # 等待服务启动

            if check_service(service['port']):
                print("✓")
            else:
                print("⏳ (等待中)")
        else:
            print("✗")

    print("\n" + "="*60)
    print("服务状态:")
    print("="*60)

    for service in SERVICES:
        status = "🟢 运行中" if check_service(service['port'], timeout=1) else "🔴 未启动"
        print(f"  {service['desc']:12} :{service['port']}  {status}")

    print("="*60)
    print(f"\n控制面板: http://127.0.0.1:{PORTS['dashboard']}")
    print("\n按 Ctrl+C 停止所有服务...")


def stop_all():
    """停止所有服务"""
    print("\n\n正在停止服务...")
    for item in processes:
        proc = item['process']
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except:
                proc.kill()
    print("所有服务已停止")


def signal_handler(sig, frame):
    stop_all()
    sys.exit(0)


def show_logs():
    """显示所有服务的日志"""
    while True:
        for item in processes:
            proc = item['process']
            service = item['service']
            if proc.stdout:
                line = proc.stdout.readline()
                if line:
                    print(f"[{service['name']}] {line}", end='')
        time.sleep(0.1)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    start_all()

    try:
        show_logs()
    except KeyboardInterrupt:
        stop_all()
