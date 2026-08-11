"""内置技能：为常见分析场景注入提示并声明推荐工具子集。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Skill:
    """为分析场景注入提示，并声明推荐使用的工具子集。"""

    skill_id: str
    name: str
    description: str
    system_prompt: str
    tools: tuple[str, ...]

SKILL_HOLDINGS_OVERVIEW = Skill(
    skill_id="holdings_overview",
    name="持仓透视",
    description="查看当前真实持仓、成本与现价，评估持仓结构与仓位现状",
    system_prompt=(
        "优先调用 get_holdings 获取当前真实持仓与成本，再用 get_kline 查看关注标的的近期走势。"
        "持仓数量与成本以工具返回为准；未获取到的数据不得编造，行情快照注意 as_of 时效。"
    ),
    tools=("get_holdings", "get_kline"),
)

SKILL_NEWS_RADAR = Skill(
    skill_id="news_radar",
    name="资讯雷达",
    description="检索目标标的或产业赛道最近 7 天的新闻与宏观动向",
    system_prompt=(
        "调用 get_news 检索目标标的或产业赛道最近 7 天的新闻。新闻匹配为启发式（标的→赛道映射），"
        "注意时效与来源；引用时以工具返回的标题/摘要为准并标注来源与时间。"
    ),
    tools=("get_news",),
)

SKILL_DEEP_IMPACT = Skill(
    skill_id="deep_impact",
    name="深度影响分析",
    description="结合持仓、K 线与新闻做完整的标的影响分析（默认技能）",
    system_prompt=(
        "先 get_holdings 获取持仓上下文，再 get_kline 查看目标标的近期走势，"
        "用 get_news 补充产业与宏观新闻，最后按输出契约给出结构化影响分析。"
        "工具数据均为快照，注意 as_of 时效；推断需列明假设与失效条件，无法确证的放入 unknown。"
    ),
    tools=("get_holdings", "get_kline", "get_news"),
)

BUILTIN_SKILLS = (SKILL_HOLDINGS_OVERVIEW, SKILL_NEWS_RADAR, SKILL_DEEP_IMPACT)

DEFAULT_ACTIVE_SKILLS = (SKILL_DEEP_IMPACT,)
