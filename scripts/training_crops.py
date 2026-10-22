"""Lossless, traceable training crop proposals with explicit coverage and stale-review gates."""
from pathlib import Path
import html
import json
import os
import shutil
import time

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance

import metadata
import review_assets

VERSION = 1


def crop_key(wf, source, selection, box, image_cache):
    return wf.digest(dict(version=VERSION, source=source["source_sha256"], boundary=wf.boundary_key(selection, source),
        box=box, coverage=dict(complete=selection.get("annotation_coverage"), excluded=selection.get("excluded", [])),
        masks={k: v for k, v in image_cache["artifacts"].items() if "instance_masks" in k}))


def create(wf, run, args):
    inventory, selections = wf.data(run)
    sources = {s["image_id"]: s for s in inventory}
    profile = wf.profile(wf.read(run / "workflow.json"))
    cache = wf.read(run / "cache.json", {})
    chosen = set(args.images or sources)
    if chosen - sources.keys():
        raise ValueError("Unknown image ID for crops")
    if args.padding < 0:
        raise ValueError("Padding must be nonnegative")
    requests = wf.read(Path(args.boxes), []) if args.boxes else None
    if requests is not None:
        if not isinstance(requests, list) or any(r.get("image_id") not in sources for r in requests):
            raise ValueError("Crop requests must be a list with known image_id and box [left,top,right,bottom]")
        chosen = {r["image_id"] for r in requests}
    existing = wf.read(run / "training/crop_proposals.json", [])
    by_id = {r['CropID']: r for r in existing}
    proposals = [r for r in existing if r["SourceImageID"] not in chosen]
    made = reused = 0
    for identifier in sorted(chosen):
        source, selection = sources[identifier], selections.get(identifier)
        wf.check_source(source)
        if not review_assets.valid(wf, run, source, selection, profile):
            continue
        rgb = np.asarray(Image.open(source["source_path"]))
        height, width = rgb.shape[:2]
        shapes = [(worm, wf.points(selection, source, worm)) for worm in selection["worms"]]
        masks = {w["worm_id"]: np.asarray(Image.open(run / "training/instance_masks" / identifier /
                 (identifier + "_" + w["worm_id"] + ".png"))) > 0 for w, _ in shapes}
        targets = [(w["worm_id"], review_assets.box_for(points, width, height, args.padding)) for w, points in shapes]
        if requests is not None:
            targets = [("custom" + str(index + 1), r["box"]) for index, r in enumerate(requests) if r["image_id"] == identifier]
        for target, box in targets:
            if len(box) != 4 or any(type(v) is not int for v in box):
                raise ValueError("Crop box requires four integer coordinates")
            x0, y0, x1, y1 = box
            if not 0 <= x0 < x1 <= width or not 0 <= y0 < y1 <= height:
                raise ValueError("Crop box outside source image")
            fingerprint = crop_key(wf, source, selection, box, cache[identifier])
            crop_id = identifier + "_" + target + "_" + fingerprint[:12]
            previous = by_id.get(crop_id)
            if previous and previous.get('CropFingerprint') == fingerprint and all(
                (run / name).is_file() and wf.sha(run / name) == checksum for name, checksum in previous['Artifacts'].items()):
                proposals.append(previous)
                reused += 1
                continue
            folder = wf.inside(run, run / "training/crop_candidates" / crop_id)
            folder.mkdir(parents=True, exist_ok=True)
            patch = rgb[y0:y1, x0:x1]
            Image.fromarray(patch).save(folder / "image.png")
            overlay = ImageEnhance.Brightness(Image.fromarray(patch)).enhance(2)
            draw = ImageDraw.Draw(overlay)
            included, partial, labels, lm_shapes, files = [], [], [], [], []
            for worm, points in shapes:
                mask = masks[worm["worm_id"]]
                subset = mask[y0:y1, x0:x1]
                if not subset.any():
                    continue
                local = points - np.array([x0, y0])
                fully_inside = (int(subset.sum()) == int(mask.sum()) and (local >= 0).all()
                                and (local[:, 0] < x1 - x0).all() and (local[:, 1] < y1 - y0).all())
                draw.line([tuple(p) for p in local] + [tuple(local[0])], fill="#36e0ed" if fully_inside else "#ff5959", width=2)
                if not fully_inside:
                    partial.append(identifier + "_" + worm["worm_id"])
                    continue
                label = identifier + "_" + worm["worm_id"]
                included.append(label)
                Image.fromarray(subset.astype(np.uint8) * 255).save(folder / (label + "_mask.png"))
                assert int(subset.sum()) == int(mask.sum())
                normalized = local / [x1 - x0, y1 - y0]
                labels.append("0 " + " ".join(f"{value:.7f}" for value in normalized.flatten()))
                lm_shapes.append(dict(label="worm", points=local.tolist(), group_id=None, shape_type="polygon",
                    flags=dict(assistant_traced=True), description=label))
            overlay.save(folder / "preview.png")
            (folder / "labels.txt").write_text("\n".join(labels) + ("\n" if labels else ""), encoding="ascii")
            wf.write(folder / "labelme.json", dict(version="5.0.0", flags={}, shapes=lm_shapes, imagePath="image.png",
                imageData=None, imageWidth=x1 - x0, imageHeight=y1 - y0))
            assert np.array_equal(np.asarray(Image.open(folder / "image.png")), patch)
            files = [folder / name for name in ("image.png", "preview.png", "labels.txt", "labelme.json")]
            files += [folder / (label + "_mask.png") for label in included]
            proposals.append(dict(CropID=crop_id, SourceImageID=identifier, Group=source["group"],
                SourceSHA256=source["source_sha256"], ParentBoundaryKey=wf.boundary_key(selection, source),
                CropFingerprint=fingerprint, Box=box, IncludedWorms=included, PartiallyCutWorms=partial,
                ParentFrameComplete=review_assets.complete(selection), Folder=folder.relative_to(run).as_posix(),
                Artifacts={p.relative_to(run).as_posix(): wf.sha(p) for p in files}))
            made += 1
    # Identical custom requests must not duplicate samples or manifest rows.
    if len({r["CropID"] for r in proposals}) != len(proposals):
        raise ValueError("Duplicate crop proposals; remove duplicate boxes")
    wf.write(run / "training/crop_proposals.json", proposals)
    result = refresh(wf, run)
    result["generated_candidates"] = made
    result['reused_candidates'] = reused
    return result


def current_state(wf, run, source, selection, proposal, profile, cache):
    if not source or not selection or not review_assets.valid(wf, run, source, selection, profile):
        return "STALE_SOURCE_OR_MEASUREMENT"
    if crop_key(wf, source, selection, proposal["Box"], cache[source["image_id"]]) != proposal["CropFingerprint"]:
        return "STALE_BOUNDARY_OR_COVERAGE"
    if not all((run / name).is_file() and wf.sha(run / name) == checksum for name, checksum in proposal["Artifacts"].items()):
        return "BLOCKED_CHANGED_ARTIFACTS"
    if proposal["PartiallyCutWorms"]:
        return "BLOCKED_PARTIAL_LABEL"
    if not proposal["IncludedWorms"]:
        return "BLOCKED_NO_TARGET"
    return None


def refresh(wf, run):
    proposals = wf.read(run / "training/crop_proposals.json", [])
    if not proposals and not (run / "training/crop_proposals.json").is_file():
        return dict(candidates=0, ready=0, page=None)
    inventory, selections = wf.data(run)
    sources = {s["image_id"]: s for s in inventory}
    lineage = metadata.lineages(run, inventory)
    profile = wf.profile(wf.read(run / "workflow.json"))
    cache = wf.read(run / "cache.json", {})
    reviews = wf.read(run / "training/crop_reviews.json", {})
    rows, desired, ready = [], {}, []
    for proposal in proposals:
        identifier = proposal["SourceImageID"]
        source, selection = sources.get(identifier), selections.get(identifier)
        if source:
            wf.check_source(source)
        state = current_state(wf, run, source, selection, proposal, profile, cache)
        review = reviews.get(proposal["CropID"], {})
        reviewed = (review.get("decision") == "accept" and review.get("coverage") == "complete"
                    and review.get("fingerprint") == proposal["CropFingerprint"])
        inherited = (source and selection and review_assets.accepted(wf, run, source, selection)
                     and review_assets.complete(selection))
        if not state:
            if review.get('fingerprint') == proposal['CropFingerprint'] and review.get('decision') == 'revise':
                state = 'REJECTED_CROP_REVIEW'
            elif reviewed:
                state = "READY_CROP_REVIEWED"
            elif inherited:
                state = "READY_DERIVED_FROM_REVIEWED_FRAME"
            else:
                state = "AWAITING_CROP_BOUNDARY_AND_COVERAGE_REVIEW"
        row = dict(proposal, **lineage.get(identifier, {}), Status=state)
        rows.append(row)
        if not state.startswith("READY_"):
            continue
        crop_id = proposal["CropID"]
        folder = run / proposal["Folder"]
        mapping = {folder / "image.png": run / "training/crops_ready/images" / (crop_id + ".png"),
                   folder / "labels.txt": run / "training/crops_ready/labels" / (crop_id + ".txt")}
        for label in proposal["IncludedWorms"]:
            mapping[folder / (label + "_mask.png")] = run / "training/crops_ready/instance_masks" / crop_id / (label + ".png")
        for source_file, destination in mapping.items():
            wf.inside(run, destination).parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists() or wf.sha(destination) != wf.sha(source_file):
                shutil.copy2(source_file, destination)
            desired[destination] = True
        labelme_path = run / "training/crops_ready/labelme" / (crop_id + ".json")
        labelme = wf.read(folder / "labelme.json")
        labelme["imagePath"] = "../images/" + crop_id + ".png"
        labelme["flags"] = dict(user_boundary_verified=True, crop_coverage_verified=True, acceptance=state)
        wf.write(wf.inside(run, labelme_path), labelme)
        desired[labelme_path] = True
        ready.append("crops_ready/images/" + crop_id + ".png")
    # Retire files no longer whitelisted, within this run only; retain them for recovery.
    published = run / "training/crops_ready"
    if published.is_dir():
        for path in list(published.rglob("*")):
            if path.is_file() and path not in desired:
                destination = wf.inside(run, run / "history" / ("stale_crops_" + wf.stamp()) / path.relative_to(run))
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(wf.inside(run, path), destination)
    wf.write(run / "training/crop_manifest.json", rows)
    csv_rows = [{key: (json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value)
                 for key, value in row.items()} for row in rows]
    fields = list(dict.fromkeys(k for row in csv_rows for k in row)) or ["CropID", "Status"]
    wf.csvwrite(run / "training/crop_manifest.csv", csv_rows, fields)
    (run / "training/ready_reviewed_crops.txt").write_text("\n".join(ready) + ("\n" if ready else ""), encoding="utf-8")
    cards = []
    for row in rows:
        folder = Path(row["Folder"]).relative_to("training").as_posix()
        cards.append('<article><h2>' + html.escape(row["CropID"]) + ' · ' + html.escape(row["Group"]) +
            '</h2><p>' + html.escape(row["Status"]) + '</p><p>原图坐标 ' + str(row["Box"]) +
            '；已完整保留：' + html.escape(', '.join(row["IncludedWorms"])) + '；被裁断：' +
            html.escape(', '.join(row["PartiallyCutWorms"])) + '</p><p>整图未穷尽标注时，须检查局部是否仍有未标虫/虫段；青色为完整保留标注，红色为被裁断标注。</p><a href="' +
            folder + '/image.png">原始局部图</a><img loading="lazy" src="' + folder + '/preview.png"></article>')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>训练候选裁剪复核</title><style>body{background:#171b21;color:#eee;max-width:1100px;margin:24px auto;font:16px "Microsoft YaHei",sans-serif}article{border:1px solid #526173;margin:20px 0;padding:14px}img{display:block;max-width:100%}a{color:#86d3ff}p{overflow-wrap:anywhere}</style><h1>训练候选裁剪</h1><p>候选不等于可训练数据。明确截断已标虫的裁剪不能通过复核；原始图像与掩膜未重采样。来自同源图/个体/批次的样本不能跨训练测试划分。完整图和裁剪是替代表示，不能当作新增独立个体计数。</p>''' + ''.join(cards) + '</html>'
    (run / "training/crop_review.html").write_text(page, encoding="utf-8")
    return dict(candidates=len(rows), ready=len(ready), pending_or_blocked=len(rows) - len(ready),
                page=str(run / "training/crop_review.html"), training_started=False)


def review(wf, run, args):
    if not args.evidence.strip():
        raise ValueError("User evidence is required for crop boundary and coverage review")
    refresh(wf, run)
    rows = {r["CropID"]: r for r in wf.read(run / "training/crop_manifest.json", [])}
    reviews = wf.read(run / "training/crop_reviews.json", {})
    for crop_id in args.crop_ids:
        if crop_id not in rows:
            raise ValueError("Unknown crop ID")
        row = rows[crop_id]
        if args.decision == "accept" and (row["Status"].startswith(("STALE_", "BLOCKED_")) or args.coverage != "complete"):
            raise ValueError("Cannot accept stale/incomplete or partially cut labels; choose a different crop or complete annotation")
        reviews[crop_id] = dict(decision=args.decision, coverage=args.coverage, fingerprint=row["CropFingerprint"],
            evidence=args.evidence, recorded_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            meaning="user_acceptance_of_current_crop_boundaries_and_all_visible_target_coverage_only")
    wf.write(run / "training/crop_reviews.json", reviews)
    result = refresh(wf, run)
    result["recorded"] = len(args.crop_ids)
    return result
