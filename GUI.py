import os
import tempfile
from pathlib import Path
from datetime import datetime
from collections import Counter

import pandas as pd
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, scrolledtext, simpledialog

import certificateEditor as ce
from driveUpload import Upload, DriveAuthError

import sys
import re

from certificateDistribution import (
    read_recipients_from_file,
    match_certificates_to_recipients,
    export_distribution_report,
    save_email_draft,
    load_email_draft,
    send_matched_certificates,
    split_already_sent_matches,
    ensure_connected_gmail_account,
    GmailAuthError,
)

BASE_DIR = Path(__file__).resolve().parent

GOOGLE_BLUE = "#4285F4"
GOOGLE_RED = "#EA4335"
GOOGLE_YELLOW = "#FBBC05"
GOOGLE_GREEN = "#34A853"
BG_COLOR = "#F8F9FA"
TEXT_COLOR = "#202124"
WHITE = "#FFFFFF"
LIGHT_BORDER = "#DADCE0"

class CanvasProgressBar:
    def __init__(
        self,
        parent: tk.Widget,
        height: int = 22,
        bg: str = WHITE,
        border_color: str = LIGHT_BORDER,
        fill_color: str = GOOGLE_GREEN,
    ) -> None:
        self.maximum = 100
        self.value = 0
        self.height = height
        self.fill_color = fill_color

        self.canvas = tk.Canvas(
            parent,
            height=height,
            bg=bg,
            highlightthickness=1,
            highlightbackground=border_color,
            bd=0,
        )
        self.fill_id = self.canvas.create_rectangle(
            0, 0, 0, height,
            fill=fill_color,
            outline=fill_color,
        )

        self.canvas.bind("<Configure>", self._on_resize)

    def _on_resize(self, event=None) -> None:
        self._redraw()

    def _redraw(self) -> None:
        self.canvas.update_idletasks()
        width = self.canvas.winfo_width()

        if self.maximum <= 0:
            fill_width = 0
        else:
            fill_width = (self.value / self.maximum) * width

        self.canvas.coords(self.fill_id, 0, 0, fill_width, self.height)

    def grid(self, *args, **kwargs) -> None:
        self.canvas.grid(*args, **kwargs)

    def __setitem__(self, key: str, value: float) -> None:
        if key == "maximum":
            self.maximum = max(float(value), 1)
        elif key == "value":
            self.value = max(0, float(value))
        else:
            raise KeyError(f"Unsupported key: {key}")

        self._redraw()

    def __getitem__(self, key: str) -> float:
        if key == "maximum":
            return self.maximum
        if key == "value":
            return self.value
        raise KeyError(f"Unsupported key: {key}")

def resource_path(relative_path: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / relative_path
    return BASE_DIR / relative_path

def validate_date(date_text: str) -> bool:
    try:
        datetime.strptime(date_text, "%d/%m/%Y")
        return True
    except ValueError:
        return False


def select_csv_file() -> str | None:
    filepath = filedialog.askopenfilename(
        title="Select a CSV file",
        filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
    )
    if filepath:
        print(f"You selected: {filepath}")
        return filepath
    return None


def select_upload_path() -> str | None:
    filepath = filedialog.askdirectory(
        title="Select a folder to upload the certificates"
    )
    if filepath:
        print(f"You selected: {filepath}")
        return filepath
    return None

def select_certificates_folder() -> str | None:
    folder_path = filedialog.askdirectory(
        title="Select Folder That Contains Generated Certificates"
    )
    if folder_path:
        print(f"You selected: {folder_path}")
        return folder_path
    return None

def certificates_save_dir() -> str | None:
    save_directory = filedialog.askdirectory(title="Select Directory to Save Certificates")
    if save_directory:
        print(f"You selected: {save_directory}")
        return save_directory
    return None

def suggest_output_folder_name(event_title: str, event_date: str) -> str:
    safe_title = " ".join(event_title.strip().split()) or "Certificates"

    try:
        dt = datetime.strptime(event_date.strip(), "%d/%m/%Y")
        formatted_date = dt.strftime("%Y-%m-%d")
        return f"{safe_title} - {formatted_date}"
    except ValueError:
        return safe_title


def select_output_directory(event_title: str, event_date: str) -> str | None:
    parent_dir = filedialog.askdirectory(title="Select Parent Folder for Certificates")
    if not parent_dir:
        return None

    folder_name = suggest_output_folder_name(event_title, event_date)
    output_dir = os.path.join(parent_dir, folder_name)
    os.makedirs(output_dir, exist_ok=True)

    print(f"Certificates will be saved to: {output_dir}")
    return output_dir


def open_folder(path: str) -> None:
    try:
        os.startfile(path)  # Windows
    except Exception as e:
        print(f"Could not open folder automatically: {e}")

def open_file(path: str) -> None:
    try:
        os.startfile(path)  # Windows
    except Exception as e:
        print(f"Could not open file automatically: {e}")

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

def find_duplicate_names(names: list[str]) -> list[tuple[str, int]]:
    counts = Counter(names)
    duplicates = [(name, count) for name, count in counts.items() if count > 1]
    duplicates.sort(key=lambda x: (-x[1], x[0].lower()))
    return duplicates


def find_long_names(names: list[str], max_chars: int = 28, max_words: int = 3) -> list[str]:
    long_names = []
    for name in names:
        if len(name) > max_chars or len(name.split()) > max_words:
            long_names.append(name)
    return long_names


def build_send_confirmation_message(sendable_count: int, sender_email: str) -> str:
    return (
        f"This will send emails to {sendable_count} matched recipient(s).\n\n"
        f"Sending Gmail account:\n<{sender_email}>\n\n"
        "Do you want to continue?"
    )

def require_large_send_confirmation(sendable_count: int, parent_window) -> bool:
    if sendable_count <= 10:
        return True

    typed_value = simpledialog.askstring(
        "Large send confirmation",
        (
            f"You are about to send {sendable_count} emails.\n\n"
            "To confirm, type SEND below."
        ),
        parent=parent_window,
    )

    if typed_value is None:
        return False

    if typed_value.strip().upper() != "SEND":
        messagebox.showwarning(
            "Sending cancelled",
            "Confirmation text did not match. Emails were not sent.",
            parent=parent_window,
        )
        return False

    return True


def show_pre_generation_warnings(names: list[str]) -> None:
    duplicates = find_duplicate_names(names)
    long_names = find_long_names(names)

    warning_parts = []

    if duplicates:
        duplicate_lines = "\n".join(f"- {name} ({count} times)" for name, count in duplicates[:10])
        if len(duplicates) > 10:
            duplicate_lines += f"\n...and {len(duplicates) - 10} more"
        warning_parts.append("Duplicate names detected:\n" + duplicate_lines)

    if long_names:
        long_name_lines = "\n".join(f"- {name}" for name in long_names[:10])
        if len(long_names) > 10:
            long_name_lines += f"\n...and {len(long_names) - 10} more"
        warning_parts.append("These names may appear small on the certificate:\n" + long_name_lines)

    if warning_parts:
        messagebox.showwarning(
            "Pre-generation warnings",
            "\n\n".join(warning_parts)
        )

def read_names_from_file(
    filename: str,
    checked_in_only: bool = True,
    export_skipped: bool = False,
    skipped_output_path: str = "skipped.csv",
) -> tuple[list[str], dict]:
    df = pd.read_csv(filename)

    required_columns = ["First Name", "Last Name"]
    if checked_in_only:
        required_columns.append("Checkin Date (UTC)")

    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        raise ValueError(f"Missing required CSV columns: {missing_columns}")

    accepted_names: list[str] = []
    skipped_rows: list[dict] = []

    total_rows = len(df)
    eligible_count = 0
    skipped_not_checked_in = 0
    skipped_missing_name = 0

    for index, row in df.iterrows():
        first_name = str(row["First Name"]).strip() if pd.notna(row["First Name"]) else ""
        last_name = str(row["Last Name"]).strip() if pd.notna(row["Last Name"]) else ""
        full_name = capitalize_initials_only(f"{first_name} {last_name}".strip())

        if checked_in_only:
            checkin_date = row["Checkin Date (UTC)"]
            if pd.isna(checkin_date) or str(checkin_date).strip() == "":
                skipped_not_checked_in += 1
                skipped_rows.append({
                    **row.to_dict(),
                    "Full Name": full_name,
                    "Skip Reason": "not_checked_in",
                    "Row Number": index + 2,
                })
                continue

        eligible_count += 1

        if not is_name_present(first_name, last_name):
            skipped_missing_name += 1
            skipped_rows.append({
                **row.to_dict(),
                "Full Name": full_name,
                "Skip Reason": "missing_first_or_last_name",
                "Row Number": index + 2,
            })
            continue

        accepted_names.append(full_name)

    accepted_count = len(accepted_names)
    skipped_count = len(skipped_rows)

    skipped_csv_real_path = None
    if export_skipped and skipped_rows:
        skipped_df = pd.DataFrame(skipped_rows)
        skipped_df.to_csv(skipped_output_path, index=False, encoding="utf-8-sig")
        skipped_csv_real_path = os.path.abspath(skipped_output_path)

    report = {
        "total_rows": total_rows,
        "eligible_count": eligible_count,
        "accepted_count": accepted_count,
        "skipped_count": skipped_count,
        "skipped_not_checked_in": skipped_not_checked_in,
        "skipped_missing_name": skipped_missing_name,
        "checked_in_only": checked_in_only,
        "skipped_output_path": skipped_csv_real_path,
    }

    return accepted_names, report

def generate_certificates(
    filepath: str,
    event_title_entry: tk.Entry,
    event_date_entry: tk.Entry,
    eligibility_var: tk.StringVar,
    progress_var: tk.StringVar,
    progress_bar: CanvasProgressBar,
    root: tk.Tk,
) -> None:
    event_title = event_title_entry.get().strip()
    event_date = event_date_entry.get().strip()
    checked_in_only = eligibility_var.get() == "Checked-in only"

    save_dir = select_output_directory(event_title, event_date)
    if not save_dir:
        return

    attendees_list: list[str] = []
    report = None

    try:
        progress_var.set("Reading CSV...")
        progress_bar["value"] = 0
        root.update_idletasks()

        if filepath:
            attendees_list, report = read_names_from_file(
                filepath,
                checked_in_only=checked_in_only,
                export_skipped=False,
            )

        if not attendees_list:
            progress_var.set("No certificates generated")
            messagebox.showwarning(
                "No certificates generated",
                "No valid attendees were found in the CSV."
            )
            return

        show_pre_generation_warnings(attendees_list)

        progress_bar["maximum"] = len(attendees_list)
        progress_bar["value"] = 0

        def on_progress(current: int, total: int, full_name: str) -> None:
            progress_bar["maximum"] = total
            progress_bar["value"] = current
            progress_var.set(f"Generating {current} / {total}: {full_name}")
            root.update_idletasks()

        ce.edit_certificate(
            "template_certificate_no_line.jpg",
            attendees_list,
            event_title,
            event_date,
            ce.name_font_path,
            ce.regular_font_path,
            save_dir,
            progress_callback=on_progress,
        )

        mode_label = "Checked-in only" if checked_in_only else "All registrants"

        summary_message = (
            f"Certificates generated successfully!\n\n"
            f"Saved to:\n{save_dir}\n\n"
            f"Eligibility mode: {mode_label}\n"
            f"Total rows in CSV: {report['total_rows']}\n"
            f"Eligible rows: {report['eligible_count']}\n"
            f"Certificates created: {report['accepted_count']}\n"
            f"Skipped: {report['skipped_count']}\n"
            f"  - Not checked in: {report['skipped_not_checked_in']}\n"
            f"  - Missing first/last name: {report['skipped_missing_name']}\n"
        )

        progress_bar["value"] = progress_bar["maximum"]
        progress_var.set("Generation complete")

        print(summary_message)
        messagebox.showinfo("Generation complete", summary_message)
        open_folder(save_dir)

    except ValueError as e:
        progress_var.set("CSV error")
        messagebox.showerror("CSV Error", str(e))
    except Exception as e:
        progress_var.set("Generation failed")
        messagebox.showerror("Generation failed", f"Something went wrong:\n{e}")


def generate_single_certificate(
    participant_name_entry: tk.Entry,
    event_title_entry: tk.Entry,
    event_date_entry: tk.Entry,
    progress_var: tk.StringVar,
    progress_bar: CanvasProgressBar,
    root: tk.Tk,
) -> None:
    participant_name = capitalize_initials_only(participant_name_entry.get().strip())
    event_title = event_title_entry.get().strip()
    event_date = event_date_entry.get().strip()

    participant_name_entry.delete(0, tk.END)
    participant_name_entry.insert(0, participant_name)

    if not participant_name:
        messagebox.showerror("Missing participant name", "Please enter the participant name")
        return

    if not event_title:
        messagebox.showerror("Missing event title", "Please enter the event title")
        return

    if not validate_date(event_date):
        messagebox.showerror("Invalid date", "Please enter a valid date in the format DD/MM/YYYY")
        return

    save_dir = select_output_directory(event_title, event_date)
    if not save_dir:
        return

    try:
        progress_bar["maximum"] = 1
        progress_bar["value"] = 0
        progress_var.set("Generating 1 / 1...")
        root.update_idletasks()

        def on_progress(current: int, total: int, full_name: str) -> None:
            progress_bar["maximum"] = total
            progress_bar["value"] = current
            progress_var.set(f"Generating {current} / {total}: {full_name}")
            root.update_idletasks()

        ce.edit_certificate(
            "template_certificate_no_line.jpg",
            [participant_name],
            event_title,
            event_date,
            ce.name_font_path,
            ce.regular_font_path,
            save_dir,
            progress_callback=on_progress,
        )

        participant_name_entry.delete(0, tk.END)
        participant_name_entry.focus_set()

        progress_bar["value"] = 1
        progress_var.set("Generation complete")

        messagebox.showinfo(
            "Generation complete",
            f"Certificate generated successfully for:\n{participant_name}\n\nSaved to:\n{save_dir}"
        )
        open_folder(save_dir)

    except Exception as e:
        progress_var.set("Generation failed")
        messagebox.showerror("Generation failed", f"Something went wrong:\n{e}")

def preview_single_certificate(
    participant_name_entry: tk.Entry,
    event_title_entry: tk.Entry,
    event_date_entry: tk.Entry,
    progress_var: tk.StringVar,
    progress_bar: CanvasProgressBar,
    root: tk.Tk,
) -> None:
    participant_name = capitalize_initials_only(participant_name_entry.get().strip())
    event_title = event_title_entry.get().strip()
    event_date = event_date_entry.get().strip()

    participant_name_entry.delete(0, tk.END)
    participant_name_entry.insert(0, participant_name)

    if not participant_name:
        messagebox.showerror("Missing participant name", "Please enter the participant name")
        return

    if not event_title:
        messagebox.showerror("Missing event title", "Please enter the event title")
        return

    if not validate_date(event_date):
        messagebox.showerror("Invalid date", "Please enter a valid date in the format DD/MM/YYYY")
        return

    try:
        preview_dir = os.path.join(tempfile.gettempdir(), "gdscbme_certificate_preview")
        os.makedirs(preview_dir, exist_ok=True)

        progress_bar["maximum"] = 1
        progress_bar["value"] = 0
        progress_var.set("Generating preview...")
        root.update_idletasks()

        preview_path = ce.preview_certificate(
            "template_certificate_no_line.jpg",
            participant_name,
            event_title,
            event_date,
            ce.name_font_path,
            ce.regular_font_path,
            preview_dir,
        )

        progress_bar["value"] = 1
        progress_var.set("Preview ready")
        root.update_idletasks()

        if preview_path:
            open_file(preview_path)
            messagebox.showinfo(
                "Preview ready",
                f"Preview generated successfully for:\n{participant_name}"
            )
        else:
            messagebox.showerror("Preview failed", "Could not generate preview.")

    except Exception as e:
        progress_var.set("Preview failed")
        messagebox.showerror("Preview failed", f"Something went wrong:\n{e}")

def upload_to_drive() -> None:
    folder_path = select_upload_path()
    if folder_path:
        parent_folder_name = os.path.basename(folder_path)
        try:
            folder_url = Upload(folder_path, parent_folder_name)
            if folder_url:
                messagebox.showinfo("Upload complete", f"Uploaded to:\n{folder_url}")
        except DriveAuthError as e:
            messagebox.showerror("Drive setup required", str(e))
        except Exception as e:
            messagebox.showerror("Upload failed", f"Something went wrong:\n{e}")


def setup_gui() -> None:
    root = tk.Tk()
    root.title("GDSCBME Certificate Generator")
    root.geometry("760x830")
    root.minsize(700, 760)
    root.configure(bg=BG_COLOR)

    root.columnconfigure(0, weight=0)
    root.columnconfigure(1, weight=1)

    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure(
        "Google.TCombobox",
        fieldbackground=WHITE,
        background=WHITE,
        foreground=TEXT_COLOR,
        bordercolor=LIGHT_BORDER,
        arrowsize=16,
        padding=4,
    )

    logo_image = None
    logo_path = resource_path("logo.png")
    if logo_path.exists():
        try:
            logo_image = tk.PhotoImage(file=str(logo_path)).subsample(3, 3)
        except tk.TclError:
            logo_image = None

    if logo_image is not None:
        logo_label = tk.Label(root, image=logo_image, bg=BG_COLOR)
        logo_label.image = logo_image
        logo_label.grid(row=0, column=0, columnspan=2, pady=(20, 6))
    else:
        logo_label = tk.Label(
            root,
            text="GDSCBME Certificate Generator",
            bg=BG_COLOR,
            fg=TEXT_COLOR,
            font=("Helvetica", 14, "bold"),
        )
        logo_label.grid(row=0, column=0, columnspan=2, pady=(10, 6))

    filepath = tk.StringVar()
    selected_file_var = tk.StringVar(value="No file selected")
    eligibility_var = tk.StringVar(value="Checked-in only")
    progress_var = tk.StringVar(value="Ready")

    label_font = ("Helvetica", 11, "bold")
    entry_font = ("Helvetica", 11)
    button_font = ("Helvetica", 12, "bold")

    def select_csv_file_button() -> None:
        selected_path = select_csv_file() or ""
        filepath.set(selected_path)

        if selected_path:
            selected_file_var.set(f"Selected file: {Path(selected_path).name}")
        else:
            selected_file_var.set("No file selected")

    csv_button = tk.Button(
        root,
        text="Select CSV File",
        command=select_csv_file_button,
        bg=GOOGLE_BLUE,
        fg=WHITE,
        activebackground="#3367D6",
        activeforeground=WHITE,
        relief="flat",
        bd=0,
        cursor="hand2",
        font=button_font,
        height=1,
        padx=12,
        pady=12,
    )
    csv_button.grid(row=1, column=0, columnspan=2, sticky="ew", padx=20, pady=(6, 10))

    selected_file_label = tk.Label(
        root,
        textvariable=selected_file_var,
        bg=BG_COLOR,
        fg="#6B7280",
        font=("Helvetica", 9),
        anchor="w",
        justify="left",
        wraplength=700,
    )
    selected_file_label.grid(row=2, column=0, columnspan=2, sticky="w", padx=22, pady=(0, 4))

    event_title_label = tk.Label(
        root,
        text="Event Title:",
        bg=BG_COLOR,
        fg=TEXT_COLOR,
        font=label_font,
    )
    event_title_entry = tk.Entry(
        root,
        width=40,
        bg=WHITE,
        fg=TEXT_COLOR,
        insertbackground=TEXT_COLOR,
        relief="solid",
        bd=1,
        font=entry_font,
    )

    event_date_label = tk.Label(
        root,
        text="Event Date (DD/MM/YYYY):",
        bg=BG_COLOR,
        fg=TEXT_COLOR,
        font=label_font,
    )
    event_date_entry = tk.Entry(
        root,
        width=40,
        bg=WHITE,
        fg=TEXT_COLOR,
        insertbackground=TEXT_COLOR,
        relief="solid",
        bd=1,
        font=entry_font,
    )

    participant_name_label = tk.Label(
        root,
        text="Participant Name:",
        bg=BG_COLOR,
        fg=TEXT_COLOR,
        font=label_font,
    )
    participant_name_entry = tk.Entry(
        root,
        width=40,
        bg=WHITE,
        fg=TEXT_COLOR,
        insertbackground=TEXT_COLOR,
        relief="solid",
        bd=1,
        font=entry_font,
    )

    eligibility_label = tk.Label(
        root,
        text="Eligibility Mode:",
        bg=BG_COLOR,
        fg=TEXT_COLOR,
        font=label_font,
    )
    eligibility_menu = ttk.Combobox(
        root,
        textvariable=eligibility_var,
        values=["Checked-in only", "All registrants"],
        state="readonly",
        style="Google.TCombobox",
        font=entry_font,
    )

    progress_label = tk.Label(
        root,
        textvariable=progress_var,
        bg=BG_COLOR,
        fg=GOOGLE_BLUE,
        font=("Helvetica", 11, "bold"),
    )
    progress_bar = CanvasProgressBar(root, height=22)
    progress_bar["maximum"] = 100
    progress_bar["value"] = 0

    event_title_label.grid(row=3, column=0, padx=(20, 12), pady=8, sticky="w")
    event_title_entry.grid(row=3, column=1, padx=(0, 20), pady=8, sticky="ew")

    event_date_label.grid(row=4, column=0, padx=(20, 12), pady=8, sticky="w")
    event_date_entry.grid(row=4, column=1, padx=(0, 20), pady=8, sticky="ew")

    participant_name_label.grid(row=5, column=0, padx=(20, 12), pady=8, sticky="w")
    participant_name_entry.grid(row=5, column=1, padx=(0, 20), pady=8, sticky="ew")

    eligibility_label.grid(row=6, column=0, padx=(20, 12), pady=8, sticky="w")
    eligibility_menu.grid(row=6, column=1, padx=(0, 20), pady=8, sticky="ew")

    progress_label.grid(row=7, column=0, columnspan=2, padx=20, pady=(16, 6), sticky="w")
    progress_bar.grid(row=8, column=0, columnspan=2, padx=20, pady=(0, 18), sticky="ew")

    def generate_certificates_button() -> None:
        if not filepath.get():
            messagebox.showerror(
                "No CSV file selected",
                "Please select a CSV file before generating certificates",
            )
            return
        if not validate_date(event_date_entry.get()):
            messagebox.showerror(
                "Invalid date",
                "Please enter a valid date in the format DD/MM/YYYY",
            )
            return
        if not event_title_entry.get().strip():
            messagebox.showerror(
                "Missing event title",
                "Please enter the event title",
            )
            return

        generate_certificates(
            filepath.get(),
            event_title_entry,
            event_date_entry,
            eligibility_var,
            progress_var,
            progress_bar,
            root,
        )

    def generate_single_certificate_button() -> None:
        generate_single_certificate(
            participant_name_entry,
            event_title_entry,
            event_date_entry,
            progress_var,
            progress_bar,
            root,
        )

    def preview_single_certificate_button() -> None:
        preview_single_certificate(
            participant_name_entry,
            event_title_entry,
            event_date_entry,
            progress_var,
            progress_bar,
            root,
        )

    def open_email_template_window(
        matches: list[dict],
        match_report: dict,
        certificates_folder: str,
    ) -> None:
        matched_rows = [item for item in matches if item.get("status") == "matched"]
        existing_draft = load_email_draft(certificates_folder)

        default_subject = "Your certificate for {event_title}"
        default_body = (
            "Hello {full_name},\n\n"
            "Thank you for attending {event_title} on {event_date}.\n"
            "Please find your certificate attached.\n\n"
            "Best regards,\n"
            "GDG on Campus BME"
        )

        if existing_draft:
            default_subject = existing_draft.get("subject_template", default_subject)
            default_body = existing_draft.get("body_template", default_body)

        window = tk.Toplevel(root)
        window.title("Email Template")
        window.geometry("720x560")
        window.minsize(680, 500)
        window.configure(bg=BG_COLOR)

        window.columnconfigure(0, weight=0)
        window.columnconfigure(1, weight=1)

        info_text = (
            f"Matched recipients: {match_report['matched_count']}\n"
            f"Missing certificates: {match_report['missing_certificate_count']}\n"
            f"Ambiguous matches: {match_report['ambiguous_certificate_count']}"
        )

        info_label = tk.Label(
            window,
            text=info_text,
            bg=BG_COLOR,
            fg=TEXT_COLOR,
            font=("Helvetica", 10, "bold"),
            anchor="w",
            justify="left",
        )
        info_label.grid(row=0, column=0, columnspan=2, padx=20, pady=(20, 12), sticky="w")

        subject_label = tk.Label(
            window,
            text="Email Subject:",
            bg=BG_COLOR,
            fg=TEXT_COLOR,
            font=("Helvetica", 11, "bold"),
        )
        subject_label.grid(row=1, column=0, padx=(20, 12), pady=8, sticky="w")

        subject_entry = tk.Entry(
            window,
            bg=WHITE,
            fg=TEXT_COLOR,
            insertbackground=TEXT_COLOR,
            relief="solid",
            bd=1,
            font=("Helvetica", 11),
        )
        subject_entry.grid(row=1, column=1, padx=(0, 20), pady=8, sticky="ew")
        subject_entry.insert(0, default_subject)

        body_label = tk.Label(
            window,
            text="Email Body:",
            bg=BG_COLOR,
            fg=TEXT_COLOR,
            font=("Helvetica", 11, "bold"),
        )
        body_label.grid(row=2, column=0, padx=(20, 12), pady=8, sticky="nw")

        body_text = scrolledtext.ScrolledText(
            window,
            height=10,
            bg=WHITE,
            fg=TEXT_COLOR,
            insertbackground=TEXT_COLOR,
            relief="solid",
            bd=1,
            font=("Helvetica", 11),
            wrap="word",
        )
        body_text.grid(row=2, column=1, padx=(0, 20), pady=8, sticky="nsew")
        body_text.insert("1.0", default_body)

        help_label = tk.Label(
            window,
            text="You can use: {full_name}, {event_title}, {event_date}",
            bg=BG_COLOR,
            fg="#6B7280",
            font=("Helvetica", 9),
            anchor="w",
            justify="left",
        )
        help_label.grid(row=3, column=1, padx=(0, 20), pady=(0, 10), sticky="w")

        preview_box_label = tk.Label(
            window,
            text="Matched recipients preview:",
            bg=BG_COLOR,
            fg=TEXT_COLOR,
            font=("Helvetica", 11, "bold"),
        )
        preview_box_label.grid(row=4, column=0, padx=(20, 12), pady=8, sticky="nw")

        preview_box = scrolledtext.ScrolledText(
            window,
            height=10,
            bg=WHITE,
            fg=TEXT_COLOR,
            relief="solid",
            bd=1,
            font=("Consolas", 10),
            wrap="word",
        )
        preview_box.grid(row=4, column=1, padx=(0, 20), pady=8, sticky="nsew")

        preview_lines = []
        for item in matched_rows[:20]:
            preview_lines.append(f"{item['full_name']}  <{item['email']}>")
        if len(matched_rows) > 20:
            preview_lines.append(f"\n...and {len(matched_rows) - 20} more")

        preview_box.insert("1.0", "\n".join(preview_lines) if preview_lines else "No matched recipients.")
        preview_box.config(state="disabled")

        def save_email_draft_button() -> None:
            subject = subject_entry.get().strip()
            body = body_text.get("1.0", tk.END).strip()

            if not subject:
                messagebox.showerror(
                    "Missing email subject",
                    "Please enter the email subject.",
                    parent=window,
                )
                return

            if not body:
                messagebox.showerror(
                    "Missing email body",
                    "Please enter the email body.",
                    parent=window,
                )
                return

            try:
                saved_path = save_email_draft(
                    certificates_folder,
                    subject,
                    body,
                    event_title_entry.get().strip(),
                    event_date_entry.get().strip(),
                )

                messagebox.showinfo(
                    "Draft saved",
                    f"Email draft saved successfully.\n\nSaved to:\n{saved_path}",
                    parent=window,
                )
            except Exception as e:
                messagebox.showerror(
                    "Save failed",
                    f"Could not save email draft:\n{e}",
                    parent=window,
                )
                

        def send_emails_button() -> None:
            subject = subject_entry.get().strip()
            body = body_text.get("1.0", tk.END).strip()

            if not subject:
                messagebox.showerror(
                    "Missing email subject",
                    "Please enter the email subject.",
                    parent=window,
                )
                return

            if not body:
                messagebox.showerror(
                    "Missing email body",
                    "Please enter the email body.",
                    parent=window,
                )
                return

            event_title = event_title_entry.get().strip()
            event_date = event_date_entry.get().strip()

            needs_event_title = "{event_title}" in subject or "{event_title}" in body
            needs_event_date = "{event_date}" in subject or "{event_date}" in body

            if needs_event_title and not event_title:
                messagebox.showerror(
                    "Missing event title",
                    "Your email template uses {event_title}, so please fill in the Event Title field.",
                    parent=window,
                )
                return

            if needs_event_date and not validate_date(event_date):
                messagebox.showerror(
                    "Invalid event date",
                    "Your email template uses {event_date}, so please enter a valid date in the format DD/MM/YYYY.",
                    parent=window,
                )
                return

            send_report_path = os.path.join(certificates_folder, "email_send_report.csv")

            pending_matches, already_sent_matches = split_already_sent_matches(
                matches,
                send_report_path,
            )

            if already_sent_matches:
                choice = messagebox.askyesnocancel(
                    "Previous send report found",
                    f"{len(already_sent_matches)} matched recipient(s) were already sent successfully.\n\n"
                    f"Yes = skip already sent and continue\n"
                    f"No = send all again\n"
                    f"Cancel = abort",
                    parent=window,
                )

                if choice is None:
                    return

                if choice:
                    matches_to_send = pending_matches
                else:
                    matches_to_send = matches
            else:
                matches_to_send = matches

            sendable_count = len([
                item for item in matches_to_send
                if item.get("status") == "matched"
            ])

            if sendable_count == 0:
                messagebox.showinfo(
                    "Nothing to send",
                    "No new matched recipients need to be sent.",
                    parent=window,
                )
                return

            try:
                sender_email = ensure_connected_gmail_account(
                    client_secret_path=str(resource_path("client_secret.json")),
                    token_path=str(BASE_DIR / "gmail_token.json"),
                )
            except GmailAuthError as e:
                messagebox.showerror("Gmail setup required", str(e), parent=window)
                return

            confirm = messagebox.askyesno(
                "Confirm sending",
                build_send_confirmation_message(sendable_count, sender_email),
                parent=window,
            )
            if not confirm:
                return
            
            if not require_large_send_confirmation(sendable_count, window):
                return

            try:
                save_email_draft(
                    certificates_folder,
                    subject,
                    body,
                    event_title,
                    event_date,
                )

                send_button.config(state="disabled")
                save_button.config(state="disabled")
                window.update_idletasks()

                _, send_report = send_matched_certificates(
                    matches=matches_to_send,
                    subject_template=subject,
                    body_template=body,
                    event_title=event_title,
                    event_date=event_date,
                    client_secret_path=str(resource_path("client_secret.json")),
                    token_path=str(BASE_DIR / "gmail_token.json"),
                    report_output_path=send_report_path,
                )

                message = (
                    f"Matched recipients: {send_report['matched_count']}\n"
                    f"Sent successfully: {send_report['sent_count']}\n"
                    f"Failed: {send_report['failed_count']}\n\n"
                    f"Send report saved to:\n{send_report_path}"
                )

                if send_report["failed_count"] > 0 and send_report.get("first_error"):
                    message += f"\n\nFirst error:\n{send_report['first_error']}"

                messagebox.showinfo(
                    "Email sending complete",
                    message,
                    parent=window,
                )

            except GmailAuthError as e:
                messagebox.showerror("Gmail setup required", str(e), parent=window)
            except Exception as e:
                messagebox.showerror(
                    "Email sending failed",
                    f"Something went wrong:\n{e}",
                    parent=window,
                )
            finally:
                send_button.config(state="normal")
                save_button.config(state="normal")

        button_frame = tk.Frame(window, bg=BG_COLOR)
        button_frame.grid(row=5, column=0, columnspan=2, padx=20, pady=(10, 20), sticky="ew")
        button_frame.columnconfigure(0, weight=1)
        button_frame.columnconfigure(1, weight=1)

        save_button = tk.Button(
            button_frame,
            text="Save Draft",
            command=save_email_draft_button,
            bg=GOOGLE_BLUE,
            fg=WHITE,
            activebackground="#3367D6",
            activeforeground=WHITE,
            relief="flat",
            bd=0,
            cursor="hand2",
            font=("Helvetica", 11, "bold"),
            padx=12,
            pady=10,
        )
        save_button.grid(row=0, column=0, padx=(0, 8), sticky="ew")

        send_button = tk.Button(
            button_frame,
            text="Send Matched Emails",
            command=send_emails_button,
            bg=GOOGLE_GREEN,
            fg=WHITE,
            activebackground="#2D9249",
            activeforeground=WHITE,
            relief="flat",
            bd=0,
            cursor="hand2",
            font=("Helvetica", 11, "bold"),
            padx=12,
            pady=10,
        )
        send_button.grid(row=0, column=1, padx=(8, 0), sticky="ew")

        window.rowconfigure(2, weight=1)
        window.rowconfigure(4, weight=1)
        
    def prepare_email_distribution_button() -> None:
        if not filepath.get():
            messagebox.showerror(
                "No CSV file selected",
                "Please select a CSV file before previewing email distribution",
            )
            return

        checked_in_only = eligibility_var.get() == "Checked-in only"

        if not checked_in_only:
            confirm = messagebox.askyesno(
                "Confirm preview",
                "You selected 'All registrants'. This will prepare certificate distribution for everyone with a valid name and email, even if they did not check in.\n\nDo you want to continue?"
            )
            if not confirm:
                return

        certificates_folder = select_certificates_folder()
        if not certificates_folder:
            return

        try:
            recipients, recipient_report = read_recipients_from_file(
                filepath.get(),
                checked_in_only=checked_in_only,
            )

            matches, match_report = match_certificates_to_recipients(
                recipients,
                certificates_folder,
            )

            report_output_path = os.path.join(certificates_folder, "distribution_report.csv")
            saved_report_path = export_distribution_report(matches, report_output_path)

            mode_label = "Checked-in only" if checked_in_only else "All registrants"
            skipped_not_checked_in_text = (
                str(recipient_report["skipped_not_checked_in"])
                if checked_in_only else "N/A"
            )

            summary_message = (
                f"Email distribution preview is ready.\n\n"
                f"Eligibility mode: {mode_label}\n"
                f"Total rows in CSV: {recipient_report['total_rows']}\n"
                f"Eligible rows: {recipient_report['eligible_count']}\n"
                f"Recipients with valid email: {recipient_report['recipients_count']}\n"
                f"Skipped not checked in: {skipped_not_checked_in_text}\n"
                f"Skipped missing first/last name: {recipient_report['skipped_missing_name']}\n"
                f"Skipped missing email: {recipient_report['skipped_missing_email']}\n\n"
                f"Certificate files found: {match_report['certificate_files_found']}\n"
                f"Matched recipients: {match_report['matched_count']}\n"
                f"Missing certificate file: {match_report['missing_certificate_count']}\n"
                f"Ambiguous certificate matches: {match_report['ambiguous_certificate_count']}\n\n"
                f"Report saved to:\n{saved_report_path}"
            )

            messagebox.showinfo("Distribution preview", summary_message)

            if match_report["matched_count"] > 0:
                open_email_template_window(matches, match_report, certificates_folder)

        except ValueError as e:
            messagebox.showerror("Distribution Error", str(e))
        except Exception as e:
            messagebox.showerror("Distribution Error", f"Something went wrong:\n{e}")

    generate_button = tk.Button(
        root,
        text="Generate Certificates",
        command=generate_certificates_button,
        bg=GOOGLE_GREEN,
        fg=WHITE,
        activebackground="#2D9249",
        activeforeground=WHITE,
        relief="flat",
        bd=0,
        cursor="hand2",
        font=button_font,
        height=1,
        padx=12,
        pady=12,
    )
    generate_button.grid(row=9, column=0, columnspan=2, sticky="ew", padx=20, pady=8)

    preview_button = tk.Button(
        root,
        text="Preview Single Certificate",
        command=preview_single_certificate_button,
        bg=GOOGLE_YELLOW,
        fg=WHITE,
        activebackground="#E2A800",
        activeforeground=WHITE,
        relief="flat",
        bd=0,
        cursor="hand2",
        font=button_font,
        height=1,
        padx=12,
        pady=12,
    )
    preview_button.grid(row=10, column=0, columnspan=2, sticky="ew", padx=20, pady=8)

    single_generate_button = tk.Button(
        root,
        text="Generate Single Certificate",
        command=generate_single_certificate_button,
        bg=GOOGLE_BLUE,
        fg=WHITE,
        activebackground="#3367D6",
        activeforeground=WHITE,
        relief="flat",
        bd=0,
        cursor="hand2",
        font=button_font,
        height=1,
        padx=12,
        pady=12,
    )
    single_generate_button.grid(row=11, column=0, columnspan=2, sticky="ew", padx=20, pady=8)

    upload_button = tk.Button(
        root,
        text="Upload to Drive",
        command=upload_to_drive,
        bg=GOOGLE_RED,
        fg=WHITE,
        activebackground="#C5221F",
        activeforeground=WHITE,
        relief="flat",
        bd=0,
        cursor="hand2",
        font=button_font,
        height=1,
        padx=12,
        pady=12,
    )
    upload_button.grid(row=12, column=0, columnspan=2, sticky="ew", padx=20, pady=8)

    distribute_button = tk.Button(
        root,
        text="Preview Email Distribution",
        command=prepare_email_distribution_button,
        bg=GOOGLE_GREEN,
        fg=WHITE,
        activebackground="#2D9249",
        activeforeground=WHITE,
        relief="flat",
        bd=0,
        cursor="hand2",
        font=button_font,
        height=1,
        padx=12,
        pady=12,
    )
    distribute_button.grid(row=13, column=0, columnspan=2, sticky="ew", padx=20, pady=(8, 20))

    root.mainloop()


if __name__ == "__main__":
    setup_gui()