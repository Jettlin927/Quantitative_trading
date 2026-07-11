"""按研究切片执行只读数据质量检查并登记可审计结果。"""

from .contracts import QualityCheckContract, QualityRuleResult, summarize_quality_status
from .runner import run_data_quality_check

__all__ = ["QualityCheckContract", "QualityRuleResult", "run_data_quality_check", "summarize_quality_status"]
