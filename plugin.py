"""
每日万年历插件 —— 适配 MaiBot / 麦麦 Bot
==========================================

功能
----
* 每天定时（用户可在 config.toml 中配置推送时间）在指定群聊或私聊
  发送今日万年历，内容包括：

  - 公历日期、星期
  - 农历日期、干支纪年、生肖
  - 当日节气（如有）
  - 建除十二值星 + 天神 + 黄/黑道综合评级
  - 彭祖百忌
  - 日吉神 / 凶煞
  - 宜 / 忌 事项（基于 lunar-python 专业算法）
  - 今日黄道吉时（天神吉时辰）
  - 随机古典文学语录（从 data/quotes.json 抽取）

* 支持 /万年历 命令立即推送（在当前聊天回复）

作者：Shao Chi
历法库：6tail/lunar-python
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Type

from src.plugin_system import BaseCommand, BasePlugin, ComponentInfo, register_plugin  # type: ignore
from src.plugin_system.apis import chat_api, send_api  # type: ignore
from src.plugin_system.base.config_types import ConfigField  # type: ignore

try:
    from lunar_python import Lunar, Solar  # type: ignore
    HAS_LUNAR_PYTHON = True
except ImportError:
    HAS_LUNAR_PYTHON = False
    Solar = None  # type: ignore
    Lunar = None  # type: ignore

logger = logging.getLogger(__name__)

PLUGIN_DIR = Path(__file__).parent
DATA_DIR   = PLUGIN_DIR / "data"

# 十二时辰（地支序号 0=子 … 11=亥）
_BRANCHES   = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
_TIME_RANGE = [
    "23:00~01:00", "01:00~03:00", "03:00~05:00", "05:00~07:00",
    "07:00~09:00", "09:00~11:00", "11:00~13:00", "13:00~15:00",
    "15:00~17:00", "17:00~19:00", "19:00~21:00", "21:00~23:00",
]
_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# ============================================================
# 辅助函数
# ============================================================

def load_random_quote() -> Dict[str, str]:
    """从 data/quotes.json 中随机抽取一条文学语录。"""
    quotes_file = DATA_DIR / "quotes.json"
    try:
        with open(quotes_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        quotes = data.get("quotes", [])
        if quotes:
            return random.choice(quotes)
    except Exception as exc:
        logger.error(f"加载语录文件失败: {exc}")
    return {"text": "天行健，君子以自强不息。", "source": "《周易·乾卦》", "author": ""}


def _get_lucky_hours(lunar: "Lunar") -> List[str]:
    """
    遍历十二时辰，筛选天神为"吉"（黄道）的时辰作为吉时。
    使用 Lunar.fromYmdHms 构造时辰级对象，调用 getTimeTianShenLuck()。
    """
    ly = lunar.getYear()
    lm = lunar.getMonth()
    ld = lunar.getDay()
    lucky: List[str] = []
    for i in range(12):
        hour = i * 2  # 子=0h, 丑=2h, 寅=4h …
        try:
            lt   = Lunar.fromYmdHms(ly, lm, ld, hour, 0, 0)
            luck = lt.getTimeTianShenLuck()       # "吉" 或 "凶"
            if luck == "吉":
                lucky.append(f"{_BRANCHES[i]}时（{_TIME_RANGE[i]}）")
        except Exception:
            pass
    return lucky if lucky else ["（今日无黄道时辰）"]


# ============================================================
# 万年历消息构建
# ============================================================

def build_calendar_message(today: Optional[date] = None) -> str:
    """构建今日万年历完整消息文本。"""
    if today is None:
        today = date.today()

    if not HAS_LUNAR_PYTHON:
        return (
            "❌ 万年历功能不可用：未安装 lunar-python 库。\n"
            "请在 Bot 环境中执行：pip install lunar-python"
        )

    try:
        year    = today.year
        month   = today.month
        day     = today.day
        weekday = _WEEKDAYS[today.weekday()]

        # ── 构造历法对象 ───────────────────────────────────────
        solar = Solar.fromYmd(year, month, day)
        lunar = solar.getLunar()

        # ── 农历基础信息 ───────────────────────────────────────
        lunar_year_str  = lunar.getYearInChinese()     # 如：二零二六
        sheng_xiao      = lunar.getYearShengXiao()     # 马
        lunar_month_str = lunar.getMonthInChinese()    # 二月
        lunar_day_str   = lunar.getDayInChinese()      # 廿七

        # ── 干支（月干支已由 lunar-python 按节气自动算出）────────
        year_gz  = lunar.getYearInGanZhi()    # 丙午
        month_gz = lunar.getMonthInGanZhi()   # 壬辰（节气换月，自动正确）
        day_gz   = lunar.getDayInGanZhi()     # 戊午

        # ── 节气 ───────────────────────────────────────────────
        jie_qi = lunar.getJieQi()             # 有节气则返回名称，否则空串

        # ── 建除十二值星 & 天神 ────────────────────────────────
        zhi_xing    = lunar.getZhiXing()          # 满 / 建 / 除 …
        tian_shen   = lunar.getDayTianShen()      # 天刑 / 青龙 …
        tian_luck   = lunar.getDayTianShenLuck()  # 吉 / 凶

        # 综合评级
        if tian_luck == "吉":
            dao_label = "🟡 黄道吉日"
        else:
            dao_label = "⚫ 黑道凶日"

        # ── 彭祖百忌 ───────────────────────────────────────────
        peng_zu_gan = lunar.getPengZuGan()    # 戊不受田田主不祥
        peng_zu_zhi = lunar.getPengZuZhi()    # 午不苫盖屋主更张

        # ── 日吉神 / 凶煞 ──────────────────────────────────────
        ji_shen   = lunar.getDayJiShen()      # List[str]
        xiong_sha = lunar.getDayXiongSha()    # List[str]

        # ── 宜 / 忌 ────────────────────────────────────────────
        yi_list = lunar.getDayYi()            # List[str]
        ji_list = lunar.getDayJi()            # List[str]

        # ── 黄道吉时 ───────────────────────────────────────────
        lucky_hours = _get_lucky_hours(lunar)

        # ── 随机语录 ───────────────────────────────────────────
        quote  = load_random_quote()
        q_text = quote.get("text", "")
        q_src  = quote.get("source", "")
        q_auth = quote.get("author", "")

        # ── 组装消息 ───────────────────────────────────────────
        lines: List[str] = []

        lines.append(f"📅  {year}年{month}月{day}日  {weekday}")
        lines.append(
            f"农历 {lunar_year_str}年（{sheng_xiao}年）"
            f"{lunar_month_str}{lunar_day_str}"
        )
        lines.append(f"干支  {year_gz}年 · {month_gz}月 · {day_gz}日")
        lines.append(
            f"值星  {zhi_xing}    "
            f"天神  {tian_shen}"
        )
        lines.append(dao_label)

        if jie_qi:
            lines.append(f"🌿 今日节气：【{jie_qi}】")

        lines.append("")
        lines.append(f"📖 彭祖百忌")
        lines.append(f"   干忌：{peng_zu_gan}")
        lines.append(f"   支忌：{peng_zu_zhi}")

        lines.append("")
        if ji_shen:
            lines.append(f"✨ 吉神：{'  '.join(ji_shen)}")
        if xiong_sha:
            lines.append(f"💀 凶煞：{'  '.join(xiong_sha)}")

        lines.append("")
        lines.append(f"✅ 宜：{'  '.join(yi_list) if yi_list else '无'}")
        lines.append(f"❌ 忌：{'  '.join(ji_list) if ji_list else '无'}")

        lines.append("")
        lines.append("⏰ 黄道吉时：")
        # 每行最多 2 个时辰
        for i in range(0, len(lucky_hours), 2):
            pair = lucky_hours[i : i + 2]
            lines.append("  " + "  ".join(pair))

        lines.append("")
        lines.append("─" * 22)

        if q_auth:
            lines.append(f"「{q_text}」")
            lines.append(f"—— {q_auth}  {q_src}")
        else:
            lines.append(f"「{q_text}」")
            lines.append(f"—— {q_src}")

        return "\n".join(lines)

    except Exception as exc:
        logger.error(f"生成万年历消息失败: {exc}", exc_info=True)
        return "❌ 生成万年历消息时出错，请稍后重试。"


# ============================================================
# 简单 asyncio 每日定时器
# ============================================================

class _DailyScheduler:
    """基于 asyncio.sleep 的轻量级每日定时器，无需额外依赖。"""

    def __init__(self) -> None:
        self._task:    Optional[asyncio.Task] = None  # type: ignore[type-arg]
        self._running: bool = False

    def start(self, coro_factory, hour: int, minute: int) -> None:
        if self._running:
            logger.warning("定时任务已在运行，忽略重复启动")
            return
        self._running = True
        try:
            loop = asyncio.get_running_loop()
            self._task = loop.create_task(
                self._loop(coro_factory, hour, minute), name="daily_calendar"
            )
        except RuntimeError:
            self._task = asyncio.ensure_future(
                self._loop(coro_factory, hour, minute)
            )
        logger.info(f"📅 每日万年历定时任务已启动，发送时间：{hour:02d}:{minute:02d}")

    async def _loop(self, coro_factory, hour: int, minute: int) -> None:
        while self._running:
            try:
                now    = datetime.now()
                target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                wait_secs = (target - now).total_seconds()
                logger.debug(f"下次万年历推送在 {wait_secs:.0f} 秒后（{target:%Y-%m-%d %H:%M}）")
                await asyncio.sleep(wait_secs)
                if self._running:
                    await coro_factory()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"定时任务执行异常: {exc}", exc_info=True)
                await asyncio.sleep(60)

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("每日万年历定时任务已停止")


# ============================================================
# 命令：/万年历
# ============================================================

class CalendarNowCommand(BaseCommand):
    """在当前聊天立即发送今日万年历。"""

    command_name        = "calendar_now"
    command_description = "立即发送今日万年历（农历、节气、宜忌、吉时、文学语录）"
    command_pattern     = r"^[/！!]万年历$"

    async def execute(self) -> Tuple[bool, Optional[str], bool]:
        try:
            msg = build_calendar_message()
            await self.send_text(msg)
        except Exception as exc:
            logger.error(f"/万年历 命令执行失败: {exc}", exc_info=True)
            await self.send_text("❌ 万年历生成失败，请稍后重试。")
            return False, str(exc), True
        return True, None, True


# ============================================================
# 插件主类
# ============================================================

@register_plugin
class DailyCalendarPlugin(BasePlugin):
    """每日万年历插件主类。"""

    plugin_name          = "daily_calendar"
    enable_plugin        = True
    dependencies:        list = []
    python_dependencies: list = ["lunar-python"]
    config_file_name     = "config.toml"

    config_schema = {
        "plugin": {
            "enabled": ConfigField(
                type=bool,
                default=True,
                description="是否启用每日万年历插件",
            ),
        },
        "schedule": {
            "send_time": ConfigField(
                type=str,
                default="08:00",
                description="每日推送时间，格式 HH:MM，例如 08:30 表示早上八点半",
                example="07:30",
            ),
        },
        "targets": {
            "group_ids": ConfigField(
                type=list,
                item_type="string",
                default=[],
                description="要定时推送的群号列表（字符串格式），例如 ['123456789']",
            ),
            "user_ids": ConfigField(
                type=list,
                item_type="string",
                default=[],
                description="要定时私聊推送的用户 ID 列表（字符串格式），例如 ['10001']",
            ),
            "platform": ConfigField(
                type=str,
                default="qq",
                description="平台标识，默认为 qq",
                choices=["qq"],
            ),
        },
    }

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._scheduler: _DailyScheduler = _DailyScheduler()
        self._scheduler_started: bool = False
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._delayed_start(), name="calendar_delayed_start")
        except RuntimeError:
            pass

    async def _delayed_start(self) -> None:
        await asyncio.sleep(2.0)
        await self._start_scheduler_once()

    async def _start_scheduler_once(self) -> None:
        if self._scheduler_started:
            return
        self._scheduler_started = True
        if not self.get_config("plugin.enabled", True):
            logger.info("每日万年历插件已禁用，不启动定时任务")
            return
        send_time_str: str = self.get_config("schedule.send_time", "08:00")
        try:
            parts  = send_time_str.strip().split(":")
            hour   = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            logger.warning(f"无效的推送时间格式 '{send_time_str}'，已回退到 08:00")
            hour, minute = 8, 0
        self._scheduler.start(self._send_daily_calendar, hour, minute)

    async def _send_daily_calendar(self) -> None:
        msg      = build_calendar_message()
        platform = self.get_config("targets.platform", "qq")
        group_ids: List[str] = [str(g) for g in self.get_config("targets.group_ids", [])]
        user_ids:  List[str] = [str(u) for u in self.get_config("targets.user_ids",  [])]
        sent = failed = 0

        for group_id in group_ids:
            try:
                stream = chat_api.get_stream_by_group_id(group_id, platform)
                if stream is None:
                    failed += 1; continue
                info      = chat_api.get_stream_info(stream)
                stream_id = info.get("stream_id") or getattr(stream, "stream_id", None)
                if stream_id:
                    await send_api.text_to_stream(text=msg, stream_id=stream_id)
                    sent += 1
                    logger.info(f"✅ 万年历已发送到群 {group_id}")
                else:
                    failed += 1
            except Exception as exc:
                logger.error(f"发送到群 {group_id} 失败: {exc}", exc_info=True)
                failed += 1

        for user_id in user_ids:
            try:
                stream = chat_api.get_stream_by_user_id(user_id, platform)
                if stream is None:
                    failed += 1; continue
                info      = chat_api.get_stream_info(stream)
                stream_id = info.get("stream_id") or getattr(stream, "stream_id", None)
                if stream_id:
                    await send_api.text_to_stream(text=msg, stream_id=stream_id)
                    sent += 1
                    logger.info(f"✅ 万年历已发送到用户 {user_id}")
                else:
                    failed += 1
            except Exception as exc:
                logger.error(f"发送到用户 {user_id} 失败: {exc}", exc_info=True)
                failed += 1

        logger.info(f"今日万年历推送完成：成功 {sent} 个，失败 {failed} 个")

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        return [
            (CalendarNowCommand.get_command_info(), CalendarNowCommand),
        ]
