from google.oauth2 import credentials
from googleapiclient.discovery import build
from certificateEditor import edit_certificate, font_path
import csvReader


attendees_list = csvReader.read_names_from_file(csvReader.filename)
# You can also print the list to see what it looks like.
print(attendees_list)

edit_certificate("Certificates_GDSC_BME_page-0001.jpg", attendees_list, font_path)
