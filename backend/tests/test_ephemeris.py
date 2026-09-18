"""Tests for the Krakow EPHEM.TXT / allstars-cat.txt parsers.

These are regression tests for the exact edge cases found in the live files.
Every one of them corresponds to a real row.
"""
from __future__ import annotations

import pytest

from app.ephemeris import (
    CONST_UPPER_TO_IAU,
    IAU_CONSTELLATIONS,
    Star,
    StarKey,
    build_stars,
    is_eclipsing_type,
    parse_allstars_cat,
    parse_ephem_txt,
)


# ---------------------------------------------------------------------- #
# EPHEM.TXT
# ---------------------------------------------------------------------- #
class TestParseEphemTxt:
    def test_parses_expected_record_count(self, elements):
        # 19 data rows in the sample (the two header rows are excluded).
        assert len(elements) == 19

    def test_skips_the_two_header_rows(self, elements):
        # The header row "STAR  Name  (HJD) ..." superficially matches a
        # naive regex because "Name" looks like a star name.
        names = {e.key.name for e in elements}
        assert "Name" not in names
        assert all(e.key.constellation in IAU_CONSTELLATIONS for e in elements)

    def test_basic_fields(self, elements):
        rz = next(e for e in elements if e.key.name == "RZ" and e.key.constellation == "Cas")
        assert rz.min_type == "PRI"
        assert rz.epoch_hjd == pytest.approx(2452500.68)
        assert rz.period_days == pytest.approx(1.195233)
        assert rz.n_minima == 9
        assert rz.year_range == "2019-2021"
        assert rz.last_updated == "30.Nov.21"

    def test_last_digit_errors_are_absolute(self, elements):
        rz = next(e for e in elements if e.key.name == "RZ")
        # "2452500.68 (1)" -> 0.01 d ; "1.195233 (2)" -> 2e-6 d
        assert rz.epoch_err_days == pytest.approx(0.01)
        assert rz.period_err_days == pytest.approx(2e-6)

    def test_star_errors_are_none(self, elements):
        """``(*)`` in the error column means "unknown", not zero."""
        khi = next(
            e for e in elements
            if e.key.name == "KHI 2" and e.key.constellation == "Hya"
        )
        assert khi.epoch_err_days is None
        assert khi.period_err_days is None

    def test_constellation_codes_are_normalised(self, elements):
        cma = [e for e in elements if e.key.constellation == "CMa"]
        assert cma, "CMa must be recognised (regression: it was once missing)"
        assert CONST_UPPER_TO_IAU["CMA"] == "CMa"
        assert CONST_UPPER_TO_IAU["CMI"] == "CMi"

    def test_three_token_star_name(self, elements):
        khi = [e for e in elements if e.key.name == "KHI 2"]
        assert khi, "'HYA KHI 2' must keep its numeric suffix"
        assert khi[0].key.constellation == "Hya"

    def test_lowercase_min_type_is_accepted(self, elements):
        """``AND BS    pri ...`` appears in lowercase in the live file."""
        bs = [e for e in elements if e.key.name == "BS" and e.key.constellation == "And"]
        assert bs
        assert bs[0].min_type == "PRI"

    def test_greek_abbreviation_rows_parse(self, elements):
        names = {(e.key.name, e.key.constellation) for e in elements}
        assert ("BET", "Per") in names   # Algol
        assert ("BET", "Lyr") in names   # beta Lyrae
        assert ("BET", "Aur") in names   # beta Aurigae
        assert ("LAM", "Tau") in names

    def test_separate_primary_and_secondary_rows(self, elements):
        v495 = [
            e for e in elements
            if e.key.name == "V495" and e.key.constellation == "Vul"
        ]
        assert {e.min_type for e in v495} == {"PRI", "SEC"}
        pri = next(e for e in v495 if e.min_type == "PRI")
        sec = next(e for e in v495 if e.min_type == "SEC")
        # The two rows have genuinely different periods in this database.
        assert pri.period_days != pytest.approx(sec.period_days, abs=1e-9)

    def test_malformed_lines_are_skipped_not_fatal(self):
        text = (
            "garbage line\n"
            "CAS RZ    PRI 2452500.68  (1)    1.195233     (2)     9   9   0   0   9   0   0   0  2019-2021   30.Nov.21\n"
            "ZZZ NOPE   PRI notanumber (1)   1.0 (1) 1 1 0 0 1 0 0 0 2000-2001 1.Jan.00\n"
        )
        out = parse_ephem_txt(text)
        assert len(out) == 1
        assert out[0].key.name == "RZ"


# ---------------------------------------------------------------------- #
# allstars-cat.txt
# ---------------------------------------------------------------------- #
class TestParseAllstarsCat:
    def test_parses_expected_record_count(self, catalog):
        assert len(catalog) == 10

    def test_right_ascension_is_in_hours_not_degrees(self, catalog):
        """Regression test for a units bug.

        RZ Cas is stored as ``2 48 55.5`` which is 2.8154 h -> 42.2313 deg.
        Reading it as degrees puts the star in the wrong constellation.
        """
        rz = next(c for c in catalog if c.key.name == "RZ")
        assert rz.ra_deg == pytest.approx(42.23125, abs=1e-4)
        assert rz.dec_deg == pytest.approx(69.634278, abs=1e-4)

    def test_coordinates_match_simbad(self, catalog):
        """The catalogue positions are J2000 and accurate to ~0.2 arcsec here."""
        rz = next(c for c in catalog if c.key.name == "RZ")
        # SIMBAD V* RZ Cas
        assert rz.ra_deg == pytest.approx(42.23129265439, abs=0.0001)
        assert rz.dec_deg == pytest.approx(69.63428930629, abs=0.0001)

    def test_photometry(self, catalog):
        rz = next(c for c in catalog if c.key.name == "RZ")
        assert rz.v_max == pytest.approx(6.18)
        assert rz.v_min == pytest.approx(7.72)
        assert rz.depth_mag == pytest.approx(1.54)
        assert rz.variability_type == "EA"
        assert rz.spectral_type == "A3V"

    def test_corrupt_magnitude_row_yields_no_depth(self, catalog):
        """``WW And 10.920 - 0.67`` is junk in the real file; Vmin < Vmax."""
        ww = next(c for c in catalog if c.key.name == "WW")
        assert ww.v_min < ww.v_max
        assert ww.depth_mag is None

    def test_missing_spectral_type_does_not_desync_pairing(self, catalog):
        """Rows with no spectral type must not swallow the next record.

        In the live file a single such row used to shift the two-line pairing
        and corrupt every record after it.
        """
        sw = [c for c in catalog if c.key.name == "SW" and c.key.constellation == "CMa"]
        assert len(sw) == 2
        assert {c.raw["rt"] for c in sw} == {"PRI", "SEC"}
        r_cma = next(c for c in catalog if c.key.name == "R" and c.key.constellation == "CMa")
        assert r_cma.ra_deg == pytest.approx((7 + 19 / 60 + 28.2 / 3600) * 15, abs=1e-6)

    def test_multi_token_star_name(self, catalog):
        mu = [c for c in catalog if c.key.name.startswith("mu.")]
        assert mu, "'mu. 1 Sco' must parse as a single name"
        assert mu[0].key.constellation == "Sco"

    def test_elements_on_line_two(self, catalog):
        rz = next(c for c in catalog if c.key.name == "RZ")
        assert rz.period_days == pytest.approx(1.1952514)
        assert rz.epoch_hjd == pytest.approx(2452500.581)
        assert rz.secondary_phase == pytest.approx(0.5)


# ---------------------------------------------------------------------- #
# Joining
# ---------------------------------------------------------------------- #
class TestBuildStars:
    def test_join_on_name_and_constellation(self, stars):
        rz = stars[("RZ", "CAS")]
        assert rz.catalog is not None
        assert len(rz.elements) == 1
        assert rz.elements[0].source == "EPHEM.TXT"

    def test_primary_and_secondary_records_merge_into_one_star(self, stars):
        v495 = stars[("V495", "VUL")]
        assert v495.catalog is not None, "PRI+SEC catalogue rows must merge per star"
        assert len(v495.elements) == 2

    def test_elements_win_over_catalogue(self, stars):
        """EPHEM.TXT carries newer epochs (2022) than allstars-cat (2020)."""
        rz = stars[("RZ", "CAS")]
        assert rz.elements[0].epoch_hjd == pytest.approx(2452500.68)      # EPHEM.TXT
        assert rz.catalog.epoch_hjd == pytest.approx(2452500.581)          # catalogue

    def test_ephem_only_star_has_no_catalog(self, stars):
        assert stars[("BET", "PER")].catalog is None
        assert stars[("BET", "LYR")].catalog is None

    def test_secondary_phase_propagates_to_elements(self, stars):
        rt = stars[("RT", "AND")]
        assert rt.elements[0].secondary_phase == pytest.approx(0.5)


# ---------------------------------------------------------------------- #
# Naming
# ---------------------------------------------------------------------- #
class TestNaming:
    @pytest.mark.parametrize(
        "name,constellation,expected",
        [
            ("RZ", "Cas", "RZ Cas"),
            ("V495", "Vul", "V495 Vul"),
            ("BET", "Per", "bet Per"),
            ("BET", "Lyr", "bet Lyr"),
            ("LAM", "Tau", "lam Tau"),
            ("KHI 2", "Hya", "khi 2 Hya"),
            ("MU1", "Sco", "MU1 Sco"),
            ("mu.", "Sco", "mu. Sco"),
        ],
    )
    def test_display_name(self, name, constellation, expected):
        star = Star(key=StarKey(name=name, constellation=constellation), elements=[])
        assert star.display_name == expected

    def test_pretty_name_expands_greek(self):
        star = Star(key=StarKey(name="BET", constellation="Per"), elements=[])
        assert star.pretty_name == "beta Per"

    def test_simbad_identifiers_are_ordered_and_deduplicated(self, stars):
        algol = stars[("BET", "PER")]
        ids = algol.simbad_identifiers()
        assert ids[0] == "bet Per"
        assert "beta Per" in ids
        assert len(ids) == len(set(ids))
        assert len(ids) <= 6

    def test_simbad_identifiers_handle_punctuation(self, stars):
        mu = stars[("MU1", "SCO")]
        ids = mu.simbad_identifiers()
        # "MU1 Sco" is the form SIMBAD actually resolves.
        assert ids[0] == "MU1 Sco"

    def test_fuzzy_join_recovers_punctuation_mismatch(self, stars):
        """EPHEM.TXT says ``SCO MU1``; allstars-cat says ``mu. 1  Sco``.

        Without name normalisation this star silently loses its coordinates,
        V range and variability type -- and therefore never reaches the UI.
        """
        mu = stars[("MU1", "SCO")]
        assert mu.catalog is not None
        assert mu.catalog.v_max == pytest.approx(2.94)
        assert mu.catalog.ra_deg == pytest.approx(252.9675, abs=1e-3)
        assert mu.catalog.variability_type == "EB/SD"


# ---------------------------------------------------------------------- #
# Variability-type filter
# ---------------------------------------------------------------------- #
class TestIsEclipsing:
    @pytest.mark.parametrize("vt", ["EA", "EB", "EW", "EA/SD", "EA/DM", "EW/KW", "EB/KE", "E", "EA+GS"])
    def test_accepts_eclipsing_types(self, vt):
        assert is_eclipsing_type(vt) is True

    @pytest.mark.parametrize(
        "vt", ["NA", "NB", "NR", "NL", "UG", "NB+EA", "E+NL", "RR", "M", "SR", "", None]
    )
    def test_rejects_non_eclipsing_types(self, vt):
        assert is_eclipsing_type(vt) is False

    def test_nova_dominated_composite_is_rejected(self):
        """DQ Her is ``NB+EA``: its 16 mag range is an outburst, not an eclipse."""
        assert is_eclipsing_type("NB+EA") is False
