"""Lossless worm context crops, review overlays and advisory QC. Never changes an ROI."""
from pathlib import Path
import html
import json
import math
from urllib.parse import quote

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance

VERSION = 4
POLICY = dict(context_padding_px=32, area_change_fraction=0.20, saturation_fraction=0.01)


def box_for(points, width, height, padding):
    return [max(0, int(points[:, 0].min()) - padding), max(0, int(points[:, 1].min()) - padding),
            min(width, int(points[:, 0].max()) + padding + 1), min(height, int(points[:, 1].max()) + padding + 1)]


def accepted(wf, run, source, selection):
    review = wf.read(run / "reviews.json", {}).get(source["image_id"], {})
    return review.get("decision") == "accept" and review.get("boundary_key") == wf.boundary_key(selection, source)


def complete(selection):
    return selection.get("annotation_coverage") == "complete" and all(
        item.get("kind") == "non_worm" for item in selection.get("excluded", []))


def valid(wf, run, source, selection, profile):
    return bool(selection and selection.get("status") == "ready" and wf.cache_ok(
        run, wf.read(run / "cache.json", {}).get(source["image_id"]), wf.calculation_key(selection, source, profile)))


def render_card(row):
    escape = html.escape
    link = lambda value: quote(value, safe="/")
    if row.get("Error"):
        question = ('<p><a href="' + link(row['QuestionImage']) + '">查看边界问题放大图</a></p>') if row.get('QuestionImage') else ''
        return '<article class="warn"><h2>' + escape(row["ImageID"]) + '</h2><p>' + escape(row["Error"]) + '</p>' + question + '</article>'
    box = row["ContextBox"]
    width, height = box[2] - box[0], box[3] - box[1]
    shapes = []
    for shape in row["ContextContours"]:
        points = " ".join(str(x) + "," + str(y) for x, y in shape["points"])
        color = "#ff42cf" if shape["active"] else "#36e0ed"
        shapes.append('<polygon points="' + points + '" fill="none" stroke="' + color +
                      '" stroke-width="1.5" vector-effect="non-scaling-stroke"/>')
    flags = "；".join(row["Flags"]) or "无自动提示；仍须看图确认边界"
    return ('<article class="' + ('warn' if row["Flags"] else '') + '"><h2>' + escape(row["WormID"]) +
        ' · ' + escape(row["Group"]) + '</h2><p>' + escape(row["SourceFile"]) + '</p><p>Mean（平均亮度）=' +
        f'{row["Mean"]:.3f}' + '；像素面积=' + str(row["Area_px"]) + '；当前边界验收：' +
        ('已认可' if row["BoundaryAccepted"] else '待确认') + '</p><p class="flags">' + escape(flags) +
        '</p><div class="context" style="aspect-ratio:' + str(width) + '/' + str(height) + '"><img loading="lazy" src="' +
        link(row["RawContext"]) + '" alt="原图局部，无重采样"><svg viewBox="0 0 ' + str(width) + ' ' + str(height) +
        '">' + ''.join(shapes) + '</svg></div><p>粉色：本虫；青色：邻近已标虫。原图局部坐标 ' + escape(str(box)) +
        '，右、下边界不含。</p><p><a href="' + link(row["RawContext"]) + '">原始局部图</a> · <a href="' +
        link(row["OverlayContext"]) + '">轮廓局部图</a> · <a href="' + link(row["FullPreview"]) + '">整图定位</a> · <a href="' +
        link(row["ROISet"]) + '">ImageJ ROI集合</a></p></article>')


def build(wf, run, args):
    inventory, selections = wf.data(run)
    config = wf.read(run / "workflow.json")
    profile = wf.profile(config)
    policy = dict(POLICY)
    for field in policy:
        value = getattr(args, field, None)
        if value is not None:
            policy[field] = value
    if policy["context_padding_px"] < 0 or not 0 <= policy["area_change_fraction"] <= 1 or not 0 <= policy["saturation_fraction"] <= 1:
        raise ValueError("Invalid QC/context policy")
    target_ids = set(getattr(args, "images", None) or [s["image_id"] for s in inventory])
    if target_ids - {s["image_id"] for s in inventory}:
        raise ValueError("Unknown image ID for inspection")
    destination = wf.inside(run, run / "review/contexts")
    destination.mkdir(parents=True, exist_ok=True)
    cache = wf.read(run / "review/assets_cache.json", {})
    references = wf.read(run / "review/area_references.json", {})
    all_rows, generated, hits = [], 0, 0
    for source in inventory:
        identifier = source["image_id"]
        selection = selections.get(identifier)
        wf.check_source(source)
        if not valid(wf, run, source, selection, profile):
            pending = bool(selection and selection.get('status') == 'pending')
            row = dict(ImageID=identifier, PendingUser=pending, Error=('待用户确认：' + selection.get('pending_question','当前边界或纳入方式有疑问')) if pending
                       else "缺少当前有效测量或文件已变化；先处理该图，不使用旧结果。")
            if pending and selection.get('pending_question_image'):
                question = wf.inside(run,run / selection['pending_question_image'])
                if question.is_file():row['QuestionImage'] = question.relative_to(run).as_posix()
            all_rows.append(row)
            continue
        directory = wf.image_dir(run, source)
        current_key = wf.boundary_key(selection, source)
        boundary_accepted = accepted(wf, run, source, selection)
        measurements = wf.read(directory / "verified_measurements.json")
        image_state = wf.read(run / "cache.json", {})[identifier]
        key = wf.digest(dict(version=VERSION, boundary=current_key, policy=policy, accepted=boundary_accepted,
                             measurement=image_state["calculation_key"], masks={p: h for p, h in image_state["artifacts"].items()
                             if "instance_masks" in p}))
        previous = cache.get(identifier, {})
        cache_valid = previous.get("key") == key and all((run / name).is_file() and wf.sha(run / name) == checksum
                        for name, checksum in previous.get("artifacts", {}).items())
        if cache_valid and "rows" in previous:
            all_rows.extend(previous["rows"])
            hits += 1
            continue
        if identifier not in target_ids:
            all_rows.append(dict(ImageID=identifier, Error="局部复核图需要更新；执行 inspect --images " + identifier))
            continue
        rgb = np.asarray(Image.open(source["source_path"]))
        height, width = rgb.shape[:2]
        measurement_map = {v["WormID"]: v for v in measurements}
        ref = references.get(identifier, {})
        same_source = ref.get("source_sha256") == source["source_sha256"]
        if same_source and ref.get("boundary_key") == current_key:
            comparison = ref.get("comparison", {})
        else:
            comparison = ref.get("areas", {}) if same_source else {}
        rows, artifacts = [], {}
        contours = [(worm, wf.points(selection, source, worm)) for worm in selection["worms"]]
        for worm, points in contours:
            label = identifier + "_" + worm["worm_id"]
            values = measurement_map[label]
            mask = np.asarray(Image.open(run / "training/instance_masks" / identifier / (label + ".png"))) > 0
            if mask.shape != (height, width) or int(mask.sum()) != values["Area_px"] or not mask.any():
                raise ValueError("Invalid measured instance mask: " + label)
            flags = []
            # ImageJ's polygon rasterization may omit the bottom/right endpoint row.
            # Report contour contact as well as occupied edge pixels.
            touches = bool(mask[0, :].any() or mask[-1, :].any() or mask[:, 0].any() or mask[:, -1].any()
                           or (points[:, 0] == 0).any() or (points[:, 0] == width - 1).any()
                           or (points[:, 1] == 0).any() or (points[:, 1] == height - 1).any())
            if touches:
                flags.append("接触图像边缘：检查纳入规则，不据此推算完整百分比")
            clipped = (rgb[mask] == 255)
            saturation = clipped.mean(axis=0)
            any_clipped = float(clipped.any(axis=1).mean())
            if any_clipped > 0 and any_clipped >= policy["saturation_fraction"]:
                flags.append("存在通道亮度截顶：检查曝光/导出，提示不等于已判定过曝")
            old_area = comparison.get(label)
            change = (values["Area_px"] - old_area) / old_area if old_area else None
            if change is not None and abs(change) >= policy["area_change_fraction"]:
                flags.append("同图同编号ROI面积变化较大：检查边界修正及编号对应")
            box = box_for(points, width, height, policy["context_padding_px"])
            context = Image.fromarray(rgb).crop(tuple(box))
            raw_path = destination / (label + "_raw.png")
            overlay_path = destination / (label + "_overlay.png")
            context.save(raw_path)
            overlay = ImageEnhance.Brightness(context).enhance(2)
            draw = ImageDraw.Draw(overlay)
            context_contours = []
            for other, other_points in contours:
                if (other_points[:, 0].max() < box[0] or other_points[:, 0].min() >= box[2]
                    or other_points[:, 1].max() < box[1] or other_points[:, 1].min() >= box[3]):
                    continue
                local = other_points - np.array(box[:2])
                active = other["worm_id"] == worm["worm_id"]
                context_contours.append(dict(points=local.tolist(), active=active))
                draw.line([tuple(p) for p in local] + [tuple(local[0])], fill="#ff42cf" if active else "#36e0ed", width=2)
            overlay.save(overlay_path)
            for path in (raw_path, overlay_path):
                artifacts[path.relative_to(run).as_posix()] = wf.sha(path)
            rows.append(dict(Group=source["group"], ImageID=identifier, SourceFile=source["source_file"], WormID=label,
                Mean=values["Mean"], Area_px=values["Area_px"], BoundaryAccepted=boundary_accepted,
                TouchesImageBorder=touches, SaturatedR=float(saturation[0]), SaturatedG=float(saturation[1]),
                SaturatedB=float(saturation[2]), AnyChannelAtMaxFraction=any_clipped,
                PreviousArea_px=old_area, AreaChangeFraction=change, Flags=flags, ContextBox=box,
                ContextContours=context_contours, RawContext=raw_path.relative_to(run).as_posix(),
                OverlayContext=overlay_path.relative_to(run).as_posix(), FullPreview=(directory / "ROI_preview.png").relative_to(run).as_posix(),
                ROISet=(directory / (identifier + "_RoiSet.zip")).relative_to(run).as_posix()))
        references[identifier] = dict(source_sha256=source["source_sha256"], boundary_key=current_key,
                                    areas={v["WormID"]: v["Area_px"] for v in measurements}, comparison=comparison)
        cache[identifier] = dict(key=key, artifacts=artifacts, rows=rows)
        all_rows.extend(rows)
        generated += 1
    wf.write(run / "review/assets_cache.json", cache)
    wf.write(run / "review/area_references.json", references)
    qc_rows = []
    for row in all_rows:
        if row.get("Error"):
            qc_rows.append(dict(ImageID=row["ImageID"], Flags=row["Error"], ArtifactStatus="PENDING_USER" if row.get('PendingUser') else "MISSING_OR_STALE"))
        else:
            selected = {k: v for k, v in row.items() if k not in ("ContextContours", "ContextBox")}
            selected["Flags"] = "；".join(row["Flags"])
            selected["ArtifactStatus"] = "VALID"
            qc_rows.append(selected)
    fields = list(dict.fromkeys(key for row in qc_rows for key in row)) or ["ImageID", "Flags"]
    wf.csvwrite(run / "tables/qc_flags.csv", qc_rows, fields)
    wf.write(run / "review/qc_report.json", dict(policy=policy, advisory_only=True, blind_mode=False, rows=all_rows))
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>逐虫局部复核</title>
<style>body{background:#171b21;color:#eee;max-width:1250px;margin:20px auto;padding:14px;font:16px "Microsoft YaHei",sans-serif}article{border:1px solid #526173;margin:22px 0;padding:14px}.warn{border-color:#ffb75c}a{color:#86d3ff}.context{position:relative;max-width:100%;overflow:hidden}.context img{display:block;width:100%;height:100%;filter:brightness(2)}svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}.controls{position:sticky;top:0;background:#171b21;padding:12px;z-index:2}.flags{color:#ffc675}button,input{font:inherit}p{overflow-wrap:anywhere}</style>
<h1>线虫荧光与ROI逐虫复核</h1><p>保留组别、文件名和数值显示。原始RGB像素、不扣背景；局部图不重采样。质控仅提示，不自动改边界、剔除小虫或证明边界准确。</p>
<p><a href="tables/ALL_ImageJ_Results.csv">全部ImageJ结果</a> · <a href="tables/qc_flags.csv">质控提示表</a> · <a href="training/dataset_manifest.csv">训练素材清单</a> · __CROP_LINK__</p>
<div class="controls"><label><input id="outlines" type="checkbox" checked>显示轮廓</label>　<label>显示亮度 <input id="brightness" type="range" min="1" max="4" step="0.25" value="2"><span id="factor">2</span>倍</label>　<button id="raw">原始亮度</button></div>'''
    crop_link = ('<a href="training/crop_review.html">训练候选裁剪复核</a>' if (run/'training/crop_review.html').is_file()
                 else '<span>训练候选裁剪尚未生成</span>')
    page = page.replace('__CROP_LINK__', crop_link)
    page += ''.join(render_card(row) for row in all_rows)
    page += '''<script>document.getElementById('outlines').onchange=function(){document.querySelectorAll('svg').forEach(s=>s.style.display=this.checked?'':'none')};const b=document.getElementById('brightness');function light(){document.querySelectorAll('.context img').forEach(i=>i.style.filter='brightness('+b.value+')');document.getElementById('factor').textContent=b.value}b.oninput=light;document.getElementById('raw').onclick=()=>{b.value=1;light()};</script></html>'''
    (run / "ROI_逐图复核.html").write_text(page, encoding="utf-8")
    return dict(generated_images=generated, reused_images=hits, worm_contexts=sum("WormID" in r for r in all_rows),
                flagged_worms=sum(bool(r.get("Flags")) for r in all_rows if "WormID" in r),
                unavailable_images=[r["ImageID"] for r in all_rows if r.get("Error")],
                page=str(run / "ROI_逐图复核.html"), advisory_only=True, blind_mode=False)
