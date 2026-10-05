from datetime import date

from pigtail.domain.confidence import CausalAttribution, cap_temporal_attribution
from pigtail.domain.ids import is_uuid7, new_id, uuid7
from pigtail.domain.time import human_label, range_from_partial


def test_uuid7_format_and_order():
    ids = [new_id() for _ in range(2000)]
    assert all(is_uuid7(i) for i in ids)
    assert len(set(ids)) == len(ids)
    assert ids == sorted(ids)
    assert uuid7().version == 7


def test_range_precision_is_never_invented():
    assert range_from_partial("2021")["precision"] == "year"
    assert range_from_partial("2021-03")["precision"] == "month"
    assert range_from_partial("2021-03-04")["precision"] == "day"
    assert range_from_partial(None)["precision"] == "unknown"
    assert range_from_partial("sometime")["precision"] == "unknown"
    m = range_from_partial("2024-02")
    assert m["start"] == "2024-02-01T00:00:00Z" and m["end"] == "2024-02-29T23:59:59Z"


def test_human_label():
    assert human_label(range_from_partial("2021-03")) == "Mar 2021"
    assert human_label(range_from_partial("2021-03-04", label="early March")) == "early March"
    assert human_label(None) == "Date unknown"
    assert date(2021, 3, 4)


def test_temporal_attribution_cap():
    assert cap_temporal_attribution(CausalAttribution.directly_measured) == CausalAttribution.weakly_associated
    assert cap_temporal_attribution(CausalAttribution.unknown) == CausalAttribution.unknown
