import pytest

from app.analysis import analyze, array_analysis, percentile
from app.config import Settings


def interval(seconds, start_censored=0, end_censored=0):
    return {"duration_seconds": seconds, "start_is_censored": start_censored, "end_is_censored": end_censored}


def session(duration, open_duration=None, initial=False):
    return {"started_at": 0, "last_observed_at": duration,
            "ended_at": duration if open_duration is None else None,
            "last_activity_at": None if initial else duration-(open_duration or 0),
            "idle_started_at": duration-(open_duration or 0)}


def test_three_hour_example():
    result = analyze([interval(10800)], [session(10800)], Settings())
    assert [r["spun_down_hours"] for r in result["timeouts"]] == [2.75, 2.5, 2, 1]
    assert [r["completed_spin_ups"] for r in result["timeouts"]] == [1]*4
    assert result["timeouts"][0]["spin_ups_per_day"] == 8


def test_equal_timeout_open_and_censored():
    settings = Settings(timeouts_minutes=[30])
    equal = analyze([interval(1800)], [session(1800)], settings)
    assert equal["timeouts"][0]["completed_spin_ups"] == 0
    opened = analyze([], [session(10800, 10800)], settings)["timeouts"][0]
    assert opened["completed_spin_ups"] == 0
    assert opened["spin_down_opportunities"] == 1
    assert opened["current_potential_spun_down_hours"] == 2.5
    unknown = analyze([interval(10800, 1)], [session(10800, 10800, True)], settings)
    assert unknown["timeouts"][0]["spun_down_hours"] == 0
    assert unknown["median_idle_seconds"] is None
    tail = analyze([interval(10800, 0, 1)], [session(10800)], settings)
    assert tail["timeouts"][0]["completed_spin_ups"] == 0
    assert tail["timeouts"][0]["censored_end_spun_down_hours"] == 2.5


def test_daily_normalization_sums_proven_segments_only():
    result = analyze([interval(10800)], [session(10800), session(10800)], Settings())
    assert result["valid_observation_seconds"] == 21600
    assert result["timeouts"][0]["spin_ups_per_day"] == 4
    assert result["timeouts"][0]["spun_down_hours_per_day"] == 11


def test_percentiles_and_confidence_and_recommendation():
    assert percentile([1, 2, 3, 4], .5) == 2.5
    assert percentile([1, 2, 3, 4], .75) == 3.25
    for hours, preliminary in [(71, True), (72, False)]:
        result = analyze([interval(10800)], [session(hours*3600)], Settings())
        assert result["preliminary"] == preliminary
        assert result["recommendation_minutes"] == 15
    result = analyze([interval(10800)], [session(10800)], Settings())
    assert result["recommendation_minutes"] is None


def test_array_sums_disk_hours_and_ignores_disabled():
    data = analyze([interval(10800)], [session(86400)], Settings())
    active = {**data, "enabled": 1, "eligible": 1, "present": 1}
    array = array_analysis([active, active, {**active, "enabled": 0}], Settings())
    assert array["timeouts"][0]["spun_down_disk_hours_per_day"] == 5.5
    assert array["timeouts"][0]["spin_ups_per_day"] == 2


@pytest.mark.parametrize("values", [[0], [-5], [float("nan")], [float("inf")]])
def test_invalid_timeouts(values):
    with pytest.raises(ValueError):
        Settings(timeouts_minutes=values)
