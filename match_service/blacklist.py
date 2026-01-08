"""
黑名单管理模块
- 代币名称黑名单（AI提取关键词时排除）
- 优质代币合约黑名单（老币匹配时排除）
"""
import os
import json
import config


def load_blacklist():
    """加载代币名称黑名单"""
    try:
        if os.path.exists(config.BLACKLIST_FILE):
            with open(config.BLACKLIST_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"[黑名单] 加载失败: {e}", flush=True)
    return []


def save_blacklist(blacklist):
    """保存代币名称黑名单"""
    try:
        with open(config.BLACKLIST_FILE, 'w', encoding='utf-8') as f:
            json.dump(blacklist, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[黑名单] 保存失败: {e}", flush=True)
        return False


def add_to_blacklist(token_name):
    """添加到黑名单"""
    blacklist = load_blacklist()
    token_name = token_name.strip().lower()
    if token_name and token_name not in blacklist:
        blacklist.append(token_name)
        save_blacklist(blacklist)
        return True
    return False


def remove_from_blacklist(token_name):
    """从黑名单移除"""
    blacklist = load_blacklist()
    token_name = token_name.strip().lower()
    if token_name in blacklist:
        blacklist.remove(token_name)
        save_blacklist(blacklist)
        return True
    return False


def build_blacklist_prompt():
    """构建黑名单提示词（用于AI）"""
    blacklist = load_blacklist()
    if not blacklist:
        return ""
    return f" 黑名单（绝对禁止返回这些词）：{', '.join(blacklist)}"


# ==================== 优质代币合约黑名单 ====================

def load_exclusive_blacklist():
    """加载优质代币合约黑名单"""
    try:
        if os.path.exists(config.EXCLUSIVE_BLACKLIST_FILE):
            with open(config.EXCLUSIVE_BLACKLIST_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"[合约黑名单] 加载失败: {e}", flush=True)
    return []


def save_exclusive_blacklist(blacklist):
    """保存优质代币合约黑名单"""
    try:
        with open(config.EXCLUSIVE_BLACKLIST_FILE, 'w', encoding='utf-8') as f:
            json.dump(blacklist, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[合约黑名单] 保存失败: {e}", flush=True)
        return False


def add_to_exclusive_blacklist(address):
    """添加合约到黑名单"""
    blacklist = load_exclusive_blacklist()
    address = address.strip().lower()
    if address and address not in blacklist:
        blacklist.append(address)
        save_exclusive_blacklist(blacklist)
        return True
    return False


def remove_from_exclusive_blacklist(address):
    """从黑名单移除合约"""
    blacklist = load_exclusive_blacklist()
    address = address.strip().lower()
    if address in blacklist:
        blacklist.remove(address)
        save_exclusive_blacklist(blacklist)
        return True
    return False
