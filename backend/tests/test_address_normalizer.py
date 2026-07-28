"""Address normalizer — invariants over a real-shape corpus, plus regressions.

The ZIP-extraction bug survived the original five unit tests because all of
them used ``"123 Main St"``: with a three-digit house number the bug is
unreachable. The lesson encoded here is that the parser is tested against
the *shapes operators actually send* — five-digit house numbers, no commas,
spelled-out states, numbered streets, unit markers — and that the
properties are asserted as laws over the whole corpus rather than as a
handful of expected values.

``tests/fixtures/address_corpus.py`` is generated from production by
``scripts/generate_address_corpus.py``; house numbers and street stems are
substituted, every structural property is real.
"""

import re

import pytest

from app.services.address_normalizer import (
    STATE_NAME_TO_CODE,
    ZIP_PREFIX_BY_STATE,
    normalize_address,
    zip_is_plausible,
)
from tests.fixtures.address_corpus import ADDRESS_CORPUS

_FIVE_DIGIT_RE = re.compile(r"\b\d{5}\b")


def test_corpus_is_representative():
    """Guard the guard: a corpus that lost its edge shapes tests nothing."""
    assert len(ADDRESS_CORPUS) > 100

    five_digit_house_numbers = [a for a in ADDRESS_CORPUS if re.match(r"^\d{5}\s", a) is not None]
    comma_less = [a for a in ADDRESS_CORPUS if "," not in a]
    spelled_out_state = [
        a for a in ADDRESS_CORPUS if any(name in a.lower() for name in ("illinois", "indiana"))
    ]

    # The exact shapes that broke production. If a regeneration drops them,
    # this test fails loudly instead of silently weakening the suite.
    assert five_digit_house_numbers, "corpus lost its five-digit house numbers"
    assert comma_less, "corpus lost its comma-less addresses"
    assert spelled_out_state, "corpus lost its spelled-out state names"


class TestCorpusInvariants:
    """Properties that must hold for every address, not just chosen ones."""

    @pytest.mark.parametrize("address", ADDRESS_CORPUS)
    def test_zip_is_never_the_house_number(self, address):
        # The original bug, stated as a law.
        result = normalize_address(address)
        if result.zip_code and result.street_number:
            assert result.zip_code != result.street_number, (
                f"house number read as ZIP in {address!r}"
            )

    @pytest.mark.parametrize("address", ADDRESS_CORPUS)
    def test_zip_is_plausible_for_its_state(self, address):
        # An IL job cannot have a ZIP outside 60xxx-62xxx. Returns None —
        # "no opinion" — for states outside the service region, which is
        # how a legitimate out-of-region job stays quiet.
        result = normalize_address(address)
        verdict = zip_is_plausible(result.state, result.zip_code)
        assert verdict is not False, f"{result.state} job with ZIP {result.zip_code} in {address!r}"

    @pytest.mark.parametrize("address", ADDRESS_CORPUS)
    def test_zip_actually_appears_in_the_address(self, address):
        result = normalize_address(address)
        if result.zip_code:
            assert result.zip_code in _FIVE_DIGIT_RE.findall(address)

    @pytest.mark.parametrize("address", ADDRESS_CORPUS)
    def test_street_name_holds_no_locality(self, address):
        # street_name is a dedup key. A city, state or ZIP leaking into it
        # means two spellings of one address never match each other.
        result = normalize_address(address)
        if not result.street_name:
            return
        assert not _FIVE_DIGIT_RE.search(result.street_name)
        # A street may legitimately be *named* after a state ("Indiana Ave"),
        # so only a trailing state name is a parse failure.
        last_token = result.street_name.split()[-1]
        assert last_token not in STATE_NAME_TO_CODE, (
            f"state name trailing street_name in {address!r}: {result.street_name!r}"
        )

    @pytest.mark.parametrize("address", ADDRESS_CORPUS)
    def test_street_number_is_never_invented(self, address):
        # One-directional on purpose. A street number must always come from
        # the front of the string, but the parser is free to decline one:
        # "135 , Carol Stream, IL, 60188" has a house number and no street,
        # which cannot serve as a dedup key, so street_number stays None.
        result = normalize_address(address)
        if result.street_number:
            assert address.lstrip().startswith(result.street_number)

    @pytest.mark.parametrize("address", ADDRESS_CORPUS)
    def test_never_raises_and_types_hold(self, address):
        result = normalize_address(address)
        for field in ("street_number", "street_name", "city", "state", "zip_code"):
            value = getattr(result, field)
            assert value is None or isinstance(value, str)
        if result.state:
            assert result.state.isupper() and len(result.state) == 2


class TestReportedBug:
    """The job that surfaced this: ZIP 60464 parsed as 12650."""

    def test_five_digit_house_number_is_not_the_zip(self):
        result = normalize_address("12650 Wisteria Ct , Palos Park, IL, 60464")
        assert result.street_number == "12650"
        assert result.zip_code == "60464"
        assert result.street_name == "wisteria court"
        assert result.city == "palos park"
        assert result.state == "IL"

    def test_house_number_longer_than_the_zip_token(self):
        # A six-digit house number has a five-digit substring; word
        # boundaries must stop it being read as a ZIP.
        result = normalize_address("123456 Main St, Chicago, IL 60601")
        assert result.zip_code == "60601"


class TestCommaLessAddresses:
    """42 production addresses arrive with no commas at all."""

    def test_spelled_out_state_and_city_are_recovered(self):
        result = normalize_address("3787 W 70th Ave Merrillville Indiana 46410")
        assert result.street_number == "3787"
        assert result.street_name == "west 70th avenue"
        assert result.city == "merrillville"
        assert result.state == "IN"
        assert result.zip_code == "46410"

    def test_two_word_city(self):
        result = normalize_address("1570 Chesapeake Dr Hoffman Estates Illinois 60192")
        assert result.street_name == "chesapeake drive"
        assert result.city == "hoffman estates"
        assert result.state == "IL"
        assert result.zip_code == "60192"

    def test_city_inside_the_street_chunk(self):
        result = normalize_address("819 N California Ave Chicago, Illinois 60622")
        assert result.street_name == "north california avenue"
        assert result.city == "chicago"
        assert result.state == "IL"


class TestStateParsing:
    def test_spelled_out_names_resolve_to_codes(self):
        assert normalize_address("1 Oak St, Gary, Indiana 46402").state == "IN"
        assert normalize_address("1 Oak St, Chicago, Illinois 60601").state == "IL"

    def test_two_letter_codes_still_work(self):
        assert normalize_address("32 E Golf Rd, Schaumburg IL 60173").state == "IL"

    def test_city_sharing_the_state_chunk_is_recovered(self):
        result = normalize_address("32 E Golf Rd, Schaumburg IL 60173")
        assert result.city == "schaumburg"

    def test_street_named_after_a_state_is_not_the_state(self):
        # "2646 W Chicago Ave, Chicago, Illinois" — the last state-looking
        # token wins, and only chunks after the street line are searched.
        result = normalize_address("2646 W Indiana Ave, Chicago, Illinois 60622")
        assert result.state == "IL"
        assert result.street_name == "west indiana avenue"

    def test_directional_is_not_mistaken_for_a_state(self):
        # The old regex read any two capitals as a state; "NE" is a
        # directional, not Nebraska.
        result = normalize_address("100 NE Oak St, Chicago, IL 60601")
        assert result.state == "IL"


class TestStreetNameIntegrity:
    """street_name is a dedup key — collisions merge unrelated jobs."""

    def test_numbered_streets_keep_their_digits(self):
        # The alphabetic-only tokenizer turned every numbered street into
        # the same key: "1st St", "10th St" and "17th St" all became
        # "st street" / "th street".
        assert normalize_address("11145 1st St , Mokena, IL, 60448").street_name == "1st street"
        assert normalize_address("1018 10th st , Wilmette, IL, 60091").street_name == "10th street"
        assert normalize_address("122 17th St , Wilmette, IL, 60091").street_name == "17th street"

    def test_numbered_streets_do_not_collide(self):
        names = {
            normalize_address(f"100 {n} St, Chicago, IL 60601").street_name
            for n in ("1st", "10th", "17th", "70th")
        }
        assert len(names) == 4

    def test_trailing_directional_is_kept(self):
        # "Lake Shore Dr N" and "Lake Shore Dr S" are different streets.
        north = normalize_address("1306 Lake Shore Dr N , Barrington, IL, 60010")
        south = normalize_address("1306 Lake Shore Dr S , Barrington, IL, 60010")
        assert north.street_name == "lake shore drive north"
        assert south.street_name == "lake shore drive south"
        assert north.street_name != south.street_name

    def test_lettered_street_keeps_its_letter(self):
        # Chicago's "Avenue O" is a real street, not a city named "O".
        result = normalize_address("11109 South Avenue O, Chicago, IL, 60617")
        assert result.street_name == "south avenue o"
        assert result.city == "chicago"

    def test_unit_marker_is_dropped(self):
        for raw in (
            "1006 N Plum Grove Rd #1006 Schaumburg, Illinois 60173",
            "1006 N Plum Grove Rd Apt 2, Schaumburg, Illinois 60173",
            "100 N Plum Grove Rd Suite 270, Schaumburg, IL, 60173",
        ):
            assert normalize_address(raw).street_name == "north plum grove road"


class TestPlausibilityIsAdvisoryOnly:
    def test_out_of_region_state_gets_no_opinion(self):
        # Production holds a GA job and a CA job. The guard must stay silent
        # rather than flag them.
        assert zip_is_plausible("GA", "30301") is None
        assert zip_is_plausible("CA", "90210") is None

    def test_in_region_mismatch_is_flagged(self):
        assert zip_is_plausible("IL", "12650") is False
        assert zip_is_plausible("IN", "60601") is False

    def test_in_region_match_passes(self):
        assert zip_is_plausible("IL", "60464") is True
        assert zip_is_plausible("IN", "46410") is True

    def test_unknown_inputs_get_no_opinion(self):
        assert zip_is_plausible(None, "60601") is None
        assert zip_is_plausible("IL", None) is None

    def test_implausible_zip_is_never_rewritten(self):
        # An out-of-range ZIP is logged, not corrected or dropped — the
        # parser must not invent data.
        result = normalize_address("100 Oak St, Springfield, IL, 12345")
        assert result.zip_code == "12345"
        assert result.state == "IL"

    def test_every_region_state_has_prefixes(self):
        for state, prefixes in ZIP_PREFIX_BY_STATE.items():
            assert len(state) == 2 and state.isupper()
            assert prefixes and all(len(p) == 2 and p.isdigit() for p in prefixes)


class TestPreservedBehavior:
    """Contracts the rest of the pipeline already depends on."""

    def test_no_leading_number_disqualifies_from_dedup(self):
        result = normalize_address("Main Street, Chicago, IL 60601")
        assert result.street_number is None
        assert result.street_name is None

    def test_empty_input(self):
        result = normalize_address("")
        assert result.street_number is None
        assert result.zip_code is None

    def test_directional_and_suffix_spellings_still_collapse(self):
        a = normalize_address("123 N Main St, Chicago, IL 60601")
        b = normalize_address("123 North Main Street, Chicago, IL 60601")
        assert a.street_name == b.street_name == "north main street"

    def test_zip_plus_four(self):
        assert normalize_address("123 Main St, Chicago, IL 60601-1234").zip_code == "60601"
