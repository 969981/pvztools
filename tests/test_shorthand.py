"""Tests for pvztools.schema.track.shorthand (docs/track-format.md §3)."""
import json
import pathlib
import re
from dataclasses import replace

import pytest

from pvztools.schema.track.shorthand import ShorthandError, dump_shorthand, parse_shorthand

DOCS = pathlib.Path(__file__).resolve().parents[1] / "docs"

SPEC_EXAMPLES = [
    "P6: PP|PP|PPSS|PP|PP|PPSS (6|6|6|6|6|6)",
    "P4: PC|PC|PC|PC 869|869|869|869",
    "ch4: IP-P|IP-P 1738|1738",
    "ch6: P|I-P|P|I-P 601|1150|601|1150",
    "ch5: P|I-P|I-P 601|1500|1500",
    "邻C6u: I-P|I-P|N|P (1975|1975|750|750)",
    "|PP|I-PP|N (6, 21, 9)",
    "P5u: P|P|P|P|P 6|6|6|9|9",
    "ICP-BPD/Pd",
    "PIPIPN",
]


def symbols(wave, half="top"):
    hw = wave.top if half == "top" else wave.bottom
    return [op.symbol for op in hw.ops]


def phases(wave):
    return [op.phase for op in wave.top.ops]


def lengths(track):
    return [w.length_cs for w in track.waves]


# --- §3 examples -------------------------------------------------------------
@pytest.mark.parametrize("text", SPEC_EXAMPLES)
def test_spec_examples_parse(text):
    t = parse_shorthand(text)
    assert t.kind == "shorthand"
    assert t.waves
    assert [w.selector for w in t.waves] == [(i,) for i in range(1, len(t.waves) + 1)]


def test_p6_full_notation():
    t = parse_shorthand("P6: PP|PP|PPSS|PP|PP|PPSS (6|6|6|6|6|6)")
    assert t.rhythm.family == "P" and t.rhythm.param == 6
    assert len(t.waves) == 6
    assert lengths(t) == [600] * 6
    assert symbols(t.waves[2]) == ["PP", "SS"]
    assert not any(w.mirrored for w in t.waves)


def test_p4_fodder_bare_tail():
    t = parse_shorthand("P4: PC|PC|PC|PC 869|869|869|869")
    assert lengths(t) == [869] * 4
    assert [op.kind for op in t.waves[0].top.ops] == ["cob", "fodder"]
    assert t.waves[0].top.ops[1].variant == "hard"
    assert all(w.mirrored for w in t.waves)  # al = 1 < 2


def test_ch4_phases():
    t = parse_shorthand("ch4: IP-P|IP-P 1738|1738")
    assert len(t.waves) == 2
    assert symbols(t.waves[0]) == ["I", "P", "P"]
    assert phases(t.waves[0]) == ["pre", "pre", "post"]
    assert lengths(t) == [1738, 1738]
    assert not t.waves[0].mirrored  # al = 2: full notation per §3.1 rule 1


def test_ch6_mixed_units_are_per_item():
    t = parse_shorthand("ch6: P|I-P|P|I-P 601|1150|601|1150")
    assert lengths(t) == [601, 1150, 601, 1150]
    t2 = parse_shorthand("ch6: P|I-P|P|I-P (6|11.5|6|11.5)")
    assert lengths(t2) == [600, 1150, 600, 1150]


def test_stray_bars_and_spaces_in_lengths():
    t = parse_shorthand("|PP|I-PP|N (6, 21, 9)")
    assert t.rhythm is None
    assert len(t.waves) == 3
    assert lengths(t) == [600, 2100, 900]
    assert [w.mirrored for w in t.waves] == [False, False, False]
    assert "empty waves from stray '|' dropped" in t.meta["normalized_from"]


def test_half_field_single_wave():
    t = parse_shorthand("ICP-BPD/Pd")
    assert len(t.waves) == 1
    w = t.waves[0]
    assert symbols(w) == ["I", "C", "P", "B", "P", "D"]
    assert phases(w) == ["pre", "pre", "pre", "post", "post", "post"]
    assert symbols(w, "bottom") == ["P", "d"]
    assert not w.mirrored


def test_pure_simplified_split():
    t = parse_shorthand("PIPIPN")
    assert [w.raw for w in t.waves] == ["P", "IP", "IP", "N"]
    assert [w.mirrored for w in t.waves] == [True, True, True, False]
    assert "pure 简写 split by accumulated al" in t.meta["normalized_from"]


def test_simplified_ambiguous():
    with pytest.raises(ShorthandError) as ei:
        parse_shorthand("PIPI")
    assert ei.value.code == "SIMPLIFIED_AMBIGUOUS"
    assert isinstance(ei.value, ValueError)


# --- §3.1b tolerance rules ------------------------------------------------
def test_plus_and_fullwidth():
    t = parse_shorthand("邻c6u：I+PP|I+PP|N|PP （2030，2030，750，750）")
    assert t.rhythm.family == "Cu" and t.rhythm.prefix == "邻" and t.rhythm.param == 6
    assert lengths(t) == [2030, 2030, 750, 750]
    assert phases(t.waves[0]) == ["pre", "post"]
    notes = t.meta["normalized_from"]
    assert "'+' -> '-'" in notes and "fullwidth punctuation -> ASCII" in notes


def test_length_qualifiers():
    t = parse_shorthand("C5u: NA|IP-Pd|I-Pd (7.5,25-,20+)")
    assert lengths(t) == [750, 2500, 2000]
    assert t.meta["length_hint"] == [None, "-", "+"]
    t = parse_shorthand("I6b:I3PPDD-PP|I3PPDD-PP|I3PPDD-PP(1361×3),18=16+2+0")
    assert lengths(t) == [1361, 1361, 1361]
    assert any(w["code"] == "ANNOTATION_DROPPED" for w in t.meta["warnings"])
    t = parse_shorthand("I6: I0P-ccP/PD(12s)")
    assert lengths(t) == [1200]
    assert t.waves[0].top.ops[0].symbol == "I0" and t.waves[0].top.ops[0].variant == "ice0"
    t = parse_shorthand("间双核C4：I-N|A|A'a|N,(25|9|9|9|)")
    assert lengths(t) == [2500, 900, 900, 900]
    t = parse_shorthand("I4i: I-Pd|I-Pd, (1560连续冰波)")
    assert lengths(t) == [1560, None]
    assert any(w["code"] == "LENGTH_COUNT_MISMATCH" for w in t.meta["warnings"])


def test_length_range():
    t = parse_shorthand("ch4: I-P|I-P (17.5~19|17.5~19)")
    assert t.waves[0].length_cs is None
    assert t.waves[0].length_range == (1750, 1900)


def test_ice3_io_backtick():
    t = parse_shorthand("I6i：Ice3PD/I-cc-PPd|Ice3PD/I-cc-PPd，(1308|1308)。")
    assert symbols(t.waves[0]) == ["I3", "P", "D"]
    assert t.waves[0].top.ops[0].variant == "ice3"
    assert symbols(t.waves[0], "bottom") == ["I", "c", "c", "PP", "d"]
    assert lengths(t) == [1308, 1308]
    t = parse_shorthand("C2io:PP|I-io")
    assert symbols(t.waves[1]) == ["I", "IO"]
    assert t.waves[1].top.ops[1].kind == "natural"
    t = parse_shorthand("P6:PP|PP|PP|PP|N|N`(6,6,6,6,6,6)")
    assert t.waves[5].top.ops[0].symbol == "N'"
    assert t.waves[5].top.ops[0].variant == "imitater"
    t = parse_shorthand("A₁Aa|N")
    assert symbols(t.waves[0]) == ["A'", "A", "a"]


def test_annotations_dropped():
    t = parse_shorthand("对ch6：Ia-PP|Pc/P|IBb-PA(N)|Pc/P（高坚果） (12,6,12,6)")
    assert t.meta["annotations"] == ["N", "高坚果"]
    codes = [w["code"] for w in t.meta["warnings"]]
    assert codes.count("ANNOTATION_DROPPED") == 2
    assert symbols(t.waves[2]) == ["I", "B", "b", "P", "A"]
    assert lengths(t) == [1200, 600, 1200, 600]


def test_wave_prefix_mode():
    t = parse_shorthand("w1:c(二路)PSD/PDD\nw3:PDD/PDcD\nw19:PSD/PSD")
    assert [w.selector for w in t.waves] == [(1,), (3,), (19,)]
    assert symbols(t.waves[0]) == ["c", "P", "S", "D"]
    assert "二路" in t.meta["annotations"]
    assert t.rhythm is None


def test_unknown_symbol():
    with pytest.raises(ShorthandError) as ei:
        parse_shorthand("Pa|IPa+iPa")
    assert ei.value.code == "UNKNOWN_SYMBOL"


def test_dialect():
    t = parse_shorthand("PA'a|PC", dialect="be2sio4")
    assert t.waves[0].top.ops[2].variant == "light"
    assert t.waves[1].top.ops[1].variant == "soft"
    with pytest.raises(ValueError):
        parse_shorthand("P|P", dialect="nope")


def test_rhythm_split_only_when_name_like():
    t = parse_shorthand("邻C6u I-PP|I-PP|N|PP 1976|1976|750|749")
    assert t.rhythm.raw == "邻C6u"
    assert lengths(t) == [1976, 1976, 750, 749]
    t = parse_shorthand("P5: PP | PP | PP | PP | PP (7, 7, 7, 7, 7)")
    assert len(t.waves) == 5 and lengths(t) == [700] * 5


# --- dump / round trip ----------------------------------------------------
def _strip_raw(track):
    return [replace(w, raw="") for w in track.waves]


@pytest.mark.parametrize("text", SPEC_EXAMPLES)
def test_dump_round_trip(text):
    t1 = parse_shorthand(text)
    dumped = dump_shorthand(t1)
    t2 = parse_shorthand(dumped)
    assert _strip_raw(t1) == _strip_raw(t2)
    assert t1.rhythm == t2.rhythm
    assert dump_shorthand(t2) == dumped


def test_dump_canonical():
    t = parse_shorthand("P6：PP|PP|PPSS|PP|PP|PPSS （6，6，6，6，6，6）")
    assert dump_shorthand(t) == "P6: PP|PP|PPSS|PP|PP|PPSS (6|6|6|6|6|6)"
    assert dump_shorthand(parse_shorthand("PIPIPN")) == "P|IP|IP|N"
    assert dump_shorthand(parse_shorthand("ch6: P|I-P|P|I-P 601|1150|601|1150")) == "ch6: P|I-P|P|I-P (601|1150|601|1150)"
    assert dump_shorthand(parse_shorthand("w1:PSD/PDD w19:PSD/PSD")) == "w1:PSD/PDD w19:PSD/PSD"


# --- corpus ------------------------------------------------------------------
def load_corpus():
    text = (DOCS / "research" / "track-corpus.md").read_text(encoding="utf-8")
    m = re.search(r"```json\s*(\[.*?\])\s*```", text, re.S)
    assert m, "no json fence in corpus"
    return json.loads(m.group(1))


#: Corpus entries the parser is known to reject (§3.1b: unknown symbols are errors).
#: 62: ``b'`` (back-field before cannon, replanted) is not in the symbol table;
#: 75: lowercase ``i``; 78: ``~`` inside an op string; 91: free text ``收尾`` in ops.
CORPUS_KNOWN_FAILURES = {62: "UNKNOWN_SYMBOL", 75: "UNKNOWN_SYMBOL", 78: "UNKNOWN_SYMBOL", 91: "UNKNOWN_SYMBOL"}


def test_corpus_parse_rate():
    corpus = load_corpus()
    assert len(corpus) >= 100
    failures = {}
    for entry in corpus:
        try:
            t = parse_shorthand(entry["track"])
            assert t.waves
        except ShorthandError as exc:
            failures[entry["id"]] = exc.code
    # every failure is pinned: a new one is a regression, a vanished one an (unexpected) fix
    assert failures == CORPUS_KNOWN_FAILURES, {k: v for k, v in failures.items() if CORPUS_KNOWN_FAILURES.get(k) != v}
    assert 1 - len(failures) / len(corpus) >= 0.95


@pytest.fixture(scope="module")
def corpus_by_id():
    return {e["id"]: e["track"] for e in load_corpus()}


def test_corpus_hand_picked(corpus_by_id):
    t = parse_shorthand(corpus_by_id[1])  # P4:PP|PP|PP|PP(9,9,9,9)
    assert len(t.waves) == 4 and lengths(t) == [900] * 4 and t.rhythm.family == "P"

    t = parse_shorthand(corpus_by_id[2])  # S5:I-PP|PP|PP|PP(18,6,6,6)
    assert phases(t.waves[0]) == ["pre", "post"] and lengths(t) == [1800, 600, 600, 600]

    t = parse_shorthand(corpus_by_id[5])  # P6-2I
    assert t.rhythm.suffixes == ("-2I",) and len(t.waves) == 6

    t = parse_shorthand(corpus_by_id[17])  # fullwidth, I1
    assert lengths(t) == [600] * 6 and t.waves[2].top.ops[1].variant == "ice1"

    t = parse_shorthand(corpus_by_id[19])  # P5u cs lengths
    assert lengths(t) == [657, 752, 657, 752, 657]
    assert t.waves[1].top.ops[1].variant == "delay_back"

    t = parse_shorthand(corpus_by_id[23])  # D5: PP|IP-c-PP|IP-c-PP(601|1437|1437)
    assert t.rhythm.family == "?" and lengths(t) == [601, 1437, 1437]
    assert phases(t.waves[1]) == ["pre", "pre", "post", "post"]

    t = parse_shorthand(corpus_by_id[26])  # ch4: I-PSDd/PSD|I-PSDd/PSD (18,18)
    assert t.waves[0].bottom is not None and not t.waves[0].mirrored
    assert lengths(t) == [1800, 1800]

    t = parse_shorthand(corpus_by_id[38])  # I6: I0P-ccP/PD(12s)
    assert lengths(t) == [1200] and t.waves[0].top.ops[0].variant == "ice0"

    t = parse_shorthand(corpus_by_id[41])  # name and track on separate lines
    assert t.rhythm.raw == "P6" and len(t.waves) == 6 and all(w.mirrored for w in t.waves)

    t = parse_shorthand(corpus_by_id[42])  # T4:P|P|P|P
    assert t.rhythm.family == "P" and lengths(t) == [None] * 4

    t = parse_shorthand(corpus_by_id[46])  # 1361×3 + trailing junk
    assert lengths(t) == [1361] * 3

    t = parse_shorthand(corpus_by_id[58])  # |I'-AA'|I-N|(25，25)
    assert len(t.waves) == 2 and lengths(t) == [2500, 2500]
    assert t.waves[0].top.ops[0].variant == "imitater"

    t = parse_shorthand(corpus_by_id[66])  # c5u with '+'
    assert t.rhythm.family == "Cu" and lengths(t) == [2030, 2030, 1250]
    assert phases(t.waves[2]) == ["pre", "post", "post"]

    t = parse_shorthand(corpus_by_id[76])  # AvZ bare tail
    assert lengths(t) == [1976, 1976, 750, 749]

    t = parse_shorthand(corpus_by_id[88])  # per-wave list
    assert t.waves[0].selector == (1,) and t.waves[-1].selector == (19,)
    assert any(w.selector == (9,) for w in t.waves)

    t = parse_shorthand(corpus_by_id[98])  # (20+|15+|6|9)
    assert lengths(t) == [2000, 1500, 600, 900] and t.meta["length_hint"][:2] == ["+", "+"]
    assert symbols(t.waves[0]) == ["a", "IO"]

    t = parse_shorthand(corpus_by_id[112])  # S3-io: I-|PSSN(AA'), (25|10)
    assert symbols(t.waves[0]) == ["I"] and phases(t.waves[0]) == ["pre"]
    assert symbols(t.waves[1]) == ["P", "SS", "N"] and lengths(t) == [2500, 1000]

    t = parse_shorthand(corpus_by_id[138])  # (1560连续冰波)
    assert lengths(t)[0] == 1560


# --- review regressions -----------------------------------------------------
def test_wave_prefix_after_bar():
    t = parse_shorthand("w1:PP|w2:I-PP")
    assert [w.selector for w in t.waves] == [(1,), (2,)]
    assert [op.symbol for op in t.waves[1].top.ops] == ["I", "PP"]


def test_io_alias_glued_to_symbols():
    t = parse_shorthand("ioPP|PPio")
    assert [[op.symbol for op in w.top.ops] for w in t.waves] == [["IO", "PP"], ["PP", "IO"]]


def test_single_wave_ops_with_family_letter_is_not_a_rhythm_name():
    t = parse_shorthand("I3PP 1976")
    assert t.rhythm is None and len(t.waves) == 1 and lengths(t) == [1976]
    assert [op.symbol for op in t.waves[0].top.ops] == ["I3", "PP"]
    # a real rhythm name followed by ops still splits
    assert parse_shorthand("P6 PP|PP (6|6)").rhythm.family == "P"


@pytest.mark.parametrize("cs", [50, 100, 600, 601, 10000, 10100, 12000])
def test_dump_length_round_trips(cs):
    t = parse_shorthand("PP|PP")
    t = replace(t, waves=tuple(replace(w, length_cs=cs) for w in t.waves))
    assert lengths(parse_shorthand(dump_shorthand(t))) == [cs, cs]


def test_normalize_expands_mirrored_waves():
    t = parse_shorthand("P|I-P|I-P|N").normalize()
    assert not any(w.mirrored for w in t.waves)
    assert dump_shorthand(t) == "PP|I-PP|I-PP|N"
    assert [op.al for op in t.waves[0].top.ops] == [2]
    # already-复写 tracks are unchanged
    t2 = parse_shorthand("PP|I-PP|N (6|21|9)")
    assert t2.normalize() == t2


def test_track_to_dict_matches_jsonio():
    from pvztools.schema.track.jsonio import from_dict, to_dict

    t = parse_shorthand("P2: PP|PP (6|6)")
    assert t.to_dict() == to_dict(t) and from_dict(t.to_dict()) == t
