"""
彩色/黑白页面识别 - 命令行原型
用法:
    python color_detect.py input.pdf
    python color_detect.py input.pdf --threshold 2 --dpi 100
"""
import sys
import base64
import argparse
from pathlib import Path
import fitz  # PyMuPDF
import numpy as np

# 默认打印单价（元/页）—— HTML/CLI/GUI 共用，可被 GUI 覆盖
COLOR_PRICE = 1.0
BW_PRICE = 0.1

# Windows 控制台中文输出
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def analyze_page(pix: fitz.Pixmap) -> tuple[int, int, float]:
    """返回 (总像素数, 彩色像素数, 彩色像素占比)。
    判定: RGB 三通道最大差值 > 30 视为彩色像素（经验阈值）。
    """
    arr = np.frombuffer(pix.samples, dtype=np.uint8)
    n_channels = pix.n  # RGB=3, 灰度=1, CMYK=4
    h, w = pix.height, pix.width
    total = h * w

    if n_channels <= 1:
        # 纯灰度图，直接黑白
        return total, 0, 0.0

    # 取 R/G/B（CMYK 时取前 3 通道近似）
    arr = arr.reshape(h, w, n_channels)
    rgb = arr[:, :, :3].astype(np.int16)
    maxc = rgb.max(axis=2)
    minc = rgb.min(axis=2)
    diff = maxc - minc
    color_pixels = int((diff > 30).sum())

    return total, color_pixels, color_pixels / total


def detect(pdf_path: str, threshold_pct: float, dpi: int, preview: bool):
    doc = fitz.open(pdf_path)
    print(f"PDF: {pdf_path}")
    print(f"总页数: {len(doc)}  |  彩色判定阈值: {threshold_pct}%  |  渲染 DPI: {dpi}")
    print("-" * 60)

    results = []  # (page_num, ratio_pct, is_color)
    color_pages, bw_pages = [], []

    for i, page in enumerate(doc, start=1):
        pix = page.get_pixmap(dpi=dpi)
        total, color_px, ratio = analyze_page(pix)
        ratio_pct = ratio * 100
        is_color = ratio_pct >= threshold_pct
        (color_pages if is_color else bw_pages).append(i)
        tag = "彩" if is_color else "黑"
        bar = "█" * min(int(ratio_pct), 30)
        print(f"P{i:>3}  [{tag}]  彩色像素 {ratio_pct:6.2f}%  {bar}")
        results.append((i, ratio_pct, is_color))

    print("-" * 60)
    print(f"彩色页: {len(color_pages)} 页  ->  {color_pages}")
    print(f"黑白页: {len(bw_pages)} 页  ->  {bw_pages}")

    # 价格估算（示例价：彩 1 元/页，黑白 0.1 元/页）
    color_price, bw_price = COLOR_PRICE, BW_PRICE
    total_price = len(color_pages) * color_price + len(bw_pages) * bw_price
    print(f"\n价格估算（彩 {color_price} 元/页，黑白 {bw_price} 元/页）:")
    print(f"  彩色: {len(color_pages)} × {color_price} = {len(color_pages)*color_price:.2f} 元")
    print(f"  黑白: {len(bw_pages)} × {bw_price} = {len(bw_pages)*bw_price:.2f} 元")
    print(f"  合计: {total_price:.2f} 元")
    print(f"  vs 全彩打印: {len(doc) * color_price:.2f} 元（省 {len(doc)*color_price - total_price:.2f} 元）")

    doc.close()
    return results, color_pages, bw_pages


def split_pdf(pdf_path: str, color_pages: list[int], bw_pages: list[int]):
    """按判定结果拆成两份 PDF：{stem}_彩色.pdf 和 {stem}_黑白.pdf"""
    src = fitz.open(pdf_path)
    stem = Path(pdf_path).stem
    outputs = []

    for pages, suffix in [(color_pages, "彩色"), (bw_pages, "黑白")]:
        if not pages:
            print(f"跳过 {suffix} 部分（无对应页面）")
            continue
        out = fitz.open()
        for p in pages:
            out.insert_pdf(src, from_page=p - 1, to_page=p - 1)  # 0-indexed
        out_path = f"{stem}_{suffix}.pdf"
        out.save(out_path)
        out.close()
        outputs.append(out_path)
        print(f"✓ 已生成 {out_path}  ({len(pages)} 页)")

    src.close()
    return outputs


HTML_CSS = """
* { box-sizing: border-box; }
body { font-family: -apple-system, "Microsoft YaHei", "Segoe UI", sans-serif; margin: 0; padding: 24px; background: #f5f5f7; color: #1d1d1f; }
h1 { font-size: 22px; margin: 0 0 4px 0; }
.note { color: #6e6e73; font-size: 13px; margin-bottom: 20px; }
.summary { display: flex; gap: 12px; margin: 16px 0 20px 0; flex-wrap: wrap; }
.stat { background: white; padding: 14px 20px; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); min-width: 140px; }
.stat .num { font-size: 26px; font-weight: 700; line-height: 1.2; }
.stat .lbl { color: #6e6e73; font-size: 12px; margin-top: 2px; }
.stat.color .num { color: #d32f2f; }
.stat.bw .num { color: #555; }
.stat.save { background: #e8f5e9; }
.stat.save .num { color: #2e7d32; }
.legend { font-size: 12px; color: #6e6e73; margin: 8px 0 14px 0; }
.legend .edge-mark { display: inline-block; width: 14px; height: 10px; border: 2px dashed #f9a825; vertical-align: middle; margin: 0 6px 0 2px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }
.card { background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08); border-left: 4px solid #999; transition: transform 0.1s; }
.card:hover { transform: translateY(-2px); }
.card.color { border-left-color: #d32f2f; }
.card.edge { outline: 2px dashed #f9a825; outline-offset: -2px; }
.card img { width: 100%; height: 230px; object-fit: contain; background: #fafafa; border-bottom: 1px solid #eee; display: block; }
.meta { padding: 8px 12px; display: flex; flex-direction: column; gap: 3px; }
.meta .row { display: flex; justify-content: space-between; align-items: center; }
.meta .page { font-weight: 600; font-size: 14px; }
.meta .badge { padding: 2px 8px; border-radius: 10px; font-size: 11px; }
.card.color .badge { background: #ffebee; color: #d32f2f; }
.card.bw .badge { background: #f0f0f0; color: #555; }
.meta .ratio { color: #6e6e73; font-size: 11px; }
"""


def generate_html_report(pdf_path: str, results: list, color_pages: list[int],
                         bw_pages: list[int], threshold_pct: float):
    """生成自包含 HTML 报告（缩略图 base64 内嵌，单文件可邮件发送）"""
    src = fitz.open(pdf_path)
    stem = Path(pdf_path).stem
    total_pages = len(src)

    # 渲染缩略图
    thumbs = {}
    for i, page in enumerate(src, 1):
        png = page.get_pixmap(dpi=80).tobytes("png")
        thumbs[i] = base64.b64encode(png).decode("ascii")
    src.close()

    color_price, bw_price = COLOR_PRICE, BW_PRICE
    smart_total = len(color_pages) * color_price + len(bw_pages) * bw_price
    full_color_total = total_pages * color_price
    saved = full_color_total - smart_total

    # 页面卡片
    cards_html = []
    for page_num, ratio_pct, is_color in results:
        is_edge = abs(ratio_pct - threshold_pct) < 1.0
        cls = "color" if is_color else "bw"
        if is_edge:
            cls += " edge"
        badge = "彩色" if is_color else "黑白"
        edge_tip = ' · 边缘案例' if is_edge else ''
        cards_html.append(f'''
        <div class="card {cls}">
            <img src="data:image/png;base64,{thumbs[page_num]}" alt="P{page_num}" />
            <div class="meta">
                <div class="row">
                    <span class="page">P{page_num}</span>
                    <span class="badge">{badge}</span>
                </div>
                <span class="ratio">彩色像素 {ratio_pct:.2f}%{edge_tip}</span>
            </div>
        </div>''')

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{stem} - 彩色识别报告</title>
<style>{HTML_CSS}</style>
</head>
<body>
<h1>彩色/黑白页面识别报告</h1>
<div class="note">{stem}.pdf · 共 {total_pages} 页 · 彩色判定阈值 {threshold_pct}%</div>

<div class="summary">
    <div class="stat color"><div class="num">{len(color_pages)}</div><div class="lbl">彩色页数 · {color_price:.1f} 元/页</div></div>
    <div class="stat bw"><div class="num">{len(bw_pages)}</div><div class="lbl">黑白页数 · {bw_price:.1f} 元/页</div></div>
    <div class="stat"><div class="num">{smart_total:.2f} 元</div><div class="lbl">智能打印合计</div></div>
    <div class="stat save"><div class="num">省 {saved:.2f} 元</div><div class="lbl">vs 全彩 {full_color_total:.2f} 元</div></div>
</div>

<div class="legend"><span class="edge-mark"></span>虚线 = 边缘案例（彩色像素占比与阈值相差不到 1%，建议人工核对）</div>

<div class="grid">
{''.join(cards_html)}
</div>
</body>
</html>
"""

    out_path = f"{stem}_报告.html"
    Path(out_path).write_text(html, encoding="utf-8")
    print(f"✓ 已生成 {out_path}  （{total_pages} 页缩略图，自包含 HTML）")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="PDF 彩色/黑白页面识别")
    ap.add_argument("pdf", help="PDF 文件路径")
    ap.add_argument("--threshold", type=float, default=2.0,
                    help="彩色像素占比阈值 (%%)，默认 2")
    ap.add_argument("--dpi", type=int, default=100,
                    help="渲染 DPI，默认 100（越高越准但越慢）")
    ap.add_argument("--split", action="store_true",
                    help="拆分成 {stem}_彩色.pdf 和 {stem}_黑白.pdf 两份文件")
    ap.add_argument("--preview", action="store_true",
                    help="生成 {stem}_报告.html（含每页缩略图与判定标签）")
    args = ap.parse_args()

    results, color_pages, bw_pages = detect(args.pdf, args.threshold, args.dpi, preview=False)
    if args.split:
        print("\n拆分中...")
        split_pdf(args.pdf, color_pages, bw_pages)
    if args.preview:
        print("\n生成 HTML 报告中（渲染缩略图，稍等）...")
        generate_html_report(args.pdf, results, color_pages, bw_pages, args.threshold)


if __name__ == "__main__":
    main()
