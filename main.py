"""
封禁检查插件 - 查询GTA玩家BattlEye封禁状态
命令: /查封禁 <用户名/RID>
"""
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import logger
from typing import Optional
from . import ban_check

@register("astrbot_plugin_be_checker", "oakboat", "查询GTA玩家的BattlEye封禁状态", "1.0.1")
class BanCheckerPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    async def initialize(self):
        """插件初始化方法"""
        # 设置缓存文件路径并加载缓存
        # 不传入参数，交给 StarTools.get_data_dir 自动根据插件元数据推断插件名
        data_dir = StarTools.get_data_dir()
        cache_file = str(data_dir / "rid_cache.json")
        ban_check.set_cache_file_path(cache_file)

        # 加载已保存的缓存（同步操作，仅在初始化时调用）
        cached_data = ban_check.load_cache_from_file()
        # 使用接口函数初始化缓存
        await ban_check.init_cache(cached_data)

        logger.info(f"封禁检查插件已加载，已加载 {len(cached_data)} 条缓存记录")

    async def _handle_check_ban(self, event: AstrMessageEvent, identifier: Optional[str], use_cache: bool, loading_msg: str):
        """处理封禁查询的公共方法"""
        if not identifier:
            cmd_name = "查封禁" if use_cache else "查封禁强制"
            yield event.plain_result(f"请输入要查询的用户名或RID！\n例如：/{cmd_name} oakboat")
            return
        
        # 发送处理中消息
        yield event.plain_result(loading_msg)
        
        # 异步查询
        success, result = await ban_check.check_ban_async(identifier, use_cache=use_cache)
        
        if success:
            yield event.plain_result(result)
        else:
            yield event.plain_result(f"查询失败: {result}")

    @filter.command("查封禁", alias={'封禁查询', 'bancheck', 'checkban'})
    async def check_ban(self, event: AstrMessageEvent, identifier: Optional[str] = None):
        """查询封禁状态（使用缓存）"""
        async for result in self._handle_check_ban(
            event, identifier, use_cache=True, loading_msg="正在查询，请稍候..."
        ):
            yield result

    @filter.command("查封禁强制", alias={'强制查封禁', 'forcebancheck'})
    async def force_check_ban(self, event: AstrMessageEvent, identifier: Optional[str] = None):
        """强制重新查询封禁状态（不使用缓存）"""
        async for result in self._handle_check_ban(
            event, identifier, use_cache=False, loading_msg="正在强制重新查询（不使用缓存），请稍候..."
        ):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("清空缓存")
    async def clear_cache(self, event: AstrMessageEvent):
        """清空RID缓存（仅管理员）"""
        cache_size = await ban_check.clear_cache()
        yield event.plain_result(f"✅ 缓存已清空！原缓存大小: {cache_size}")

    @filter.command("缓存状态", alias={'查看缓存'})
    async def cache_status(self, event: AstrMessageEvent):
        """查看当前缓存状态"""
        # 使用接口函数获取缓存状态
        cache_size, cache_items = await ban_check.get_cache_stats()
        
        status_msg = f"📊 缓存状态\n"
        status_msg += f"缓存条目数: {cache_size}\n\n"
        
        if cache_items:
            status_msg += "最近缓存的条目（最多显示10个）:\n"
            for identifier, rid in cache_items:
                status_msg += f"  - {identifier} → RID: {rid}\n"
        else:
            status_msg += "缓存为空"
        
        yield event.plain_result(status_msg)

    @filter.command("封禁帮助", alias={'banhelp', '封禁插件帮助'})
    async def help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_text = (
            "命令列表:\n"
            "1. /查封禁 <用户名/RID> - 查询封禁状态（使用缓存）\n"
            "2. /查封禁强制 <用户名/RID> - 强制重新查询（不使用缓存）\n"
            "3. /清空缓存 - 清空RID缓存（仅管理员）\n"
            "4. /缓存状态 - 查看当前缓存状态"
        )
        yield event.plain_result(help_text)

    async def terminate(self):
        """插件销毁方法"""
        logger.info("封禁检查插件已卸载")
