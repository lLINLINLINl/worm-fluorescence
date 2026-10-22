"""Local, incremental ImageJ fluorescence/ROI workflow. No model/API calls."""
from pathlib import Path
import argparse, contextlib, csv, hashlib, html, importlib.metadata, io, json
import os, re, shutil, sys, time, traceback, uuid, zipfile
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFont
from roifile import roiread
from shapely.geometry import Polygon
import imagej_engine as engine
import environment
import metadata
import review_assets
import training_crops
import rules

HERE=Path(__file__).resolve().parent
IMAGE_EXTS={'.tif','.tiff','.png','.jpg','.jpeg','.bmp'}
ID_PATTERN=re.compile(r'^[A-Za-z0-9_-]+$')
PROFILE='RGB8_unweighted_original_pixels_no_background'
SKILL_VERSION='0.3.0-beta.1'

def read(p,default=None):
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default
def write(p,value):
    p.parent.mkdir(parents=True,exist_ok=True)
    temporary=p.with_name(p.name+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(temporary,p)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def stamp():return time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
def inside(root,p):
    if not p.resolve().is_relative_to(root.resolve()):raise ValueError('Output escaped run: '+str(p))
    return p
def natural(p):return [int(s) if s.isdigit() else s.lower() for s in re.split(r'(\d+)',str(p))]
def csvwrite(p,rows,fields=None):
    p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fields or (list(rows[0]) if rows else []));w.writeheader();w.writerows(rows)
def config(path=None):return environment.load_config(path)
def profile(c):
    for key in ['java','ij_jar','prefs','font']:
        if not Path(c[key]).is_file():raise ValueError(f'Missing {key}: {c[key]}; correct workflow.json paths first')
    prefs={}
    for line in Path(c['prefs']).read_text(encoding='utf-8',errors='replace').splitlines():
        if '=' in line:
            k,v=line.split('=',1);prefs[k]=v
    if c['profile']!=PROFILE or int(prefs.get('measurements','-1'))!=19 or int(prefs.get('precision','-1'))!=3:
        raise ValueError('Measurement profile changed. Resolve user measurement settings; do not silently edit ImageJ preferences.')
    if int(prefs.get('prefs.options','0')) & 256:
        raise ValueError('Weighted RGB is enabled; this engine validates unweighted RGB only. Confirm a separate measurement profile.')
    return dict(profile=PROFILE,prefs_sha256=sha(Path(c['prefs'])),ij_sha256=sha(Path(c['ij_jar'])),java_sha256=sha(Path(c['java'])),engine_sha256=sha(HERE/'imagej_engine.py'),numpy=importlib.metadata.version('numpy'),roifile=importlib.metadata.version('roifile'))
def set_engine(run,c):
    engine.OUT=run;engine.JAVA=Path(c['java']);engine.IJ=Path(c['ij_jar']);engine.PREFS=Path(c['prefs']);engine.FONT=Path(c['font'])
def check_source(src):
    p=Path(src['source_path'])
    if sha(p)!=src['source_sha256']:raise ValueError('Source changed since inventory: '+str(p)+'; inventory a new version')
    with p.open('rb') as f:header=f.read(29)
    if header.startswith(b'\x89PNG\r\n\x1a\n') and header[24]!=8:
        raise ValueError('Only single-frame 8-bit RGB is validated: '+str(p)+'. PNG bit depth is '+str(header[24])+'; do not silently downconvert.')
    with Image.open(p) as im:
        bits=im.tag_v2.get(258,(8,8,8)) if hasattr(im,'tag_v2') else (8,8,8)
        if im.mode!='RGB' or getattr(im,'n_frames',1)!=1 or any(int(x)!=8 for x in (bits if isinstance(bits,tuple) else [bits])):
            raise ValueError('Only single-frame 8-bit RGB is validated: '+str(p)+'. Do not convert scientific source pixels silently.')
        if im.size!=(src['width'],src['height']):raise ValueError('Source dimensions changed')
def points(s,src,w):
    if 'points_original' in w:array=np.rint(np.array(w['points_original'],float)).astype(int)
    else:
        vw=s.get('view_width',src.get('view_width',1000));vh=s.get('view_height',src.get('view_height',754))
        array=np.rint(np.array(w['points_view'],float)*[(src['width']-1)/(vw-1),(src['height']-1)/(vh-1)]).astype(int)
    if array.ndim!=2 or array.shape[1]!=2 or len(array)<3 or not Polygon(array).is_valid or Polygon(array).area<=0:
        raise ValueError('Invalid contour for '+src['image_id']+' '+w['worm_id'])
    if (array<0).any() or (array[:,0]>=src['width']).any() or (array[:,1]>=src['height']).any():raise ValueError('Contour outside source pixels')
    return array
def boundary_key(s,src):
    return digest(dict(source_sha256=src['source_sha256'],size=[src['width'],src['height']],worms=[dict(id=w['worm_id'],points=points(s,src,w).tolist()) for w in s['worms']]))
def calculation_key(s,src,p):
    return digest(dict(boundary=boundary_key(s,src),group=src['group'],id=src['image_id'],source_file=src['source_file'],measurement=p))
def data(run):
    inv=read(run/'source_inventory.json',[]);sels=read(run/'selections.json',[])
    if len({x['image_id'] for x in inv})!=len(inv) or len({x['image_id'] for x in sels})!=len(sels):raise ValueError('Duplicate image IDs')
    return inv,{x['image_id']:x for x in sels}
def image_dir(run,src):return inside(run,run/'imagej'/src['group']/src['image_id'])
def artifacts(run,src,s):
    d=image_dir(run,src);id=src['image_id']
    files=[d/'source.tif',d/'annotation.json',d/'ImageJ_Results.csv',d/'verified_measurements.json',d/'ROI_preview.png',d/(id+'_RoiSet.zip'),run/'training/images'/(id+'.tif'),run/'training/labels'/(id+'.txt'),run/'training/labelme'/(id+'.json')]
    for w in s['worms']:
        label=id+'_'+w['worm_id'];files.extend([d/(label+'.roi'),d/(label+'_ImageJ_Results.csv'),d/(label+'_ImageJ_mask.png'),run/'training/instance_masks'/id/(label+'.png')])
    return {p.relative_to(run).as_posix():sha(p) for p in files}
def cache_ok(run,entry,key):
    return bool(entry and entry.get('calculation_key')==key and all((run/p).is_file() and sha(run/p)==v for p,v in entry.get('artifacts',{}).items()) and entry.get('artifacts'))
def blank(run,src,s):
    if not s.get('empty_reviewed'):raise ValueError('Zero worms requires empty_reviewed=true after visual inspection')
    d=image_dir(run,src);d.mkdir(parents=True,exist_ok=True);shutil.copy2(src['source_path'],d/'source.tif')
    write(d/'annotation.json',dict(image=src,decision=s['decision_source'],worms=[],excluded=s.get('excluded',[])))
    write(d/'verified_measurements.json',[]);csvwrite(d/'ImageJ_Results.csv',[],[' ','Label','Area','Mean','Min','Max'])
    with zipfile.ZipFile(d/(src['image_id']+'_RoiSet.zip'),'w'):pass
    Image.open(d/'source.tif').save(d/'ROI_preview.png')
    for sub in ['images','labels','labelme','instance_masks']:(run/'training'/sub).mkdir(parents=True,exist_ok=True)
    shutil.copy2(d/'source.tif',run/'training/images'/(src['image_id']+'.tif'))
    (run/'training/labels'/(src['image_id']+'.txt')).write_text('',encoding='ascii')
    write(run/'training/labelme'/(src['image_id']+'.json'),dict(version='5.0.0',flags={},shapes=[],imagePath='../images/'+src['image_id']+'.tif',imageData=None,imageHeight=src['height'],imageWidth=src['width']))
def audit_image(run,src,s):
    check_source(src);d=image_dir(run,src);id=src['image_id'];a=read(d/'annotation.json');values=read(d/'verified_measurements.json')
    if not a or values is None:raise ValueError('Missing measurement artifacts: '+id)
    assert sha(d/'source.tif')==sha(run/'training/images'/(id+'.tif'))==src['source_sha256']
    native=list(csv.DictReader((d/'ImageJ_Results.csv').open(encoding='utf-8-sig',newline='')))
    labels=(run/'training/labels'/(id+'.txt')).read_text().splitlines();lm=read(run/'training/labelme'/(id+'.json'))
    with zipfile.ZipFile(d/(id+'_RoiSet.zip')) as z:
        assert set(z.namelist())=={id+'_'+w['worm_id']+'.roi' for w in s['worms']}
    assert len(s['worms'])==len(a['worms'])==len(values)==len(native)==len(labels)==len(lm['shapes'])
    gray=np.floor(np.array(Image.open(d/'source.tif'),dtype=float).mean(2)+0.5);masks=[]
    for w,aw,v,nat,line,shape in zip(s['worms'],a['worms'],values,native,labels,lm['shapes']):
        pts=points(s,src,w);label=id+'_'+w['worm_id'];assert aw['label']==v['WormID']==nat['Label']==shape['description']==label
        assert np.array_equal(np.array(aw['points_original']),pts)
        assert np.array_equal(np.rint(roiread(d/(label+'.roi')).coordinates()).astype(int),pts)
        assert line.split()[0]=='0';np.testing.assert_allclose(np.array(line.split()[1:],float).reshape(-1,2),pts/[src['width'],src['height']],atol=5.1e-8,rtol=0)
        assert np.array_equal(np.array(shape['points']),pts)
        m=np.array(Image.open(run/'training/instance_masks'/id/(label+'.png')))>0
        assert m.shape==gray.shape and int(m.sum())==v['Area_px']
        raw=np.array(Image.open(d/(label+'_ImageJ_mask.png')))>0;assert np.array_equal(m,raw) or np.array_equal(m,~raw)
        assert abs(float(gray[m].mean())-v['Mean'])<1e-8 and int(gray[m].min())==v['Min'] and int(gray[m].max())==v['Max']
        assert abs(float(nat['Mean'])-v['Mean'])<=0.000501 and abs(float(nat['Area'])-v['Area_ImageJ'])<=0.000501
        assert int(nat['Min'])==v['Min'] and int(nat['Max'])==v['Max'];masks.append(m)
    return dict(worms=len(values),overlap_pairs=sum(int((masks[a]&masks[b]).sum())>0 for a in range(len(masks)) for b in range(a+1,len(masks))))
def init(args):
    source=Path(args.source).resolve();run=Path(args.run).resolve()
    if run.exists() or run.is_relative_to(source):raise ValueError('Use a new output directory outside the source folder')
    files=sorted([p for p in source.rglob('*') if p.is_file() and p.suffix.lower() in IMAGE_EXTS],key=natural)
    if not files:raise ValueError('No source images')
    c=config(getattr(args,'config',None));profile(c);inv=[];counts={};groups={}
    for p in files:
        rel=p.relative_to(source);group=rel.parts[0] if len(rel.parts)>1 else source.name
        if group not in groups:groups[group]=group if ID_PATTERN.fullmatch(group) else 'G'+str(len(groups)+1).zfill(3)
        counts[group]=counts.get(group,0)+1;id=groups[group]+'_'+str(counts[group]).zfill(3)
        with Image.open(p) as im:
            width,height=im.size
            if min(width,height)<2:raise ValueError('Image too small')
            vw=min(1000,width);vh=round((height-1)*(vw-1)/(width-1))+1
            src=dict(image_id=id,group=group,source_file=p.name,source_path=str(p),source_sha256=sha(p),width=width,height=height,mode=im.mode,frames=getattr(im,'n_frames',1),view_width=vw,view_height=vh)
        check_source(src);inv.append(src)
    run.mkdir(parents=True);write(run/'workflow.json',c);write(run/'source_inventory.json',inv);write(run/'selections.json',[]);write(run/'cache.json',{});write(run/'reviews.json',{})
    write(run/'batch_metadata.json',metadata.template())
    write(run/'analysis_rules.json',rules.template())
    return dict(run=str(run),images=len(inv),groups=counts,next='prepare previews, visually select worms; record selections.json')
def import_batch(args):
    old=Path(args.source).resolve();run=Path(args.run).resolve()
    if run.exists() or run.is_relative_to(old):raise ValueError('Import requires a separate new run')
    inv,sels=data(old);chosen=set(args.images or [r['image_id'] for r in inv]);inv=[r for r in inv if r['image_id'] in chosen]
    if chosen!={r['image_id'] for r in inv}:raise ValueError('Unknown image ID')
    for src in inv:check_source(src)
    c=config(args.config) if getattr(args,'config',None) else read(old/'workflow.json') or config()
    profile(c);run.mkdir(parents=True)
    for src in inv:src.setdefault('view_width',1000);src.setdefault('view_height',754)
    write(run/'workflow.json',c);write(run/'source_inventory.json',inv);write(run/'selections.json',[sels[r['image_id']] for r in inv if r['image_id'] in sels]);write(run/'cache.json',{});write(run/'reviews.json',{})
    write(run/'batch_metadata.json',metadata.metadata(old))
    write(run/'analysis_rules.json',rules.load(old))
    return dict(run=str(run),imported_images=len(inv),next='measure; no measurement or approval imported implicitly')
def adopt(args):
    """Seed a cache only after auditing actual saved ImageJ/mask artifacts."""
    run=Path(args.run).resolve();inv,sels=data(run);c=config(getattr(args,'config',None));p=profile(c);cache={};total=0
    existing=read(run/'workflow.json')
    if existing:raise ValueError('Run already has workflow.json; use measure/export instead')
    coverage={}
    manifest=run/'training/dataset_manifest.csv'
    if manifest.exists():
        for row in csv.DictReader(manifest.open(encoding='utf-8-sig',newline='')):
            coverage[row['ImageID']]=row.get('WholeFrameCandidate','').lower()=='true'
    for src in inv:
        id=src['image_id'];s=sels[id];d=image_dir(run,src)
        if not s['worms'] or not (d/'ImageJ_run.log').exists() or 'run("Measure")' not in (d/'measure.ijm').read_text(encoding='ascii'):
            raise ValueError('Validated import requires saved actual ImageJ provenance: '+id)
        checked=audit_image(run,src,s);total+=checked['worms']
        if id in coverage:
            s['annotation_coverage']='complete' if coverage[id] else 'incomplete'
            if coverage[id]:
                for e in s.get('excluded',[]):e['kind']='non_worm'
        cache[id]=dict(calculation_key=calculation_key(s,src,p),boundary_key=boundary_key(s,src),origin='validated_import_of_saved_ImageJ_measurements',audit=checked,artifacts=artifacts(run,src,s))
    write(run/'workflow.json',c);write(run/'cache.json',cache);write(run/'reviews.json',{});write(run/'selections.json',list(sels.values()))
    return dict(adopted_images=len(inv),audited_worms=total,cache_seeded=True,approval_imported=False)
def prepare(args):
    run=Path(args.run).resolve();inv,sels=data(run);d=run/'inspection';d.mkdir(exist_ok=True);prepared=0;prepared_ids=[];cache=read(d/'preview_cache.json',{})
    for src in inv:
        id=src['image_id']
        if args.images and id not in args.images:continue
        check_source(src);key=digest([src['source_sha256'],src.get('view_width',1000),src.get('view_height',754),args.factor])
        if cache.get(id)==key and (d/(id+'_view.png')).exists():continue
        if prepared>=args.limit:break
        with Image.open(src['source_path']) as im:
            im=im.resize((src.get('view_width',1000),src.get('view_height',754)),Image.Resampling.LANCZOS);ImageEnhance.Brightness(im).enhance(args.factor).save(d/(id+'_view.png'))
        cache[id]=key;prepared+=1;prepared_ids.append(id)
    selected=set(args.images or prepared_ids)
    write(d/'preview_cache.json',cache);return dict(prepared=prepared,prepared_ids=prepared_ids,display_only=True,view_sizes={r['image_id']:[r.get('view_width',1000),r.get('view_height',754)] for r in inv if r['image_id'] in selected})
def measure(args):
    run=Path(args.run).resolve();inv,sels=data(run);c=read(run/'workflow.json');p=profile(c);cache=read(run/'cache.json',{});done=hit=0;pending=[]
    rules.load(run,require_confirmed=True)
    for src in inv:
        id=src['image_id']
        if args.images and id not in args.images:continue
        check_source(src);s=sels.get(id)
        if not s or s.get('status')!='ready':pending.append(id);continue
        if not s.get('decision_source'):raise ValueError('Missing inclusion decision provenance: '+id)
        if not ID_PATTERN.fullmatch(id) or any(not ID_PATTERN.fullmatch(w['worm_id']) for w in s['worms']):raise ValueError('ROI IDs must be ASCII letters/digits/underscore/hyphen')
        if len({w['worm_id'] for w in s['worms']})!=len(s['worms']):raise ValueError('Duplicate worm ID')
        key=calculation_key(s,src,p)
        if cache_ok(run,cache.get(id),key):hit+=1;continue
        # Compute in an isolated, retained staging folder; publish only after audit.
        stage=inside(run,run/'.staging'/(id+'_'+stamp()));stage.mkdir(parents=True)
        set_engine(stage,c)
        with contextlib.redirect_stdout(io.StringIO()):
            if s['worms']:engine.process(s,src)
            else:blank(stage,src,s)
        audited=audit_image(stage,src,s)
        pairs=[(image_dir(stage,src),image_dir(run,src))]
        for sub,suffix in [('labels','.txt'),('labelme','.json'),('images','.tif')]:pairs.append((stage/'training'/sub/(id+suffix),run/'training'/sub/(id+suffix)))
        masks=stage/'training/instance_masks'/id
        if masks.exists():pairs.append((masks,run/'training/instance_masks'/id))
        elif (run/'training/instance_masks'/id).exists():pairs.append((None,run/'training/instance_masks'/id))
        history=inside(run,run/'history'/(id+'_'+stamp()))
        for srcpath,dst in pairs:
            inside(run,dst);dst.parent.mkdir(parents=True,exist_ok=True)
            if dst.exists():
                saved=inside(run,history/dst.relative_to(run));saved.parent.mkdir(parents=True,exist_ok=True);os.replace(dst,saved)
            if srcpath is not None:inside(run,srcpath);os.replace(srcpath,dst)
        macro=image_dir(run,src)/'measure.ijm'
        if macro.exists():
            text=macro.read_text(encoding='ascii').replace(json.dumps(stage.as_posix())[1:-1],json.dumps(run.as_posix())[1:-1]);macro.write_text(text,encoding='ascii')
        cache[id]=dict(calculation_key=key,boundary_key=boundary_key(s,src),origin='actual_ImageJ_measurement' if s['worms'] else 'visually_reviewed_empty',audit=audited,artifacts=artifacts(run,src,s));write(run/'cache.json',cache);done+=1
    result=dict(measured_images=done,cache_hits=hit,pending_images=pending)
    write(run/'last_run.json',result);return result
def review(args):
    run=Path(args.run).resolve();inv,sels=data(run);inv={r['image_id']:r for r in inv};reviews=read(run/'reviews.json',{})
    if not args.evidence.strip():raise ValueError('Review evidence from the user is required')
    for id in args.images:
        if id not in inv or id not in sels:raise ValueError('No contour: '+id)
        reviews[id]=dict(decision=args.decision,boundary_key=boundary_key(sels[id],inv[id]),evidence=args.evidence,recorded_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),meaning='user_acceptance_of_assistant_contours_not_independent_test_ground_truth')
    write(run/'reviews.json',reviews);return dict(recorded=len(args.images),decision=args.decision)
def export(args):
    run=Path(args.run).resolve();inv,sels=data(run);c=read(run/'workflow.json');p=profile(c);cache=read(run/'cache.json',{});reviews=read(run/'reviews.json',{});records=[];native=[];manifest=[];excluded=[];cards=[];missing=[];audits=0
    lineage=metadata.lineages(run,inv)
    for src in inv:
        id=src['image_id'];s=sels.get(id);check_source(src)
        if not s or s.get('status')!='ready' or not cache_ok(run,cache.get(id),calculation_key(s,src,p)):
            missing.append(id);continue
        if args.audit:audit_image(run,src,s);audits+=1
        accepted=reviews.get(id,{}).get('decision')=='accept' and reviews[id]['boundary_key']==boundary_key(s,src)
        complete=s.get('annotation_coverage')=='complete' and all(e.get('kind')=='non_worm' for e in s.get('excluded',[]))
        status='READY_REVIEWED' if accepted and complete else ('INCOMPLETE_FRAME' if not complete else 'AWAITING_USER_REVIEW')
        src['roi_review_status']='user_accepted_current_boundaries' if accepted else 'assistant_traced_not_explicitly_accepted'
        d=image_dir(run,src);values=read(d/'verified_measurements.json');a=read(d/'annotation.json');lm=read(run/'training/labelme'/(id+'.json'))
        src['worm_count']=len(values)
        a.update(image=src,decision=s['decision_source'],excluded=s.get('excluded',[]),user_review=reviews.get(id),human_boundary_verified=accepted)
        for w,aw,v,shape in zip(s['worms'],a['worms'],values,lm['shapes']):
            aw['note']=w.get('note','');aw['human_boundary_verified']=accepted;v['BoundaryVerified']=accepted;v['InclusionNote']=w.get('note','');shape['flags']['user_boundary_verified']=accepted
        write(d/'annotation.json',a);write(d/'verified_measurements.json',values);write(run/'training/labelme'/(id+'.json'),lm)
        vmap={v['WormID']:v for v in values}
        for row in csv.DictReader((d/'ImageJ_Results.csv').open(encoding='utf-8-sig',newline='')):
            v=vmap[row['Label']]
            native.append(dict(Group=src['group'],ImageID=id,SourceFile=src['source_file'],Label=row['Label'],Area=row['Area'],Mean=row['Mean'],Min=row['Min'],Max=row['Max'],AreaUnit=v['AreaUnit'],Area_px=v['Area_px']))
        records.extend(values)
        manifest.append(dict(Group=src['group'],ImageID=id,Worms=len(values),BoundaryAccepted=accepted,FrameFullyAnnotated=complete,TrainingStatus=status,**lineage[id]))
        excluded.extend(dict(ImageID=id,Location=e.get('location',''),Reason=e.get('reason',''),Kind=e.get('kind','unknown')) for e in s.get('excluded',[]))
        cache[id]['artifacts']=artifacts(run,src,s)
        preview=(d/'ROI_preview.png').relative_to(run).as_posix();roi=(d/(id+'_RoiSet.zip')).relative_to(run).as_posix();means='；'.join(v['WormID']+' Mean='+f"{v['Mean']:.3f}" for v in values)
        cards.append('<article><h2>'+html.escape(id+' · '+src['source_file'])+'</h2><p>'+html.escape(means or '本图无纳入虫')+'</p><p>边界验收：'+('通过' if accepted else '待确认')+'；训练素材：'+status+'</p><a href="'+roi+'">ROI选区</a><a href="'+preview+'"><img loading="lazy" src="'+preview+'"></a></article>')
    for group in sorted({r['group'] for r in inv})+['ALL']:
        safe=re.sub(r'[^\w-]','_',group)
        csvwrite(run/'tables'/(safe+'_per_worm.csv'),[v for v in records if group=='ALL' or v['Group']==group],list(records[0]) if records else ['Group','ImageID','WormID','Mean'])
        csvwrite(run/'tables'/(safe+'_ImageJ_Results.csv'),[v for v in native if group=='ALL' or v['Group']==group],list(native[0]) if native else ['Group','ImageID','SourceFile','Label','Area','Mean','Min','Max'])
    csvwrite(run/'training/dataset_manifest.csv',manifest,['Group','ImageID','Worms','BoundaryAccepted','FrameFullyAnnotated','TrainingStatus','SourceImageID','SourceSHA256','ExperimentID','BiologicalReplicate','ImagingSession','AnimalID','SplitGroup','Split'])
    document=metadata.metadata(run)
    metadata_rows=[dict(ImageID=src['image_id'],Group=src['group'],**{k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in metadata.resolve(document,src).items()}) for src in inv]
    csvwrite(run/'tables/experiment_metadata.csv',metadata_rows,list(dict.fromkeys(k for row in metadata_rows for k in row)) if metadata_rows else ['ImageID'])
    csvwrite(run/'tables/exclusions.csv',excluded,['ImageID','Location','Reason','Kind'])
    ready=[r['ImageID'] for r in manifest if r['TrainingStatus']=='READY_REVIEWED']
    (run/'training').mkdir(exist_ok=True);(run/'training/ready_reviewed.txt').write_text('\n'.join('images/'+id+'.tif' for id in ready)+ ('\n' if ready else ''),encoding='utf-8')
    write(run/'cache.json',cache)
    write(run/'source_inventory.json',inv)
    if inv:csvwrite(run/'source_inventory.csv',inv,list(dict.fromkeys(k for row in inv for k in row)))
    report=dict(status='COMPLETE' if not missing else 'IN_PROGRESS',images=len(inv),measured_images=len(manifest),worms=len(records),pending_images=missing,boundary_accepted_images=sum(r['BoundaryAccepted'] for r in manifest),training_ready_images=len(ready),incomplete_frame_images=sum(r['TrainingStatus']=='INCOMPLETE_FRAME' for r in manifest),audited_images=audits,training_started=False,profile=PROFILE,quota_savings='not_measured')
    write(run/'audit_summary.json',report)
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>线虫荧光复核</title><style>body{background:#171b21;color:#eee;max-width:1200px;margin:24px auto;padding:16px;font:16px "Microsoft YaHei",sans-serif}article{border:1px solid #526173;margin:24px 0;padding:16px}img{width:100%;height:auto}a{color:#86d3ff}p{line-height:1.7}</style><h1>线虫荧光与ROI复核</h1><p>原始RGB像素、不扣背景。数值核验与边界验收分别记录；尚未训练模型。</p>'''+''.join(cards)+'</html>'
    (run/'ROI_逐图复核.html').write_text(page,encoding='utf-8')
    report['skill_version']=SKILL_VERSION
    report['inspection']=review_assets.build(sys.modules[__name__],run,args)
    if (run/'training/crop_proposals.json').is_file():report['crop_materials']=training_crops.refresh(sys.modules[__name__],run)
    write(run/'audit_summary.json',report)
    return report

def inspect(args):
    return review_assets.build(sys.modules[__name__],Path(args.run).resolve(),args)

def crops(args):
    return training_crops.create(sys.modules[__name__],Path(args.run).resolve(),args)

def metadata_command(args):
    run=Path(args.run).resolve();inv,_=data(run);path=run/'batch_metadata.json'
    if args.file:
        document=read(Path(args.file))
        if not isinstance(document,dict) or any(not isinstance(document.get(key,{}),dict) for key in ['dataset','groups','images']):raise ValueError('Metadata must have dataset/groups/images objects')
        metadata.lineages(run,inv,document)
        if path.exists():write(run/'history'/('metadata_'+stamp()+'.json'),read(path))
        write(path,document)
    elif not path.exists():write(path,metadata.template())
    lineage=metadata.lineages(run,inv)
    return dict(file=str(path),images=len(inv),split_groups=len({v['SplitGroup'] for v in lineage.values()}),unknown_fields_retained=True,measurement_changed=False)

def set_environment(args):
    run=Path(args.run).resolve();c=config(args.config);profile(c)
    old=read(run/'workflow.json')
    if old:write(run/'history'/('environment_'+stamp()+'.json'),old)
    write(run/'workflow.json',c)
    return dict(updated=True,measurement_changed=False,next='measure checks profile fingerprints before reusing results')

def rules_command(args):
    run=Path(args.run).resolve();path=run/'analysis_rules.json'
    if not (run/'workflow.json').is_file():raise ValueError('Initialize a run before recording rules')
    document=read(Path(args.file)) if args.file else rules.load(run)
    confirmed=rules.validate(document)
    if args.file and path.exists():write(run/'history'/('rules_'+stamp()+'.json'),read(path))
    write(path,document)
    return dict(file=str(path),confirmed=confirmed,measurement_profile=PROFILE,
                scope='Policy records guide the annotating agent; they do not automatically infer body boundaries or completeness',
                next='measure' if confirmed else 'record the user measurement and inclusion choices, then import with rules --file')
def status(args):
    run=Path(args.run).resolve();inv,sels=data(run)
    return dict(images=len(inv),selected_images=len(sels),selection_ready=sum(s.get('status')=='ready' for s in sels.values()),last_command=read(run/'last_command.json',{}),last_run=read(run/'last_run.json',{}),summary=read(run/'audit_summary.json',{}),missing_selection=[r['image_id'] for r in inv if r['image_id'] not in sels])
def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    for command in ['init','import','adopt','prepare','measure','review','export','status','inspect','crops','crop-review','metadata','rules','set-environment']:
        q=sub.add_parser(command);q.add_argument('--run',required=True)
        if command in ['init','import']:q.add_argument('--source',required=True)
        if command in ['import','prepare','measure']:q.add_argument('--images',nargs='*')
        if command in ['init','import','adopt']:q.add_argument('--config')
        if command in ['inspect','crops']:q.add_argument('--images',nargs='*')
        if command=='inspect':
            q.add_argument('--context-padding-px',type=int,default=32);q.add_argument('--area-change-fraction',type=float,default=0.20);q.add_argument('--saturation-fraction',type=float,default=0.01)
        if command=='crops':
            q.add_argument('--padding',type=int,default=32);q.add_argument('--boxes')
        if command=='crop-review':
            q.add_argument('--crop-ids',nargs='+',required=True);q.add_argument('--decision',choices=['accept','revise'],required=True);q.add_argument('--coverage',choices=['complete','incomplete'],required=True);q.add_argument('--evidence',required=True)
        if command in ['metadata','rules']:q.add_argument('--file')
        if command=='set-environment':q.add_argument('--config',required=True)
        if command=='prepare':
            q.add_argument('--factor',type=float,default=3);q.add_argument('--limit',type=int,default=12)
        if command=='export':q.add_argument('--audit',action='store_true')
        if command=='review':
            q.add_argument('--images',nargs='+',required=True);q.add_argument('--decision',choices=['accept','revise'],required=True);q.add_argument('--evidence',required=True)
    args=parser.parse_args()
    try:
        if args.command=='init':result=init(args)
        elif args.command=='import':result=import_batch(args)
        elif args.command=='metadata':result=metadata_command(args)
        elif args.command=='rules':result=rules_command(args)
        elif args.command=='set-environment':result=set_environment(args)
        elif args.command=='crop-review':result=training_crops.review(sys.modules[__name__],Path(args.run).resolve(),args)
        else:result=globals()[args.command](args)
    except Exception as e:
        error=dict(command=args.command,ok=False,error=str(e),error_type=type(e).__name__,time_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
        run=Path(args.run).resolve()
        if run.is_dir():
            write(run/'last_command.json',error);write(run/'last_error.json',dict(error,traceback=traceback.format_exc()))
        print(json.dumps(error,ensure_ascii=False),file=sys.stderr);raise SystemExit(1)
    write(Path(args.run).resolve()/'last_command.json',dict(command=args.command,ok=True,time_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())))
    def compact(x):
        if isinstance(x,list) and len(x)>25:return dict(count=len(x),first_25=x[:25])
        if isinstance(x,dict):return {k:compact(v) for k,v in x.items()}
        return x
    print(json.dumps(compact(result),ensure_ascii=False))
if __name__=='__main__':main()
