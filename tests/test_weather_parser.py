import pytest

from agent.weather_parser import WeatherParseError, parse_weather_text

SAMPLE = """Current weather for Vorokhta:
    Conditions:  scattered clouds
    Now:         11.42 metric
    High:        12.10 metric
    Low:         9.80 metric
    Pressure:    1015
    Humidity:    72
    FeelsLike:   10.61
    Wind Speed:  5.30
    Wind Degree:    240
    Sunrise:     1757649600 Unixtime
    Sunset:      1757695200 Unixtime

Weather Forecast for Vorokhta:
Date & Time: 2026-09-12 09:00:00 +0000 UTC
Conditions:  Clouds scattered clouds
Temp:        10.20
High:        11.90
Low:         9.10

Date & Time: 2026-09-12 15:00:00 +0000 UTC
Conditions:  Rain light rain
Temp:        12.40
High:        13.20
Low:         11.00

Date & Time: 2026-09-13 12:00:00 +0000 UTC
Conditions:  Thunderstorm thunderstorm with heavy rain
Temp:        9.80
High:        10.10
Low:         8.20
"""


class TestParseWeatherText:
    def test_parses_target_date_min_max(self):
        summary = parse_weather_text(SAMPLE, "2026-09-12")
        assert summary.temp_min_c == 9.10
        assert summary.temp_max_c == 13.20
        assert summary.wind_ms == 5.30
        assert summary.thunderstorm is False
        assert summary.precip_mm == pytest.approx(12.0)  # "rain" keyword estimate

    def test_detects_thunderstorm_and_heavy_rain(self):
        summary = parse_weather_text(SAMPLE, "2026-09-13")
        assert summary.thunderstorm is True
        # Worst matching keyword wins: "heavy rain" (25) outranks "thunderstorm" (20).
        assert summary.precip_mm == pytest.approx(25.0)

    def test_missing_date_raises(self):
        with pytest.raises(WeatherParseError) as excinfo:
            parse_weather_text(SAMPLE, "2026-09-20")
        assert excinfo.value.code == "NO_FORECAST_FOR_DATE"

    def test_garbage_input_raises(self):
        with pytest.raises(WeatherParseError):
            parse_weather_text("503 Service Unavailable", "2026-09-12")

    def test_implausible_wind_rejected(self):
        broken = SAMPLE.replace("Wind Speed:  5.30", "Wind Speed:  999")
        with pytest.raises(WeatherParseError) as excinfo:
            parse_weather_text(broken, "2026-09-12")
        assert excinfo.value.code == "IMPLAUSIBLE_VALUE"
