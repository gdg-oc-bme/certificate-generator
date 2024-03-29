from PIL import Image, ImageDraw, ImageFont
import numpy as np

name_font_path = "Open_Sans\static\OpenSans-Bold.ttf"
regular_font_path = "Open_Sans\static\OpenSans-Regular.ttf"
# This function will take parameters from the user input via the GUI, and its output will be .jpg certificates corresponding to each name in the .csv
def edit_certificate(template_path, attendees_list, eventTitle, eventDate, name_font_path, regular_font_path):
    list_of_jpgs_paths = []
    for index, name in enumerate(attendees_list):
        template = Image.open(template_path)
        draw = ImageDraw.Draw(template)
        # font_size = 50 for old template
        font_size = 40
        font = ImageFont.truetype(name_font_path, font_size)
        text_width = draw.textlength(name, font=font)
        # The X coordinate is the midpoint of the line minus half the text width. The midpoint of the line is 1210 in this case.
        # The following two coordinates are the coordinates of the old template. A new template is being tested now.
        #startX = 1210 - (text_width // 2)
        #startY = 502 # will probably need some tweaking
        startX = 577 - (text_width // 2)
        startY = 242

        # Set the font size for the eventTitle and eventDate
        eventTitle_font_size = 20
        eventTitle_font = ImageFont.truetype(regular_font_path, eventTitle_font_size)

        eventDate_font_size = 15
        eventDate_font = ImageFont.truetype(regular_font_path, eventDate_font_size)

        eventTitle_text_width = draw.textlength(eventTitle, font=eventTitle_font)
        eventDate_text_width = draw.textlength(eventDate, font=eventDate_font)
        # Set the starting coordinates for the eventTitle and eventDate
        eventTitle_startX = 577 - (eventTitle_text_width // 2)  # adjust these values as needed, this is to centralize text
        eventTitle_startY = 335  # adjust these values as needed
        eventDate_startX = 577 - (eventDate_text_width // 2)  # adjust these values as needed, this is to centralize text
        eventDate_startY = 360  # adjust these values as needed
        
        draw.text((eventTitle_startX, eventTitle_startY), eventTitle, (0, 0, 0), font=eventTitle_font)
        draw.text((eventDate_startX, eventDate_startY), eventDate, (0, 0, 0), font=eventDate_font)
        draw.text((startX, startY), name, (0, 0, 0), font=font)

        output_path = rf'GeneratedCertificates\{name}.jpg'
        template.save(output_path)
        list_of_jpgs_paths.append(output_path)
        print(f'Processing Certificate {index+1}/{len(attendees_list)}')
    return list_of_jpgs_paths

