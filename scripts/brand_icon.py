#!/usr/bin/env python3
"""Precisely composite Hank's approved general H; never use a branded input."""
from pathlib import Path
import base64
import io
import json
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]


def main():
    original = Image.open(ROOT/'assets/originals/simulator-manager-app-icon.png').convert('RGBA')
    watermark = Image.open(ROOT/'assets/branding/h-watermark-general-italic.png').convert('RGBA')
    watermark = watermark.crop(watermark.getchannel('A').getbbox())
    width = 168
    height = round(watermark.height * width / watermark.width)
    overlay = watermark.resize((width, height), Image.Resampling.LANCZOS)
    x, y = 740, 728  # Blank lower-right interior; avoids the routing ring and device panels.
    branded = original.copy()
    branded.alpha_composite(overlay, (x, y))
    difference = ImageChops.difference(original, branded)
    region = (x, y, x + width, y + height)
    outside = difference.copy()
    outside.paste((0, 0, 0, 0), region)
    assert all(channel.getbbox() is None for channel in outside.split()), 'Original pixels changed outside H'
    branded.save(ROOT/'assets/simulator-manager-app-icon.png')
    branded.save(ROOT/'assets/SimulatorManager.icns', format='ICNS')
    branded.save(ROOT/'website/simulator-manager-icon.png')
    # Keep existing SVG designs intact, adding the same approved layer in their empty areas.
    stream = io.BytesIO()
    watermark.resize((128, round(watermark.height * 128 / watermark.width)), Image.Resampling.LANCZOS).save(stream, format='PNG')
    encoded = base64.b64encode(stream.getvalue()).decode('ascii')
    for name, bounds in [('icon', (45, 3, 12, 12)), ('logo', (510, 110, 28, 29))]:
        source = (ROOT/f'assets/originals/plugin-{name}.svg').read_text()
        bx, by, bw, bh = bounds
        layer = f'<image x="{bx}" y="{by}" width="{bw}" height="{bh}" preserveAspectRatio="xMidYMid meet" href="data:image/png;base64,{encoded}"/>\n'
        (ROOT/f'plugins/simulator-manager/assets/{name}.svg').write_text(source.replace('</svg>', layer+'</svg>'))
    canvas = Image.new('RGBA', (720, 230), '#20252e')
    left = 12
    for size in [16, 32, 64, 128, 256]:
        preview = branded.resize((size, size), Image.Resampling.LANCZOS)
        if size == 256:
            preview = branded.resize((192, 192), Image.Resampling.LANCZOS)
        canvas.alpha_composite(preview, (left, 12))
        left += preview.width + 20
    canvas.convert('RGB').save(ROOT/'release/icon-preview.png')
    print(json.dumps({'h_layer': 'general', 'overlay_bounds': region,
                      'outside_overlay_pixels_unchanged': True, 'originals_preserved': True,
                      'icns_sizes': sorted(Image.open(ROOT/'assets/SimulatorManager.icns').info['sizes']),
                      'installed_app_changed': False, 'published': False}))


if __name__ == '__main__':
    main()
