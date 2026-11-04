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
    assert bare_state.country == "United States"
    assert bare_state.us_state == "California"
    assert bare_state.confidence == 0.7


def test_london_with_a_country_is_not_ambiguous() -> None:
    result = normalize_location("London, United Kingdom")
    assert result.country == "United Kingdom"
    assert result.us_state is None


def test_unambiguous_places_resolve_and_conflicts_do_not() -> None:
    caracas = normalize_location("Caracas")
    assert caracas.country == "Venezuela"
    assert caracas.us_state is None
    assert caracas.confidence == 0.75

    assert normalize_location("Maturín").country == "Venezuela"
    assert normalize_location("Maturin").country == "Venezuela"
    assert normalize_location("Berlin").country == "Germany"
    assert normalize_location("Vzla").country == "Venezuela"

    austin = normalize_location("Austin, Texas")
    assert austin.country == "United States"
    assert austin.us_state == "Texas"
    assert austin.confidence == 0.7

    carabobo = normalize_location("Valencia, Carabobo")
    assert carabobo.country == "Venezuela"
    assert carabobo.us_state is None

    for raw in ("Paris, Texas", "Valencia", "Georgia", "Washington"):
        result = normalize_location(raw)
        assert result.country is None
        assert result.us_state is None

    explicit = normalize_location("Valencia, Venezuela")
    assert explicit.country == "Venezuela"
    assert explicit.confidence == 0.9
