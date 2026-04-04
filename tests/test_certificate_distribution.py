import base64
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pandas as pd
import pytest

import certificateDistribution as cd
from certificateDistribution import (
    GmailAuthError,
    get_connected_gmail_account,
    ensure_connected_gmail_account,
    read_recipients_from_file,
    match_certificates_to_recipients,
    export_distribution_report,
    split_already_sent_matches,
    save_email_draft,
    load_email_draft,
    render_email_template,
    get_gmail_service,
    send_email_with_attachment,
    export_email_send_report,
    make_send_key,
    send_matched_certificates,
)


class DummyRequest:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {}
        self.error = error

    def execute(self):
        if self.error:
            raise self.error
        return self.payload


class DummyUsersAPI:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {}
        self.error = error
        self.requested_user_id = None

    def getProfile(self, userId):
        self.requested_user_id = userId
        return DummyRequest(payload=self.payload, error=self.error)


class DummyService:
    def __init__(self, payload=None, error=None):
        self.users_api = DummyUsersAPI(payload=payload, error=error)

    def users(self):
        return self.users_api


def test_get_connected_gmail_account_returns_email():
    service = DummyService(payload={"emailAddress": "team@example.com"})

    result = get_connected_gmail_account(service)

    assert result == "team@example.com"
    assert service.users_api.requested_user_id == "me"


def test_get_connected_gmail_account_returns_none_when_missing_email():
    service = DummyService(payload={})

    result = get_connected_gmail_account(service)

    assert result is None


def test_get_connected_gmail_account_returns_none_when_profile_call_fails():
    service = DummyService(error=RuntimeError("boom"))

    result = get_connected_gmail_account(service)

    assert result is None


def test_ensure_connected_gmail_account_returns_email(monkeypatch):
    service = DummyService(payload={"emailAddress": "gdg@example.com"})

    def fake_get_gmail_service(client_secret_path, token_path):
        assert client_secret_path == "client_secret.json"
        assert token_path == "gmail_token.json"
        return service

    monkeypatch.setattr(cd, "get_gmail_service", fake_get_gmail_service)

    result = ensure_connected_gmail_account(
        client_secret_path="client_secret.json",
        token_path="gmail_token.json",
    )

    assert result == "gdg@example.com"


def test_ensure_connected_gmail_account_raises_when_email_unknown(monkeypatch):
    service = DummyService(payload={})

    monkeypatch.setattr(cd, "get_gmail_service", lambda *args, **kwargs: service)

    with pytest.raises(GmailAuthError):
        ensure_connected_gmail_account(
            client_secret_path="client_secret.json",
            token_path="gmail_token.json",
        )


def test_read_recipients_checked_in_only_counts_skips(tmp_path):
    csv_path = tmp_path / "recipients.csv"
    df = pd.DataFrame([
        {
            "First Name": "john",
            "Last Name": "doe",
            "Email": "john@example.com",
            "Checkin Date (UTC)": "2026-03-27 10:00:00",
        },
        {
            "First Name": "Jane",
            "Last Name": "Smith",
            "Email": "",
            "Checkin Date (UTC)": "2026-03-27 10:05:00",
        },
        {
            "First Name": "",
            "Last Name": "Brown",
            "Email": "brown@example.com",
            "Checkin Date (UTC)": "2026-03-27 10:10:00",
        },
        {
            "First Name": "Not",
            "Last Name": "Checked",
            "Email": "nope@example.com",
            "Checkin Date (UTC)": "",
        },
    ])
    df.to_csv(csv_path, index=False)

    recipients, report = read_recipients_from_file(str(csv_path), checked_in_only=True)

    assert recipients == [
        {
            "full_name": "John Doe",
            "email": "john@example.com",
            "row_number": 2,
        }
    ]
    assert report["total_rows"] == 4
    assert report["eligible_count"] == 3
    assert report["recipients_count"] == 1
    assert report["skipped_not_checked_in"] == 1
    assert report["skipped_missing_name"] == 1
    assert report["skipped_missing_email"] == 1
    assert report["checked_in_only"] is True


def test_read_recipients_all_registrants_does_not_require_checkin_column(tmp_path):
    csv_path = tmp_path / "all_registrants.csv"
    df = pd.DataFrame([
        {"First Name": "ahmad", "Last Name": "saleh", "Email": "ahmad@example.com"},
        {"First Name": "Sara", "Last Name": "Ali", "Email": "sara@example.com"},
    ])
    df.to_csv(csv_path, index=False)

    recipients, report = read_recipients_from_file(str(csv_path), checked_in_only=False)

    assert [item["full_name"] for item in recipients] == ["Ahmad Saleh", "Sara Ali"]
    assert report["eligible_count"] == 2
    assert report["skipped_not_checked_in"] == 0
    assert report["checked_in_only"] is False


def test_read_recipients_raises_for_missing_email_column(tmp_path):
    csv_path = tmp_path / "missing_email.csv"
    df = pd.DataFrame([
        {
            "First Name": "John",
            "Last Name": "Doe",
            "Checkin Date (UTC)": "2026-03-27 10:00:00",
        },
    ])
    df.to_csv(csv_path, index=False)

    with pytest.raises(ValueError) as exc:
        read_recipients_from_file(str(csv_path), checked_in_only=True)

    assert "Email" in str(exc.value)


def test_match_certificates_to_recipients_covers_exact_prefix_missing_and_ambiguous(tmp_path):
    (tmp_path / "John Doe.pdf").write_text("x", encoding="utf-8")
    (tmp_path / "Jane Smith Workshop.jpg").write_text("x", encoding="utf-8")
    (tmp_path / "Alex Brown v1.pdf").write_text("x", encoding="utf-8")
    (tmp_path / "Alex Brown final.pdf").write_text("x", encoding="utf-8")

    recipients = [
        {"full_name": "John Doe", "email": "john@example.com", "row_number": 2},
        {"full_name": "Jane Smith", "email": "jane@example.com", "row_number": 3},
        {"full_name": "Missing Person", "email": "missing@example.com", "row_number": 4},
        {"full_name": "Alex Brown", "email": "alex@example.com", "row_number": 5},
    ]

    matches, report = match_certificates_to_recipients(recipients, str(tmp_path))

    assert report == {
        "total_recipients": 4,
        "certificate_files_found": 4,
        "matched_count": 2,
        "missing_certificate_count": 1,
        "ambiguous_certificate_count": 1,
    }

    assert matches[0]["status"] == "matched"
    assert Path(matches[0]["certificate_path"]).name == "John Doe.pdf"

    assert matches[1]["status"] == "matched"
    assert Path(matches[1]["certificate_path"]).name == "Jane Smith Workshop.jpg"

    assert matches[2]["status"] == "missing_certificate"
    assert matches[2]["certificate_path"] == ""

    assert matches[3]["status"] == "ambiguous_certificate_match"
    assert len(matches[3]["candidate_files"]) == 2
    assert any("Alex Brown v1.pdf" in p for p in matches[3]["candidate_files"])
    assert any("Alex Brown final.pdf" in p for p in matches[3]["candidate_files"])


def test_match_certificates_to_recipients_normalizes_accents_and_case(tmp_path):
    (tmp_path / "José Álvarez.PDF").write_text("x", encoding="utf-8")

    recipients = [
        {"full_name": "Jose Alvarez", "email": "jose@example.com", "row_number": 2},
    ]

    matches, report = match_certificates_to_recipients(recipients, str(tmp_path))

    assert report["matched_count"] == 1
    assert matches[0]["status"] == "matched"
    assert Path(matches[0]["certificate_path"]).name == "José Álvarez.PDF"


def test_export_distribution_report_writes_candidate_files_column(tmp_path):
    output_csv = tmp_path / "distribution_report.csv"
    matches = [
        {
            "full_name": "Alex Brown",
            "email": "alex@example.com",
            "row_number": 5,
            "certificate_path": "",
            "status": "ambiguous_certificate_match",
            "candidate_files": ["a.pdf", "b.pdf"],
        }
    ]

    output_path = export_distribution_report(matches, str(output_csv))
    df = pd.read_csv(output_path)

    assert Path(output_path) == output_csv.resolve()
    assert df.loc[0, "Full Name"] == "Alex Brown"
    assert df.loc[0, "Candidate Files"] == "a.pdf | b.pdf"


def test_split_already_sent_matches_separates_sent_rows(tmp_path):
    report_csv = tmp_path / "email_send_report.csv"
    pd.DataFrame([
        {
            "Email": "JOHN@example.com",
            "Certificate Path": "John Doe.PDF",
            "Status": "sent",
        },
        {
            "Email": "jane@example.com",
            "Certificate Path": "Jane Smith.pdf",
            "Status": "failed",
        },
    ]).to_csv(report_csv, index=False)

    matches = [
        {
            "full_name": "John Doe",
            "email": "john@example.com",
            "certificate_path": "/tmp/John Doe.pdf",
            "status": "matched",
        },
        {
            "full_name": "Jane Smith",
            "email": "jane@example.com",
            "certificate_path": "/tmp/Jane Smith.pdf",
            "status": "matched",
        },
        {
            "full_name": "Missing Person",
            "email": "missing@example.com",
            "certificate_path": "",
            "status": "missing_certificate",
        },
    ]

    pending, already_sent = split_already_sent_matches(matches, str(report_csv))

    assert len(already_sent) == 1
    assert already_sent[0]["email"] == "john@example.com"
    assert len(pending) == 1
    assert pending[0]["email"] == "jane@example.com"


def test_split_already_sent_matches_returns_all_when_report_has_wrong_columns(tmp_path):
    report_csv = tmp_path / "email_send_report.csv"
    pd.DataFrame([
        {"Email": "john@example.com", "Status": "sent"},
    ]).to_csv(report_csv, index=False)

    matches = [
        {
            "full_name": "John Doe",
            "email": "john@example.com",
            "certificate_path": "John Doe.pdf",
            "status": "matched",
        },
    ]

    pending, already_sent = split_already_sent_matches(matches, str(report_csv))

    assert pending == matches
    assert already_sent == []


def test_save_and_load_email_draft_roundtrip(tmp_path):
    output_path = save_email_draft(
        folder_path=str(tmp_path),
        subject_template="Certificate for {full_name}",
        body_template="Hello {full_name}",
        event_title="Workshop",
        event_date="2026-04-02",
    )

    assert Path(output_path).name == "email_draft.json"

    loaded = load_email_draft(str(tmp_path))
    assert loaded == {
        "subject_template": "Certificate for {full_name}",
        "body_template": "Hello {full_name}",
        "event_title": "Workshop",
        "event_date": "2026-04-02",
    }


def test_load_email_draft_returns_none_when_missing(tmp_path):
    assert load_email_draft(str(tmp_path)) is None


def test_render_email_template_replaces_placeholders():
    result = render_email_template(
        "Hi {full_name}, welcome to {event_title} on {event_date}",
        full_name="Ahmad Saleh",
        event_title="Tech Thursday",
        event_date="2026-04-02",
    )

    assert result == "Hi Ahmad Saleh, welcome to Tech Thursday on 2026-04-02"


def test_get_gmail_service_raises_when_client_secret_missing(tmp_path):
    missing_secret = tmp_path / "client_secret.json"
    token_file = tmp_path / "gmail_token.json"

    with pytest.raises(GmailAuthError) as exc:
        get_gmail_service(
            client_secret_path=str(missing_secret),
            token_path=str(token_file),
        )

    assert "client_secret.json was not found" in str(exc.value)


class DummySendRequest:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class DummyMessagesAPI:
    def __init__(self):
        self.last_user_id = None
        self.last_body = None

    def send(self, userId=None, body=None):
        self.last_user_id = userId
        self.last_body = body
        return DummySendRequest({"id": "gmail_msg_123"})


class DummyUsersForSendAPI:
    def __init__(self):
        self.messages_api = DummyMessagesAPI()

    def messages(self):
        return self.messages_api


class DummySendService:
    def __init__(self):
        self.users_api = DummyUsersForSendAPI()

    def users(self):
        return self.users_api


def test_send_email_with_attachment_raises_for_missing_file(tmp_path):
    missing_file = tmp_path / "missing.pdf"
    service = DummySendService()

    with pytest.raises(FileNotFoundError) as exc:
        send_email_with_attachment(
            service=service,
            to_email="user@example.com",
            subject="Subject",
            body="Hello",
            attachment_path=str(missing_file),
        )

    assert "Attachment not found" in str(exc.value)


def test_send_email_with_attachment_builds_and_sends_message(tmp_path):
    attachment = tmp_path / "certificate.pdf"
    attachment.write_bytes(b"fake-pdf-content")

    service = DummySendService()

    message_id = send_email_with_attachment(
        service=service,
        to_email="user@example.com",
        subject="Your Certificate",
        body="Congratulations!",
        attachment_path=str(attachment),
    )

    assert message_id == "gmail_msg_123"
    assert service.users_api.messages_api.last_user_id == "me"
    assert "raw" in service.users_api.messages_api.last_body

    raw_message = service.users_api.messages_api.last_body["raw"]
    parsed = BytesParser(policy=policy.default).parsebytes(
        base64.urlsafe_b64decode(raw_message.encode("utf-8"))
    )

    assert parsed["To"] == "user@example.com"
    assert parsed["Subject"] == "Your Certificate"

    plain_part = parsed.get_body(preferencelist=("plain",))
    html_part = parsed.get_body(preferencelist=("html",))

    assert plain_part is not None
    assert html_part is not None
    assert plain_part.get_content().strip() == "Congratulations!"
    assert '<div style="font-family:Arial, Helvetica, sans-serif;' in html_part.get_content()
    assert '<p style="margin:0 0 16px 0;">Congratulations!</p>' in html_part.get_content()

    attachment_names = [part.get_filename() for part in parsed.iter_attachments()]
    assert "certificate.pdf" in attachment_names


def test_convert_plain_text_to_html_makes_urls_clickable():
    html_body = cd.convert_plain_text_to_html(
        "Community page:\nhttps://example.com/test."
    )

    assert 'href="https://example.com/test"' in html_body
    assert '>https://example.com/test</a>.' in html_body


def test_convert_plain_text_to_html_supports_markdown_style_links():
    html_body = cd.convert_plain_text_to_html(
        "We uploaded the photos. Click [here](https://example.com/gallery)."
    )

    assert 'href="https://example.com/gallery"' in html_body
    assert ">here</a>." in html_body
    assert ">https://example.com/gallery</a>" not in html_body


def test_export_email_send_report_creates_new_file(tmp_path):
    output_csv = tmp_path / "email_send_report.csv"
    results = [
        {
            "Full Name": "John Doe",
            "Email": "john@example.com",
            "Certificate Path": "John Doe.pdf",
            "Status": "sent",
            "Gmail Message ID": "abc123",
            "Error": "",
        }
    ]

    output_path = export_email_send_report(results, str(output_csv))
    df = pd.read_csv(output_path)

    assert Path(output_path).resolve() == output_csv.resolve()
    assert len(df) == 1
    assert df.loc[0, "Email"] == "john@example.com"
    assert df.loc[0, "Status"] == "sent"


def test_export_email_send_report_appends_existing_rows(tmp_path):
    output_csv = tmp_path / "email_send_report.csv"

    pd.DataFrame([
        {
            "Full Name": "Old User",
            "Email": "old@example.com",
            "Certificate Path": "old.pdf",
            "Status": "sent",
            "Gmail Message ID": "old123",
            "Error": "",
        }
    ]).to_csv(output_csv, index=False, encoding="utf-8-sig")

    new_results = [
        {
            "Full Name": "New User",
            "Email": "new@example.com",
            "Certificate Path": "new.pdf",
            "Status": "failed",
            "Gmail Message ID": "",
            "Error": "boom",
        }
    ]

    output_path = export_email_send_report(new_results, str(output_csv))
    df = pd.read_csv(output_path)

    assert len(df) == 2
    assert set(df["Email"]) == {"old@example.com", "new@example.com"}


def test_export_email_send_report_replaces_invalid_existing_file(tmp_path, monkeypatch):
    output_csv = tmp_path / "email_send_report.csv"
    output_csv.write_text("not,a,real,csv\n", encoding="utf-8")

    real_read_csv = pd.read_csv

    def fake_read_csv(*args, **kwargs):
        raise ValueError("bad csv")

    monkeypatch.setattr(cd.pd, "read_csv", fake_read_csv)

    results = [
        {
            "Full Name": "Only User",
            "Email": "only@example.com",
            "Certificate Path": "only.pdf",
            "Status": "sent",
            "Gmail Message ID": "msg1",
            "Error": "",
        }
    ]

    output_path = export_email_send_report(results, str(output_csv))
    df = real_read_csv(output_path)

    assert len(df) == 1
    assert df.loc[0, "Email"] == "only@example.com"


def test_make_send_key_normalizes_email_and_filename():
    result = make_send_key("  USER@Example.COM ", "/tmp/path/John Doe.PDF")
    assert result == ("user@example.com", "john doe.pdf")


def test_send_matched_certificates_raises_when_no_matched_rows():
    matches = [
        {"full_name": "Missing User", "email": "x@example.com", "status": "missing_certificate"}
    ]

    with pytest.raises(ValueError) as exc:
        send_matched_certificates(
            matches=matches,
            subject_template="Hi",
            body_template="Hello",
            event_title="Workshop",
            event_date="2026-04-02",
        )

    assert "No matched recipients are available to send" in str(exc.value)


def test_send_matched_certificates_success_failure_and_report_export(monkeypatch, tmp_path):
    matches = [
        {
            "full_name": "John Doe",
            "email": "john@example.com",
            "certificate_path": "John Doe.pdf",
            "status": "matched",
        },
        {
            "full_name": "Jane Smith",
            "email": "jane@example.com",
            "certificate_path": "Jane Smith.pdf",
            "status": "matched",
        },
        {
            "full_name": "Missing User",
            "email": "missing@example.com",
            "certificate_path": "",
            "status": "missing_certificate",
        },
    ]

    fake_service = object()
    exported = {}

    monkeypatch.setattr(cd, "get_gmail_service", lambda *args, **kwargs: fake_service)

    def fake_send_email_with_attachment(service, to_email, subject, body, attachment_path):
        assert service is fake_service
        if to_email == "john@example.com":
            assert "John Doe" in subject
            assert "Workshop" in subject
            return "msg_john"
        raise RuntimeError("SMTP-ish failure")

    def fake_export_email_send_report(results, output_csv_path):
        exported["results"] = results
        exported["path"] = output_csv_path
        return output_csv_path

    monkeypatch.setattr(cd, "send_email_with_attachment", fake_send_email_with_attachment)
    monkeypatch.setattr(cd, "export_email_send_report", fake_export_email_send_report)

    results, report = send_matched_certificates(
        matches=matches,
        subject_template="Certificate for {full_name} - {event_title}",
        body_template="Hello {full_name}, event date: {event_date}",
        event_title="Workshop",
        event_date="2026-04-02",
        client_secret_path="client_secret.json",
        token_path="gmail_token.json",
        report_output_path=str(tmp_path / "email_send_report.csv"),
    )

    assert len(results) == 2
    assert results[0]["Status"] == "sent"
    assert results[0]["Gmail Message ID"] == "msg_john"

    assert results[1]["Status"] == "failed"
    assert results[1]["Gmail Message ID"] == ""
    assert "SMTP-ish failure" in results[1]["Error"]

    assert report == {
        "matched_count": 2,
        "sent_count": 1,
        "failed_count": 1,
        "first_error": "SMTP-ish failure",
    }

    assert exported["path"].endswith("email_send_report.csv")
    assert len(exported["results"]) == 2


def test_send_matched_certificates_without_report_output_does_not_export(monkeypatch):
    matches = [
        {
            "full_name": "John Doe",
            "email": "john@example.com",
            "certificate_path": "John Doe.pdf",
            "status": "matched",
        }
    ]

    monkeypatch.setattr(cd, "get_gmail_service", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        cd,
        "send_email_with_attachment",
        lambda **kwargs: "msg_1",
    )

    called = {"exported": False}

    def fake_export(*args, **kwargs):
        called["exported"] = True

    monkeypatch.setattr(cd, "export_email_send_report", fake_export)

    results, report = send_matched_certificates(
        matches=matches,
        subject_template="Certificate for {full_name}",
        body_template="Hello {full_name}",
        event_title="Workshop",
        event_date="2026-04-02",
        report_output_path=None,
    )

    assert len(results) == 1
    assert report["sent_count"] == 1
    assert report["failed_count"] == 0
    assert report["first_error"] == ""
    assert called["exported"] is False