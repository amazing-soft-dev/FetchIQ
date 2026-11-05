"""Write ``app/data/places.json`` from GeoNames and countriesnow.space.

Run from ``backend``:

    python scripts/build_places.py

The raw downloads stay in a temporary directory. GeoNames is CC BY 4.0.
"""

import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.geo import COUNTRIES, US_STATES, canonical_country  # noqa: E402
from app.locations import AMBIGUOUS  # noqa: E402
from app.places import fold_place  # noqa: E402

CITIES_URL = "https://download.geonames.org/export/dump/cities15000.zip"
ADMIN1_URL = "https://download.geonames.org/export/dump/admin1CodesASCII.txt"
COUNTRY_INFO_URL = "https://download.geonames.org/export/dump/countryInfo.txt"
COUNTRIESNOW_URL = "https://countriesnow.space/api/v0.1/countries"
OUTPUT = ROOT / "app" / "data" / "places.json"

MAX_PER_COUNTRY = 30
MAX_REGIONS = 5
OVERFLOW_PER_COUNTRY = 30
# A second city must be at least this large before it makes the name unsafe.
COLLISION_POPULATION = 50_000

# Names the population cutoff would still treat as safe, and names that are
# places in more than one country even when only one of them is a large city.
HAND_EXCLUSIONS = frozenset(
    {
        "barcelona",
        "bolivar",
        "cambridge",
        "cordoba",
        "guadalajara",
        "hyderabad",
        "lara",
        "leon",
        "merida",
        "miranda",
        "portland",
        "santiago",
        "toledo",
        "valencia",
        "victoria",
    }
)

# First-level regions people write on a profile. Matched against the GeoNames
# admin-1 name and its ASCII form.
CURATED_REGIONS = {
    "Venezuela": ("Carabobo", "Zulia", "Aragua", "Anzoátegui", "Táchira"),
    "Germany": ("Bayern", "Bavaria"),
    "United Kingdom": ("England", "Scotland", "Wales"),
    "Spain": ("Andalucía", "Andalusia"),
}

# GeoNames country labels that do not match COUNTRIES or COUNTRY_ALIASES.
ISO_OVERRIDES = {
    "BO": "Bolivia",
    "BN": "Brunei",
    "CD": "Democratic Republic of the Congo",
    "CG": "Congo",
    "CI": None,
    "CV": "Cabo Verde",
    "CZ": "Czechia",
    "FM": "Micronesia",
    "GB": "United Kingdom",
    "GM": "Gambia",
    "IR": "Iran",
    "KP": "North Korea",
    "KR": "South Korea",
    "LA": "Laos",
    "MD": "Moldova",
    "MK": "North Macedonia",
    "MO": "Macao",
    "PS": "Palestine",
    "RU": "Russia",
    "SY": "Syria",
    "SZ": "Eswatini",
    "TL": "Timor-Leste",
    "TW": "Taiwan",
    "TZ": "Tanzania",
    "US": "United States",
    "VA": "Vatican City",
    "VE": "Venezuela",
    "VN": "Vietnam",
    "XK": "Kosovo",
}

US_STATE_NAMES = frozenset(fold_place(name) for name in US_STATES)
COUNTRY_NAMES = frozenset(fold_place(name) for name in COUNTRIES)
ALIAS_ROWS = (("Venezuela", "Vzla"),)


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "FetchIQ place builder"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def _country_from_label(iso: str, label: str) -> str | None:
    if iso in ISO_OVERRIDES:
        return ISO_OVERRIDES[iso]
    candidates = [
        label,
        label.split(",")[0].strip(),
        label.removeprefix("The ").strip(),
        label.split(",")[0].strip().removeprefix("The "),
    ]
    for candidate in candidates:
        found = canonical_country(candidate)
        if found:
            return found
    return None


def _load_country_info(raw: str) -> dict[str, tuple[str, str]]:
    """Map ISO code to ``(canonical country, capital)``."""

    found: dict[str, tuple[str, str]] = {}
    for line in raw.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        iso, label, capital = parts[0], parts[4], parts[5]
        country = _country_from_label(iso, label)
        if country not in set(COUNTRIES):
            continue
        found[iso] = (country, capital)
    return found


def _load_admin1(raw: str) -> dict[tuple[str, str], tuple[str, str]]:
    """Map ``(ISO, admin1 code)`` to ``(local name, ascii name)``."""

    found: dict[tuple[str, str], tuple[str, str]] = {}
    for line in raw.splitlines():
        if not line:
            continue
        code, name, ascii_name, *_rest = line.split("\t")
        iso, _, admin = code.partition(".")
        if iso and admin:
            found[(iso, admin)] = (name, ascii_name)
    return found


def _usable_token(value: str) -> bool:
    folded = fold_place(value)
    if len(folded) < 3:
        return False
    if folded in AMBIGUOUS or folded in HAND_EXCLUSIONS:
        return False
    letters = [character for character in value if character.isalpha()]
    return len(letters) >= 3


def _remember(
    collisions: dict[str, dict[str, int]], name: str, country: str, population: int
) -> None:
    if not _usable_token(name):
        return
    owners = collisions.setdefault(fold_place(name), {})
    owners[country] = max(owners.get(country, 0), population)


def _blocked(folded: str, country: str, collisions: dict[str, dict[str, int]]) -> bool:
    if folded in AMBIGUOUS or folded in HAND_EXCLUSIONS:
        return True
    if folded == fold_place(country):
        return True
    if folded in COUNTRY_NAMES and folded != fold_place(country):
        return True
    if folded in US_STATE_NAMES and country != "United States":
        return True
    owners = collisions.get(folded, {})
    if len(owners) <= 1:
        return False
    second = sorted(owners.values(), reverse=True)[1]
    return second >= COLLISION_POPULATION


def _countriesnow_names(payload: dict, iso_country: dict[str, str]) -> dict[str, set[str]]:
    by_label = {country: iso for iso, country in iso_country.items()}
    found: dict[str, set[str]] = {country: set() for country in COUNTRIES}
    for row in payload.get("data") or []:
        label = str(row.get("country") or "")
        country = canonical_country(label)
        if country is None:
            country = by_label.get(label)
        if country is None or country not in found:
            continue
        for city in row.get("cities") or []:
            if isinstance(city, str) and _usable_token(city):
                found[country].add(city.strip())
    return found


def _add(
    country_rows: list[dict],
    used: set[str],
    collisions: dict[str, dict[str, int]],
    country: str,
    kind: str,
    name: str,
    region: str,
    rank: int,
    *,
    population: int = 0,
    tier: str = "primary",
    limit: int | None = None,
) -> bool:
    cap = MAX_PER_COUNTRY if limit is None else limit
    if len(country_rows) >= cap or not _usable_token(name):
        return False
    folded = fold_place(name)
    if name.casefold() in used or _blocked(folded, country, collisions):
        return False
    used.add(name.casefold())
    row = {
        "country": country,
        "name": name,
        "kind": kind,
        "region": region,
        "rank": rank,
        "tier": tier,
    }
    if tier == "overflow":
        row["population"] = population
    country_rows.append(row)
    return True


def _spellings(name: str, ascii_name: str) -> list[str]:
    """Local name, plus the ASCII form when GitHub will not fold them together.

    A live search returned 692 users for ``location:Merida`` and 1,723 for
    ``location:Mérida``, so an accented name and its ASCII twin both ship.
    """

    local = name.strip()
    chosen = [local]
    ascii_text = ascii_name.strip()
    if ascii_text and ascii_text.casefold() != local.casefold() and _usable_token(ascii_text):
        chosen.append(ascii_text)
    return chosen


def build() -> dict:
    countries = _load_country_info(_fetch(COUNTRY_INFO_URL).decode("utf-8"))
    admin1 = _load_admin1(_fetch(ADMIN1_URL).decode("utf-8"))
    iso_country = {iso: pair[0] for iso, pair in countries.items()}
    now_payload = json.loads(_fetch(COUNTRIESNOW_URL).decode("utf-8"))
    if not now_payload.get("data"):
        raise RuntimeError("countriesnow.space returned no countries")
    now_names = _countriesnow_names(now_payload, iso_country)

    archive = zipfile.ZipFile(io.BytesIO(_fetch(CITIES_URL)))
    member = next(name for name in archive.namelist() if name.endswith(".txt"))
    collisions: dict[str, dict[str, int]] = {}
    cities: list[dict] = []
    for line in archive.read(member).decode("utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) < 15 or parts[6] != "P":
            continue
        country = iso_country.get(parts[8])
        if country is None:
            continue
        name, ascii_name = parts[1], parts[2]
        try:
            population = int(parts[14] or 0)
        except ValueError:
            population = 0
        _remember(collisions, name, country, population)
        _remember(collisions, ascii_name, country, population)
        region_name = ""
        admin = admin1.get((parts[8], parts[10]))
        if admin is not None:
            region_name = admin[0]
        cities.append(
            {
                "country": country,
                "name": name,
                "ascii": ascii_name,
                "population": population,
                "region": region_name,
            }
        )

    rows: list[dict] = []
    for country in COUNTRIES:
        country_rows: list[dict] = []
        used: set[str] = set()

        region_rank = 0
        for wanted in CURATED_REGIONS.get(country, ()):
            if region_rank >= MAX_REGIONS:
                break
            if _add(country_rows, used, collisions, country, "region", wanted, "", region_rank):
                region_rank += 1

        for alias_country, alias in ALIAS_ROWS:
            if alias_country == country:
                _add(country_rows, used, collisions, country, "alias", alias, "", 0)

        grouped: dict[str, dict] = {}
        for city in cities:
            if city["country"] != country:
                continue
            current = grouped.get(city["name"].casefold())
            if current is None or city["population"] > current["population"]:
                grouped[city["name"].casefold()] = city
        ordered = sorted(grouped.values(), key=lambda item: (-item["population"], item["name"]))
        city_rank = 0
        overflow_rows: list[dict] = []
        overflow_rank = 0
        for city in ordered:
            primary_open = len(country_rows) < MAX_PER_COUNTRY
            target = country_rows if primary_open else overflow_rows
            rank = city_rank if primary_open else overflow_rank
            if not primary_open and overflow_rank >= OVERFLOW_PER_COUNTRY:
                break
            added = False
            for spelling in _spellings(city["name"], city["ascii"]):
                if _add(
                    target,
                    used,
                    collisions,
                    country,
                    "city",
                    spelling,
                    city["region"],
                    rank,
                    population=int(city["population"]),
                    tier="primary" if primary_open else "overflow",
                    limit=None if primary_open else OVERFLOW_PER_COUNTRY,
                ):
                    added = True
            if added and primary_open:
                city_rank += 1
            elif added:
                overflow_rank += 1
        country_rows.extend(overflow_rows)

        if not any(row["kind"] == "city" for row in country_rows):
            listed = sorted(now_names.get(country, set()), key=lambda item: item.casefold())
            for candidate in listed:
                if _add(country_rows, used, collisions, country, "city", candidate, "", 0):
                    break
            if not any(row["kind"] == "city" for row in country_rows):
                capital = next((pair[1] for pair in countries.values() if pair[0] == country), "")
                _add(country_rows, used, collisions, country, "city", capital, "", 0)

        rows.extend(country_rows)

    kind_order = {"city": 0, "region": 1, "alias": 2}
    tier_order = {"primary": 0, "overflow": 1}
    rows.sort(
        key=lambda row: (
            row["country"],
            tier_order.get(row.get("tier") or "primary", 0),
            kind_order[row["kind"]],
            row["rank"],
            row["name"],
        )
    )
    return {
        "version": 1,
        "sources": ["https://www.geonames.org/", "https://countriesnow.space/"],
        "license": "GeoNames is CC BY 4.0. countriesnow.space supplies city name lists.",
        "places": rows,
    }


def main() -> None:
    payload = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for row in payload["places"]:
        counts[row["country"]] = counts.get(row["country"], 0) + 1
    missing = [country for country in COUNTRIES if country not in counts]
    print(f"wrote {len(payload['places'])} places to {OUTPUT}")
    print(f"countries with a place: {len(counts)} / {len(COUNTRIES)}")
    if missing:
        print("no place row: " + ", ".join(missing))
    required = {
        ("Germany", "Berlin"),
        ("Germany", "Bayern"),
        ("Venezuela", "Caracas"),
        ("Venezuela", "Carabobo"),
        ("Netherlands", "Amsterdam"),
        ("United States", "Austin"),
    }
    present = {(row["country"], row["name"]) for row in payload["places"]}
    absent = sorted(required - present)
    if absent:
        raise SystemExit(
            "missing required places: "
            + ", ".join(f"{country} / {name}" for country, name in absent)
        )


if __name__ == "__main__":
    main()
