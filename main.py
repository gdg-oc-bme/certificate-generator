import os
import pickle
import pandas as pd
import cv2
import json
from google.oauth2 import credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaFileUpload

# Importing necessary libraries for JPG/PDF editing and Google Drive API, may or may not import ReportLab to manage PDFs.

SCOPES = ['https://www.googleapis.com/auth/drive']
CLIENT_SECRETS = ''
CREDENTIALS_JSON = 'credentials.json'

#This function will take a .csv file (downloaded from the community page), filter out names with special characters and people who were not checked in, and return the rest of names as a list.
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

# This function handles the authentication process with Google Drive. To prevent requesting consent constantly, the credentials are stored as a pickle
def Authenticate():  
    try:
        if os.path.exists(CREDENTIALS_JSON):
            with open(CREDENTIALS_JSON, 'r') as file:
                credentials_json = json.load(file)
                credentials = Credentials.from_authorized_user_info(credentials_json, scopes=SCOPES)
        else:  
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS, SCOPES)
            credentials = flow.run_local_server()
            credentials_json = credentials.to_json()
            with open(CREDENTIALS_JSON, 'w') as file:
                file.write(credentials_json)
    
        service = build('drive', 'v3', credentials=credentials)
        print('Authentication successful!')
        return service
    
    except HttpError as error:
        print(f'An error occured: {error}')
        return None

# This function checks if the user has the required permissions to upload files to Google Drive. If not, it raises an exception
def CheckPermissions(service, required_permissions):
    try:
        # Fetch current user's permissions
        permissions = service.permissions().list(fileId='root').execute()
        
        # Check if user has required permissions
        for permission in permissions.get('permissions', []):
            role = permission.get('role')
            if role in required_permissions:
                return True
        
        return False
    
    except HttpError as error:
        print(f'An error occurred: {error}')
        return False
  
# This function creates a folder in google drive. The name of the folder can be specified. It checks for duplicate folders 
# It checks if a folder with the specified name already exists in the parent folder (if provided). If not, it creates a new folder and returns its ID. 
def CreateFolder(service, folder_name, parent_id=None):
    try:
        # Check permissions to create folder
        if not CheckPermissions(service, ['owner', 'writer']):
            raise Exception("Insufficient permissions.")
        
        query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder'"
        if parent_id:
            query += f" and '{parent_id}' in parents"
        
        results = service.files().list(q=query, fields='files(id)').execute()
        folders = results.get('files', [])
    
        if folders:
            print('Folder already exists.')
            return folders[0]['id']
        else:        
            folder_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            if parent_id:
                folder_metadata['parents'] = [parent_id]

            folder = service.files().create(body=folder_metadata, fields='id').execute()
            print('Folder created successfully')
            folder_id = folder.get('id')
        # Set permissions
        permission = {
            'type': 'anyone',
            'role': 'reader'
        }   
        service.permissions().create(fileId=folder_id, body=permission).execute()
        print('Folder permission set successfully')
        return folder_id
        
    except HttpError as error:
        print(f'An error occured: {error}')
        return None
    
# This function uploads the files to a specified folder in google drive.  It checks if the parent folder exists and creates it if necessary using the CreateFolder function.
# It avoids uploading duplicate files
# Use case example: Upload("path/to/folder or file", "Name of drive folder to upload to")
def Upload(file_path, parent_folder_name):
    service = Authenticate()
    try:
        # Check for permission to upload files
        if not CheckPermissions(service, ['owner', 'writer']):
            raise Exception("Insufficient permissions.")
        
        parent_folder_id = CreateFolder(service, parent_folder_name)
    
        if os.path.isdir(file_path):
            for root, dirs, files in os.walk(file_path):
                for file in files:
                    file_metadata = {
                        'name' : file,
                        'parents' : [parent_folder_id]
                    }
                    query = f"name='{file}' and '{parent_folder_id}' in parents"
                    results = service.files().list(q=query, fields='files(id)').execute()
                    existing_files = results.get('files', [])
                
                    if existing_files:
                        print(f"File '{file}' already exists in the folder. Skipping upload.")
                    else:
                        file_path = os.path.join(root, file)
                        media = MediaFileUpload(file_path, resumable=True)
                        file = service.files().create(
                            body=file_metadata,
                            media_body=media
                        ).execute()
                        print(f"Uploaded {file['name']}")
            #Uncomment to delete local file
            #os.remove(file_path)
        else:
            file_metadata = {
                'name' : os.path.basename(file_path),
                'parents' : [parent_folder_id]
            }
            query = f"name='{os.path.basename(file_path)}' and '{parent_folder_id}' in parents"
            results = service.files().list(q=query, fields='files(id)').execute()
            existing_files = results.get('files', [])
        
            if existing_files:
                print(f"File '{os.path.basename(file_path)}' already exists in the folder. Skipping upload.")
            else:
                media = MediaFileUpload(file_path, resumable=True)
                file = service.files().create(
                    body=file_metadata,
                    media_body=media
                ).execute()
                print(f"Uploaded {file['name']}")
            #Uncomment to remove local file
            #os.remove(file_path)
    except HttpError as error:
        print(f'An error occured: {error}')
        return None