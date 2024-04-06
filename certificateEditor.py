from PIL import Image, ImageDraw, ImageFont
import numpy as np
import os

name_font_path = "Open_Sans\static\OpenSans-Bold.ttf"
regular_font_path = "Open_Sans\static\OpenSans-Regular.ttf"
# This function will take parameters from the user input via the GUI, and its output will be .jpg certificates corresponding to each name in the .csv
def edit_certificate(template_path, attendees_list, eventTitle, eventDate, name_font_path, regular_font_path, save_dir):
    list_of_jpgs_paths = []
    for index, name in enumerate(attendees_list):
        template = Image.open(template_path)
        draw = ImageDraw.Draw(template)
        font_size = 120
        font = ImageFont.truetype(name_font_path, font_size)
        text_width = draw.textlength(name, font=font)
        # The X coordinate is the midpoint of the line minus half the text width. The midpoint of the line is 2353 in this case.

        startX = 2353 - (text_width // 2)
        startY = 1030

        # Set the font size for the eventTitle and eventDate
        eventTitle_font_size = 65
        eventTitle_font = ImageFont.truetype(regular_font_path, eventTitle_font_size)

        eventDate_font_size = 65
        eventDate_font = ImageFont.truetype(regular_font_path, eventDate_font_size)

        eventTitle_text_width = draw.textlength(eventTitle, font=eventTitle_font)
        eventDate_text_width = draw.textlength(eventDate, font=eventDate_font)
        # Set the starting coordinates for the eventTitle and eventDate
        eventTitle_startX = 2353 - (eventTitle_text_width // 2)  # adjust these values as needed, this is to centralize text
        eventTitle_startY = 1413  # adjust these values as needed
        eventDate_startX = 2353 - (eventDate_text_width // 2)  # adjust these values as needed, this is to centralize text
        eventDate_startY = 1559  # adjust these values as needed
        
        draw.text((eventTitle_startX, eventTitle_startY), eventTitle, (0, 0, 0), font=eventTitle_font)
        draw.text((eventDate_startX, eventDate_startY), eventDate, (0, 0, 0), font=eventDate_font)
        draw.text((startX, startY), name, (0, 0, 0), font=font)
            
        output_path = rf'{save_dir}\{name}.jpg'
        template.save(output_path)
        list_of_jpgs_paths.append(output_path)
        print(f'Processing Certificate {index+1}/{len(attendees_list)}')
    return list_of_jpgs_paths

