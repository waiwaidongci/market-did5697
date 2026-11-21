"""固定的门岗日汇总，包含一个试点后缺报的对照市场。"""

from datetime import date, timedelta


_MARKETS = (
    ("南桥", "试点组", 32), ("西苑", "试点组", 36), ("滨河", "试点组", 40),
    ("新街", "对照组", 24), ("石桥", "对照组", 28), ("北仓", "对照组", 32),
    ("东埠", "对照组", 48),
)


def demo_rows():
    start = date(2026, 10, 26)
    weekday_adjustment = (-2, -1, 0, 1, 0, -1, 3)
    rows = []
    for offset in range(21):
        day = start + timedelta(days=offset)
        week, weekday = divmod(offset, 7)
        for index, (market, group, baseline) in enumerate(_MARKETS):
            if market == "东埠" and day == date(2026, 11, 12):
                continue
            reduction = 8 if week == 2 and group == "试点组" else 0
            rows.append({
                "market_id": market, "date": day.isoformat(), "group": group,
                "wait_minutes": baseline + 2 * week + weekday_adjustment[weekday] - reduction,
                "truck_count": 24 + 5 * index + 2 * weekday,
            })
    return rows
