"""平衡市场日面板上的双重差分与试点前安慰剂诊断。"""

from datetime import date
from fractions import Fraction
from statistics import mean

from .spec import SPEC


_TREATED, _CONTROL = _GROUPS = tuple(SPEC["fields"][2]["options"])
_REQUIRED_FIELDS = {field["key"] for field in SPEC["fields"]}
_DEFAULTS = {field["key"]: field["default"] for field in SPEC["params"]}
_PERIODS = ("试点前段", "试点前后段", "试点后")


def _date(value, label):
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
            if value == parsed.isoformat():
                return parsed
        except ValueError:
            pass
    raise ValueError(f"{label}须使用有效的 YYYY-MM-DD 日期。")


def _number(value, minimum, maximum):
    return type(value) in (int, float) and minimum <= value <= maximum


def validate_rows(rows):
    if not isinstance(rows, (list, tuple)) or not rows:
        raise ValueError("门岗明细至少需一条市场日记录。")
    result, groups, seen = [], {}, set()
    for index, row in enumerate(rows, start=1):
        label = f"第 {index} 条记录"
        if not isinstance(row, dict) or not _REQUIRED_FIELDS <= row.keys():
            raise ValueError(f"{label}缺少市场日字段或格式有误。")
        market = row["market_id"]
        if not isinstance(market, str) or not market.strip() or len(market.strip()) > 300:
            raise ValueError(f"{label}的市场名称须为 1–300 个字符。")
        market = market.strip()
        day = _date(row["date"], f"{label}的观测日期").isoformat()
        group = row["group"]
        if group not in _GROUPS:
            raise ValueError(f"{label}的分组须为试点组或对照组。")
        if groups.setdefault(market, group) != group:
            raise ValueError(f"市场「{market}」的试点分组不一致。")
        if (market, day) in seen:
            raise ValueError(f"市场「{market}」在 {day} 的记录重复。")
        wait = row["wait_minutes"]
        trucks = row["truck_count"]
        if not _number(wait, 0, 1440):
            raise ValueError(f"{label}的平均等候须为 0–1440 分钟。")
        if type(trucks) is not int or not 1 <= trucks <= 10000:
            raise ValueError(f"{label}的有效车辆数须为 1–10000 的整数。")
        seen.add((market, day))
        result.append({**row, "market_id": market})
    return result


def _parameters(normalized_params):
    if not isinstance(normalized_params, dict) or normalized_params.keys() - _DEFAULTS.keys():
        raise ValueError("运行参数格式有误或包含未知参数。")
    options = {**_DEFAULTS, **normalized_params}
    pilot = _date(options["pilot_date"], "试点启动日").toordinal()
    days = options["window_days"]
    threshold = options["pretrend_threshold"]
    if type(days) is not int or not 1 <= days <= 28:
        raise ValueError("每段自然日数须为 1–28 的整数。")
    if not _number(threshold, 0, 1440):
        raise ValueError("试点前偏离阈值须为 0–1440 分钟。")
    start, end = pilot - 2 * days, pilot + days
    if start < date.min.toordinal() or end - 1 > date.max.toordinal():
        raise ValueError("试点窗口超出有效日期范围，请调整启动日或每段天数。")
    return start, pilot, end, days, Fraction(str(threshold))


def _balanced_panel(readings, start, end, days):
    by_market = {}
    outside = 0
    for row in readings:
        day = date.fromisoformat(row["date"]).toordinal()
        if start <= day < end:
            by_market.setdefault(row["market_id"], {})[day] = row
        else:
            outside += 1

    panel = {group: [[], [], []] for group in _GROUPS}
    sizes = dict.fromkeys(_GROUPS, 0)
    coverage = []
    for market, observed in sorted(by_market.items()):
        group = next(iter(observed.values()))["group"]
        missing = [date.fromordinal(day).isoformat() for day in range(start, end) if day not in observed]
        coverage.append({
            "market": market, "group": group, "observed_days": len(observed),
            "expected_days": end - start, "status": "排除" if missing else "纳入",
            "missing": "、".join(missing) if missing else "无",
        })
        if not missing:
            sizes[group] += 1
            for day, row in observed.items():
                panel[group][(day - start) // days].append(row)

    absent = [group for group, count in sizes.items() if count == 0]
    if absent:
        raise ValueError(f"{'、'.join(absent)}无完整观测市场。请补齐每日记录或调整窗口。")
    return panel, sizes, coverage, outside


def _display(value):
    return float(round(value, 2))


def _table(title, columns, rows):
    return {"title": title, "columns": [{"key": key, "label": label} for key, label in columns], "rows": rows}


def _range_label(start, end):
    first, last = date.fromordinal(start), date.fromordinal(end - 1)
    return f"{first.isoformat()} 至 {last.isoformat()}"


def analyze(rows, normalized_params):
    readings = validate_rows(rows)
    start, pilot, end, days, threshold = _parameters(normalized_params)
    panel, sizes, coverage, outside = _balanced_panel(readings, start, end, days)
    means = {
        group: [mean(Fraction(str(row["wait_minutes"])) for row in block) for block in panel[group]]
        for group in _GROUPS
    }
    before = {group: mean(means[group][:2]) for group in _GROUPS}
    changes = {group: means[group][2] - before[group] for group in _GROUPS}
    effect = changes[_TREATED] - changes[_CONTROL]
    gaps = [treated - control for treated, control in zip(means[_TREATED], means[_CONTROL])]
    placebo = gaps[1] - gaps[0]
    review_pretrend = abs(placebo) > threshold
    excluded = len(coverage) - sum(sizes.values())
    sparse = min(sizes.values()) < 2
    pretrend_status = "超过复核阈值" if review_pretrend else "在复核阈值内"
    cutoff = date.fromordinal(pilot - days).isoformat()
    warnings = []
    if excluded:
        warnings.append(f"{excluded} 家市场因缺报排除。")
    if sparse:
        warnings.append("至少一组仅有一家完整市场，估计易受单个市场影响。")
    if review_pretrend:
        warnings.append("试点前组间差值变化超过阈值，复核同期变化假设。")

    cells, diagnostics = [], []
    for group in _GROUPS:
        early, late, after = panel[group]
        for period, dates, observations, value in (
            ("试点前", _range_label(start, pilot), early + late, before[group]),
            ("试点后", _range_label(pilot, end), after, means[group][2]),
        ):
            cells.append({
                "group": group, "period": period, "dates": dates, "markets": sizes[group],
                "market_days": len(observations), "trucks": sum(row["truck_count"] for row in observations),
                "wait": _display(value),
            })
        diagnostics.append({
            "group": group, "early": _display(means[group][0]), "late": _display(means[group][1]),
            "change": _display(means[group][1] - means[group][0]),
        })

    return {
        "state": "warning" if warnings else "ok",
        "headline": f"双重差分为 {_display(effect):+g} 分钟。",
        "note": f"纳入 {sum(sizes.values())} 家市场。窗口外 {outside} 条。" + "".join(warnings),
        "metrics": [
            {"label": "双重差分（分钟）", "value": _display(effect), "detail": "试点组变化减去对照组变化，负值表示相对缩短。"},
            {"label": "试点组变化（分钟）", "value": _display(changes[_TREATED]), "detail": f"{sizes[_TREATED]} 家完整市场，试点后减试点前。"},
            {"label": "对照组变化（分钟）", "value": _display(changes[_CONTROL]), "detail": f"{sizes[_CONTROL]} 家完整市场，试点后减试点前。"},
            {"label": "试点前安慰剂差分（分钟）", "value": _display(placebo),
             "detail": f"{pretrend_status}（{float(threshold):g} 分钟），假启动日 {cutoff}。"},
            {"label": "平衡市场数", "value": sum(sizes.values()), "detail": f"窗口内 {len(coverage)} 家市场，排除 {excluded} 家。"},
        ],
        "tables": [
            _table("试点组与对照组前后均值", (
                ("group", "分组"), ("period", "阶段"), ("dates", "观测日期（含首尾）"),
                ("markets", "市场数"), ("market_days", "市场日数"), ("trucks", "有效车辆数"),
                ("wait", "平均等候（分钟）"),
            ), cells),
            _table("试点前趋势与安慰剂", (
                ("group", "分组"), ("early", f"{_range_label(start, pilot - days)}（分钟）"),
                ("late", f"{_range_label(pilot - days, pilot)}（分钟）"), ("change", "后段减前段（分钟）"),
            ), diagnostics),
            _table("市场观测覆盖", (
                ("market", "市场"), ("group", "分组"), ("observed_days", "已报日数"),
                ("expected_days", "应报日数"), ("status", "样本状态"), ("missing", "缺报日期"),
            ), coverage),
        ],
        "charts": [
            {"title": "两组前后平均等候（分钟）", "kind": "bar", "series": [
                {"label": f"{cell['group']} · {cell['period']}", "value": cell["wait"]} for cell in cells
            ]},
            {"title": "各段组间差值（试点组减对照组，分钟）", "kind": "line", "series": [
                {"label": period, "value": _display(gap)} for period, gap in zip(_PERIODS, gaps)
            ]},
        ],
        "sections": [
            {"title": "样本与计算口径", "text": "三个窗口逐日完整的市场共同进入主估计和诊断。"
             "市场日等权，试点前合并两个等长窗口。差分和阈值比较使用展示取整前的数值。"},
            {"title": "试点前诊断", "text": f"假启动日为 {cutoff}，安慰剂差分{pretrend_status}。"
             "较小的偏离仍可能伴随启动后趋势分化。该阈值用于业务复核，报告仅提供描述性点估计。"},
            {"title": "解释限制", "text": "因果解释依赖平行趋势、无提前响应和无跨市场分流。"
             "需核对同期施工、节日备货、车型与到货时段变化。缺报市场的剔除可能影响代表性，"
             "短窗口结果仅反映纳入市场和当前观测期。"},
        ],
    }
