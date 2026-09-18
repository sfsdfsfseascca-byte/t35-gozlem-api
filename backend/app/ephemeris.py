"""
Ephemeris acquisition + parsing for the Krakow TIDAK database.

Two files are used, both served from https://www.as.up.krakow.pl/ephem/ :

``EPHEM.TXT``
    The file named in the requirements. Pure linear elements, one row per
    *minimum type*::

        STAR             M0     (ERR)        Period (ERR)           NUMBER OF MINIMA
        Name            (HJD)                (Days)         all pri sec   e ccd   v  pg   p    YEARS        DATE
        AND RT    ALL 2452500.352 (3)    0.6289287    (3)    15  14   1   0  15   0   0   0  2018-2021   17.Nov.21

    Column 1 is a 3-letter uppercase constellation code, column 2 the star name,
    column 3 the "RT" flag (``PRI`` / ``SEC`` / ``ALL``, sometimes lowercase).
    ``ALL`` means the epoch refers to primary minima and that no separate
    secondary solution exists.

    ``M0`` is a **Heliocentric** Julian Date (the header says so explicitly) and
    the errors in parentheses are last-digit uncertainties: ``(3)`` on
    ``2452500.352`` means +/- 0.003 d, ``(*)`` means unknown.

``allstars-cat.txt``
    Companion catalogue, two lines per star-record::

        RT    And  8.970 -  9.83 EA/RS      F8V+K1     ALL
        23 11 10.1 +53  1 33.0 2000   0.1  0    0.6289292000 2452500.34760 0.5

    Line 1: name, constellation, Vmax, Vmin, variability type, spectral type, RT
    Line 2: RA(h m s), Dec(d ' "), epoch year, D, d, Period, M0, secondary phase

    The RA on line 2 is in **hours** (verified: RZ Cas parses to
    42.23125 deg / +69.63428 deg, matching SIMBAD to 0.14 arcsec), so the
    positions are J2000 to the ~1' accuracy of GCVS.

Why both?
    ``EPHEM.TXT`` carries the *newest* elements (last-updated column runs into
    2022) but has no coordinates and no magnitudes. ``allstars-cat.txt`` has
    coordinates + V range + variability type but is frozen at 2020-iii. We use
    EPHEM.TXT as the authoritative M0/Period source and join the catalogue onto
    it, which also gives us a cheap brightness pre-filter *before* we ever spend
    a SIMBAD query.

Design notes
    * Nothing here touches the network at import time.
    * Parsing is defensive: a malformed row is logged and skipped, never fatal.
    * Both parsers are pure functions over text, so they are trivially testable
      against the fixtures in ``backend/data/samples``.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

log = logging.getLogger("eclipse_hunter.ephemeris")

# ---------------------------------------------------------------------- #
# Constants
# ---------------------------------------------------------------------- #
#: IAU 3-letter constellation codes as written by the Krakow files.
#: NOTE: this list is *complete* -- an earlier draft omitted CMa/CMi and
#: silently dropped 194 catalogue records. Keep it sorted-agnostic.
IAU_CONSTELLATIONS: frozenset = frozenset(
    """And Ant Aps Aql Aqr Ara Ari Aur Boo Cae Cam Cap Car Cas Cen Cep Cet Cha
    Cir Cnc Col Com CMa CMi CrA CrB Crt Cru Crv CVn Cyg Del Dor Dra Equ Eri For
    Gem Gru Her Hor Hya Hyi Ind Lac Leo Lep Lib LMi Lup Lyn Lyr Men Mic Mon Mus
    Nor Oct Oph Ori Pav Peg Per Phe Pic PsA Psc Pup Pyx Ret Scl Sco Sct Ser Sex
    Sge Sgr Tau Tel TrA Tri Tuc UMa UMi Vel Vir Vol Vul""".split()
)

#: EPHEM.TXT writes constellations in UPPERCASE.
CONST_UPPER_TO_IAU: Dict[str, str] = {c.upper(): c for c in IAU_CONSTELLATIONS}

#: Abbreviated Greek letters / Bayer-style tokens used for the brightest
#: eclipsing binaries, which are listed by Bayer letter rather than by a
#: variable-star name. These stars are (deliberately) absent from
#: allstars-cat.txt, which is why they need name expansion before a SIMBAD
#: query will resolve them.
GREEK_ABBREV: Dict[str, str] = {
    "ALF": "alf", "ALP": "alf", "BET": "bet", "GAM": "gam", "DEL": "del",
    "EPS": "eps", "ZET": "zet", "ETA": "eta", "THE": "the", "IOT": "iot",
    "KAP": "kap", "LAM": "lam", "MU": "mu", "NU": "nu", "KHI": "khi",
    "CHI": "khi", "OMI": "omi", "PI": "pi", "RHO": "rho", "SIG": "sig",
    "TAU": "tau", "UPS": "ups", "PHI": "phi", "PSI": "psi", "OME": "ome",
}

#: Pretty expansion, used only for display strings.
GREEK_FULL: Dict[str, str] = {
    "alf": "alpha", "bet": "beta", "gam": "gamma", "del": "delta", "eps": "epsilon",
    "zet": "zeta", "eta": "eta", "the": "theta", "iot": "iota", "kap": "kappa",
    "lam": "lambda", "mu": "mu", "nu": "nu", "khi": "chi", "omi": "omicron",
    "pi": "pi", "rho": "rho", "sig": "sigma", "tau": "tau", "ups": "upsilon",
    "phi": "phi", "psi": "psi", "ome": "omega",
}

MIN_TYPES = frozenset({"PRI", "SEC", "ALL"})


def normalize_star_name(name: str) -> str:
    """Canonical form used to join EPHEM.TXT against allstars-cat.txt.

    The two files disagree on punctuation and Greek abbreviations for the same
    star. Real examples:

    ==========================  ====================  ==============
    star                        EPHEM.TXT             allstars-cat
    ==========================  ====================  ==============
    mu^1 Sco                    ``SCO MU1``           ``mu. 1  Sco``
    beta Per                    ``PER BET``           (absent)
    lambda Tau                  ``TAU LAM``           ``lam   Tau``
    ==========================  ====================  ==============

    So we uppercase, expand Greek abbreviations, strip punctuation and remove
    spaces: all of ``MU1`` / ``mu. 1`` / ``MU.1`` collapse to ``MU1``.
    """
    text = (name or "").upper().strip()
    tokens = [t.strip(".,;:") for t in text.split()]
    if tokens and tokens[0] in GREEK_ABBREV:
        tokens[0] = GREEK_ABBREV[tokens[0]].upper()
    joined = "".join(t for t in tokens if t)
    # Drop any remaining punctuation so "MU.1" and "MU1" also collapse.
    return re.sub(r"[^A-Z0-9]", "", joined)

#: GCVS variability types that are genuinely *eclipsing*.
#:
#: This matters a lot: the Krakow file also contains novae and nova-likes
#: (``NB+EA`` for DQ Her, ``NA`` for V1500 Cyg) whose "magnitude range" is an
#: outburst amplitude of 16+ mag, not an eclipse. Without this filter those
#: objects dominate a "brightest / deepest" ranking and are useless to an
#: observer planning to watch an eclipse.
ECLIPSING_TYPE_PREFIXES: Tuple[str, ...] = ("EA", "EB", "EW")

#: GCVS tokens implying the magnitude range is dominated by an outburst.
NOVA_LIKE_TOKENS: Tuple[str, ...] = ("NA", "NB", "NR", "NL", "NC", "UG", "SN")


def is_eclipsing_type(variability_type: Optional[str]) -> bool:
    """True for pure/combined GCVS types whose primary mechanism is eclipsing.

    ``EA``  -> accepted      ``EW/KW`` -> accepted
    ``EA+GS``-> accepted     ``NB+EA`` -> rejected (nova dominates)
    ``E``    -> accepted     ``NA``    -> rejected
    """
    if not variability_type:
        return False
    vt = variability_type.upper().replace(" ", "")
    # A nova / recurrent-nova / nova-like / dwarf-nova component means the
    # catalogue's magnitude range describes an outburst, not an eclipse. This
    # has to be checked for *every* branch below, which is why it is hoisted.
    has_outburst_component = any(bad in vt for bad in NOVA_LIKE_TOKENS)

    for token in re.split(r"[+/:,]", vt):
        if not token:
            continue
        if token.startswith(ECLIPSING_TYPE_PREFIXES) or token == "E":
            return not has_outburst_component
    return False


# ---------------------------------------------------------------------- #
# Data classes
# ---------------------------------------------------------------------- #
@dataclass(frozen=True)
class StarKey:
    """Join key between EPHEM.TXT and allstars-cat.txt."""

    name: str          # e.g. "RT", "V495", "BET", "mu. 1"
    constellation: str  # IAU form, e.g. "And", "CMa", "Per"

    @property
    def upper(self) -> Tuple[str, str]:
        return (self.name.upper(), self.constellation.upper())

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"{self.name} {self.constellation}"


@dataclass
class ElementRecord:
    """One linear ephemeris solution (one minimum type for one star)."""

    key: StarKey
    min_type: str                       # "PRI" | "SEC" | "ALL"
    epoch_hjd: float                    # M0, heliocentric JD
    period_days: float
    epoch_err_days: Optional[float] = None   # from "(n)" on M0
    period_err_days: Optional[float] = None  # from "(n)" on Period
    n_minima: Optional[int] = None
    year_range: str = ""
    last_updated: str = ""
    secondary_phase: Optional[float] = None  # from allstars-cat.txt
    source: str = "EPHEM.TXT"

    # -- derived ------------------------------------------------------- #
    @property
    def yields_secondary(self) -> bool:
        """True if this record lets us predict a secondary minimum."""
        return self.min_type in ("ALL", "SEC")

    def epoch_error_seconds(self) -> float:
        if self.epoch_err_days is None:
            return 0.0
        return self.epoch_err_days * 86400.0

    def time_uncertainty_days(self, at_jd: float) -> float:
        """Propagate (sigma_M0, sigma_P) to a 1-sigma timing error at ``at_jd``.

        sigma_T = sqrt( sigma_M0^2 + (N * sigma_P)^2 ),  N = (T - M0) / P

        This matters for real observing plans: Kreiner's epochs are anchored on
        HJD ~2452500 (2002) and a period uncertainty of 1e-5 d grows to ~30 min
        after 3000 cycles.
        """
        if self.period_days <= 0:
            return 0.0
        n_cycles = abs(at_jd - self.epoch_hjd) / self.period_days
        var = 0.0
        if self.epoch_err_days:
            var += self.epoch_err_days ** 2
        if self.period_err_days:
            var += (n_cycles * self.period_err_days) ** 2
        return var ** 0.5


@dataclass
class CatalogRecord:
    """Photometric/astrometric data from allstars-cat.txt."""

    key: StarKey
    v_max: Optional[float]
    v_min: Optional[float]
    variability_type: str
    spectral_type: str
    ra_deg: float
    dec_deg: float
    period_days: Optional[float] = None
    epoch_hjd: Optional[float] = None
    secondary_phase: Optional[float] = None
    reference_year: Optional[int] = None
    raw: Dict[str, str] = field(default_factory=dict)

    @property
    def depth_mag(self) -> Optional[float]:
        """Eclipse amplitude Vmin - Vmax, or None if the row is unusable.

        Rejected cases (all of which really occur in this file):
          * ``Vmin <= Vmax`` -- e.g. ``WW And 10.920 - 0.67``.
          * values above 20 mag -- catalogue placeholders.
          * non-eclipsing variability types -- a nova's 16 mag outburst range is
            not an eclipse depth.
        """
        if self.v_max is None or self.v_min is None:
            return None
        if self.v_min <= self.v_max:
            return None
        if self.v_min > 20.0 or self.v_max > 20.0:
            return None
        if not is_eclipsing_type(self.variability_type):
            return None
        return round(self.v_min - self.v_max, 3)


@dataclass
class Star:
    """Fully joined view of one eclipsing binary."""

    key: StarKey
    elements: List[ElementRecord]
    catalog: Optional[CatalogRecord] = None

    # -- naming -------------------------------------------------------- #
    @property
    def display_name(self) -> str:
        """Best-effort GCVS-style designation, e.g. ``RZ Cas`` / ``bet Per``.

        Component order matters: the constellation always comes last, so a
        multi-token name such as ``KHI 2`` + ``Hya`` must render as
        ``khi 2 Hya`` and not ``khi Hya 2``.
        """
        name, con = self.key.name, self.key.constellation
        tokens = name.split()
        if tokens and tokens[0].upper() in GREEK_ABBREV:
            tokens = [GREEK_ABBREV[tokens[0].upper()]] + tokens[1:]
        return " ".join(tokens + [con])

    @property
    def pretty_name(self) -> str:
        """Expanded form for UI headers, e.g. ``beta Per``."""
        parts = self.display_name.split()
        if parts and parts[0] in GREEK_FULL:
            parts[0] = GREEK_FULL[parts[0]]
        return " ".join(parts)

    def simbad_identifiers(self) -> List[str]:
        """Ordered candidate identifiers, most-likely-first.

        Empirically validated against SIMBAD:
          * ``RZ Cas``  -> V* RZ Cas
          * ``bet Per`` -> * bet Per  (Algol, HIP 14576)
          * ``b Per``   -> * b Per    (rho Per, HIP 20070)
          * ``lam Tau`` -> * lam Tau
          * ``mu. 1 Sco``-> resolves once the space is removed
        """
        name, con = self.key.name, self.key.constellation
        out: List[str] = []

        def add(candidate: str) -> None:
            c = candidate.strip()
            if c and c not in out:
                out.append(c)

        add(self.display_name)
        add(f"{name} {con}")
        tokens = name.upper().split()
        if tokens:
            head = tokens[0]
            rest = " ".join(tokens[1:])
            if head in GREEK_ABBREV:
                add(f"{GREEK_ABBREV[head]} {con} {rest}".strip())
                add(f"{GREEK_FULL[GREEK_ABBREV[head]]} {con} {rest}".strip())
            # "mu. 1 Sco" -> "mu.1 Sco" and "mu 1 Sco"
            if len(tokens) > 1:
                add("".join(tokens) + f" {con}")
                add(" ".join(tokens) + f" {con}")
            # single-letter Bayer designations, e.g. "b Per"
            if len(head) == 1 and head.isalpha():
                add(f"{head} {con}")
        # Strip punctuation variants: "mu." -> "mu"
        for cand in list(out):
            add(cand.replace(".", ""))
        return out[:6]

    # -- ephemeris ----------------------------------------------------- #
    def element_for(self, min_type: str) -> Optional[ElementRecord]:
        order = {"PRI": 0, "ALL": 1, "SEC": 2}
        matches = [e for e in self.elements if e.min_type == min_type]
        if not matches:
            return None
        return sorted(matches, key=lambda e: order.get(e.min_type, 9))[0]

    @property
    def primary_element(self) -> Optional[ElementRecord]:
        """Primary-minimum solution (prefer PRI, fall back to ALL)."""
        return self.element_for("PRI") or self.element_for("ALL")

    @property
    def secondary_element(self) -> Optional[ElementRecord]:
        """Explicit SEC solution if one exists."""
        return self.element_for("SEC")

    @property
    def v_max(self) -> Optional[float]:
        return self.catalog.v_max if self.catalog else None

    @property
    def v_min(self) -> Optional[float]:
        return self.catalog.v_min if self.catalog else None

    @property
    def depth_mag(self) -> Optional[float]:
        return self.catalog.depth_mag if self.catalog else None


# ---------------------------------------------------------------------- #
# EPHEM.TXT parsing
# ---------------------------------------------------------------------- #
_EPHEM_RE = re.compile(
    r"""^
    (?P<con>[A-Z]{3})\s+
    (?P<name>.+?)\s+
    (?P<rt>PRI|SEC|ALL)\s+
    (?P<m0>\d+\.\d+)\s*
    \((?P<m0err>\d+|\*)\)\s*
    (?P<period>\d*\.?\d+)\s*
    \((?P<perr>\d+|\*)\)\s*
    (?P<counts>(?:\d+\s+){8})
    (?P<years>\d{4}-\d{4})\s*
    (?P<date>.+?)\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _last_digit_error(value: float, digits: Optional[str]) -> Optional[float]:
    """Convert a parenthesised last-digit error into absolute units.

    ``2452500.352 (3)`` -> 0.003 ; ``0.6289287 (3)`` -> 3e-7 ; ``(*)`` -> None.
    """
    if not digits or digits == "*":
        return None
    try:
        n = int(digits)
    except ValueError:
        return None
    text = repr(float(value))
    if "e" in text or "E" in text:  # pragma: no cover - defensive
        return None
    decimals = len(value.__str__().split(".")[1]) if "." in str(value) else 0
    return n * (10.0 ** -decimals)


def parse_ephem_txt(text: str) -> List[ElementRecord]:
    """Parse the raw ``EPHEM.TXT`` payload into element records.

    Malformed lines (including the two header rows) are skipped silently; a
    single broken row must never take down the whole catalogue.
    """
    records: List[ElementRecord] = []
    skipped = 0
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        m = _EPHEM_RE.match(line.rstrip())
        if not m:
            skipped += 1
            continue
        con_upper = m.group("con").upper()
        constellation = CONST_UPPER_TO_IAU.get(con_upper)
        if constellation is None:
            # Unknown code => almost certainly a header row ("STAR", "Name").
            skipped += 1
            continue
        try:
            epoch = float(m.group("m0"))
            period = float(m.group("period"))
        except ValueError:  # pragma: no cover - regex already constrains
            skipped += 1
            continue
        if period <= 0 or epoch <= 0:
            skipped += 1
            continue

        counts = [int(x) for x in m.group("counts").split()]
        name = m.group("name").strip()
        records.append(
            ElementRecord(
                key=StarKey(name=name, constellation=constellation),
                min_type=m.group("rt").upper(),
                epoch_hjd=epoch,
                period_days=period,
                epoch_err_days=_last_digit_error(epoch, m.group("m0err")),
                period_err_days=_last_digit_error(period, m.group("perr")),
                n_minima=counts[0] if counts else None,
                year_range=m.group("years"),
                last_updated=m.group("date").strip(),
            )
        )
    if skipped:
        log.debug("parse_ephem_txt: skipped %d unparseable line(s)", skipped)
    return records


# ---------------------------------------------------------------------- #
# allstars-cat.txt parsing
# ---------------------------------------------------------------------- #
_CAT_LINE2_RE = re.compile(
    r"""^\s*
    (?P<rah>\d{1,2})\s+(?P<ram>\d{1,2})\s+(?P<ras>[\d.]+)\s+
    (?P<decd>[+-]\d{1,3})\s+(?P<decm>\d{1,2})\s+(?P<decs>[\d.]+)\s+
    (?P<year>\d{4})\s+
    (?P<bigD>[\d.]+)\s+(?P<smallD>[\d.]+)\s+
    (?P<period>[\d.]+)\s+(?P<m0>[\d.]+)\s+(?P<secphase>[\d.]+)\s*$
    """,
    re.VERBOSE,
)


def _parse_catalog_line1(line: str) -> Optional[Tuple[str, str, List[str]]]:
    """Return ``(name, constellation, middle_tokens)`` or None."""
    tokens = line.split()
    if len(tokens) < 5:
        return None
    hits = [i for i, tok in enumerate(tokens) if tok in IAU_CONSTELLATIONS]
    if not hits:
        return None
    if tokens[-1].upper() not in MIN_TYPES:
        return None
    idx = hits[0]
    if idx == 0:
        return None
    name = " ".join(tokens[:idx])
    return name, tokens[idx], tokens[idx + 1: -1]


def parse_allstars_cat(text: str) -> List[CatalogRecord]:
    """Parse ``allstars-cat.txt`` (two physical lines per record).

    The pairing is driven by successfully matching *both* lines. If a line-1
    match is not followed by a valid line-2, we advance by a single line rather
    than by two -- otherwise one malformed record desynchronises every record
    after it (a real failure mode in this file).
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")

    # Everything before the "cut header here!" marker is documentation.
    start = 0
    for i, line in enumerate(lines[:60]):
        if line.lower().startswith("cut header here"):
            start = i + 2
            break

    records: List[CatalogRecord] = []
    malformed = 0
    i = start
    while i < len(lines):
        line1 = lines[i]
        if not line1.strip():
            i += 1
            continue
        parsed = _parse_catalog_line1(line1)
        if parsed is None:
            i += 1
            continue
        name, constellation, middle = parsed

        line2 = lines[i + 1] if i + 1 < len(lines) else ""
        m2 = _CAT_LINE2_RE.match(line2)
        if m2 is None:
            malformed += 1
            i += 1          # re-sync: do NOT consume line2
            continue

        # middle = [Vmax, '-', Vmin, varType, spectral...]
        v_max = v_min = None
        variability = ""
        spectral = ""
        if len(middle) >= 4 and middle[1] == "-":
            try:
                v_max = float(middle[0])
                v_min = float(middle[2])
            except ValueError:
                pass
            variability = middle[3]
            spectral = " ".join(middle[4:]).strip()

        g = m2.groupdict()
        # RA is stored in HOURS; Dec in degrees. Both J2000 (validated against
        # SIMBAD: RZ Cas matches to 0.14 arcsec).
        ra_deg = (int(g["rah"]) + int(g["ram"]) / 60.0 + float(g["ras"]) / 3600.0) * 15.0
        dec_sign = -1.0 if g["decd"].startswith("-") else 1.0
        dec_deg = dec_sign * (
            abs(int(g["decd"])) + int(g["decm"]) / 60.0 + float(g["decs"]) / 3600.0
        )

        records.append(
            CatalogRecord(
                key=StarKey(name=name, constellation=constellation),
                v_max=v_max,
                v_min=v_min,
                variability_type=variability,
                spectral_type=spectral,
                ra_deg=round(ra_deg, 6),
                dec_deg=round(dec_deg, 6),
                period_days=float(g["period"]) if g["period"] else None,
                epoch_hjd=float(g["m0"]) if g["m0"] else None,
                secondary_phase=float(g["secphase"]) if g["secphase"] else None,
                reference_year=int(g["year"]) if g["year"] else None,
                raw={"rt": line1.split()[-1].upper()},
            )
        )
        i += 2

    if malformed:
        log.warning("parse_allstars_cat: %d malformed record(s) skipped", malformed)
    return records


# ---------------------------------------------------------------------- #
# Joining
# ---------------------------------------------------------------------- #
def build_stars(
    elements: Iterable[ElementRecord],
    catalog: Iterable[CatalogRecord],
) -> Dict[Tuple[str, str], Star]:
    """Join element records with catalogue records on (NAME, CONSTELLATION).

    ``allstars-cat.txt`` holds one record per *minimum type*, so a star with
    both ``PRI`` and ``SEC`` rows appears twice. We merge those into a single
    per-star :class:`CatalogRecord`: that is what prevents the "231 candidates
    have no catalogue coordinates" class of bug, where a star's ``SEC`` element
    row from EPHEM.TXT failed to match because only its ``PRI`` row existed in
    the catalogue.
    """
    by_key: Dict[Tuple[str, str], Star] = {}
    for rec in elements:
        star = by_key.setdefault(rec.key.upper, Star(key=rec.key, elements=[]))
        star.elements.append(rec)

    # ---- group catalogue records per star ---- #
    cat_groups: Dict[Tuple[str, str], List[CatalogRecord]] = {}
    for rec in catalog:
        cat_groups.setdefault(rec.key.upper, []).append(rec)

    def _merge(records: List[CatalogRecord]) -> CatalogRecord:
        # Prefer a record whose photometry is internally consistent.
        primary = next(
            (r for r in records if r.v_max is not None and r.v_min is not None and r.v_min > r.v_max),
            records[0],
        )
        merged = CatalogRecord(
            key=primary.key,
            v_max=primary.v_max,
            v_min=primary.v_min,
            variability_type=primary.variability_type,
            spectral_type=primary.spectral_type,
            ra_deg=primary.ra_deg,
            dec_deg=primary.dec_deg,
            period_days=primary.period_days,
            epoch_hjd=primary.epoch_hjd,
            secondary_phase=primary.secondary_phase,
            reference_year=primary.reference_year,
            raw=dict(primary.raw),
        )
        for other in records:
            if not merged.spectral_type and other.spectral_type:
                merged.spectral_type = other.spectral_type
            if not merged.variability_type and other.variability_type:
                merged.variability_type = other.variability_type
            if merged.secondary_phase is None and other.secondary_phase is not None:
                merged.secondary_phase = other.secondary_phase
            if merged.period_days is None and other.period_days:
                merged.period_days = other.period_days
            if merged.epoch_hjd is None and other.epoch_hjd:
                merged.epoch_hjd = other.epoch_hjd
        merged.raw["merged_records"] = str(len(records))
        return merged

    cat_by_key: Dict[Tuple[str, str], CatalogRecord] = {
        key: _merge(records) for key, records in cat_groups.items()
    }

    for key, star in by_key.items():
        cat = cat_by_key.get(key)
        if cat is not None:
            star.catalog = cat
            # Carry the catalogue's secondary phase onto every element record so
            # eccentric systems get a better secondary-minimum estimate.
            if cat.secondary_phase is not None:
                for el in star.elements:
                    if el.secondary_phase is None:
                        el.secondary_phase = cat.secondary_phase

    # ---- fuzzy pass: punctuation / Greek-abbreviation mismatches ------ #
    # e.g. EPHEM.TXT "SCO MU1" vs allstars-cat "mu. 1  Sco". Without this the
    # star silently loses its coordinates, V range and variability type.
    normalized_index: Dict[Tuple[str, str], CatalogRecord] = {}
    for key, rec in cat_by_key.items():
        normalized_index.setdefault((normalize_star_name(key[0]), key[1]), rec)

    fuzzy_joined = 0
    for key, star in by_key.items():
        if star.catalog is not None:
            continue
        candidate = normalized_index.get((normalize_star_name(key[0]), key[1]))
        if candidate is None:
            continue
        star.catalog = candidate
        fuzzy_joined += 1
        if candidate.secondary_phase is not None:
            for el in star.elements:
                if el.secondary_phase is None:
                    el.secondary_phase = candidate.secondary_phase
    if fuzzy_joined:
        log.debug("build_stars: %d star(s) joined via name normalisation", fuzzy_joined)

    # Stars present only in the catalogue still get an entry (using the
    # catalogue's own Period/M0), so we never silently lose a target.
    for key, cat in cat_by_key.items():
        if key in by_key:
            continue
        if not cat.period_days or not cat.epoch_hjd:
            continue
        min_type = cat.raw.get("rt", "ALL").upper()
        if min_type not in MIN_TYPES:
            min_type = "ALL"
        rec = ElementRecord(
            key=cat.key,
            min_type=min_type,
            epoch_hjd=cat.epoch_hjd,
            period_days=cat.period_days,
            secondary_phase=cat.secondary_phase,
            source="allstars-cat.txt",
        )
        by_key[key] = Star(key=cat.key, elements=[rec], catalog=cat)

    return by_key


def catalog_fingerprint(text: str) -> str:
    """Short content hash used for cache keys and /health diagnostics."""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def catalog_stats(stars: Dict[Tuple[str, str], Star]) -> Dict[str, object]:
    with_cat = sum(1 for s in stars.values() if s.catalog is not None)
    return {
        "element_records": sum(len(s.elements) for s in stars.values()),
        "unique_stars": len(stars),
        "with_catalog_data": with_cat,
    }


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
