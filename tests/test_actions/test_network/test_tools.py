"""Happy-path, network-error, and consent-denied coverage for every phase 2b tool.

Each tool's module-level ``fetch_json`` / ``fetch_text`` import is patched
via ``monkeypatch.setattr`` so no real HTTP ever fires. One tool also covers
the sensitive-term scrub path to prove the _http.wrap_result filter works
end-to-end.
"""

from __future__ import annotations

from typing import Any

import pytest

from tokenpal.actions.network import (
    air_quality,
    book_suggestion,
    crypto_price,
    currency,
    joke_of_the_day,
    on_this_day,
    random_fact,
    random_recipe,
    sports_score,
    trivia_question,
    weather_forecast_week,
    word_of_the_day,
)


def _ok(data: Any) -> Any:
    async def _inner(*_args: Any, **_kwargs: Any) -> tuple[Any, None]:
        return data, None

    return _inner


def _err(msg: str = "network down") -> Any:
    async def _inner(*_args: Any, **_kwargs: Any) -> tuple[None, str]:
        return None, msg

    return _inner


# ---------- currency ----------

async def test_currency_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        currency,
        "fetch_json",
        _ok({"result": "success", "rates": {"EUR": 0.9, "USD": 1.0}}),
    )
    # Clear cache so our patch is hit.
    currency._rate_cache.clear()
    action = currency.CurrencyAction({})
    result = await action.execute(amount=10, from_code="USD", to_code="EUR")
    assert result.success
    assert "9.00 EUR" in result.output
    assert "<tool_result" in result.output


async def test_currency_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(currency, "fetch_json", _err())
    currency._rate_cache.clear()
    action = currency.CurrencyAction({})
    result = await action.execute(amount=5, from_code="USD", to_code="JPY")
    assert not result.success


async def test_currency_consent_denied(deny_consent) -> None:  # type: ignore[no-untyped-def]
    action = currency.CurrencyAction({})
    result = await action.execute(amount=1, from_code="USD", to_code="EUR")
    assert not result.success
    assert "consent" in result.output.lower()


async def test_currency_sensitive_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prove wrap_result's sensitive-term filter fires end-to-end."""
    monkeypatch.setattr(
        currency,
        "fetch_json",
        _ok({"result": "success", "rates": {"EUR": 1.0}}),
    )
    # Force the scrubber to treat anything with EUR as sensitive.
    monkeypatch.setattr(
        "tokenpal.actions.network._http.contains_sensitive_term",
        lambda text: "EUR" in (text or ""),
    )
    currency._rate_cache.clear()
    action = currency.CurrencyAction({})
    result = await action.execute(amount=1, from_code="USD", to_code="EUR")
    assert result.success
    assert "[filtered]" in result.output


# ---------- weather_forecast_week ----------

async def test_weather_forecast_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(weather_forecast_week, "get_lat_lon", lambda: (40.0, -74.0))
    monkeypatch.setattr(
        weather_forecast_week,
        "fetch_json",
        _ok(
            {
                "daily": {
                    "time": ["2026-04-14", "2026-04-15"],
                    "temperature_2m_max": [70, 72],
                    "temperature_2m_min": [50, 52],
                    "weathercode": [0, 61],
                }
            }
        ),
    )
    result = await weather_forecast_week.WeatherForecastWeekAction({}).execute()
    assert result.success
    assert "clear" in result.output
    assert "light rain" in result.output


async def test_weather_forecast_no_location(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(weather_forecast_week, "get_lat_lon", lambda: None)
    result = await weather_forecast_week.WeatherForecastWeekAction({}).execute()
    assert not result.success
    assert "/zip" in result.output


async def test_weather_forecast_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(weather_forecast_week, "get_lat_lon", lambda: (1.0, 2.0))
    monkeypatch.setattr(weather_forecast_week, "fetch_json", _err())
    result = await weather_forecast_week.WeatherForecastWeekAction({}).execute()
    assert not result.success


async def test_weather_forecast_consent_denied(deny_consent) -> None:  # type: ignore[no-untyped-def]
    result = await weather_forecast_week.WeatherForecastWeekAction({}).execute()
    assert not result.success


# ---------- air quality + pollen ----------

async def test_air_quality_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(air_quality, "get_lat_lon", lambda: (1.0, 2.0))
    monkeypatch.setattr(
        air_quality,
        "fetch_json",
        _ok({"current": {"european_aqi": 42, "us_aqi": 55, "pm2_5": 9, "pm10": 20}}),
    )
    result = await air_quality.AirQualityAction({}).execute()
    assert result.success
    assert "42" in result.output


async def test_air_quality_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(air_quality, "get_lat_lon", lambda: (1.0, 2.0))
    monkeypatch.setattr(air_quality, "fetch_json", _err())
    result = await air_quality.AirQualityAction({}).execute()
    assert not result.success


async def test_air_quality_consent_denied(deny_consent) -> None:  # type: ignore[no-untyped-def]
    result = await air_quality.AirQualityAction({}).execute()
    assert not result.success


async def test_pollen_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(air_quality, "get_lat_lon", lambda: (1.0, 2.0))
    monkeypatch.setattr(
        air_quality,
        "fetch_json",
        _ok(
            {
                "hourly": {
                    "alder_pollen": [0.1],
                    "birch_pollen": [0.2],
                    "grass_pollen": [0.3],
                    "ragweed_pollen": [0.4],
                }
            }
        ),
    )
    result = await air_quality.PollenCountAction({}).execute()
    assert result.success
    assert "grass" in result.output


async def test_pollen_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(air_quality, "get_lat_lon", lambda: (1.0, 2.0))
    monkeypatch.setattr(air_quality, "fetch_json", _err())
    result = await air_quality.PollenCountAction({}).execute()
    assert not result.success


async def test_pollen_consent_denied(deny_consent) -> None:  # type: ignore[no-untyped-def]
    result = await air_quality.PollenCountAction({}).execute()
    assert not result.success


# ---------- random_fact ----------

async def test_random_fact_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(random_fact, "fetch_json", _ok({"text": "Octopuses have three hearts."}))
    result = await random_fact.RandomFactAction({}).execute()
    assert result.success
    assert "Octopus" in result.output


async def test_random_fact_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(random_fact, "fetch_json", _err())
    result = await random_fact.RandomFactAction({}).execute()
    assert not result.success


async def test_random_fact_consent_denied(deny_consent) -> None:  # type: ignore[no-untyped-def]
    result = await random_fact.RandomFactAction({}).execute()
    assert not result.success


# ---------- joke_of_the_day ----------

async def test_joke_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(joke_of_the_day, "fetch_json", _ok({"joke": "Why did the chicken..."}))
    result = await joke_of_the_day.JokeOfTheDayAction({}).execute()
    assert result.success
    assert "chicken" in result.output


async def test_joke_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(joke_of_the_day, "fetch_json", _err())
    result = await joke_of_the_day.JokeOfTheDayAction({}).execute()
    assert not result.success


async def test_joke_consent_denied(deny_consent) -> None:  # type: ignore[no-untyped-def]
    result = await joke_of_the_day.JokeOfTheDayAction({}).execute()
    assert not result.success


# ---------- word_of_the_day ----------

_WOTD_RSS = """<?xml version='1.0'?>
<rss><channel>
  <item>
    <title>perspicacious</title>
    <description>having a ready insight into things</description>
  </item>
</channel></rss>"""


async def test_wotd_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_text(*_a: Any, **_k: Any) -> tuple[str, None]:
        return _WOTD_RSS, None

    monkeypatch.setattr(word_of_the_day, "fetch_text", fake_text)
    result = await word_of_the_day.WordOfTheDayAction({}).execute()
    assert result.success
    assert "perspicacious" in result.output


async def test_wotd_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_text(*_a: Any, **_k: Any) -> tuple[None, str]:
        return None, "boom"

    monkeypatch.setattr(word_of_the_day, "fetch_text", fake_text)
    result = await word_of_the_day.WordOfTheDayAction({}).execute()
    assert not result.success


async def test_wotd_consent_denied(deny_consent) -> None:  # type: ignore[no-untyped-def]
    result = await word_of_the_day.WordOfTheDayAction({}).execute()
    assert not result.success


# ---------- on_this_day ----------

async def test_otd_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        on_this_day,
        "fetch_json",
        _ok({"events": [{"year": 1776, "text": "Big thing happened."}]}),
    )
    result = await on_this_day.OnThisDayAction({}).execute()
    assert result.success
    assert "1776" in result.output


async def test_otd_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(on_this_day, "fetch_json", _err())
    result = await on_this_day.OnThisDayAction({}).execute()
    assert not result.success


async def test_otd_consent_denied(deny_consent) -> None:  # type: ignore[no-untyped-def]
    result = await on_this_day.OnThisDayAction({}).execute()
    assert not result.success


# ---------- random_recipe ----------

async def test_recipe_random_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        random_recipe,
        "fetch_json",
        _ok(
            {
                "meals": [
                    {
                        "strMeal": "Carbonara",
                        "strArea": "Italian",
                        "strCategory": "Pasta",
                        "strInstructions": "Boil pasta. Mix eggs. Combine.",
                    }
                ]
            }
        ),
    )
    result = await random_recipe.RandomRecipeAction({}).execute()
    assert result.success
    assert "Carbonara" in result.output


async def test_recipe_filter_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = iter(
        [
            # filter.php
            ({"meals": [{"idMeal": "123"}]}, None),
            # lookup.php
            (
                {
                    "meals": [
                        {
                            "strMeal": "Chicken Pie",
                            "strArea": "British",
                            "strCategory": "Chicken",
                            "strInstructions": "Bake.",
                        }
                    ]
                },
                None,
            ),
        ]
    )

    async def fake(*_a: Any, **_k: Any) -> tuple[Any, None]:
        return next(calls)

    monkeypatch.setattr(random_recipe, "fetch_json", fake)
    result = await random_recipe.RandomRecipeAction({}).execute(ingredient="chicken")
    assert result.success
    assert "Chicken Pie" in result.output


async def test_recipe_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(random_recipe, "fetch_json", _err())
    result = await random_recipe.RandomRecipeAction({}).execute()
    assert not result.success


async def test_recipe_consent_denied(deny_consent) -> None:  # type: ignore[no-untyped-def]
    result = await random_recipe.RandomRecipeAction({}).execute()
    assert not result.success


# ---------- trivia_question ----------

async def test_trivia_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    # bypass real rate-limit
    monkeypatch.setattr(trivia_question, "_MIN_SPACING_S", 0.0)
    trivia_question._cached_token = "tok"

    monkeypatch.setattr(
        trivia_question,
        "fetch_json",
        _ok(
            {
                "response_code": 0,
                "results": [
                    {
                        "question": "What is 2+2?",
                        "correct_answer": "4",
                        "incorrect_answers": ["3", "5", "22"],
                    }
                ],
            }
        ),
    )
    result = await trivia_question.TriviaQuestionAction({}).execute()
    assert result.success
    assert "2+2" in result.output
    assert "A: 4" in result.output


async def test_trivia_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trivia_question, "_MIN_SPACING_S", 0.0)
    trivia_question._cached_token = "tok"
    monkeypatch.setattr(trivia_question, "fetch_json", _err())
    result = await trivia_question.TriviaQuestionAction({}).execute()
    assert not result.success


async def test_trivia_consent_denied(deny_consent) -> None:  # type: ignore[no-untyped-def]
    result = await trivia_question.TriviaQuestionAction({}).execute()
    assert not result.success


# ---------- sports_score ----------

async def test_sports_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = iter(
        [
            ({"teams": [{"idTeam": "133604"}]}, None),
            (
                {
                    "results": [
                        {
                            "dateEvent": "2026-03-01",
                            "strHomeTeam": "Arsenal",
                            "strAwayTeam": "Chelsea",
                            "intHomeScore": "2",
                            "intAwayScore": "1",
                        }
                    ]
                },
                None,
            ),
        ]
    )

    async def fake(*_a: Any, **_k: Any) -> tuple[Any, None]:
        return next(calls)

    monkeypatch.setattr(sports_score, "fetch_json", fake)
    result = await sports_score.SportsScoreAction({}).execute(team="Arsenal")
    assert result.success
    assert "Arsenal 2 - 1 Chelsea" in result.output


async def test_sports_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sports_score, "fetch_json", _err())
    result = await sports_score.SportsScoreAction({}).execute(team="Arsenal")
    assert not result.success


async def test_sports_consent_denied(deny_consent) -> None:  # type: ignore[no-untyped-def]
    result = await sports_score.SportsScoreAction({}).execute(team="Arsenal")
    assert not result.success


# ---------- crypto_price ----------

async def test_crypto_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crypto_price, "fetch_json", _ok({"bitcoin": {"usd": 67000}}))
    crypto_price._timestamps.clear()
    result = await crypto_price.CryptoPriceAction({}).execute(symbol="btc")
    assert result.success
    assert "67000" in result.output


async def test_crypto_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crypto_price, "fetch_json", _err())
    crypto_price._timestamps.clear()
    result = await crypto_price.CryptoPriceAction({}).execute(symbol="btc")
    assert not result.success


async def test_crypto_consent_denied(deny_consent) -> None:  # type: ignore[no-untyped-def]
    result = await crypto_price.CryptoPriceAction({}).execute(symbol="btc")
    assert not result.success


# ---------- book_suggestion ----------

async def test_book_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        book_suggestion,
        "fetch_json",
        _ok(
            {
                "items": [
                    {
                        "volumeInfo": {
                            "title": "The Big Sleep",
                            "authors": ["Raymond Chandler"],
                            "description": "Hardboiled detective story.",
                        }
                    }
                ]
            }
        ),
    )
    result = await book_suggestion.BookSuggestionAction({}).execute(genre="mystery")
    assert result.success
    assert "The Big Sleep" in result.output


async def test_book_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(book_suggestion, "fetch_json", _err())
    result = await book_suggestion.BookSuggestionAction({}).execute(genre="mystery")
    assert not result.success


async def test_book_consent_denied(deny_consent) -> None:  # type: ignore[no-untyped-def]
    result = await book_suggestion.BookSuggestionAction({}).execute(genre="mystery")
    assert not result.success
