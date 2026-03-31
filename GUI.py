import os
import tempfile
from pathlib import Path
from datetime import datetime
from collections import Counter

import pandas as pd
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import certificateEditor as ce
from driveUpload import Upload, DriveAuthError

import sys
import re

BASE_DIR = Path(__file__).resolve().parent

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
    progress_bar: ttk.Progressbar,
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
    progress_bar: ttk.Progressbar,
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
    progress_bar: ttk.Progressbar,
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
    root.geometry("600x720")
    root.configure(bg="navy")

    logo_image = None
    logo_path = resource_path("logo.png")
    if logo_path.exists():
        try:
            logo_image = tk.PhotoImage(file=str(logo_path)).subsample(3, 3)
        except tk.TclError:
            logo_image = None

    if logo_image is not None:
        logo_label = tk.Label(root, image=logo_image, bg="navy")
        logo_label.image = logo_image
        logo_label.grid(row=14, column=0, columnspan=2, padx=10, pady=30)
    else:
        logo_label = tk.Label(
            root,
            text="GDSCBME Certificate Generator",
            bg="navy",
            fg="white",
            font=("Helvetica", 12, "bold"),
        )
        logo_label.grid(row=14, column=0, columnspan=2, padx=10, pady=30)

    filepath = tk.StringVar()
    eligibility_var = tk.StringVar(value="Checked-in only")
    progress_var = tk.StringVar(value="Ready")

    def select_csv_file_button() -> None:
        filepath.set(select_csv_file() or "")

    csv_button = tk.Button(root, text="Select CSV File", command=select_csv_file_button)
    csv_button.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=10)
    csv_button.config(width=20, height=2, font=("Helvetica", 12))

    event_title_label = tk.Label(root, text="Event Title:", bg="navy", fg="white", font=(9))
    event_title_entry = tk.Entry(root, width=50)

    event_date_label = tk.Label(root, text="Event Date (DD/MM/YYYY):", bg="navy", fg="white", font=(9))
    event_date_entry = tk.Entry(root, width=50)

    participant_name_label = tk.Label(root, text="Participant Name:", bg="navy", fg="white", font=(9))
    participant_name_entry = tk.Entry(root, width=50)

    eligibility_label = tk.Label(root, text="Eligibility Mode:", bg="navy", fg="white", font=(9))
    eligibility_menu = ttk.Combobox(
        root,
        textvariable=eligibility_var,
        values=["Checked-in only", "All registrants"],
        state="readonly",
        width=47,
    )

    progress_label = tk.Label(root, textvariable=progress_var, bg="navy", fg="white", font=("Helvetica", 10, "bold"))
    progress_bar = ttk.Progressbar(root, orient="horizontal", mode="determinate", length=400)

    event_title_label.grid(row=1, column=0, padx=10, pady=5, sticky="w")
    event_title_entry.grid(row=1, column=1, padx=10, pady=5)

    event_date_label.grid(row=2, column=0, padx=10, pady=5, sticky="w")
    event_date_entry.grid(row=2, column=1, padx=10, pady=5)

    participant_name_label.grid(row=3, column=0, padx=10, pady=5, sticky="w")
    participant_name_entry.grid(row=3, column=1, padx=10, pady=5)

    eligibility_label.grid(row=4, column=0, padx=10, pady=5, sticky="w")
    eligibility_menu.grid(row=4, column=1, padx=10, pady=5)

    progress_label.grid(row=5, column=0, columnspan=2, padx=10, pady=(10, 5), sticky="w")
    progress_bar.grid(row=6, column=0, columnspan=2, padx=10, pady=(0, 10), sticky="ew")

    def generate_certificates_button() -> None:
        if not filepath.get():
            messagebox.showerror("No CSV file selected", "Please select a CSV file before generating certificates")
            return
        if not validate_date(event_date_entry.get()):
            messagebox.showerror("Invalid date", "Please enter a valid date in the format DD/MM/YYYY")
            return
        if not event_title_entry.get().strip():
            messagebox.showerror("Missing event title", "Please enter the event title")
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

    generate_button = tk.Button(root, text="Generate Certificates", command=generate_certificates_button)
    generate_button.config(width=20, height=2, font=("Helvetica", 12))
    generate_button.grid(row=7, column=0, columnspan=2, sticky="ew", padx=10, pady=10)

    preview_button = tk.Button(
        root,
        text="Preview Single Certificate",
        command=preview_single_certificate_button,
    )
    preview_button.config(width=20, height=2, font=("Helvetica", 12))
    preview_button.grid(row=8, column=0, columnspan=2, sticky="ew", padx=10, pady=10)

    single_generate_button = tk.Button(
        root,
        text="Generate Single Certificate",
        command=generate_single_certificate_button,
    )
    single_generate_button.config(width=20, height=2, font=("Helvetica", 12))
    single_generate_button.grid(row=9, column=0, columnspan=2, sticky="ew", padx=10, pady=10)

    upload_button = tk.Button(root, text="Upload to Drive", command=upload_to_drive)
    upload_button.config(width=20, height=2, font=("Helvetica", 12))
    upload_button.grid(row=10, column=0, columnspan=2, sticky="ew", padx=10, pady=10)

    root.mainloop()


if __name__ == "__main__":
    setup_gui()