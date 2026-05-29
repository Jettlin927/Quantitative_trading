import os
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException


def get_pro_api(token: str | None = None) -> Any:
    effective_token = token or os.getenv("TUSHARE_TOKEN")
    if not effective_token or effective_token == "在这里填你的_tushare_token":
        raise HTTPException(status_code=400, detail="请先在 .env 里配置 TUSHARE_TOKEN，或在请求体里临时传入 token。")

    import tushare as ts

    return ts.pro_api(effective_token)


def tushare_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def parse_tushare_date(value: str | int | float | None) -> date | None:
    if value in (None, ""):
        return None
    return datetime.strptime(str(int(value)) if isinstance(value, float) else str(value), "%Y%m%d").date()


def decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        if str(value).lower() == "nan":
            return None
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
