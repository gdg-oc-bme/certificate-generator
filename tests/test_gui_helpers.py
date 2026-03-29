from GUI import (
    validate_date,
    is_name_present,
    suggest_output_folder_name,
    find_duplicate_names,
    find_long_names,
)


def test_suggest_output_folder_name_without_valid_date():
    result = suggest_output_folder_name("Hardware Workshop", "2026-03-27")
    assert result == "Hardware Workshop"


def test_suggest_output_folder_name_normalizes_spaces():
    result = suggest_output_folder_name("  Hardware    Workshop  ", "27/03/2026")
    assert result == "Hardware Workshop - 2026-03-27"


def test_find_duplicate_names_returns_empty_when_none():
    result = find_duplicate_names(["John Doe", "Jane Smith"])
    assert result == []


def test_find_duplicate_names_sorts_by_count_descending():
    names = ["A", "B", "A", "C", "B", "B"]
    result = find_duplicate_names(names)
    assert result[0] == ("B", 3)
    assert result[1] == ("A", 2)


def test_find_long_names_flags_by_word_count():
    names = ["Sam Taylor", "Christopher Jonathan Montgomery Parker"]
    result = find_long_names(names, max_chars=100, max_words=3)
    assert "Christopher Jonathan Montgomery Parker" in result
    assert "Sam Taylor" not in result


def test_find_long_names_returns_empty_when_none():
    names = ["Sam Taylor", "Jane Smith"]
    result = find_long_names(names)
    assert result == []


def test_validate_date_accepts_valid_date():
    assert validate_date("27/03/2026") is True


def test_validate_date_rejects_invalid_format():
    assert validate_date("2026-03-27") is False


def test_validate_date_rejects_impossible_date():
    assert validate_date("31/02/2026") is False


def test_is_name_present_true_for_valid_names():
    assert is_name_present("John", "Doe") is True


def test_is_name_present_false_for_missing_first_name():
    assert is_name_present("", "Doe") is False


def test_is_name_present_false_for_missing_last_name():
    assert is_name_present("John", "") is False


def test_suggest_output_folder_name_formats_date():
    result = suggest_output_folder_name("Hardware Meets Software Workshop", "27/03/2026")
    assert result == "Hardware Meets Software Workshop - 2026-03-27"


def test_find_duplicate_names_detects_duplicates():
    names = ["John Doe", "Jane Smith", "John Doe"]
    result = find_duplicate_names(names)
    assert ("John Doe", 2) in result


def test_find_long_names_flags_long_name():
    names = ["Sam Taylor", "Christopher Jonathan Montgomery"]
    result = find_long_names(names)
    assert "Christopher Jonathan Montgomery" in result
    assert "Sam Taylor" not in result