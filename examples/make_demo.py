"""Generate SYNTHETIC software fixtures. These are not experimental worms or accuracy benchmarks."""
from pathlib import Path
import argparse
import json

from PIL import Image, ImageDraw


def generate(output):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new demo directory")
    source = output / "input/DEMO"
    source.mkdir(parents=True)
    fixtures = [
        ("01_SYNTHETIC_overlap.png", [dict(worm_id="W01", points_original=[[20, 35], [140, 35], [140, 55], [20, 55]]),
                                     dict(worm_id="W02", points_original=[[70, 15], [90, 15], [90, 100], [70, 100]])]),
        ("02_SYNTHETIC_border.png", [dict(worm_id="W01", points_original=[[0, 25], [30, 25], [30, 36], [0, 36]])]),
        ("03_SYNTHETIC_empty.png", [])]
    selections = []
    for index, (filename, worms) in enumerate(fixtures, 1):
        image = Image.new("RGB", (180, 130), (3, 8, 2))
        draw = ImageDraw.Draw(image)
        for j, worm in enumerate(worms):
            draw.polygon([tuple(p) for p in worm["points_original"]], fill=(20 + j * 20, 100 + j * 80, 5))
        if index == 1:
            draw.rectangle([25, 40, 35, 50], fill=(20, 255, 5))
            draw.ellipse([8, 38, 12, 42], fill=(255, 255, 255))
        image.save(source / filename)
        selections.append(dict(image_id="DEMO_" + str(index).zfill(3), status="ready",
            decision_source="SYNTHETIC SOFTWARE FIXTURE; NOT EXPERIMENTAL; generated polygons are not real worm templates",
            annotation_coverage="complete", excluded=[], worms=worms, **({"empty_reviewed": True} if not worms else {})))
    (output / "demo_selections.json").write_text(json.dumps(selections, indent=2), encoding="utf-8")
    (output / "SYNTHETIC_NOT_EXPERIMENTAL.txt").write_text(
        "这是合成的软件测试图，不是实验数据；不能用于证明真实线虫识别精度。矩形仅用于坐标、重合和数值回归。\n", encoding="utf-8")
    return dict(synthetic_not_experimental=True, source=str(output / "input"), selections=str(output / "demo_selections.json"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    print(json.dumps(generate(parser.parse_args().output), ensure_ascii=False))
