import pandas as pd
import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()  # we don't want a full GUI, so keep the root window from appearing

# Shows an "Open" dialog box and returns the path to the selected file
filename = filedialog.askopenfilename(
    title="Select a CSV file",
    filetypes=(("CSV files", "*.csv"), ("All files", "*.*"))
)

print(f"You selected: {filename}")

# This function will take a .csv file (downloaded from the community page), filter out names with special characters and people who were not checked in, and return the rest of names as a list.
def read_names_from_file(filename):
    df = pd.read_csv(filename)
    df = df[df['Checkin Date (UTC)'].notna()]
    df['Full Name'] = df['First Name'] + ' ' + df['Last Name']
    regex_pattern = r"^[A-Za-zÀ-ÖØ-öø-ÿ\s'-]+$"
    df_filtered = df[df['Full Name'].str.contains(regex_pattern)]
    attendees_list = df_filtered['Full Name'].tolist()
    return attendees_list