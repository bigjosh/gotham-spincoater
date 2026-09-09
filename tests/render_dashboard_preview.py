"""Render the real dashboard draw calls using an approximate host bitmap font."""
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'device'))
from dashboard import Dashboard


def color(value):
    return ((value >> 11) * 255 // 31,
            ((value >> 5) & 63) * 255 // 63, (value & 31) * 255 // 31)


class PreviewDisplay:
    def __init__(self):
        self.image = Image.new('RGB', (480, 320))
        self.font = ImageFont.truetype('C:/Windows/Fonts/consola.ttf', 10)

    def fill(self, value):
        self.fill_rect(0, 0, 480, 320, value)

    def fill_rect(self, x, y, width, height, value):
        ImageDraw.Draw(self.image).rectangle(
            (x, y, x + width - 1, y + height - 1), fill=color(value))

    def hline(self, x, y, width, value):
        self.fill_rect(x, y, width, 1, value)

    def text(self, value, x, y, foreground, scale=1, bg=0):
        for index, char in enumerate(value):
            tile = Image.new('RGB', (8, 8), color(bg))
            ImageDraw.Draw(tile).text((0, -2), char, font=self.font, fill=color(foreground))
            tile = tile.resize((8 * scale, 8 * scale), Image.Resampling.NEAREST)
            self.image.paste(tile, (x + 8 * scale * index, y))


if __name__ == '__main__':
    display = PreviewDisplay()
    Dashboard(display).update({
        'state': 'IDLE', 'running': False, 'target_rpm': 0,
        'recipe_name': 'Standard coating', 'elapsed_s': 0,
        'step': 0, 'step_count': 3, 'phase_remaining_s': None,
        'message': 'Tap ENABLE / DISABLE to choose participating fans', 'ssid': 'Gotham Spinner',
        'ip': '192.168.4.1', 'loop_lag_ms': 4,
        'fans': [{'enabled': i in (0, 1, 3), 'available': True, 'rpm': 0,
                  'valid': False, 'duty': 0, 'error': 0, 'fault': None}
                 for i in range(6)],
    })
    path = ROOT / 'artifacts' / 'dashboard-preview.png'
    path.parent.mkdir(exist_ok=True)
    display.image.resize((960, 640), Image.Resampling.NEAREST).save(path)
    print(path)
