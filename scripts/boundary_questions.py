"""Versioned full-frame and context views, available before ImageJ measurement."""
from pathlib import Path
import math
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFont


def font_for(run, wf, size=20):
    path = wf.read(run / 'workflow.json', {}).get('font')
    return ImageFont.truetype(path, size) if path and Path(path).is_file() else ImageFont.load_default(size=size)


def overlay(image, contours, font, colors=None, markers=(), offset=(0, 0)):
    result = image.copy()
    draw = ImageDraw.Draw(result)
    boxes = []
    for index, (identifier, points) in enumerate(contours):
        color = (colors or {}).get(identifier, ['#ff5555', '#4bcaff', '#f7d34a', '#e98aff'][index % 4])
        local = np.asarray(points) - np.asarray(offset)
        if local[:, 0].max() < 0 or local[:, 1].max() < 0 or local[:, 0].min() >= result.width or local[:, 1].min() >= result.height:
            continue
        draw.line([tuple(p) for p in local] + [tuple(local[0])], fill=color, width=2)
        size = draw.textbbox((0, 0), identifier, font=font)
        width, height = size[2] - size[0] + 8, size[3] - size[1] + 8
        x = max(0, min(int(local[0, 0]), result.width - width))
        y = max(0, min(int(local[0, 1]), result.height - height))
        for step in range(30):
            box = (x, y, x + width, y + height)
            if not any(x < b[2] and box[2] > b[0] and y < b[3] and box[3] > b[1] for b in boxes):
                break
            y = (y + height + 2) % max(1, result.height - height + 1)
            if step % 8 == 7:
                x = (x + width + 4) % max(1, result.width - width + 1)
        boxes.append(box)
        draw.rectangle(box, fill='#111820')
        draw.text((x + 4, y + 2 - size[1]), identifier, fill=color, font=font)
    for marker in markers:
        x, y = marker['x'] - offset[0], marker['y'] - offset[1]
        if 0 <= x < result.width and 0 <= y < result.height:
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), outline='#ffffff', width=2)
            draw.text((max(0, x - 2), max(0, y - 27)), marker['label'], font=font,
                      fill='#ffffff', stroke_width=2, stroke_fill='#111820')
    return result


def create(wf, run, args):
    inventory, selections = wf.data(run)
    sources = {s['image_id']: s for s in inventory}
    if not args.images or set(args.images) - sources.keys():
        raise ValueError('Question requires known --images')
    if not math.isfinite(args.factor) or not 1 <= args.factor <= 10:
        raise ValueError('Display brightness factor must be in [1,10]')
    markers = wf.read(Path(args.markers), []) if args.markers else []
    if not isinstance(markers, list):
        raise ValueError('Markers must be a list of image_id/x/y/label objects')
    for m in markers:
        if not isinstance(m, dict) or m.get('image_id') not in args.images or not isinstance(m.get('label'), str) or not m['label'].strip():
            raise ValueError('Each marker needs a selected image_id and a label')
        src = sources[m['image_id']]
        if any(type(m.get(k)) is not int for k in ('x', 'y')) or not 0 <= m['x'] < src['width'] or not 0 <= m['y'] < src['height']:
            raise ValueError('Marker outside original pixels')
    destination = wf.inside(run, run / 'review/questions')
    destination.mkdir(parents=True, exist_ok=True)
    rows, markdown = [], []
    font = font_for(run, wf)
    for identifier in args.images:
        src = sources[identifier]
        wf.check_source(src)
        selection = selections.get(identifier, {'worms': []})
        worms = selection['worms']
        requested = set(getattr(args, 'worms', None) or [w['worm_id'] for w in worms])
        if requested - {w['worm_id'] for w in worms}:
            raise ValueError('Unknown --worms for ' + identifier)
        contours = [(w['worm_id'], wf.points(selection, src, w)) for w in worms]
        marked = [m for m in markers if m['image_id'] == identifier]
        box = args.box
        if box is None:
            selected = [p for name, p in contours if name in requested]
            pts = np.concatenate(selected) if selected else np.array([[0, 0], [src['width'] - 1, src['height'] - 1]])
            box = [max(0, int(pts[:, 0].min()) - 40), max(0, int(pts[:, 1].min()) - 40),
                   min(src['width'], int(pts[:, 0].max()) + 41), min(src['height'], int(pts[:, 1].max()) + 41)]
        if len(box) != 4 or any(type(v) is not int for v in box) or not 0 <= box[0] < box[2] <= src['width'] or not 0 <= box[1] < box[3] <= src['height']:
            raise ValueError('Question box requires original [left,top,right,bottom], right/bottom exclusive')
        key = wf.digest(dict(version=1, source=src['source_sha256'], contours=[(n, p.tolist()) for n, p in contours],
                             box=box, factor=args.factor, markers=marked, note=args.note))
        prefix = identifier + '_r' + key[:12]
        with Image.open(src['source_path']) as original:
            raw = original.copy()
        enhanced = ImageEnhance.Brightness(raw).enhance(args.factor)
        full = overlay(enhanced, contours, font, markers=marked)
        ImageDraw.Draw(full).rectangle(tuple(box), outline='#ffffff', width=2)
        detail = raw.crop(tuple(box))
        images = {'full_raw': raw, 'full_enhanced': enhanced, 'full': full, 'detail_raw': detail,
                  'detail_enhanced': ImageEnhance.Brightness(detail).enhance(args.factor),
                  'detail': overlay(ImageEnhance.Brightness(detail).enhance(args.factor), contours, font,
                                    markers=marked, offset=box[:2])}
        paths = {}
        for kind, image in images.items():
            path = destination / (prefix + '_' + kind + '.png')
            if not path.is_file():
                image.save(path)
            paths[kind] = str(path.resolve())
        title = run.name + ' / ' + identifier + ' / ' + src['source_file'] + ' / 修订 ' + key[:12]
        markdown.extend([title + '\n\n' + args.note, '![' + identifier + ' 完整图](<' + paths['full'].replace('\\', '/') + '>)',
                         '![' + identifier + ' 对应局部](<' + paths['detail'].replace('\\', '/') + '>)',
                         '[无圈线原始完整图](<' + paths['full_raw'].replace('\\', '/') + '>) · '
                         '[无圈线增强局部](<' + paths['detail_enhanced'].replace('\\', '/') + '>)'])
        rows.append(dict(image_id=identifier, source_file=src['source_file'], revision=key[:12],
                         display_only=True, coordinate_system='original_pixels', box=box, files=paths))
    document = dict(display_only=True, measurement_changed=False, images=rows,
                    instruction='Embed full and detail images directly in the user-facing message; tool images alone may be invisible.')
    wf.write(destination / 'latest_manifest.json', document)
    (destination / '展示给用户.md').write_text('\n\n'.join(markdown), encoding='utf-8')
    return dict(manifest=str(destination / 'latest_manifest.json'), markdown=str(destination / '展示给用户.md'),
                images=rows, measurement_changed=False)
