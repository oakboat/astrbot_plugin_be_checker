"""
封禁检查核心逻辑模块（完全异步化版本）
"""
import socket
import hashlib
import random
import aiohttp
import base64
import asyncio
import json
import os
from urllib.parse import quote
from typing import Optional, Dict, Tuple

# 延迟导入 logger，避免循环导入
_logger = None

def _get_logger():
    """获取 logger 实例（延迟导入）"""
    global _logger
    if _logger is None:
        from astrbot.api import logger as astrbot_logger
        _logger = astrbot_logger
    return _logger

# ==================== 配置常量 ====================
# BattlEye 服务器地址（可配置）
BATTLEYE_SERVER_HOST = "51.89.97.102"
BATTLEYE_SERVER_PORT = 61455
BATTLEYE_TIMEOUT = 5

# 缓存文件路径（将在初始化时设置）
CACHE_FILE_PATH: Optional[str] = None

# ==================== 缓存管理 ====================
# 缓存配置 - RID是永久性的，不需要过期时间
RID_CACHE: Dict[str, str] = {}  # {identifier: rid}
# 使用 asyncio.Lock 替代 threading.RLock，因为现在是完全异步的
CACHE_LOCK = asyncio.Lock()

def set_cache_file_path(file_path: str):
    """设置缓存文件路径"""
    global CACHE_FILE_PATH
    CACHE_FILE_PATH = file_path

def load_cache_from_file() -> Dict[str, str]:
    """从文件加载缓存（同步操作，仅在初始化时调用）"""
    if not CACHE_FILE_PATH or not os.path.exists(CACHE_FILE_PATH):
        return {}
    
    try:
        with open(CACHE_FILE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        _get_logger().warning(f"缓存文件 JSON 格式错误: {e}，将使用空缓存")
        return {}
    except PermissionError as e:
        _get_logger().error(f"无法读取缓存文件（权限不足）: {e}")
        return {}
    except OSError as e:
        _get_logger().error(f"读取缓存文件失败: {e}")
        return {}
    except Exception as e:
        _get_logger().error(f"加载缓存时发生未知错误: {e}", exc_info=True)
        return {}

async def init_cache(cached_data: Dict[str, str]):
    """初始化缓存（接口函数，用于封装）"""
    async with CACHE_LOCK:
        RID_CACHE.update(cached_data)

async def get_cache_stats() -> Tuple[int, list]:
    """获取缓存统计信息（接口函数，用于封装）
    
    Returns:
        (缓存大小, 缓存条目列表（最多10个）)
    """
    async with CACHE_LOCK:
        cache_size = len(RID_CACHE)
        cache_items = list(RID_CACHE.items())[:10]
        return cache_size, cache_items

async def save_cache_to_file():
    """保存缓存到文件（异步版本）"""
    if not CACHE_FILE_PATH:
        return
    
    try:
        # 使用 asyncio.to_thread 将文件操作放到线程池执行，避免阻塞
        def _save():
            os.makedirs(os.path.dirname(CACHE_FILE_PATH), exist_ok=True)
            with open(CACHE_FILE_PATH, 'w', encoding='utf-8') as f:
                json.dump(RID_CACHE, f, ensure_ascii=False, indent=2)
        
        await asyncio.to_thread(_save)
    except PermissionError as e:
        _get_logger().error(f"无法保存缓存文件（权限不足）: {e}")
    except OSError as e:
        _get_logger().error(f"保存缓存文件失败（磁盘空间不足或其他系统错误）: {e}")
    except Exception as e:
        _get_logger().error(f"保存缓存时发生未知错误: {e}", exc_info=True)

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

def _decode_ban_data(ban_data: bytes) -> str:
    """解码封禁数据，尝试多种编码方式"""
    # 按优先级尝试不同的编码方式
    encodings = ['ascii', 'utf-8', 'latin-1']
    
    for encoding in encodings:
        try:
            result = ban_data.decode(encoding, errors='replace').strip()
            if result:
                return result
        except Exception:
            continue
    
    # 如果所有编码都失败，返回十六进制表示
    return ban_data.hex()

class _BattlEyeProtocol(asyncio.DatagramProtocol):
    """BattlEye UDP 协议处理器"""
    def __init__(self):
        self.transport = None
        self.response = None
        self.future = asyncio.Future()
    
    def connection_made(self, transport):
        self.transport = transport
    
    def datagram_received(self, data, addr):
        if not self.future.done():
            self.response = data
            self.future.set_result(data)
            if self.transport:
                self.transport.close()
    
    def error_received(self, exc):
        if not self.future.done():
            self.future.set_exception(exc)
            if self.transport:
                self.transport.close()
    
    def connection_lost(self, exc):
        if not self.future.done() and exc is None:
            # 连接正常关闭但没有收到响应
            if self.response is None:
                self.future.set_exception(asyncio.TimeoutError("未收到响应"))

async def check_ban_reason(rid: int) -> str:
    """查询BattlEye封禁状态（异步版本，使用原生异步 UDP）"""
    transport = None
    try:
        loop = asyncio.get_running_loop()
        server_address = (BATTLEYE_SERVER_HOST, BATTLEYE_SERVER_PORT)
        
        # 生成随机头部数据（4字节）
        header = bytes([random.randint(0, 255) for _ in range(4)])
        
        # 计算BE ID
        be_id = compute_be_id(rid)
        
        # 构建发送数据：4字节随机头部 + BE ID
        data_to_send = header + be_id.encode('ascii')
        
        # 创建协议实例
        protocol = _BattlEyeProtocol()
        
        # 创建异步 UDP 端点（绑定到本地任意端口）
        transport, _ = await loop.create_datagram_endpoint(
            lambda: protocol,
            family=socket.AF_INET
        )
        
        # 发送数据
        transport.sendto(data_to_send, server_address)
        
        try:
            # 等待响应（带超时）
            response = await asyncio.wait_for(
                protocol.future,
                timeout=BATTLEYE_TIMEOUT
            )
            
            # 跳过前4字节头部，返回封禁原因
            if len(response) > 4:
                ban_data = response[4:]
                return _decode_ban_data(ban_data)
            return ""
        except asyncio.TimeoutError:
            return "查询超时"
        finally:
            if transport and not transport.is_closing():
                transport.close()
        
    except Exception as e:
        _get_logger().error(f"查询封禁状态时发生错误: {e}", exc_info=True)
        if transport and not transport.is_closing():
            transport.close()
        return f"查询错误: {str(e)}"

async def get_rid_from_cache(identifier: str) -> Optional[str]:
    """从缓存获取RID（异步版本）"""
    async with CACHE_LOCK:
        return RID_CACHE.get(identifier)

async def add_rid_to_cache(identifier: str, rid: str):
    """添加RID到缓存（永不过期，异步版本）"""
    async with CACHE_LOCK:
        RID_CACHE[identifier] = rid
        await save_cache_to_file()  # 异步持久化缓存

async def remove_from_cache(identifier: str):
    """从缓存中移除指定项（异步版本）"""
    async with CACHE_LOCK:
        RID_CACHE.pop(identifier, None)

async def clear_cache() -> int:
    """清空缓存（异步版本）"""
    async with CACHE_LOCK:
        cache_size = len(RID_CACHE)
        RID_CACHE.clear()
        await save_cache_to_file()  # 异步持久化清空操作
        return cache_size

async def get_rid_from_name(username: str) -> Optional[str]:
    """从用户名获取RID（异步版本，使用 aiohttp）"""
    try:
        # URL 编码用户名，防止特殊字符导致请求失败
        encoded_username = quote(username, safe='')
        url = f"https://sc-cache.com/n/{encoded_username}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        
        # 使用 aiohttp 进行异步 HTTP 请求
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    data = await response.json()
                    if "id" in data:
                        return str(data["id"])
        return None
            
    except aiohttp.ClientError as e:
        _get_logger().warning(f"获取用户 {username} 的 RID 时网络请求失败: {e}")
        return None
    except Exception as e:
        _get_logger().error(f"获取用户 {username} 的 RID 时发生未知错误: {e}", exc_info=True)
        return None

async def check_ban_async(identifier: str, use_cache: bool = True) -> Tuple[bool, str]:
    """检查封禁状态 - 完全异步版本
    
    Args:
        identifier: 用户名或RID
        use_cache: 是否使用缓存，默认为True
        
    Returns:
        (是否成功, 结果消息)
    """
    # 1. 首先尝试从缓存获取
    if use_cache:
        cached_rid = await get_rid_from_cache(identifier)
        if cached_rid:
            rid = cached_rid
            # 直接使用缓存的RID查询封禁状态
            try:
                rid_int = int(rid)
                ban_reason = await check_ban_reason(rid_int)
                
                if not ban_reason:
                    return True, f"{identifier} (RID: {rid}) 没有被封禁"
                else:
                    return True, f"{identifier} (RID: {rid}) 已被封禁 - 返回信息: {ban_reason}"
            except ValueError:
                # 如果RID无效，从缓存中移除并重新获取
                await remove_from_cache(identifier)
            except Exception as e:
                return False, f"错误: {str(e)}"
    
    # 2. 缓存未命中或禁用缓存，尝试获取RID
    # 首先检查identifier是否已经是RID（纯数字）
    if identifier.isdigit():
        rid = identifier
    else:
        # 尝试从用户名获取（使用 sc-cache.com，异步）
        rid = await get_rid_from_name(identifier)
    
    if not rid:
        return False, f"错误: 无法获取 {identifier} 的RID"
    
    # 3. 添加到缓存（如果启用缓存）
    if use_cache:
        await add_rid_to_cache(identifier, rid)
    
    # 4. 查询封禁状态
    try:
        rid_int = int(rid)
        ban_reason = await check_ban_reason(rid_int)
        
        if not ban_reason:
            return True, f"{identifier} (RID: {rid}) 没有被封禁"
        else:
            return True, f"{identifier} (RID: {rid}) 已被封禁 - 返回信息: {ban_reason}"
                
    except ValueError:
        return False, f"错误: 无效的RID {rid}"
    except Exception as e:
        return False, f"错误: {str(e)}"
