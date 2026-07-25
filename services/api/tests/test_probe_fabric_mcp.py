from scripts.probe_fabric_mcp import missing_expected_evidence


def test_probe_accepts_iso_dates() -> None:
    answer = "PT-1042 latest CrCl 48 on 2026-06-18; prior CrCl 55 on 2026-05-20."

    assert missing_expected_evidence(answer) == []


def test_probe_accepts_fabric_us_dates() -> None:
    answer = "PT-1042 latest CrCl 48 on 6/18/2026; prior CrCl 55 on 5/20/2026."

    assert missing_expected_evidence(answer) == []


def test_probe_reports_missing_canonical_evidence() -> None:
    answer = "PT-1042 latest CrCl 48; prior CrCl 55."

    assert missing_expected_evidence(answer) == ["2026-06-18", "2026-05-20"]
