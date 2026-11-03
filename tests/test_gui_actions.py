from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
import GUI


def configure_batch(monkeypatch, folder, names):
    from unittest.mock import Mock
    monkeypatch.setattr(GUI, "_failed_generation_folders", set())
    monkeypatch.setattr(GUI, "select_output_directory", lambda *a: str(folder))
    monkeypatch.setattr(GUI, "show_pre_generation_warnings", Mock())
    monkeypatch.setattr(GUI, "open_folder", Mock())
    report = dict(total_rows=len(names), eligible_count=len(names), accepted_count=len(names),
                  skipped_count=0, skipped_not_checked_in=0, skipped_missing_name=0,
                  accepted_csv_rows=list(range(2, len(names) + 2)))
    monkeypatch.setattr(GUI, "read_names_from_file", lambda *a, **k: (names, report))
    error, info = Mock(), Mock()
    monkeypatch.setattr(GUI.messagebox, "showerror", error)
    monkeypatch.setattr(GUI.messagebox, "showinfo", info)
    return error, info


def run_batch_ui():
    progress = DummyVar()
    GUI.generate_certificates("synthetic.csv", DummyEntry(""), DummyEntry(""),
                              DummyVar(), progress, DummyProgressbar(), DummyRoot())
    return progress


def test_layout_failure_has_generation_context_and_blocks_distribution(monkeypatch, tmp_path):
    error, info = configure_batch(monkeypatch, tmp_path, ["Alex Johnson", "W" * 200, "Jane Smith"])
    auth, send, save = Mock(), Mock(), Mock()
    monkeypatch.setattr(GUI, "ensure_connected_gmail_account", auth)
    monkeypatch.setattr(GUI, "send_matched_certificates", send)
    monkeypatch.setattr(GUI.ce, "_save_certificate_outputs", save)
    progress = run_batch_ui()
    assert error.call_args.args[0] == "Certificate Generation Error"
    message = error.call_args.args[1]
    assert "participant 2" in message and "CSV row 3" in message
    assert "generation did not begin" in message and "Correct or shorten" in message
    assert progress.get() == "Generation failed"
    info.assert_not_called()
    save.assert_not_called()
    auth.assert_not_called()
    send.assert_not_called()
    assert not GUI._allow_distribution(str(tmp_path))
    assert not GUI._allow_distribution(str(tmp_path / "subfolder"))


def test_successful_retry_releases_failed_folder_guard(monkeypatch, tmp_path):
    _, info = configure_batch(monkeypatch, tmp_path, ["Alex Johnson"])
    GUI._failed_generation_folders.add(GUI._generation_folder_key(str(tmp_path)))
    monkeypatch.setattr(GUI.ce, "edit_certificate", lambda *a, **k: ["synthetic.jpg"])
    assert run_batch_ui().get() == "Generation complete"
    info.assert_called_once()
    assert GUI._allow_distribution(str(tmp_path))


@pytest.mark.parametrize("exception", [ValueError("renderer validation"), OSError("disk error")])
def test_generation_errors_are_not_csv_errors(monkeypatch, tmp_path, exception):
    error, info = configure_batch(monkeypatch, tmp_path, ["Alex Johnson"])
    monkeypatch.setattr(GUI.ce, "edit_certificate", Mock(side_effect=exception))
    run_batch_ui()
    assert error.call_args.args[0] == "Generation Error"
    assert str(exception) in error.call_args.args[1]
    info.assert_not_called()
    assert not GUI._allow_distribution(str(tmp_path))


def test_failed_folder_cannot_be_uploaded(monkeypatch, tmp_path):
    configure_batch(monkeypatch, tmp_path, ["Alex Johnson"])
    GUI._failed_generation_folders.add(GUI._generation_folder_key(str(tmp_path)))
    upload = Mock()
    monkeypatch.setattr(GUI, "select_upload_path", lambda: str(tmp_path))
    monkeypatch.setattr(GUI, "Upload", upload)
    GUI.upload_to_drive()
    upload.assert_not_called()


@pytest.mark.parametrize("choose_root", [False, True])
def test_recursive_upload_of_parent_cannot_include_a_failed_batch(monkeypatch, tmp_path, choose_root):
    configure_batch(monkeypatch, tmp_path, ["Alex Johnson"])
    failed = tmp_path / "failed-batch"
    GUI._failed_generation_folders.add(GUI._generation_folder_key(str(failed)))
    upload = Mock()
    selected = tmp_path.anchor if choose_root else str(tmp_path)
    monkeypatch.setattr(GUI, "select_upload_path", lambda: selected)
    monkeypatch.setattr(GUI, "Upload", upload)
    GUI.upload_to_drive()
    upload.assert_not_called()


def test_failed_folder_distribution_preview_stops_before_matching(monkeypatch, tmp_path):
    configure_batch(monkeypatch, tmp_path, ["Alex Johnson"])
    GUI._failed_generation_folders.add(GUI._generation_folder_key(str(tmp_path)))
    widgets = MagicMock()

    def variable(value=""):
        var = DummyVar()
        var.set(value)
        return var

    widgets.StringVar.side_effect = variable
    monkeypatch.setattr(GUI, "tk", widgets)
    monkeypatch.setattr(GUI, "ttk", MagicMock())
    monkeypatch.setattr(GUI, "CanvasProgressBar", MagicMock())
    monkeypatch.setattr(GUI, "select_csv_file", lambda: "synthetic.csv")
    monkeypatch.setattr(GUI, "select_certificates_folder", lambda: str(tmp_path))
    read, auth = Mock(), Mock()
    monkeypatch.setattr(GUI, "read_recipients_from_file", read)
    monkeypatch.setattr(GUI, "ensure_connected_gmail_account", auth)
    GUI.setup_gui()
    commands = {call.kwargs["text"]: call.kwargs["command"]
                for call in widgets.Button.call_args_list}
    commands["Select CSV File"]()
    commands["Preview Email Distribution"]()
    read.assert_not_called()
    auth.assert_not_called()


def test_csv_row_context_survives_eligibility_filtering(tmp_path):
    import pandas as pd
    csv = tmp_path / "synthetic.csv"
    pd.DataFrame([
        {"First Name": "Alex", "Last Name": "Johnson", "Checkin Date (UTC)": "2026-03-27"},
        {"First Name": "Not", "Last Name": "Checked", "Checkin Date (UTC)": ""},
        {"First Name": "Jane", "Last Name": "Smith", "Checkin Date (UTC)": "2026-03-27"},
    ]).to_csv(csv, index=False)
    names, report = GUI.read_names_from_file(str(csv))
    assert names == ["Alex Johnson", "Jane Smith"]
    assert report["accepted_csv_rows"] == [2, 4]


def test_already_open_email_dialog_rechecks_failed_generation_guard(monkeypatch, tmp_path):
    configure_batch(monkeypatch, tmp_path, ["Alex Johnson"])
    widgets = MagicMock()
    widgets.Entry.return_value.get.return_value = ""

    def variable(value=""):
        var = DummyVar()
        var.set(value)
        return var

    widgets.StringVar.side_effect = variable
    monkeypatch.setattr(GUI, "tk", widgets)
    monkeypatch.setattr(GUI, "ttk", MagicMock())
    monkeypatch.setattr(GUI, "scrolledtext", MagicMock())
    monkeypatch.setattr(GUI, "CanvasProgressBar", MagicMock())
    monkeypatch.setattr(GUI, "select_csv_file", lambda: "synthetic.csv")
    monkeypatch.setattr(GUI, "select_certificates_folder", lambda: str(tmp_path))
    recipient_report = dict(total_rows=1, eligible_count=1, recipients_count=1,
                            skipped_not_checked_in=0, skipped_missing_name=0, skipped_missing_email=0)
    match_report = dict(matched_count=1, missing_certificate_count=0,
                       ambiguous_certificate_count=0, certificate_files_found=1)
    matches = [dict(full_name="Alex Johnson", email="alex@example.com", status="matched",
                    certificate_path=str(tmp_path / "Alex Johnson.jpg"))]
    monkeypatch.setattr(GUI, "read_recipients_from_file", Mock(return_value=([], recipient_report)))
    monkeypatch.setattr(GUI, "match_certificates_to_recipients", Mock(return_value=(matches, match_report)))
    monkeypatch.setattr(GUI, "export_distribution_report", Mock(return_value="synthetic.csv"))
    monkeypatch.setattr(GUI, "load_email_draft", Mock(return_value=None))
    auth, send = Mock(), Mock()
    monkeypatch.setattr(GUI, "ensure_connected_gmail_account", auth)
    monkeypatch.setattr(GUI, "send_matched_certificates", send)
    GUI.setup_gui()

    def commands():
        return {call.kwargs["text"]: call.kwargs["command"]
                for call in widgets.Button.call_args_list}

    commands()["Select CSV File"]()
    commands()["Preview Email Distribution"]()
    # The dialog was prepared while the folder was allowed; a later batch fails.
    GUI._failed_generation_folders.add(GUI._generation_folder_key(str(tmp_path)))
    commands()["Send Matched Emails"]()
    auth.assert_not_called()
    send.assert_not_called()


def test_open_folder_does_not_crash_if_startfile_fails(monkeypatch):
    def fake_startfile(path):
        raise OSError("cannot open")

    monkeypatch.setattr(GUI.os, "startfile", fake_startfile, raising=False)

    GUI.open_folder("C:/temp")


def test_open_file_does_not_crash_if_startfile_fails(monkeypatch):
    def fake_startfile(path):
        raise OSError("cannot open")

    monkeypatch.setattr(GUI.os, "startfile", fake_startfile, raising=False)

    GUI.open_file("C:/temp/file.jpg")


def test_generate_single_certificate_returns_if_user_cancels_output_dir(monkeypatch):
    participant = DummyEntry("John Doe")
    event_title = DummyEntry("Workshop")
    event_date = DummyEntry("27/03/2026")
    progress_var = DummyVar()
    progress_bar = DummyProgressbar()
    root = DummyRoot()

    monkeypatch.setattr(GUI, "select_output_directory", lambda title, date: None)

    called = {"edit": False}

    def fake_edit_certificate(*args, **kwargs):
        called["edit"] = True

    monkeypatch.setattr(GUI.ce, "edit_certificate", fake_edit_certificate)

    GUI.generate_single_certificate(
        participant, event_title, event_date, progress_var, progress_bar, root
    )

    assert called["edit"] is False


def test_generate_certificates_returns_if_user_cancels_output_dir(monkeypatch):
    event_title = DummyEntry("Workshop")
    event_date = DummyEntry("27/03/2026")
    eligibility_var = DummyVar()
    eligibility_var.set("Checked-in only")
    progress_var = DummyVar()
    progress_bar = DummyProgressbar()
    root = DummyRoot()

    monkeypatch.setattr(GUI, "select_output_directory", lambda title, date: None)

    called = {"read": False}

    def fake_read_names_from_file(*args, **kwargs):
        called["read"] = True
        return [], {}

    monkeypatch.setattr(GUI, "read_names_from_file", fake_read_names_from_file)

    GUI.generate_certificates(
        "dummy.csv",
        event_title,
        event_date,
        eligibility_var,
        progress_var,
        progress_bar,
        root,
    )

    assert called["read"] is False


def test_generate_certificates_handles_value_error(monkeypatch, tmp_path):
    event_title = DummyEntry("Workshop")
    event_date = DummyEntry("27/03/2026")
    eligibility_var = DummyVar()
    eligibility_var.set("Checked-in only")
    progress_var = DummyVar()
    progress_bar = DummyProgressbar()
    root = DummyRoot()

    monkeypatch.setattr(GUI, "select_output_directory", lambda title, date: str(tmp_path))

    def fake_read_names_from_file(*args, **kwargs):
        raise ValueError("bad csv")

    monkeypatch.setattr(GUI, "read_names_from_file", fake_read_names_from_file)

    called = {}

    def fake_showerror(title, msg):
        called["title"] = title
        called["msg"] = msg

    monkeypatch.setattr(GUI.messagebox, "showerror", fake_showerror)

    GUI.generate_certificates(
        "dummy.csv",
        event_title,
        event_date,
        eligibility_var,
        progress_var,
        progress_bar,
        root,
    )

    assert called["title"] == "CSV Error"
    assert progress_var.get() == "CSV error"


def test_generate_single_certificate_handles_exception(monkeypatch, tmp_path):
    participant = DummyEntry("John Doe")
    event_title = DummyEntry("Workshop")
    event_date = DummyEntry("27/03/2026")
    progress_var = DummyVar()
    progress_bar = DummyProgressbar()
    root = DummyRoot()

    monkeypatch.setattr(GUI, "select_output_directory", lambda title, date: str(tmp_path))

    def fake_edit_certificate(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(GUI.ce, "edit_certificate", fake_edit_certificate)

    called = {}

    def fake_showerror(title, msg):
        called["title"] = title
        called["msg"] = msg

    monkeypatch.setattr(GUI.messagebox, "showerror", fake_showerror)

    GUI.generate_single_certificate(
        participant, event_title, event_date, progress_var, progress_bar, root
    )

    assert called["title"] == "Generation failed"
    assert progress_var.get() == "Generation failed"


def test_preview_single_certificate_shows_error_if_name_missing(monkeypatch):
    participant = DummyEntry("")
    event_title = DummyEntry("Workshop")
    event_date = DummyEntry("27/03/2026")
    progress_var = DummyVar()
    progress_bar = DummyProgressbar()
    root = DummyRoot()

    called = {}

    def fake_showerror(title, msg):
        called["title"] = title

    monkeypatch.setattr(GUI.messagebox, "showerror", fake_showerror)

    GUI.preview_single_certificate(
        participant, event_title, event_date, progress_var, progress_bar, root
    )

    assert called["title"] == "Missing participant name"


def test_preview_single_certificate_success(monkeypatch, tmp_path):
    participant = DummyEntry("John Doe")
    event_title = DummyEntry("Workshop")
    event_date = DummyEntry("27/03/2026")
    progress_var = DummyVar()
    progress_bar = DummyProgressbar()
    root = DummyRoot()

    preview_file = tmp_path / "preview.jpg"
    preview_file.write_text("dummy")

    monkeypatch.setattr(GUI.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(GUI, "open_file", lambda path: None)
    monkeypatch.setattr(GUI.ce, "preview_certificate", lambda *args, **kwargs: str(preview_file))

    called = {}

    def fake_showinfo(title, msg):
        called["title"] = title

    monkeypatch.setattr(GUI.messagebox, "showinfo", fake_showinfo)

    GUI.preview_single_certificate(
        participant, event_title, event_date, progress_var, progress_bar, root
    )

    assert called["title"] == "Preview ready"
    assert progress_var.get() == "Preview ready"

class DummyEntry:
    def __init__(self, value=""):
        self.value = value
        self.deleted = False
        self.focused = False

    def get(self):
        return self.value

    def delete(self, start, end):
        self.value = ""
        self.deleted = True

    def insert(self, index, value):
        self.value = value

    def focus_set(self):
        self.focused = True


class DummyVar:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class DummyProgressbar(dict):
    pass


class DummyRoot:
    def __init__(self):
        self.updated = False

    def update_idletasks(self):
        self.updated = True


def test_select_output_directory_returns_none_when_user_cancels(monkeypatch):
    monkeypatch.setattr(GUI.filedialog, "askdirectory", lambda title=None: "")
    result = GUI.select_output_directory("Workshop", "27/03/2026")
    assert result is None


def test_select_output_directory_creates_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(GUI.filedialog, "askdirectory", lambda title=None: str(tmp_path))

    result = GUI.select_output_directory("Hardware Workshop", "27/03/2026")

    expected = tmp_path / "Hardware Workshop - 2026-03-27"
    assert result == str(expected)
    assert expected.exists()


def test_open_folder_calls_startfile(monkeypatch):
    called = {}

    def fake_startfile(path):
        called["path"] = path

    monkeypatch.setattr(GUI.os, "startfile", fake_startfile, raising=False)
    GUI.open_folder("C:/temp")
    assert called["path"] == "C:/temp"


def test_open_file_calls_startfile(monkeypatch):
    called = {}

    def fake_startfile(path):
        called["path"] = path

    monkeypatch.setattr(GUI.os, "startfile", fake_startfile, raising=False)
    GUI.open_file("C:/temp/file.jpg")
    assert called["path"] == "C:/temp/file.jpg"


@pytest.mark.parametrize("event_title,event_date", [("Workshop", "27/03/2026"), ("", "")])
def test_generate_single_certificate_shows_error_if_name_missing(monkeypatch, event_title, event_date):
    participant = DummyEntry("")
    event_title = DummyEntry(event_title)
    event_date = DummyEntry(event_date)
    progress_var = DummyVar()
    progress_bar = DummyProgressbar()
    root = DummyRoot()

    called = {}

    def fake_showerror(title, msg):
        called["title"] = title
        called["msg"] = msg

    monkeypatch.setattr(GUI.messagebox, "showerror", fake_showerror)

    GUI.generate_single_certificate(
        participant, event_title, event_date, progress_var, progress_bar, root
    )

    assert called["title"] == "Missing participant name"


@pytest.mark.parametrize("action_name", ["generate_single_certificate", "preview_single_certificate"])
@pytest.mark.parametrize("event_title", ["", "Workshop"])
@pytest.mark.parametrize("event_date", ["", "27/03/2026", "29/02/2024"])
def test_single_actions_accept_optional_event_fields(
    monkeypatch, tmp_path, action_name, event_title, event_date
):
    monkeypatch.setattr(GUI, "select_output_directory", lambda title, date: str(tmp_path))
    monkeypatch.setattr(GUI.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(GUI, "open_folder", lambda path: None)
    monkeypatch.setattr(GUI, "open_file", lambda path: None)
    edit = Mock()
    preview = Mock(return_value=str(tmp_path / "preview.jpg"))
    error = Mock()
    monkeypatch.setattr(GUI.ce, "edit_certificate", edit)
    monkeypatch.setattr(GUI.ce, "preview_certificate", preview)
    monkeypatch.setattr(GUI.messagebox, "showerror", error)
    monkeypatch.setattr(GUI.messagebox, "showinfo", Mock())
    progress = DummyVar()

    getattr(GUI, action_name)(
        DummyEntry("John Doe"), DummyEntry(event_title), DummyEntry(event_date),
        progress, DummyProgressbar(), DummyRoot(),
    )

    renderer = edit if action_name == "generate_single_certificate" else preview
    renderer.assert_called_once()
    assert renderer.call_args.args[2:4] == (event_title, event_date)
    assert progress.get() == ("Generation complete" if renderer is edit else "Preview ready")
    error.assert_not_called()


@pytest.mark.parametrize("action_name", ["generate_single_certificate", "preview_single_certificate"])
@pytest.mark.parametrize("event_title", ["", "Workshop"])
@pytest.mark.parametrize("event_date", ["2026-03-27", "31/02/2026", "not a date"])
def test_single_actions_reject_invalid_nonempty_dates(
    monkeypatch, action_name, event_title, event_date
):
    error, output_directory, edit, preview = Mock(), Mock(), Mock(), Mock()
    monkeypatch.setattr(GUI.messagebox, "showerror", error)
    monkeypatch.setattr(GUI, "select_output_directory", output_directory)
    monkeypatch.setattr(GUI.ce, "edit_certificate", edit)
    monkeypatch.setattr(GUI.ce, "preview_certificate", preview)

    getattr(GUI, action_name)(
        DummyEntry("John Doe"), DummyEntry(event_title), DummyEntry(event_date),
        DummyVar(), DummyProgressbar(), DummyRoot(),
    )

    error.assert_called_once_with(
        "Invalid date", "Please enter a valid date in the format DD/MM/YYYY"
    )
    output_directory.assert_not_called()
    edit.assert_not_called()
    preview.assert_not_called()



def test_generate_single_certificate_shows_error_if_date_invalid(monkeypatch):
    participant = DummyEntry("John Doe")
    event_title = DummyEntry("Workshop")
    event_date = DummyEntry("2026-03-27")
    progress_var = DummyVar()
    progress_bar = DummyProgressbar()
    root = DummyRoot()

    called = {}

    def fake_showerror(title, msg):
        called["title"] = title

    monkeypatch.setattr(GUI.messagebox, "showerror", fake_showerror)

    GUI.generate_single_certificate(
        participant, event_title, event_date, progress_var, progress_bar, root
    )

    assert called["title"] == "Invalid date"


def test_generate_single_certificate_success(monkeypatch, tmp_path):
    participant = DummyEntry("John Doe")
    event_title = DummyEntry("Workshop")
    event_date = DummyEntry("27/03/2026")
    progress_var = DummyVar()
    progress_bar = DummyProgressbar()
    root = DummyRoot()

    monkeypatch.setattr(GUI, "select_output_directory", lambda title, date: str(tmp_path))
    monkeypatch.setattr(GUI, "open_folder", lambda path: None)

    info_called = {}

    def fake_showinfo(title, msg):
        info_called["title"] = title
        info_called["msg"] = msg

    monkeypatch.setattr(GUI.messagebox, "showinfo", fake_showinfo)

    called = {}

    def fake_edit_certificate(template_path, attendees_list, eventTitle, eventDate, name_font_path, regular_font_path, save_dir, progress_callback=None):
        called["template_path"] = template_path
        called["attendees_list"] = attendees_list
        called["eventTitle"] = eventTitle
        called["eventDate"] = eventDate
        called["save_dir"] = save_dir
        if progress_callback:
            progress_callback(1, 1, attendees_list[0])

    monkeypatch.setattr(GUI.ce, "edit_certificate", fake_edit_certificate)

    GUI.generate_single_certificate(
        participant, event_title, event_date, progress_var, progress_bar, root
    )

    assert called["attendees_list"] == ["John Doe"]
    assert called["eventTitle"] == "Workshop"
    assert called["eventDate"] == "27/03/2026"
    assert participant.value == ""
    assert participant.deleted is True
    assert participant.focused is True
    assert progress_var.get() == "Generation complete"
    assert info_called["title"] == "Generation complete"


def test_generate_certificates_warns_if_no_attendees(monkeypatch, tmp_path):
    event_title = DummyEntry("Workshop")
    event_date = DummyEntry("27/03/2026")
    eligibility_var = DummyVar()
    eligibility_var.set("Checked-in only")
    progress_var = DummyVar()
    progress_bar = DummyProgressbar()
    root = DummyRoot()

    monkeypatch.setattr(GUI, "select_output_directory", lambda title, date: str(tmp_path))
    monkeypatch.setattr(
        GUI,
        "read_names_from_file",
        lambda filepath, checked_in_only=True, export_skipped=False: ([], {"total_rows": 0})
    )

    called = {}

    def fake_showwarning(title, msg):
        called["title"] = title

    monkeypatch.setattr(GUI.messagebox, "showwarning", fake_showwarning)

    GUI.generate_certificates(
        "dummy.csv",
        event_title,
        event_date,
        eligibility_var,
        progress_var,
        progress_bar,
        root,
    )

    assert called["title"] == "No certificates generated"


def test_generate_certificates_success(monkeypatch, tmp_path):
    event_title = DummyEntry("Workshop")
    event_date = DummyEntry("27/03/2026")
    eligibility_var = DummyVar()
    eligibility_var.set("Checked-in only")
    progress_var = DummyVar()
    progress_bar = DummyProgressbar()
    root = DummyRoot()

    monkeypatch.setattr(GUI, "select_output_directory", lambda title, date: str(tmp_path))
    monkeypatch.setattr(GUI, "show_pre_generation_warnings", lambda names: None)
    monkeypatch.setattr(GUI, "open_folder", lambda path: None)

    report = {
        "total_rows": 3,
        "eligible_count": 2,
        "accepted_count": 2,
        "skipped_count": 1,
        "skipped_not_checked_in": 1,
        "skipped_missing_name": 0,
    }

    monkeypatch.setattr(
        GUI,
        "read_names_from_file",
        lambda filepath, checked_in_only=True, export_skipped=False: (
            ["John Doe", "Jane Smith"],
            report,
        ),
    )

    called = {}

    def fake_edit_certificate(template_path, attendees_list, eventTitle, eventDate, name_font_path, regular_font_path, save_dir, progress_callback=None):
        called["attendees_list"] = attendees_list
        if progress_callback:
            progress_callback(1, 2, "John Doe")
            progress_callback(2, 2, "Jane Smith")

    monkeypatch.setattr(GUI.ce, "edit_certificate", fake_edit_certificate)

    info_called = {}

    def fake_showinfo(title, msg):
        info_called["title"] = title
        info_called["msg"] = msg

    monkeypatch.setattr(GUI.messagebox, "showinfo", fake_showinfo)

    GUI.generate_certificates(
        "dummy.csv",
        event_title,
        event_date,
        eligibility_var,
        progress_var,
        progress_bar,
        root,
    )

    assert called["attendees_list"] == ["John Doe", "Jane Smith"]
    assert progress_var.get() == "Generation complete"
    assert info_called["title"] == "Generation complete"

@pytest.mark.parametrize("event_title", ["", "Workshop"])
@pytest.mark.parametrize("event_date,accepted", [
    ("", True), ("27/03/2026", True), ("29/02/2024", True),
    ("2026-03-27", False), ("31/02/2026", False), ("not a date", False),
])
def test_batch_button_validates_optional_event_date(monkeypatch, event_title, event_date, accepted):
    # Build the real button callbacks with mocked widgets, without opening a window.
    widgets = MagicMock()
    widgets.Entry.side_effect = [
        Mock(get=lambda: event_title), Mock(get=lambda: event_date), Mock(),
    ]

    def string_var(value=""):
        variable = DummyVar()
        variable.set(value)
        return variable

    widgets.StringVar.side_effect = string_var
    monkeypatch.setattr(GUI, "tk", widgets)
    monkeypatch.setattr(GUI, "ttk", MagicMock())
    monkeypatch.setattr(GUI, "CanvasProgressBar", MagicMock())
    monkeypatch.setattr(GUI, "select_csv_file", lambda: "attendees.csv")
    generate, error = Mock(), Mock()
    monkeypatch.setattr(GUI, "generate_certificates", generate)
    monkeypatch.setattr(GUI.messagebox, "showerror", error)

    GUI.setup_gui()
    commands = {call.kwargs["text"]: call.kwargs["command"] for call in widgets.Button.call_args_list}
    commands["Select CSV File"]()
    commands["Generate Certificates"]()

    if accepted:
        generate.assert_called_once()
        assert generate.call_args.args[0] == "attendees.csv"
        assert generate.call_args.args[1].get() == event_title
        assert generate.call_args.args[2].get() == event_date
        error.assert_not_called()
    else:
        generate.assert_not_called()
        error.assert_called_once_with(
            "Invalid date", "Please enter a valid date in the format DD/MM/YYYY"
        )
