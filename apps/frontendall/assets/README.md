# Brand assets

`logo-source.png` is the **master artwork** (1672x941, untouched). Everything
else here is derived from it — regenerate with:

```python
from PIL import Image
src = Image.open('logo-source.png').convert('RGBA')
full = src.crop((228, 155, 1446, 781))   # IAG + INSURANCE + ALLIANCE GROUP
mark = src.crop((312, 155, 1399, 555))   # IAG monogram only

def save(im, path, w):
    h = round(im.height * w / im.width)
    im.resize((w, h), Image.LANCZOS).quantize(colors=256, method=Image.FASTOCTREE).save(path, optimize=True)

save(full, 'logo.png', 760)
save(mark, 'logo-mark.png', 320)
for size, name in ((64, 'favicon.png'), (180, 'apple-touch-icon.png')):
    pad = round(size * 0.06); w = size - pad * 2; h = round(mark.height * w / mark.width)
    c = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    c.paste(mark.resize((w, h), Image.LANCZOS), (pad, (size - h) // 2))
    c.quantize(colors=256, method=Image.FASTOCTREE).save(name, optimize=True)
```

| File | Used by |
|---|---|
| `logo.png` (760x391) | login card, anywhere with >=200px of width |
| `logo-mark.png` (320x118) | sidebar brand, Add-Deal wizard, mobile gate, collapsed rail |
| `favicon.png` (64x64) | `<link rel="icon">` on every page |
| `apple-touch-icon.png` (180x180) | iOS home-screen icon |

The full lockup is unreadable below ~120px wide (the "ALLIANCE GROUP" line
collapses), which is why the sidebar carries the monogram instead.

The artwork is navy on a transparent ground, so in dark mode it is placed on a
white rounded plate rather than recoloured — see the
`html[data-mode="dark"] .sb-brand-logo` rules.
