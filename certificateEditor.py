from PIL import Image, ImageDraw, ImageFont
import numpy as np

# This function will take your certificate template and attendees list as parameters, and its output will be .jpg certificates corresponding to each name in the list.
def edit_certificate(template_path, attendees_list, font_path):
    list_of_jpgs_paths = []
    for index, name in enumerate(attendees_list):
        template = Image.open(template_path)
        draw = ImageDraw.Draw(template)
        font_size = 50
        font = ImageFont.truetype(font_path, font_size)
        text_width = draw.textlength(name, font=font)
        # The X coordinate is the midpoint of the line minus half the text width. The midpoint of the line is 1210 in this case.
        startX = 1210 - (text_width // 2)
        startY = 502 # will probably need some tweaking
        draw.text((startX, startY), name, (0, 0, 0), font=font)
        output_path = rf'GeneratedCertificates\{name}.jpg'
        template.save(output_path)
        list_of_jpgs_paths.append(output_path)
        print(f'Processing Certificate {index+1}/{len(attendees_list)}')
    return list_of_jpgs_paths

font_path = "Open_Sans\static\OpenSans-Bold.ttf"