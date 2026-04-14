"""
每日万年历插件 —— 适配 MaiBot / 麦麦 Bot
==========================================

功能
----
* 每天定时（用户可在 config.toml 中配置推送时间）在指定群聊或私聊
  发送今日万年历，内容包括：

  - 公历日期、星期
  - 农历日期、干支纪年、生肖
  - 当日节气（如有，使用寿星公式计算）
  - 宜 / 忌 事项（基于当日干支确定性推算）
  - 今日吉时（基于日天干传统算法）
  - 随机古典文学语录（从 data/quotes.json 抽取）

* 支持 /万年历 命令立即推送（在当前聊天回复）
* 支持 /万年历预览 命令查看今日万年历而不计入定时记录

作者：Shao Chi
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

# 尝试导入农历库
try:
    from lunardate import LunarDate  # type: ignore

    HAS_LUNARDATE = True
except ImportError:
    HAS_LUNARDATE = False
    LunarDate = None  # type: ignore

logger = logging.getLogger(__name__)

PLUGIN_DIR = Path(__file__).parent
DATA_DIR = PLUGIN_DIR / "data"

# ============================================================
# 中国传统历法常量
# ============================================================

# 天干
HEAVENLY_STEMS: List[str] = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
# 地支
EARTHLY_BRANCHES: List[str] = [
    "子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"
]
# 生肖（与地支对应）
ZODIAC: List[str] = ["鼠", "牛", "虎", "兔", "龙", "蛇", "马", "羊", "猴", "鸡", "狗", "猪"]
# 农历月份
LUNAR_MONTHS: List[str] = [
    "正月", "二月", "三月", "四月", "五月", "六月",
    "七月", "八月", "九月", "十月", "冬月", "腊月",
]
# 农历日期
LUNAR_DAYS: List[str] = [
    "初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
    "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十",
]
# 星期
WEEKDAYS: List[str] = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 二十四节气（名称, 对应月份, 寿星公式 C 值 —— 适用于21世纪）
SOLAR_TERMS_INFO: List[Tuple[str, int, float]] = [
    ("小寒",  1,  5.4055), ("大寒",  1, 20.1200),
    ("立春",  2,  3.8700), ("雨水",  2, 18.7300),
    ("惊蛰",  3,  5.6300), ("春分",  3, 20.6460),
    ("清明",  4,  4.7500), ("谷雨",  4, 20.1000),
    ("立夏",  5,  5.5200), ("小满",  5, 21.0400),
    ("芒种",  6,  5.6780), ("夏至",  6, 21.3700),
    ("小暑",  7,  6.9700), ("大暑",  7, 22.8300),
    ("立秋",  8,  7.7870), ("处暑",  8, 23.0900),
    ("白露",  9,  7.1690), ("秋分",  9, 23.0420),
    ("寒露", 10,  7.1290), ("霜降", 10, 23.1380),
    ("立冬", 11,  7.4150), ("小雪", 11, 22.7400),
    ("大雪", 12,  6.7400), ("冬至", 12, 21.9400),
]

# 十二时辰（名称, 时间段）
SHI_CHEN: List[Tuple[str, str]] = [
    ("子时", "23:00~01:00"), ("丑时", "01:00~03:00"),
    ("寅时", "03:00~05:00"), ("卯时", "05:00~07:00"),
    ("辰时", "07:00~09:00"), ("巳时", "09:00~11:00"),
    ("午时", "11:00~13:00"), ("未时", "13:00~15:00"),
    ("申时", "15:00~17:00"), ("酉时", "17:00~19:00"),
    ("戌时", "19:00~21:00"), ("亥时", "21:00~23:00"),
]

# ── 建除十二值神（宜/忌核心来源）─────────────────────────────────────
# 计算公式：(日支序号 - 月支序号) % 12 → 值神索引
JIAN_CHU_NAMES: List[str] = [
    "建", "除", "满", "平", "定", "执", "破", "危", "成", "收", "开", "闭"
]

# 农历月份 → 对应地支序号（子=0…亥=11）
# 正月建寅(2)，二月建卯(3)，依次类推
LUNAR_MONTH_BRANCH: Dict[int, int] = {
    1: 2, 2: 3, 3: 4, 4: 5, 5: 6,  6: 7,
    7: 8, 8: 9, 9: 10, 10: 11, 11: 0, 12: 1,
}

# 每个值神的宜事列表
JIAN_CHU_YI: Dict[str, List[str]] = {
    "建": ["出行", "祭祀", "祈福", "开光", "上梁", "竖柱"],
    "除": ["解除", "扫舍", "沐浴", "理发", "裁衣", "求医"],
    "满": ["纳财", "开市", "入宅", "祭祀", "嫁娶"],
    "平": ["出行", "移徙", "嫁娶", "纳畜", "交易"],
    "定": ["嫁娶", "开市", "立券", "交易", "纳财", "祭祀"],
    "执": ["祭祀", "捕捉", "纳畜", "动土"],
    "破": ["求医", "疗病"],                        # 破日诸事不宜，仅宜求医
    "危": ["祭祀", "解除", "求医"],
    "成": ["嫁娶", "开市", "移徙", "入宅", "纳财", "立券"],
    "收": ["纳财", "收敛", "祭祀", "纳畜", "嫁娶"],
    "开": ["开市", "出行", "嫁娶", "移徙", "入宅", "祭祀"],
    "闭": ["动土", "修造", "安葬", "塞穴"],
}

# 每个值神的忌事列表
JIAN_CHU_JI: Dict[str, List[str]] = {
    "建": ["嫁娶", "动土", "安葬", "开市"],
    "除": ["嫁娶", "安葬", "出行"],
    "满": ["嫁娶", "动土", "安葬", "出行"],
    "平": ["动土", "安葬", "开市"],
    "定": ["词讼", "出行", "动土"],
    "执": ["嫁娶", "开市", "出行", "移徙"],
    "破": ["嫁娶", "开市", "动土", "移徙", "入宅", "出行", "安葬"],
    "危": ["登高", "安床", "出行", "嫁娶"],
    "成": ["词讼", "动土"],
    "收": ["出行", "开市", "嫁娶", "安葬"],
    "开": ["安葬", "动土"],
    "闭": ["嫁娶", "出行", "开市", "开光"],
}

# ── 天乙贵人时（吉时主要来源）──────────────────────────────────────────
# 按日天干索引（甲=0…癸=9）→ 天乙贵人所在两个时辰（地支序号）
TIANYI_GUIREN: Dict[int, List[int]] = {
    0: [1, 7],   # 甲 → 丑(1)、未(7)
    1: [0, 8],   # 乙 → 子(0)、申(8)
    2: [11, 9],  # 丙 → 亥(11)、酉(9)
    3: [11, 9],  # 丁 → 亥(11)、酉(9)
    4: [1, 7],   # 戊 → 丑(1)、未(7)
    5: [0, 8],   # 己 → 子(0)、申(8)
    6: [1, 7],   # 庚 → 丑(1)、未(7)
    7: [6, 2],   # 辛 → 午(6)、寅(2)
    8: [3, 5],   # 壬 → 卯(3)、巳(5)
    9: [3, 5],   # 癸 → 卯(3)、巳(5)
}

# 日禄时：日天干对应"禄"所在地支序号
# 甲禄在寅，乙禄在卯，丙/戊禄在巳，丁/己禄在午，庚禄在申，辛禄在酉，壬禄在亥，癸禄在子
RI_LU: Dict[int, int] = {
    0: 2,   # 甲 → 寅
    1: 3,   # 乙 → 卯
    2: 5,   # 丙 → 巳
    3: 6,   # 丁 → 午
    4: 5,   # 戊 → 巳
    5: 6,   # 己 → 午
    6: 8,   # 庚 → 申
    7: 9,   # 辛 → 酉
    8: 11,  # 壬 → 亥
    9: 0,   # 癸 → 子
}


# ============================================================
# 传统历法计算函数
# ============================================================

def calc_solar_term(year: int, month: int, day: int) -> Optional[str]:
    """
    使用寿星公式判断今天是否是节气。
    公式：节气日 = int(Y × 0.2422 + C) - int((Y-1) / 4)
    其中 Y 为年份后两位，C 见 SOLAR_TERMS_INFO。
    """
    y = year % 100
    for term_name, term_month, c in SOLAR_TERMS_INFO:
        if term_month != month:
            continue
        leap_correction = (y - 1) // 4
        term_day = int(y * 0.2422 + c) - leap_correction
        if term_day == day:
            return term_name
    return None


def get_ganzhi_year(lunar_year: int) -> Tuple[str, str]:
    """
    根据农历年份获取干支和生肖。
    以1924年（甲子年）为基准。
    """
    offset = (lunar_year - 1924) % 60
    stem   = HEAVENLY_STEMS[offset % 10]
    branch = EARTHLY_BRANCHES[offset % 12]
    zodiac = ZODIAC[(lunar_year - 1924) % 12]
    return f"{stem}{branch}", zodiac


def get_ganzhi_month(year: int, month: int) -> str:
    """
    推算月柱干支（五虎遁年法）。
    月支：寅月为正月起（寅=2），即 branch_idx = (month+1) % 12。
    月干：依年天干决定寅月天干，再顺推。
    """
    # 月支
    month_branch_idx = (month + 1) % 12
    # 年天干索引（按甲子年起推）
    year_stem_idx = (year - 1924) % 10
    # 五虎遁年：甲/己年寅月起丙，乙/庚年起戊，丙/辛年起庚，丁/壬年起壬，戊/癸年起甲
    # 即寅月天干起点 = [2, 4, 6, 8, 0, 2, 4, 6, 8, 0]
    month_stem_starts = [2, 4, 6, 8, 0, 2, 4, 6, 8, 0]
    month_stem_idx = (month_stem_starts[year_stem_idx] + (month - 1)) % 10
    return f"{HEAVENLY_STEMS[month_stem_idx]}{EARTHLY_BRANCHES[month_branch_idx]}"


def get_ganzhi_day(year: int, month: int, day: int) -> str:
    """
    推算日柱干支。以2000年1月7日（甲子日）为基准。
    """
    base   = date(2000, 1, 7)
    target = date(year, month, day)
    offset = (target - base).days % 60
    return f"{HEAVENLY_STEMS[offset % 10]}{EARTHLY_BRANCHES[offset % 12]}"


def get_jian_chu(lunar_month: int, day_branch_idx: int) -> str:
    """
    根据农历月份和日支序号，计算当日建除十二值神。
    公式：(日支序号 - 月建地支序号) % 12 → 值神索引
    """
    month_branch = LUNAR_MONTH_BRANCH.get(lunar_month, 2)
    idx = (day_branch_idx - month_branch) % 12
    return JIAN_CHU_NAMES[idx]


def get_yi_ji(
    year: int, month: int, day: int,
    lunar_month: Optional[int] = None,
) -> Tuple[List[str], List[str], str]:
    """
    使用建除十二值神推算今日宜 / 忌，同时返回值神名称。

    参数
    ----
    lunar_month : 农历月份（1‒12）。若为 None 则用公历月份近似（降级）。

    返回
    ----
    (宜列表, 忌列表, 值神名)
    """
    gz_day          = get_ganzhi_day(year, month, day)
    day_branch_idx  = EARTHLY_BRANCHES.index(gz_day[1])
    lm              = lunar_month if lunar_month is not None else month
    zhi_shen        = get_jian_chu(lm, day_branch_idx)

    yi = JIAN_CHU_YI.get(zhi_shen, [])
    ji = JIAN_CHU_JI.get(zhi_shen, [])
    return yi, ji, zhi_shen


def get_ji_shi(year: int, month: int, day: int) -> List[str]:
    """
    使用天乙贵人时 + 日禄时推算今日四个吉时。

    天乙贵人时（2个）：传统择日最重要的吉神时辰。
    日禄时（1个）：日天干的"禄"所在时辰，财运/事业吉利。
    进禄时（1个）：禄前一位时辰，为"进气"之时。
    去重后取前四，按时辰先后排序。
    """
    gz_day    = get_ganzhi_day(year, month, day)
    stem_idx  = HEAVENLY_STEMS.index(gz_day[0])

    guiren    = TIANYI_GUIREN.get(stem_idx, [1, 7])   # 天乙贵人两个时辰
    lu        = RI_LU.get(stem_idx, 2)                 # 日禄时
    jin_lu    = (lu + 1) % 12                          # 进禄时（禄后一位）

    seen: List[int] = []
    for t in guiren + [lu, jin_lu]:
        if t not in seen:
            seen.append(t)

    # 按时辰顺序排序（子=0 → 亥=11，注意子时跨日用0排序）
    seen_sorted = sorted(seen[:4], key=lambda x: x)
    return [f"{SHI_CHEN[i][0]}（{SHI_CHEN[i][1]}）" for i in seen_sorted]


def get_lunar_info(year: int, month: int, day: int) -> Dict[str, str]:
    """
    获取农历日期、干支年、生肖信息。
    优先使用 lunardate 库；不可用时仅返回干支年（以正月一日为年界）。
    """
    if HAS_LUNARDATE and LunarDate is not None:
        try:
            lunar = LunarDate.fromSolarDate(year, month, day)
            lunar_month_str = LUNAR_MONTHS[lunar.month - 1]
            if getattr(lunar, "isLeapMonth", False):
                lunar_month_str = f"闰{lunar_month_str}"
            lunar_day_str = LUNAR_DAYS[lunar.day - 1]
            year_ganzhi, zodiac = get_ganzhi_year(lunar.year)
            return {
                "lunar_date":       f"{lunar_month_str}{lunar_day_str}",
                "year_ganzhi":      year_ganzhi,
                "zodiac":           zodiac,
                "lunar_month":      str(lunar.month),
                "lunar_available":  "true",
            }
        except Exception as exc:
            logger.warning(f"lunardate 计算失败，使用降级逻辑: {exc}")

    # 降级：以公历年份推干支（不区分月份界）
    year_ganzhi, zodiac = get_ganzhi_year(year)
    return {
        "lunar_date":      "（需安装 lunardate 库）",
        "year_ganzhi":     year_ganzhi,
        "zodiac":          zodiac,
        "lunar_month":     str(month),   # 降级时用公历月份近似
        "lunar_available": "false",
    }


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


# ============================================================
# 万年历消息构建
# ============================================================

def build_calendar_message(today: Optional[date] = None) -> str:
    """构建今日万年历完整消息文本。"""
    if today is None:
        today = date.today()

    year, month, day = today.year, today.month, today.day
    weekday = WEEKDAYS[today.weekday()]

    # 农历
    lunar_info  = get_lunar_info(year, month, day)
    year_gz     = lunar_info["year_ganzhi"]
    zodiac      = lunar_info["zodiac"]
    lunar_date  = lunar_info["lunar_date"]

    # 干支
    month_gz = get_ganzhi_month(year, month)
    day_gz   = get_ganzhi_day(year, month, day)

    # 节气
    solar_term = calc_solar_term(year, month, day)

    # 宜 / 忌（建除十二值神）
    lunar_month_int = int(lunar_info.get("lunar_month", month))
    yi, ji, zhi_shen = get_yi_ji(year, month, day, lunar_month=lunar_month_int)

    # 吉时（天乙贵人时 + 日禄时）
    ji_shi = get_ji_shi(year, month, day)

    # 语录
    quote  = load_random_quote()
    q_text = quote.get("text", "")
    q_src  = quote.get("source", "")
    q_auth = quote.get("author", "")

    lines: List[str] = []

    # ── 标题行 ──
    lines.append(f"📅  {year}年{month}月{day}日  {weekday}")
    lines.append(f"农历 {year_gz}年（{zodiac}年）{lunar_date}")
    lines.append(f"干支  {year_gz}年 · {month_gz}月 · {day_gz}日")
    lines.append(f"今日值神：{zhi_shen}")

    # ── 节气 ──
    if solar_term:
        lines.append(f"")
        lines.append(f"🌿 今日节气：【{solar_term}】")

    lines.append("")

    # ── 宜 / 忌 ──
    lines.append(f"✅ 宜：{'  '.join(yi)}")
    lines.append(f"❌ 忌：{'  '.join(ji)}")

    lines.append("")

    # ── 吉时（2+2 排列）──
    lines.append("⏰ 今日吉时：")
    lines.append(f"  {ji_shi[0]}  {ji_shi[1]}")
    lines.append(f"  {ji_shi[2]}  {ji_shi[3]}")

    lines.append("")
    lines.append("─" * 22)

    # ── 文学语录 ──
    if q_auth:
        lines.append(f"「{q_text}」")
        lines.append(f"—— {q_auth}  {q_src}")
    else:
        lines.append(f"「{q_text}」")
        lines.append(f"—— {q_src}")

    return "\n".join(lines)


# ============================================================
# 简单 asyncio 每日定时器
# ============================================================

class _DailyScheduler:
    """
    基于 asyncio.sleep 的轻量级每日定时器，无需额外依赖。
    每天在指定 hour:minute 执行一次回调协程。
    """

    def __init__(self) -> None:
        self._task:    Optional[asyncio.Task] = None  # type: ignore[type-arg]
        self._running: bool = False

    def start(self, coro_factory, hour: int, minute: int) -> None:
        """在当前正在运行的事件循环中创建后台任务。"""
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
            # 兜底：ensure_future（Python 3.7+）
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
                await asyncio.sleep(60)  # 出错后等待 1 分钟再重试

    def stop(self) -> None:
        """停止定时任务。"""
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

    plugin_name         = "daily_calendar"
    enable_plugin       = True
    dependencies:       list = []
    python_dependencies: list = ["lunardate"]
    config_file_name    = "config.toml"

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
                description=(
                    "要定时推送的群号列表（字符串格式），"
                    "例如 ['123456789', '987654321']"
                ),
            ),
            "user_ids": ConfigField(
                type=list,
                item_type="string",
                default=[],
                description=(
                    "要定时私聊推送的用户 ID 列表（字符串格式），"
                    "例如 ['10001', '10002']"
                ),
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
        # 在事件循环中延迟启动定时任务（等待配置加载完成）
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._delayed_start(), name="calendar_delayed_start")
        except RuntimeError:
            # __init__ 在非异步上下文中被调用，依赖后续懒启动
            pass

    async def _delayed_start(self) -> None:
        """延迟 2 秒后启动定时任务，确保 config.toml 已被加载。"""
        await asyncio.sleep(2.0)
        await self._start_scheduler_once()

    async def _start_scheduler_once(self) -> None:
        """确保定时任务只启动一次。"""
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
            logger.warning(
                f"无效的推送时间格式 '{send_time_str}'，已回退到 08:00"
            )
            hour, minute = 8, 0

        self._scheduler.start(self._send_daily_calendar, hour, minute)

    async def _send_daily_calendar(self) -> None:
        """定时推送逻辑：向所有配置的群和用户发送今日万年历。"""
        msg      = build_calendar_message()
        platform = self.get_config("targets.platform", "qq")
        group_ids: List[str] = [
            str(g) for g in self.get_config("targets.group_ids", [])
        ]
        user_ids: List[str] = [
            str(u) for u in self.get_config("targets.user_ids", [])
        ]

        sent   = 0
        failed = 0

        for group_id in group_ids:
            try:
                stream = chat_api.get_stream_by_group_id(group_id, platform)
                if stream is None:
                    logger.warning(f"找不到群 {group_id} 的聊天流，已跳过")
                    failed += 1
                    continue
                info       = chat_api.get_stream_info(stream)
                stream_id  = info.get("stream_id")
                if not stream_id:
                    # 兜底：直接用 stream 对象的 stream_id 属性
                    stream_id = getattr(stream, "stream_id", None)
                if stream_id:
                    await send_api.text_to_stream(text=msg, stream_id=stream_id)
                    sent += 1
                    logger.info(f"✅ 万年历已发送到群 {group_id}")
                else:
                    logger.warning(f"无法获取群 {group_id} 的 stream_id，已跳过")
                    failed += 1
            except Exception as exc:
                logger.error(f"发送到群 {group_id} 失败: {exc}", exc_info=True)
                failed += 1

        for user_id in user_ids:
            try:
                stream = chat_api.get_stream_by_user_id(user_id, platform)
                if stream is None:
                    logger.warning(f"找不到用户 {user_id} 的聊天流，已跳过")
                    failed += 1
                    continue
                info       = chat_api.get_stream_info(stream)
                stream_id  = info.get("stream_id")
                if not stream_id:
                    stream_id = getattr(stream, "stream_id", None)
                if stream_id:
                    await send_api.text_to_stream(text=msg, stream_id=stream_id)
                    sent += 1
                    logger.info(f"✅ 万年历已发送到用户 {user_id}")
                else:
                    logger.warning(f"无法获取用户 {user_id} 的 stream_id，已跳过")
                    failed += 1
            except Exception as exc:
                logger.error(f"发送到用户 {user_id} 失败: {exc}", exc_info=True)
                failed += 1

        logger.info(f"今日万年历推送完成：成功 {sent} 个，失败 {failed} 个")

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        return [
            (CalendarNowCommand.get_command_info(), CalendarNowCommand),
        ]
