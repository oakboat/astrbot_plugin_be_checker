"""
封禁检查核心逻辑模块
"""
import socket
import hashlib
import random
import requests
import base64
import threading
import asyncio
from typing import Optional, Dict, Tuple

# ==================== 原有的封禁检查核心代码 ====================
# 缓存配置 - RID是永久性的，不需要过期时间
RID_CACHE: Dict[str, str] = {}  # {identifier: rid}
CACHE_LOCK = threading.RLock()  # 缓存操作的锁

def compute_be_id(rid: int) -> str:
    """计算 BattlEye ID（与原C#代码完全一致）"""
    # 1. 将rid转为字符串
    rid_str = str(rid)
    
    # 2. 将字符串转为UTF-8字节，然后转为Base64
    rid_bytes = rid_str.encode('utf-8')
    rid_base64 = base64.b64encode(rid_bytes).decode('ascii')
    
    # 3. 拼接"BE"前缀
    data = "BE" + rid_base64
    
    # 4. 计算MD5
    md5_hash = hashlib.md5(data.encode('ascii')).hexdigest()
    
    return md5_hash.lower()

def check_ban_reason(rid: int) -> str:
    """查询BattlEye封禁状态"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(5)
        server_address = ("51.89.97.102", 61455)
        
        # 生成随机头部数据（4字节）
        header = bytes([random.randint(0, 255) for _ in range(4)])
        
        # 计算BE ID
        be_id = compute_be_id(rid)
        
        # 构建发送数据：4字节随机头部 + BE ID
        data_to_send = header + be_id.encode('ascii')
        
        # 发送数据
        sock.sendto(data_to_send, server_address)
        
        # 接收响应
        response, _ = sock.recvfrom(1024)
        
        # 跳过前4字节头部，返回封禁原因
        if len(response) > 4:
            # 尝试多种解码方式
            ban_data = response[4:]
            
            # 先尝试ASCII
            try:
                result = ban_data.decode('ascii').strip()
                return result if result else ""
            except UnicodeDecodeError:
                # 再尝试UTF-8
                try:
                    result = ban_data.decode('utf-8').strip()
                    return result if result else ""
                except UnicodeDecodeError:
                    # 最后尝试Latin-1
                    try:
                        result = ban_data.decode('latin-1').strip()
                        return result if result else ""
                    except UnicodeDecodeError:
                        # 返回原始字节的十六进制表示
                        return ban_data.hex()
        return ""
        
    except socket.timeout:
        return "查询超时"
    except Exception as e:
        return f"查询错误: {str(e)}"
    finally:
        try:
            sock.close()
        except:
            pass

def get_rid_from_cache(identifier: str) -> Optional[str]:
    """从缓存获取RID"""
    with CACHE_LOCK:
        return RID_CACHE.get(identifier)

def add_rid_to_cache(identifier: str, rid: str):
    """添加RID到缓存（永不过期）"""
    with CACHE_LOCK:
        RID_CACHE[identifier] = rid

def clear_cache():
    """清空缓存"""
    with CACHE_LOCK:
        RID_CACHE.clear()
        return len(RID_CACHE)

def get_rid_from_name(username: str) -> Optional[str]:
    """从用户名获取RID"""
    try:
        url = f"https://sc-cache.com/n/{username}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        
        if "id" in data:
            return str(data["id"])
        return None
            
    except Exception as e:
        return None

def check_ban_sync(identifier: str, use_cache: bool = True) -> Tuple[bool, str]:
    """检查封禁状态 - 同步版本
    
    Args:
        identifier: 用户名或RID
        use_cache: 是否使用缓存，默认为True
        
    Returns:
        (是否成功, 结果消息)
    """
    # 1. 首先尝试从缓存获取
    if use_cache:
        cached_rid = get_rid_from_cache(identifier)
        if cached_rid:
            rid = cached_rid
            # 直接使用缓存的RID查询封禁状态
            try:
                rid_int = int(rid)
                ban_reason = check_ban_reason(rid_int)
                
                if not ban_reason:
                    return True, f"{identifier} (RID: {rid}) 没有被封禁"
                else:
                    return True, f"{identifier} (RID: {rid}) 已被封禁 - 返回信息: {ban_reason}"
            except ValueError:
                # 如果RID无效，从缓存中移除并重新获取
                with CACHE_LOCK:
                    if identifier in RID_CACHE:
                        del RID_CACHE[identifier]
            except Exception as e:
                return False, f"错误: {str(e)}"
    
    # 2. 缓存未命中或禁用缓存，尝试获取RID
    # 首先检查identifier是否已经是RID（纯数字）
    if identifier.isdigit():
        rid = identifier
    else:
        # 尝试从用户名获取（使用 sc-cache.com）
        rid = get_rid_from_name(identifier)
    
    if not rid:
        return False, f"错误: 无法获取 {identifier} 的RID"
    
    # 3. 添加到缓存（如果启用缓存）
    if use_cache:
        add_rid_to_cache(identifier, rid)
    
    # 4. 查询封禁状态
    try:
        rid_int = int(rid)
        ban_reason = check_ban_reason(rid_int)
        
        if not ban_reason:
            return True, f"{identifier} (RID: {rid}) 没有被封禁"
        else:
            return True, f"{identifier} (RID: {rid}) 已被封禁 - 返回信息: {ban_reason}"
                
    except ValueError:
        return False, f"错误: 无效的RID {rid}"
    except Exception as e:
        return False, f"错误: {str(e)}"

async def check_ban_async(identifier: str, use_cache: bool = True) -> Tuple[bool, str]:
    """检查封禁状态 - 异步版本"""
    # 将同步函数包装为异步，避免阻塞事件循环
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, check_ban_sync, identifier, use_cache)

