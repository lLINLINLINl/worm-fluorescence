"""Convert explicitly declared preview-panel coordinates to source pixels.

No detection, clipping, rotation inference, or source-image writes.
"""
from pathlib import Path
import argparse
import copy
import json
import numpy as np


def pair(value, name, minimum=None):
    a = np.asarray(value, dtype=float)
    if a.shape != (2,) or not np.isfinite(a).all() or (minimum is not None and (a < minimum).any()):
        raise ValueError('Invalid ' + name)
    return a


def to_original(points, transform, source_size):
    if not isinstance(transform, dict) or set(transform) != {'canvas_size', 'panel_origin', 'panel_size'}:
        raise ValueError('Declare canvas_size, panel_origin and panel_size; no inferred offsets')
    canvas = pair(transform['canvas_size'], 'canvas_size', 2)
    origin = pair(transform['panel_origin'], 'panel_origin', 0)
    panel = pair(transform['panel_size'], 'panel_size', 2)
    size = pair(source_size, 'source_size', 2)
    if (origin + panel > canvas).any():
        raise ValueError('Panel outside canvas')
    p = np.asarray(points, dtype=float)
    if p.ndim != 2 or p.shape[1] != 2 or len(p) < 3 or not np.isfinite(p).all():
        raise ValueError('Invalid preview points')
    local = p - origin
    if (local < 0).any() or (local > panel - 1).any():
        raise ValueError('Point outside declared image panel; do not clamp title/neighbor pixels')
    return np.rint(local * (size - 1) / (panel - 1)).astype(int).tolist()


def convert(selections, inventory):
    import workflow as wf
    sources = {s['image_id']: s for s in inventory}
    result = copy.deepcopy(selections)
    seen = set()
    for selection in result:
        identifier = selection['image_id']
        if identifier in seen or identifier not in sources:
            raise ValueError('Duplicate/unknown image ID')
        seen.add(identifier)
        source = sources[identifier]
        transform = selection.pop('preview_transform')
        for worm in selection['worms']:
            if 'points_original' in worm or 'points_view' in worm:
                raise ValueError('Ambiguous mixed coordinate fields; provide points_canvas only')
            canvas_points = worm.pop('points_canvas')
            worm['points_original'] = to_original(canvas_points, transform, [source['width'], source['height']])
            wf.points(selection, source, worm)  # Existing polygon/geometry validation.
            worm['coordinate_conversion'] = dict(transform=transform, points_canvas=canvas_points,
                                                  source_sha256=source['source_sha256'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', required=True)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    read = lambda p: json.loads(Path(p).read_text(encoding='utf-8-sig'))
    result = convert(read(args.input), read(args.inventory))
    # Exclusive creation preserves the input and any existing ROI document.
    with Path(args.output).open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(dict(images=len(result), output=str(Path(args.output).resolve()),
                          next='Overlay on the original image and review both sides before measurement')))


if __name__ == '__main__':
    main()
