import pandas as pd
from GUI import read_names_from_file


def test_read_names_skips_missing_first_name(tmp_path):
    csv_path = tmp_path / "test_missing_first.csv"

    df = pd.DataFrame([
        {"First Name": "", "Last Name": "Doe", "Checkin Date (UTC)": "2026-03-27 10:00:00"},
        {"First Name": "Jane", "Last Name": "Smith", "Checkin Date (UTC)": "2026-03-27 10:05:00"},
    ])
    df.to_csv(csv_path, index=False)

    names, report = read_names_from_file(str(csv_path), checked_in_only=True, export_skipped=False)

    assert names == ["Jane Smith"]
    assert report["accepted_count"] == 1
    assert report["skipped_missing_name"] == 1


def test_read_names_skips_missing_last_name(tmp_path):
    csv_path = tmp_path / "test_missing_last.csv"

    df = pd.DataFrame([
        {"First Name": "John", "Last Name": "", "Checkin Date (UTC)": "2026-03-27 10:00:00"},
        {"First Name": "Jane", "Last Name": "Smith", "Checkin Date (UTC)": "2026-03-27 10:05:00"},
    ])
    df.to_csv(csv_path, index=False)

    names, report = read_names_from_file(str(csv_path), checked_in_only=True, export_skipped=False)

    assert names == ["Jane Smith"]
    assert report["accepted_count"] == 1
    assert report["skipped_missing_name"] == 1


def test_read_names_all_registrants_still_skips_missing_name(tmp_path):
    csv_path = tmp_path / "test_all_registrants_missing_name.csv"

    df = pd.DataFrame([
        {"First Name": "John", "Last Name": "Doe"},
        {"First Name": "", "Last Name": "Smith"},
    ])
    df.to_csv(csv_path, index=False)

    names, report = read_names_from_file(str(csv_path), checked_in_only=False, export_skipped=False)

    assert names == ["John Doe"]
    assert report["accepted_count"] == 1
    assert report["skipped_missing_name"] == 1


def test_read_names_checked_in_only_requires_checkin_column(tmp_path):
    csv_path = tmp_path / "missing_checkin.csv"

    df = pd.DataFrame([
        {"First Name": "John", "Last Name": "Doe"},
    ])
    df.to_csv(csv_path, index=False)

    try:
        read_names_from_file(str(csv_path), checked_in_only=True, export_skipped=False)
        assert False, "Expected ValueError"
    except ValueError as e:
        assert "Missing required CSV columns" in str(e)


def test_read_names_preserves_unicode_names(tmp_path):
    csv_path = tmp_path / "unicode_names.csv"

    df = pd.DataFrame([
        {"First Name": "Michael", "Last Name": "GÜNDOĞAN", "Checkin Date (UTC)": "2026-03-27 10:00:00"},
        {"First Name": "أحمد", "Last Name": "صالح", "Checkin Date (UTC)": "2026-03-27 10:05:00"},
    ])
    df.to_csv(csv_path, index=False)

    names, report = read_names_from_file(str(csv_path), checked_in_only=True, export_skipped=False)

    assert names == ["Michael GÜNDOĞAN", "أحمد صالح"]
    assert report["accepted_count"] == 2


def test_read_names_checked_in_only(tmp_path):
    csv_path = tmp_path / "test.csv"

    df = pd.DataFrame([
        {"First Name": "John", "Last Name": "Doe", "Checkin Date (UTC)": "2026-03-27 10:00:00"},
        {"First Name": "Jane", "Last Name": "Smith", "Checkin Date (UTC)": ""},
        {"First Name": "Michael", "Last Name": "GÜNDOĞAN", "Checkin Date (UTC)": "2026-03-27 10:05:00"},
    ])
    df.to_csv(csv_path, index=False)

    names, report = read_names_from_file(str(csv_path), checked_in_only=True, export_skipped=False)

    assert names == ["John Doe", "Michael GÜNDOĞAN"]
    assert report["accepted_count"] == 2
    assert report["skipped_not_checked_in"] == 1


def test_read_names_all_registrants(tmp_path):
    csv_path = tmp_path / "test.csv"

    df = pd.DataFrame([
        {"First Name": "John", "Last Name": "Doe", "Checkin Date (UTC)": "2026-03-27 10:00:00"},
        {"First Name": "Jane", "Last Name": "Smith", "Checkin Date (UTC)": ""},
    ])
    df.to_csv(csv_path, index=False)

    names, report = read_names_from_file(str(csv_path), checked_in_only=False, export_skipped=False)

    assert names == ["John Doe", "Jane Smith"]
    assert report["accepted_count"] == 2
    assert report["skipped_not_checked_in"] == 0


def test_read_names_missing_columns_raises_error(tmp_path):
    csv_path = tmp_path / "bad.csv"

    df = pd.DataFrame([
        {"Name": "John Doe"}
    ])
    df.to_csv(csv_path, index=False)

    try:
        read_names_from_file(str(csv_path), checked_in_only=True, export_skipped=False)
        assert False, "Expected ValueError"
    except ValueError as e:
        assert "Missing required CSV columns" in str(e)