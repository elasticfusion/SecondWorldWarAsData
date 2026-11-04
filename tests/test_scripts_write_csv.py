"""DRY P2.8 guard: the ~8 extract_* scripts' CSV writers now share scripts._common.write_dict_csv.
Pin that it is byte-identical to the old csv.DictWriter boilerplate, in both the extrasaction=ignore
(eisenhower) and strict (donovan/loc_vhp) flavors.
"""

import csv
import io

from scripts._common import write_dict_csv


def _old_write(path, fieldnames, rows, extrasaction):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as h:
        w = (
            csv.DictWriter(h, fieldnames=fieldnames, extrasaction=extrasaction)
            if extrasaction
            else csv.DictWriter(h, fieldnames=fieldnames)
        )
        w.writeheader()
        w.writerows(rows)


def test_byte_identical_ignore(tmp_path):
    fields = ["a", "b"]
    rows = [{"a": "1", "b": "2"}, {"a": "x", "b": "y"}]
    new = tmp_path / "new.csv"
    old = tmp_path / "old.csv"
    write_dict_csv(new, fields, rows, extrasaction="ignore")
    _old_write(old, fields, rows, "ignore")
    assert new.read_bytes() == old.read_bytes()


def test_ignore_drops_extra_key(tmp_path):
    # extrasaction='ignore' -> an unmodeled key is silently dropped (not raised)
    p = tmp_path / "x.csv"
    write_dict_csv(p, ["a"], [{"a": "1", "extra": "z"}], extrasaction="ignore")
    text = p.read_text(encoding="utf-8")
    assert "extra" not in text and "z" not in text  # dropped
    assert [ln for ln in text.splitlines() if ln] == ["a", "1"]  # header + value only


def test_strict_raises_on_extra_key(tmp_path):
    # extrasaction=None -> DictWriter strict default raises on an extra key (donovan/loc_vhp behavior)
    import pytest

    with pytest.raises(ValueError):
        write_dict_csv(
            tmp_path / "y.csv", ["a"], [{"a": "1", "extra": "z"}], extrasaction=None
        )


def test_creates_parent_dirs(tmp_path):
    p = tmp_path / "deep" / "nested" / "out.csv"
    write_dict_csv(p, ["a"], [{"a": "1"}])
    assert p.exists()
