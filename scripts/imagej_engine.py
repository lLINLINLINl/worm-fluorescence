"""Export explicitly traced ROIs and measure with the installed ImageJ engine.

Only images marked ready in selections.json are processed. Original images and
ImageJ preferences are never edited. Instance masks remain separate at overlaps.
No model fitting, automatic worm detection or unapproved data exclusion occurs.
"""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import shutil
import subprocess

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFont
from roifile import ImagejRoi, ROI_TYPE, roiread
from shapely.geometry import Polygon

# Set by workflow.py after validating the run and measurement profile.
OUT = JAVA = IJ = PREFS = None
FONT = "C:/Windows/Fonts/msyh.ttc"
COLORS = [(255, 60, 240), (60, 220, 255), (255, 220, 60), (255, 130, 40)]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ijstr(path):
    return json.dumps(Path(path).resolve().as_posix(), ensure_ascii=True)


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def process(selection, source):
    group, image_id = source["group"], source["image_id"]
    d = OUT / "imagej" / group / image_id
    d.mkdir(parents=True, exist_ok=True)
    assert sha(source["source_path"]) == source["source_sha256"], "Source changed since inventory"
    if not (d/"source.tif").exists():
        shutil.copy2(source["source_path"], d / "source.tif")
    assert sha(d/"source.tif") == source["source_sha256"]
    prefs_before = sha(PREFS)
    width, height = source["width"], source["height"]
    # View coordinates describe pixel centers, including actual image edges.
    view_w=selection.get("view_width",source.get("view_width",1000))
    view_h=selection.get("view_height",source.get("view_height",754))
    scale = np.array([(width-1)/(view_w-1), (height-1)/(view_h-1)])
    worms = []
    zip_path = d / (image_id + "_RoiSet.zip")
    for index, worm in enumerate(selection["worms"]):
        points = (np.rint(np.asarray(worm["points_original"],float)).astype(np.int32)
                  if "points_original" in worm else np.rint(np.asarray(worm["points_view"],float)*scale).astype(np.int32))
        if not Polygon(points).is_valid or Polygon(points).area <= 0:
            raise ValueError(f"Invalid manually traced polygon: {image_id} {worm['worm_id']}")
        if (points < 0).any() or (points[:, 0] >= width).any() or (points[:, 1] >= height).any():
            raise ValueError("Contour outside original image")
        label = image_id + "_" + worm["worm_id"]
        roi = ImagejRoi.frompoints(points, name=label)
        roi.roitype = ROI_TYPE.POLYGON
        roi.tofile(d / (label + ".roi"), mode="w")
        roi.tofile(zip_path, name=label + ".roi", mode="w" if index == 0 else "a")
        check = roiread(d / (label + ".roi"))
        assert np.array_equal(np.rint(check.coordinates()).astype(int), points)
        worms.append(dict(worm, label=label, points_original=points.tolist(),
                          human_boundary_verified=False, inclusion_rule_approved=True))
    write_json(d / "annotation.json", dict(image=source, decision=selection["decision_source"],
                                            worms=worms, excluded=selection.get("excluded", [])))
    audit_path = d / "ImageJ_audit.tsv"
    macro = ["setBatchMode(true);", "run(\"Clear Results\");",
             f"open({ijstr(d/'source.tif')});", "sourceID=getImageID();",
             f"File.saveString(\"WormID\\tPixelCount\\tMeanFull\\tMin\\tMax\\tAreaNative\\tUnit\\n\",{ijstr(audit_path)});"]
    for index, worm in enumerate(worms):
        label = worm["label"]
        macro += ["selectImage(sourceID);", 'run("Clear Results");', f"open({ijstr(d/(label+'.roi'))});",
                  'if(selectionType()<0) exit("ROI not loaded");', 'run("Measure");',
                  f'setResult("Label", nResults-1, "{label}");',
                  f'saveAs("Results",{ijstr(d/(label+"_ImageJ_Results.csv"))});',
                  "getStatistics(area,mean,min,max);", "getRawStatistics(npixels);", "getPixelSize(unit,pw,ph);",
                  f'line="{label}\\t"+d2s(npixels,0)+"\\t"+d2s(mean,9)+"\\t"+min+"\\t"+max+"\\t"+d2s(area,9)+"\\t"+unit+"\\n";',
                  f"File.append(line,{ijstr(audit_path)});",
                  'run("Create Mask");', f"saveAs(\"PNG\",{ijstr(d/(label+'_ImageJ_mask.png'))});", "close();"]
    macro += [f"saveAs(\"Results\",{ijstr(d/'ImageJ_Results.csv')});", "selectImage(sourceID);", "close();"]
    macro_path = d / "measure.ijm"
    macro_path.write_text("\n".join(macro)+"\n", encoding="ascii")
    # ImageJ checks the working directory first for IJ_Prefs.txt. A private copy
    # ensures that --prefs is actually used, independently of the caller's home.
    runtime = d / '_imagej_runtime'
    runtime.mkdir()
    shutil.copy2(PREFS, runtime / 'IJ_Prefs.txt')
    loaded_prefs = d / 'ImageJ_preferences_directory.txt'
    macro.insert(0, f'File.saveString(call("ij.Prefs.getPrefsDir"),{ijstr(loaded_prefs)});')
    macro_path.write_text("\n".join(macro)+"\n", encoding="ascii")
    result = subprocess.run([str(JAVA), '-Duser.home='+str(runtime.resolve()), "-cp", str(IJ), "ij.ImageJ", "-batch", str(macro_path)],
                            cwd=runtime, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90, check=True)
    (d / "ImageJ_run.log").write_bytes(result.stdout)
    if Path(loaded_prefs.read_text(encoding='utf-8-sig').strip()).resolve() != runtime.resolve():
        raise ValueError('ImageJ did not load the explicitly isolated preferences directory')
    with audit_path.open(encoding="utf-8-sig", newline="") as f:
        audits = [r for r in csv.DictReader(f, delimiter="\t") if r.get("WormID")]
    # Adding Label changes the Results headings and this ImageJ build resets its
    # counter on the next Measure. Preserve each native row, then concatenate.
    native_rows=[]
    for worm in worms:
        with (d/(worm['label']+'_ImageJ_Results.csv')).open(newline='',encoding='utf-8-sig') as f:
            single=list(csv.DictReader(f))
        assert len(single)==1 and single[0]['Label']==worm['label']
        native_rows.append(single[0])
    with (d/'ImageJ_Results.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,native_rows[0].keys());writer.writeheader()
        for i,row in enumerate(native_rows,1):
            row[' ']=str(i);writer.writerow(row)
    if len(audits) != len(worms):
        raise ValueError("ImageJ measured row count differs from accepted ROI count")
    rgb = np.asarray(Image.open(d / "source.tif").convert("RGB"))
    # Saved local preference: non-weighted RGB brightness. Check independently.
    gray = np.floor(rgb.astype(float).mean(axis=2)+0.5)
    summary = []
    preview = np.asarray(ImageEnhance.Brightness(Image.fromarray(rgb)).enhance(2)).copy()
    masks_dir = OUT / "training" / "instance_masks" / image_id
    masks_dir.mkdir(parents=True, exist_ok=True)
    for i, (worm, audit) in enumerate(zip(worms, audits)):
        if audit["WormID"] != worm["label"]:
            raise ValueError("ROI identity and measurement row mismatch")
        raw_mask = np.asarray(Image.open(d / (worm["label"]+"_ImageJ_mask.png")).convert("L"))
        count = int(float(audit["PixelCount"]))
        white = raw_mask > 0
        if int(white.sum()) == count:
            mask = white
        elif int((~white).sum()) == count:
            mask = ~white
        else:
            raise ValueError("Mask size differs from ImageJ ROI pixel count")
        mean = float(gray[mask].mean())
        if abs(mean-float(audit["MeanFull"])) > 1e-7:
            raise ValueError("Independent RGB verification does not match ImageJ")
        Image.fromarray((mask*255).astype(np.uint8)).save(masks_dir/(worm["label"]+".png"))
        contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        color = COLORS[i % len(COLORS)]
        cv2.drawContours(preview, contours, -1, color, 3)
        dist = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5)
        _, _, _, pos = cv2.minMaxLoc(dist)
        cv2.putText(preview, worm["worm_id"], pos, cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3, cv2.LINE_AA)
        summary.append(dict(Group=group, ImageID=image_id, SourceFile=source["source_file"],
                            WormID=worm["label"], Mean=mean, Min=int(float(audit["Min"])),
                            Max=int(float(audit["Max"])), Area_px=count,
                            Area_ImageJ=float(audit["AreaNative"]), AreaUnit=audit["Unit"]+"^2",
                            BackgroundSubtracted=False, Method="ImageJ_RGB_unweighted",
                            InclusionNote=worm.get("note",""), BoundaryVerified=False))
    font = ImageFont.truetype(str(FONT), max(12,min(28,width//65)))
    preview_img = Image.fromarray(preview)
    draw = ImageDraw.Draw(preview_img)
    draw.rectangle((0,0,width,48), fill=(15,15,15))
    draw.text((12,7), image_id+"｜逐虫ROI｜仅预览亮度2倍，测量使用原图；不扣背景", font=font, fill="white")
    preview_img.save(d / "ROI_preview.png")
    for sub in ("images", "labels", "labelme"):
        (OUT / "training" / sub).mkdir(parents=True, exist_ok=True)
    image_copy=OUT/"training/images"/(image_id+".tif")
    if not image_copy.exists():
        shutil.copy2(d/"source.tif",image_copy)
    assert sha(image_copy) == source["source_sha256"]
    lines = []
    shapes = []
    for worm in worms:
        points = np.asarray(worm["points_original"],float)
        normalized = points/[width,height]
        lines.append("0 "+" ".join(f"{v:.7f}" for v in normalized.flatten()))
        shapes.append(dict(label="worm",points=points.tolist(),group_id=None,shape_type="polygon",
                           flags=dict(assistant_traced=True,user_boundary_verified=False),description=worm["label"]))
    (OUT/"training/labels"/(image_id+".txt")).write_text("\n".join(lines)+"\n",encoding="ascii")
    write_json(OUT/"training/labelme"/(image_id+".json"),dict(version="5.0.0",flags={},shapes=shapes,
                imagePath="../images/"+image_id+".tif", imageData=None,imageHeight=height,imageWidth=width))
    write_json(d/"verified_measurements.json", summary)
    assert sha(source["source_path"]) == source["source_sha256"]
    assert sha(d/"source.tif") == source["source_sha256"]
    assert sha(PREFS) == prefs_before, "User preferences changed unexpectedly"
    print(image_id+": measured "+str(len(summary))+" worm(s); ImageJ output independently verified",flush=True)

