#!/usr/bin/python3
"""Render the app's simple native graphic, as SVG and PNG, without an AI asset."""
from pathlib import Path
import cairo

OUT = Path(__file__).resolve().parent / 'boot'


def draw(surface):
    c = cairo.Context(surface)
    c.set_source_rgb(16/255, 32/255, 45/255)
    c.paint()
    c.set_line_width(4)
    c.set_line_join(cairo.LINE_JOIN_ROUND)
    c.set_source_rgb(121/255, 223/255, 178/255)
    c.move_to(151, 68)
    for x, y in [(186, 68), (202, 84), (202, 140), (151, 140)]:
        c.line_to(x, y)
    c.close_path()
    c.stroke()
    for x in (161, 174, 187):
        c.rectangle(x, 80, 5, 14)
        c.fill()
    c.move_to(222, 105)
    c.line_to(258, 105)
    c.move_to(247, 95)
    c.line_to(258, 105)
    c.line_to(247, 115)
    c.stroke()
    c.rectangle(280, 80, 62, 52)
    c.stroke()
    c.move_to(280, 117)
    c.line_to(342, 117)
    c.stroke()
    c.arc(329, 125, 2, 0, 6.2832)
    c.fill()
    c.select_font_face('DejaVu Sans', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    c.set_font_size(29)
    c.set_source_rgb(243/255, 247/255, 250/255)
    text = 'SAUVEGARDE SD'
    x, y, w, h, _, _ = c.text_extents(text)
    c.move_to((480-w)/2-x, 202)
    c.show_text(text)
    c.set_source_rgb(121/255, 223/255, 178/255)
    c.rectangle(216, 225, 48, 3)
    c.fill()
    surface.flush()


if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    image = cairo.ImageSurface(cairo.FORMAT_RGB24, 480, 320)
    draw(image)
    image.write_to_png(str(OUT / 'background.png'))
    vector = cairo.SVGSurface(str(OUT / 'background.svg'), 480, 320)
    draw(vector)
    vector.finish()
