from pathlib import Path
import GUI


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


def test_generate_single_certificate_shows_error_if_name_missing(monkeypatch):
    participant = DummyEntry("")
    event_title = DummyEntry("Workshop")
    event_date = DummyEntry("27/03/2026")
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


def test_generate_single_certificate_shows_error_if_event_title_missing(monkeypatch):
    participant = DummyEntry("John Doe")
    event_title = DummyEntry("")
    event_date = DummyEntry("27/03/2026")
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

    assert called["title"] == "Missing event title"


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