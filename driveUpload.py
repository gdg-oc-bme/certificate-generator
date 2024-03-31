import os
import json
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaFileUpload

SCOPES = ['https://www.googleapis.com/auth/drive']
CLIENT_SECRETS = ''
CREDENTIALS_JSON = 'credentials.json'

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
                    encoded_file_name = file.replace("'", "\\'")
                    
                    file_metadata = {
                        'name' : file,
                        'parents' : [parent_folder_id]
                    }
                    query = f"name='{encoded_file_name}' and '{parent_folder_id}' in parents"
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
            encoded_file_name = os.path.basename(file_path).replace("'", "\\'")
            
            file_metadata = {
                'name' : os.path.basename(file_path),
                'parents' : [parent_folder_id]
            }
            query = f"name='{encoded_file_name}' and '{parent_folder_id}' in parents"
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
        
        print('Process completed successfully!')
    except HttpError as error:
        print(f'An error occured: {error}')
        return None