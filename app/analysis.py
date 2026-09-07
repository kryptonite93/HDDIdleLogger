"""Pure timeout model. Inputs end at a successful observation, never wall-clock now."""
import math


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered)-1)*fraction
    lower, upper = math.floor(index), math.ceil(index)
    return ordered[lower] + (ordered[upper]-ordered[lower])*(index-lower)


def analyze(intervals, sessions, settings):
    valid_seconds = sum(max(0, s["last_observed_at"]-s["started_at"]) for s in sessions)
    complete = [i["duration_seconds"] for i in intervals
                if not i["start_is_censored"] and not i["end_is_censored"]]
    # A segment end is right-censored: use the proven idle portion, never infer a wake.
    tails = [i["duration_seconds"] for i in intervals
             if not i["start_is_censored"] and i["end_is_censored"]]
    open_seconds = sum(max(0, s["last_observed_at"]-s["idle_started_at"])
                       for s in sessions if s["ended_at"] is None and s["last_activity_at"] is not None)
    rows = []
    days = valid_seconds/86400
    for minutes in settings.timeouts_minutes:
        timeout = minutes*60
        completed_down = sum(max(0, gap-timeout) for gap in complete)
        tail_down = sum(max(0, gap-timeout) for gap in tails)
        current_down = max(0, open_seconds-timeout)
        total = completed_down + tail_down + current_down
        cycles = sum(gap > timeout for gap in complete)
        opportunities = cycles + sum(gap > timeout for gap in tails) + int(open_seconds > timeout)
        rows.append({"timeout_minutes": minutes, "spin_down_opportunities": opportunities,
                     "completed_spin_ups": cycles, "spin_ups_per_day": cycles/days if days else 0,
                     "spun_down_hours": total/3600, "spun_down_hours_per_day": total/3600/days if days else 0,
                     "completed_spun_down_hours": completed_down/3600,
                     "censored_end_spun_down_hours": tail_down/3600,
                     "current_potential_spun_down_hours": current_down/3600,
                     "percent_valid_time": total/valid_seconds*100 if valid_seconds else 0})
    maximum = max((r["spun_down_hours"] for r in rows), default=0)
    # Left-censored first intervals never contribute to recommendation or statistics.
    choices = [r for r in rows if r["spin_ups_per_day"] <= settings.cycling_warning_threshold
               and r["spun_down_hours"] >= maximum*settings.recommendation_efficiency_threshold]
    recommendation = min((r["timeout_minutes"] for r in choices), default=None) if maximum > 0 else None
    boundaries = [300, 900, 1800, 3600, 7200, 14400, 28800, math.inf]
    labels = ["<5m", "5–15m", "15–30m", "30–60m", "1–2h", "2–4h", "4–8h", "8h+"]
    histogram = []
    lower = 0
    for label, upper in zip(labels, boundaries):
        histogram.append({"label": label, "count": sum(lower <= v < upper for v in complete)})
        lower = upper
    return {"valid_observation_seconds": valid_seconds, "completed_interval_count": len(complete),
            "longest_idle_seconds": max(complete, default=None), "median_idle_seconds": percentile(complete, 0.5),
            "p75_idle_seconds": percentile(complete, 0.75), "p90_idle_seconds": percentile(complete, 0.9),
            "excluded_initial_intervals": sum(bool(i["start_is_censored"]) for i in intervals),
            "right_censored_intervals": len(tails), "timeouts": rows, "histogram": histogram,
            "recommendation_minutes": recommendation, "preliminary": valid_seconds < 72*3600,
            "recommendation_reason": "No modeled benefit yet" if maximum == 0 else
            "No timeout meets both thresholds" if recommendation is None else "Meets cycling and efficiency thresholds"}


def array_analysis(disks, settings):
    included = [d for d in disks if d["enabled"] and d["eligible"] and d["present"]]
    rows = []
    for minutes in settings.timeouts_minutes:
        items = [next(r for r in d["timeouts"] if r["timeout_minutes"] == minutes) for d in included]
        rows.append({"timeout_minutes": minutes,
                     "spin_ups_per_day": sum(i["spin_ups_per_day"] for i in items),
                     "spun_down_disk_hours_per_day": sum(i["spun_down_hours_per_day"] for i in items),
                     "benefiting_disks": sum(i["spun_down_hours"] > 0 for i in items),
                     "high_cycling_disks": sum(i["spin_ups_per_day"] > settings.cycling_warning_threshold for i in items)})
    maximum = max((r["spun_down_disk_hours_per_day"] for r in rows), default=0)
    candidates = [r["timeout_minutes"] for r in rows if not r["high_cycling_disks"] and
                  r["spun_down_disk_hours_per_day"] >= maximum*settings.recommendation_efficiency_threshold]
    return {"timeouts": rows, "enabled_disks": len(included),
            "recommendation_minutes": min(candidates, default=None) if maximum > 0 else None,
            "preliminary": not included or any(d["preliminary"] for d in included),
            "minimum_valid_observation_seconds": min((d["valid_observation_seconds"] for d in included), default=0)}
