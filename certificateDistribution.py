import os
from pathlib import Path
import re
import unicodedata

import pandas as pd

import base64
import json
import mimetypes
from email.message import EmailMessage


SUPPORTED_CERTIFICATE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}

CERTIFICATE_EXTENSION_PRIORITY = {
    ".pdf": 0,
    ".jpg": 1,
    ".jpeg": 2,
    ".png": 3,
}


def is_name_present(first_name: str, last_name: str) -> bool:
    return bool(str(first_name).strip()) and bool(str(last_name).strip())


def capitalize_initials_only(name: str) -> str:
    name = " ".join(str(name).strip().split())

    def uppercase_match(match):
        return match.group(0).upper()

    return re.sub(
        r"(?:(?<=^)|(?<=[\s'-]))\w",
        uppercase_match,
        name,
        flags=re.UNICODE,
    )


def normalize_for_match(text: str) -> str:
    text = Path(str(text)).stem.strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def read_recipients_from_file(
    filename: str,
    checked_in_only: bool = True,
) -> tuple[list[dict], dict]:
    df = pd.read_csv(filename)

    required_columns = ["First Name", "Last Name", "Email"]
    if checked_in_only:
        required_columns.append("Checkin Date (UTC)")

    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        raise ValueError(f"Missing required CSV columns: {missing_columns}")

    recipients: list[dict] = []

    total_rows = len(df)
    eligible_count = 0
    skipped_not_checked_in = 0
    skipped_missing_name = 0
    skipped_missing_email = 0

    for index, row in df.iterrows():
        first_name = str(row["First Name"]).strip() if pd.notna(row["First Name"]) else ""
        last_name = str(row["Last Name"]).strip() if pd.notna(row["Last Name"]) else ""
        email = str(row["Email"]).strip() if pd.notna(row["Email"]) else ""

        full_name = capitalize_initials_only(f"{first_name} {last_name}".strip())

        if checked_in_only:
            checkin_date = row["Checkin Date (UTC)"]
            if pd.isna(checkin_date) or str(checkin_date).strip() == "":
                skipped_not_checked_in += 1
                continue

        eligible_count += 1

        if not is_name_present(first_name, last_name):
            skipped_missing_name += 1
            continue

        if not email:
            skipped_missing_email += 1
            continue

        recipients.append({
            "full_name": full_name,
            "email": email,
            "row_number": index + 2,
        })

    report = {
        "total_rows": total_rows,
        "eligible_count": eligible_count,
        "recipients_count": len(recipients),
        "skipped_not_checked_in": skipped_not_checked_in,
        "skipped_missing_name": skipped_missing_name,
        "skipped_missing_email": skipped_missing_email,
        "checked_in_only": checked_in_only,
    }

    return recipients, report


def find_certificate_files(folder_path: str) -> list[Path]:
    folder = Path(folder_path)
    if not folder.exists() or not folder.is_dir():
        raise ValueError("Selected certificate folder does not exist or is not a folder.")

    files = [
        path for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_CERTIFICATE_EXTENSIONS
    ]

    return sorted(files, key=lambda p: p.name.lower())


def _prefer_best_equivalent_candidates(candidates: list[dict]) -> list[dict]:
    preferred_by_normalized_name: dict[str, dict] = {}

    for item in candidates:
        key = item["normalized_name"]
        existing = preferred_by_normalized_name.get(key)

        if existing is None:
            preferred_by_normalized_name[key] = item
            continue

        current_priority = CERTIFICATE_EXTENSION_PRIORITY.get(
            item["path"].suffix.lower(),
            99,
        )
        existing_priority = CERTIFICATE_EXTENSION_PRIORITY.get(
            existing["path"].suffix.lower(),
            99,
        )

        if current_priority < existing_priority:
            preferred_by_normalized_name[key] = item

    return list(preferred_by_normalized_name.values())


def match_certificates_to_recipients(
    recipients: list[dict],
    folder_path: str,
) -> tuple[list[dict], dict]:
    certificate_files = find_certificate_files(folder_path)

    indexed_files = [
        {
            "path": path,
            "normalized_name": normalize_for_match(path.stem),
        }
        for path in certificate_files
    ]

    matches: list[dict] = []
    matched_count = 0
    missing_certificate_count = 0
    ambiguous_certificate_count = 0

    for recipient in recipients:
        full_name = recipient["full_name"]
        email = recipient["email"]
        normalized_full_name = normalize_for_match(full_name)

        exact_matches = [
            item for item in indexed_files
            if item["normalized_name"] == normalized_full_name
        ]

        prefix_matches = [
            item for item in indexed_files
            if item["normalized_name"].startswith(normalized_full_name + " ")
        ]

        contains_matches = [
            item for item in indexed_files
            if normalized_full_name in item["normalized_name"]
        ]

        if exact_matches:
            candidates = exact_matches
        elif prefix_matches:
            candidates = prefix_matches
        else:
            candidates = contains_matches

        unique_candidates = list({
            str(item["path"]): item for item in candidates
        }.values())
        unique_candidates = _prefer_best_equivalent_candidates(unique_candidates)

        if len(unique_candidates) == 1:
            matched_count += 1
            matches.append({
                "full_name": full_name,
                "email": email,
                "row_number": recipient.get("row_number", ""),
                "certificate_path": str(unique_candidates[0]["path"]),
                "status": "matched",
            })
        elif len(unique_candidates) == 0:
            missing_certificate_count += 1
            matches.append({
                "full_name": full_name,
                "email": email,
                "row_number": recipient.get("row_number", ""),
                "certificate_path": "",
                "status": "missing_certificate",
            })
        else:
            ambiguous_certificate_count += 1
            matches.append({
                "full_name": full_name,
                "email": email,
                "row_number": recipient.get("row_number", ""),
                "certificate_path": "",
                "status": "ambiguous_certificate_match",
                "candidate_files": [str(item["path"]) for item in unique_candidates],
            })

    report = {
        "total_recipients": len(recipients),
        "certificate_files_found": len(certificate_files),
        "matched_count": matched_count,
        "missing_certificate_count": missing_certificate_count,
        "ambiguous_certificate_count": ambiguous_certificate_count,
    }

    return matches, report

def export_distribution_report(
    matches: list[dict],
    output_csv_path: str,
) -> str:
    rows = []

    for item in matches:
        rows.append({
            "Full Name": item.get("full_name", ""),
            "Email": item.get("email", ""),
            "Row Number": item.get("row_number", ""),
            "Status": item.get("status", ""),
            "Certificate Path": item.get("certificate_path", ""),
            "Candidate Files": " | ".join(item.get("candidate_files", [])),
        })

    df = pd.DataFrame(rows)
    df.to_csv(output_csv_path, index=False, encoding="utf-8-sig")
    return str(Path(output_csv_path).resolve())


def render_email_template(
    template: str,
    full_name: str,
    event_title: str,
    event_date: str,
) -> str:
    return template.format(
        full_name=full_name,
        event_title=event_title,
        event_date=event_date,
    )

def save_email_draft(
    folder_path: str,
    subject_template: str,
    body_template: str,
    event_title: str,
    event_date: str,
) -> str:
    output_path = Path(folder_path) / "email_draft.json"

    payload = {
        "subject_template": subject_template,
        "body_template": body_template,
        "event_title": event_title,
        "event_date": event_date,
    }

    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return str(output_path.resolve())


def load_email_draft(folder_path: str) -> dict | None:
    input_path = Path(folder_path) / "email_draft.json"

    if not input_path.exists():
        return None

    return json.loads(input_path.read_text(encoding="utf-8"))

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]


class GmailAuthError(Exception):
    pass


def get_connected_gmail_account(service) -> str | None:
    try:
        profile = service.users().getProfile(userId="me").execute()
    except Exception:
        return None

    email = str(profile.get("emailAddress", "")).strip()
    return email or None


def ensure_connected_gmail_account(
    client_secret_path: str = "client_secret.json",
    token_path: str = "gmail_token.json",
) -> str:
    service = get_gmail_service(
        client_secret_path=client_secret_path,
        token_path=token_path,
    )

    connected_account = get_connected_gmail_account(service)
    if connected_account:
        return connected_account

    raise GmailAuthError(
        "Gmail login completed, but the connected account email could not be determined.\n\n"
        "Please delete gmail_token.json and sign in again."
    )


def render_email_template(
    template: str,
    full_name: str,
    event_title: str,
    event_date: str,
) -> str:
    return template.format(
        full_name=full_name,
        event_title=event_title,
        event_date=event_date,
    )


def get_gmail_service(
    client_secret_path: str = "client_secret.json",
    token_path: str = "gmail_token.json",
):
    client_secret_file = Path(client_secret_path)
    token_file = Path(token_path)

    if not client_secret_file.exists():
        raise GmailAuthError(
            "client_secret.json was not found.\n\n"
            "Please place your Google OAuth client secret file next to the app before sending emails."
        )

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:
        raise GmailAuthError(
            "Gmail dependencies are missing.\n\n"
            "Install these packages first:\n"
            "google-api-python-client\n"
            "google-auth-oauthlib\n"
            "google-auth-httplib2"
        ) from e

    creds = None

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), GMAIL_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_secret_file),
                GMAIL_SCOPES,
            )
            creds = flow.run_local_server(port=0)

        token_file.write_text(creds.to_json(), encoding="utf-8")

    return build("gmail", "v1", credentials=creds)


def send_email_with_attachment(
    service,
    to_email: str,
    subject: str,
    body: str,
    attachment_path: str,
) -> str:
    attachment_file = Path(attachment_path)
    if not attachment_file.exists():
        raise FileNotFoundError(f"Attachment not found: {attachment_path}")

    mime_type, _ = mimetypes.guess_type(str(attachment_file))
    if mime_type is None:
        mime_type = "application/octet-stream"

    maintype, subtype = mime_type.split("/", 1)

    message = EmailMessage()
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    with attachment_file.open("rb") as f:
        attachment_data = f.read()

    message.add_attachment(
        attachment_data,
        maintype=maintype,
        subtype=subtype,
        filename=attachment_file.name,
    )

    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    response = service.users().messages().send(
        userId="me",
        body={"raw": raw_message},
    ).execute()

    return response.get("id", "")


def export_email_send_report(
    results: list[dict],
    output_csv_path: str,
) -> str:
    output_path = Path(output_csv_path)
    new_df = pd.DataFrame(results)

    if output_path.exists():
        try:
            existing_df = pd.read_csv(output_path)
            combined_df = pd.concat([existing_df, new_df], ignore_index=True)
        except Exception:
            combined_df = new_df
    else:
        combined_df = new_df

    combined_df.to_csv(output_path, index=False, encoding="utf-8-sig")
    return str(output_path.resolve())


def make_send_key(email: str, certificate_path: str) -> tuple[str, str]:
    normalized_email = str(email).strip().lower()
    certificate_filename = Path(str(certificate_path)).name.strip().lower()
    return normalized_email, certificate_filename


def split_already_sent_matches(
    matches: list[dict],
    send_report_path: str,
) -> tuple[list[dict], list[dict]]:
    report_file = Path(send_report_path)

    matched_rows = [item for item in matches if item.get("status") == "matched"]

    if not report_file.exists():
        return matched_rows, []

    try:
        previous_df = pd.read_csv(report_file)
    except Exception:
        return matched_rows, []

    required_columns = {"Email", "Certificate Path", "Status"}
    if not required_columns.issubset(set(previous_df.columns)):
        return matched_rows, []

    sent_keys = set()

    for _, row in previous_df.iterrows():
        status = str(row.get("Status", "")).strip().lower()
        if status != "sent":
            continue

        email = str(row.get("Email", "")).strip()
        certificate_path = str(row.get("Certificate Path", "")).strip()

        if not email or not certificate_path:
            continue

        sent_keys.add(make_send_key(email, certificate_path))

    pending_matches: list[dict] = []
    already_sent_matches: list[dict] = []

    for item in matched_rows:
        key = make_send_key(
            item.get("email", ""),
            item.get("certificate_path", ""),
        )

        if key in sent_keys:
            already_sent_matches.append(item)
        else:
            pending_matches.append(item)

    return pending_matches, already_sent_matches


def send_matched_certificates(
    matches: list[dict],
    subject_template: str,
    body_template: str,
    event_title: str,
    event_date: str,
    client_secret_path: str = "client_secret.json",
    token_path: str = "gmail_token.json",
    report_output_path: str | None = None,
) -> tuple[list[dict], dict]:
    matched_rows = [item for item in matches if item.get("status") == "matched"]

    if not matched_rows:
        raise ValueError("No matched recipients are available to send.")

    service = get_gmail_service(
        client_secret_path=client_secret_path,
        token_path=token_path,
    )

    results: list[dict] = []
    sent_count = 0
    failed_count = 0

    for item in matched_rows:
        full_name = item.get("full_name", "")
        email = item.get("email", "")
        certificate_path = item.get("certificate_path", "")

        subject = render_email_template(
            subject_template,
            full_name,
            event_title,
            event_date,
        )
        body = render_email_template(
            body_template,
            full_name,
            event_title,
            event_date,
        )

        try:
            gmail_message_id = send_email_with_attachment(
                service=service,
                to_email=email,
                subject=subject,
                body=body,
                attachment_path=certificate_path,
            )

            sent_count += 1
            results.append({
                "Full Name": full_name,
                "Email": email,
                "Certificate Path": certificate_path,
                "Status": "sent",
                "Gmail Message ID": gmail_message_id,
                "Error": "",
            })

        except Exception as e:
            failed_count += 1
            results.append({
                "Full Name": full_name,
                "Email": email,
                "Certificate Path": certificate_path,
                "Status": "failed",
                "Gmail Message ID": "",
                "Error": str(e),
            })

    first_error = ""
    for row in results:
        if row.get("Status") == "failed":
            first_error = row.get("Error", "")
            break

    report = {
        "matched_count": len(matched_rows),
        "sent_count": sent_count,
        "failed_count": failed_count,
        "first_error": first_error,
    }

    if report_output_path:
        export_email_send_report(results, report_output_path)

    return results, report