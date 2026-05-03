"""
生成 invest_tool 应用图标。

设计：跟侧边栏 Logo 完全一致 — 红底（#dc2626）+ 白色字母 T，圆角矩形。

输出：
  assets/icon.ico  — Windows 多尺寸图标（16/32/48/64/128/256），exe + 任务栏 + 窗口标题栏用
  assets/icon.png  — 256×256 PNG，给前端 favicon / 文档展示用

要重新生成：
  venv/Scripts/python.exe assets/gen_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).resolve().parent
RED = (220, 38, 38, 255)        # #dc2626（与 Sidebar.vue 完全一致）
WHITE = (255, 255, 255, 255)
SIZES = [16, 32, 48, 64, 128, 256]   # Windows 标准 ICO 含的常见尺寸


def _load_bold_font(size_px):
    """挑一个常见的粗体字体；找不到就用 PIL 默认。"""
    candidates = [
        # Windows 默认
        'C:/Windows/Fonts/segoeuib.ttf',     # Segoe UI Bold
        'C:/Windows/Fonts/arialbd.ttf',      # Arial Bold
        'C:/Windows/Fonts/calibrib.ttf',     # Calibri Bold
        # macOS/Linux fallback
        '/System/Library/Fonts/Helvetica.ttc',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size_px)
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


def render_icon(size):
    """渲染单尺寸 PNG（RGBA）。圆角矩形 + 居中白字 T。"""
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = max(2, size // 5)               # 圆角半径 ≈ 1/5 边长
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=RED)

    # T 字号：~70% 边长（视觉接近 Sidebar 那个 T）
    font_size = max(8, int(size * 0.70))
    font = _load_bold_font(font_size)

    # 居中绘制（用 textbbox 算精确尺寸 + 修正字体内置 baseline 偏移）
    text = 'T'
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    # 把 bbox 起点反向偏移，让"实际墨水"完全居中
    x = (size - tw) // 2 - bbox[0]
    y = (size - th) // 2 - bbox[1]
    draw.text((x, y), text, fill=WHITE, font=font)
    return img


def main():
    images = [render_icon(s) for s in SIZES]
    # 多尺寸 ICO：最大尺寸作为基底，其它通过 sizes= 嵌入
    base = images[-1]                          # 256×256 作为主图
    others = [(s, s) for s in SIZES[:-1]]
    base.save(OUT_DIR / 'icon.ico', format='ICO', sizes=others + [(256, 256)])
    base.save(OUT_DIR / 'icon.png', format='PNG')
    print(f'生成完毕：')
    print(f'  {OUT_DIR / "icon.ico"}（多尺寸 ICO，{",".join(map(str, SIZES))}）')
    print(f'  {OUT_DIR / "icon.png"}（256x256 PNG）')


if __name__ == '__main__':
    main()
