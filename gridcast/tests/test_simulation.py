from datetime import UTC, datetime, timedelta

from gridcast.catalog import catalog
from gridcast.demand.model import demand_mw
from gridcast.weather.model import WeatherPoint, synthetic_weather
from gridcast.weather.truth import TruthWeather, vendor_view

T0 = datetime(2026, 3, 10, 14, 30, tzinfo=UTC)


def test_synthetic_weather_is_deterministic_and_plausible():
    a = synthetic_weather(5.6, -0.19, T0)
    b = synthetic_weather(5.6, -0.19, T0)
    assert a == b
    assert 15 < a.temperature_c < 45
    assert 0 <= a.cloud_cover_pct <= 100
    assert 0 <= a.relative_humidity_pct <= 100
    night = synthetic_weather(5.6, -0.19, T0.replace(hour=2))
    assert night.shortwave_radiation_wm2 == 0


def test_lerp_interpolates_every_variable():
    a = WeatherPoint(20, 50, 10, 1, 0, 0)
    b = WeatherPoint(30, 70, 30, 3, 100, 2)
    mid = WeatherPoint.lerp(a, b, 0.5)
    assert mid == WeatherPoint(25, 60, 20, 2, 50, 1)


def test_vendor_views_differ_slightly_but_deterministically():
    truth = synthetic_weather(5.6, -0.19, T0)
    p1 = vendor_view(truth, "wx-primary", "st-accra", T0)
    p2 = vendor_view(truth, "wx-secondary", "st-accra", T0)
    assert p1 == vendor_view(truth, "wx-primary", "st-accra", T0)
    assert p1 != p2
    assert abs(p1.temperature_c - truth.temperature_c) <= 0.25


def test_truth_weather_synthetic_mode_never_calls_network():
    point, source = TruthWeather(mode="synthetic").at(5.6, -0.19, T0)
    assert source == "synthetic"
    assert point == synthetic_weather(5.6, -0.19, T0)


def test_demand_is_deterministic_and_scaled_to_zone():
    cat = catalog()
    w = synthetic_weather(5.6, -0.19, T0)
    for zone in cat.zones:
        value = demand_mw(zone, T0, w)
        assert value == demand_mw(zone, T0, w)
        assert 0.5 * zone.base_load_mw < value < 1.8 * zone.base_load_mw


def test_demand_responds_to_heat_and_calendar():
    zone = catalog().zone("zone-accra")
    w = synthetic_weather(5.6, -0.19, T0)
    hot = WeatherPoint(w.temperature_c + 5, *[getattr(w, k) for k in (
        "relative_humidity_pct", "cloud_cover_pct", "wind_speed_ms", "shortwave_radiation_wm2",
        "precipitation_mm")])
    assert demand_mw(zone, T0, hot) > demand_mw(zone, T0, w)
    weekday = datetime(2026, 3, 11, 19, tzinfo=UTC)   # Wednesday
    sunday = weekday + timedelta(days=4)
    assert demand_mw(zone, sunday, w) < demand_mw(zone, weekday, w)
    holiday = datetime(2026, 5, 1, 19, tzinfo=UTC)
    assert catalog().is_holiday(holiday)
