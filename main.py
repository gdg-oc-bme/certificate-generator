import pandas as pd
import cv2
from google.oauth2 import credentials
from googleapiclient.discovery import build
# Importing necessary libraries for JPG/PDF editing and Google Drive API, may or may not import ReportLab to manage PDFs.

# This function will take a .csv file (downloaded from the community page), filter out names with special characters and people who were not checked in, and return the rest of names as a list.
def read_names_from_file(filename):
    df = pd.read_csv(filename)
    df = df[df['Checkin Date (UTC)'].notna()]
    df['Full Name'] = df['First Name'] + ' ' + df['Last Name']
    regex_pattern = r"^[A-Za-zÀ-ÖØ-öø-ÿ\s'-]+$"
    df_filtered = df[df['Full Name'].str.contains(regex_pattern)]
    attendees_list = df_filtered['Full Name'].tolist()
    return attendees_list

# Use the function above to create the list from your .csv file, sample below.
attendees_list = read_names_from_file('developer-student-clubs-budapest-university-of-technology-and-economics-presents-why-isnt-there-only-one-programming-language.csv')
# You can also print the list to see what it looks like.
print(attendees_list)

# This function will take your certificate template and attendees list as parameters, and its output will be .jpg certificates corresponding to each name in the list.
def edit_certificate(template_path, attendees_list):
    list_of_jpgs_paths = []
    for index, name in enumerate(attendees_list):
        template = cv2.imread("Certificates_GDSC_BME_page-0001.jpg")
        cv2.putText(template, name, (1100,556), cv2.FONT_ITALIC, 1.7, (0,0,0), 2, cv2.LINE_4) # You should play around with this line to customize the printing on the .jpg
        output_path = rf'GeneratedCertificates\{name}.jpg'
        cv2.imwrite(output_path, template)
        list_of_jpgs_paths.append(output_path)
        print('Processing Certificate {}/{}'.format(index+1, len(attendees_list)))
    return list_of_jpgs_paths

""" Sample functions only.
def authenticate_google_drive(): 
    # Authenticate and return Google Drive service instance
    pass

def upload_to_google_drive(pdf_paths, drive_service):
    # Upload PDFs to Google Drive
    pass
"""