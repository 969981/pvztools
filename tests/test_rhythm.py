"""Tests for pvztools.schema.track.rhythm (docs/track-format.md §3.2)."""
import pytest

from pvztools.schema.track.rhythm import looks_like_rhythm_name, parse_rhythm_name


@pytest.mark.parametrize(
    "text, family, param, prefix, suffixes",
    [
        ("P6", "P", 6, "", ()),
        ("P5u", "P", 5, "", ("u",)),
        ("ch4", "ch", 4, "", ()),
        ("Ch4", "ch", 4, "", ()),
        ("CH4", "ch", 4, "", ()),
        ("ch5", "ch", 5, "", ()),
        ("c5u", "Cu", 5, "", ()),
        ("C5u", "Cu", 5, "", ()),
        ("邻C6u", "Cu", 6, "邻", ()),
        ("邻c6u", "Cu", 6, "邻", ()),
        ("对ch6", "ch", 6, "对", ()),
        ("间C7u", "Cu", 7, "间", ()),
        ("精准C3", "C", 3, "精准", ()),
        ("双冰C2", "C", 2, "双冰", ()),
        ("邻双冰C3", "C", 3, "邻双冰", ()),
        ("C6i", "C", 6, "", ("i",)),
        ("C6i*", "C", 6, "", ("i", "*")),
        ("ch4*", "ch", 4, "", ("*",)),
        ("C7u*", "Cu", 7, "", ("*",)),
        ("I6i", "I", 6, "", ("i",)),
        ("I6b", "I", 6, "", ("b",)),
        ("S6", "S", 6, "", ()),
        ("P6-2I", "P", 6, "", ("-2I",)),
        ("P6-N", "P", 6, "", ("-N",)),
        ("ch4-N", "ch", 4, "", ("-N",)),
        ("C4-io", "C", 4, "", ("-io",)),
        ("C4-IO", "C", 4, "", ("-io",)),
        ("C2io", "C", 2, "", ("io",)),
        ("S3-io", "S", 3, "", ("-io",)),
        ("S6-c", "S", 6, "", ("-c",)),
        ("邻C6u-54s", "Cu", 6, "邻", ("-54s",)),
        ("T4", "P", 4, "", ()),
        ("对 N6", "?", 6, "对", ()),
        ("D6", "?", 6, "", ()),
    ],
)
def test_parse_rhythm_name(text, family, param, prefix, suffixes):
    r = parse_rhythm_name(text)
    assert (r.family, r.param, r.prefix, r.suffixes) == (family, param, prefix, suffixes)
    assert r.raw == text.strip()


def test_unknown_never_raises():
    for junk in ("", "???", "瞬杀二十二炮阵解", "123", "-"):
        r = parse_rhythm_name(junk)
        assert r.family == "?"
        assert r.raw == junk.strip()


def test_looks_like_rhythm_name():
    assert looks_like_rhythm_name("P6")
    assert looks_like_rhythm_name("邻c6u")
    assert looks_like_rhythm_name("ch4-N")
    assert looks_like_rhythm_name("对 N6")
    assert not looks_like_rhythm_name("PP|PP")
    assert not looks_like_rhythm_name("PP")
    assert not looks_like_rhythm_name("w1")
    assert not looks_like_rhythm_name("I3PD-P|I3PD-P")
