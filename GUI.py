import pandas as pd
import tkinter as tk
from tkinter import simpledialog, filedialog, messagebox
from datetime import datetime
import certificateEditor as ce

def validate_date(date_text):
    try:
        datetime.strptime(date_text, "%d/%m/%Y")
        return True
    except ValueError:
        return False

def select_csv_file():
    filepath = filedialog.askopenfilename(
        title="Select a CSV file",
        filetypes=(("CSV files", "*.csv"), ("All files", "*.*"))
    )
    if filepath:  # Check if a file was selected
        print(f"You selected: {filepath}")
        return filepath  # Return the selected file path
    else:
        return None  # Return None if no file was selected

# This function will take a .csv file (downloaded from the community page), filter out names with special characters and people who were not checked in, and return the rest of names as a list.
def read_names_from_file(filename):
    df = pd.read_csv(filename)
    df = df[df['Checkin Date (UTC)'].notna()]
    df['Full Name'] = df['First Name'] + ' ' + df['Last Name']
    regex_pattern = r"^[A-Za-zÀ-ÖØ-öø-ÿ\s'-]+$"
    df_filtered = df[df['Full Name'].str.contains(regex_pattern)]
    attendees_list = df_filtered['Full Name'].tolist()
    return attendees_list


def generate_certificates(filepath, event_title_entry, event_date_entry):
    if filepath:
        attendees_list = read_names_from_file(filepath)
    ce.edit_certificate("template_certificate_line.jpg", attendees_list, event_title_entry.get(), event_date_entry.get(), ce.font_path)
    print("Generating certificates with the provided details...")


def setup_gui():
    root = tk.Tk()
    root.title("GDSCBME Certificate Generator")
    logo_image = tk.PhotoImage(file="logo.png").subsample(3,3)
    logo_label = tk.Label(root, image=logo_image, bg="navy")
    logo_label.grid(row=9, column=1, columnspan=2, sticky = 'es', padx=10, pady=30)
    root.geometry("600x350")
    root.configure(bg='navy')

    filepath = tk.StringVar()
    def select_csv_file_button():
        filepath.set(select_csv_file())

    csv_button = tk.Button(root, text="Select CSV File", command=select_csv_file_button)
    csv_button.grid(row=0, column=0, columnspan=2, sticky='ew', padx=10, pady=10)
    csv_button.config(width=20, height = 2, font =('Helvetica', 12))

    event_title_label = tk.Label(root, text="Event Title:", bg='navy', fg='white', font =(9))

    event_title_entry = tk.Entry(root, width=50)

    event_date_label = tk.Label(root, text="Event Date (DD/MM/YYYY):", bg='navy', fg='white', font =(9))

    event_date_entry = tk.Entry(root, width = 50)

    event_title_label.grid(row=1, column=0, padx=10, pady=5, sticky='w')
    event_title_entry.grid(row=1, column=1, padx=10, pady=5)
    event_date_label.grid(row=2, column=0, padx=10, pady=5, sticky='w')
    event_date_entry.grid(row=2, column=1, padx=10, pady=5)

    def generate_certificates_button():
        if not validate_date(event_date_entry.get()):
            messagebox.showerror("Invalid date", "Please enter a valid date in the format DD/MM/YYYY")
            return
    generate_certificates(filepath.get(), event_title_entry, event_date_entry)

    generate_button = tk.Button(root, text="Generate Certificates", command=generate_certificates_button)
    generate_button.config(width=20, height = 2, font =('Helvetica', 12))
    generate_button.grid(row=3, column=0, columnspan=2, sticky='ew', padx=10, pady=10)
    
    root.mainloop()