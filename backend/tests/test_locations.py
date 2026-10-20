from app.locations import normalize_location


def test_ambiguous_locations_stay_unknown() -> None:
    for raw in ("London", "California", "Remote", "CA", "  ", None):
        result = normalize_location(raw)
        assert result.country is None
        assert result.us_state is None
        assert result.confidence == 0.0


def test_explicit_country_and_us_places() -> None:
    germany = normalize_location("Berlin, Germany")
    assert germany.country == "Germany"
    assert germany.us_state is None
    assert germany.confidence == 0.9

    united = normalize_location("United States")
    assert united.country == "United States"
    assert united.confidence == 0.95

    city_state = normalize_location("San Francisco, CA")
    assert city_state.country == "United States"
    assert city_state.us_state == "California"
    assert city_state.confidence == 0.7

    qualified = normalize_location("San Francisco, CA, USA")
    assert qualified.country == "United States"
    assert qualified.us_state == "California"
    assert qualified.confidence == 0.92

    bare_state = normalize_location("San Francisco, California")
    assert bare_state.country is None


def test_london_with_a_country_is_not_ambiguous() -> None:
    result = normalize_location("London, United Kingdom")
    assert result.country == "United Kingdom"
    assert result.us_state is None
