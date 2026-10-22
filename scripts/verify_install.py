"""Run real ImageJ against synthetic fixtures. No experimental data or model calls."""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import rules


def read(path): return json.loads(path.read_text(encoding='utf-8-sig'))
def write(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(config, output):
    output = output.resolve()
    if output.exists() or output.is_relative_to(ROOT):
        raise ValueError('Use a new verification directory outside the skill repository')
    output.mkdir(parents=True)

    def run(*args, script='scripts/workflow.py', ok=True):
        process = subprocess.run([sys.executable,'-X','utf8',str(ROOT/script),*map(str,args)],
                                 capture_output=True,text=True,encoding='utf-8',timeout=300)
        log = dict(command=list(map(str,args)),exit_code=process.returncode,stdout=process.stdout,stderr=process.stderr)
        with (output/'commands.jsonl').open('a',encoding='utf-8') as handle:
            handle.write(json.dumps(log,ensure_ascii=False)+'\n')
        if (process.returncode == 0) != ok:
            raise RuntimeError(process.stdout + process.stderr)
        return json.loads(process.stdout if ok else process.stderr)

    configuration = read(config)
    prefs = Path(configuration['prefs'])
    prefs_before = sha(prefs)
    run('doctor','--config',config,script='scripts/environment.py')
    run('--output',output/'fixture',script='examples/make_demo.py')
    batch = output/'run'
    run('init','--source',output/'fixture/input','--run',batch,'--config',config)
    shutil.copy2(output/'fixture/demo_selections.json',batch/'selections.json')
    refused = run('measure','--run',batch,ok=False)
    assert 'Confirm this batch' in refused['error']
    policy = rules.template()
    policy['measurement']['confirmed'] = True
    policy['inclusion'].update(confirmed=True,border_policy='visible_if_near_complete',
        crossing_policy='independent_shared_pixels',small_worm_policy='include_unless_abnormal')
    policy['evidence'].update(user_quote='SYNTHETIC SOFTWARE TEST: generated polygons, not experimental acceptance',scope='this generated fixture only')
    write(output/'synthetic_rules.json',policy)
    run('rules','--run',batch,'--file',output/'synthetic_rules.json')
    first = run('measure','--run',batch)
    assert first['measured_images'] == 3 and first['cache_hits'] == 0
    exported = run('export','--run',batch,'--audit')
    assert exported['audited_images'] == 3 and exported['worms'] == 3
    page = batch/'ROI_逐图复核.html'
    for link in re.findall(r'(?:href|src)="([^"]+)"',page.read_text(encoding='utf-8')):
        parsed = urlsplit(link)
        if parsed.scheme or link.startswith('#'): continue
        assert (page.parent/unquote(parsed.path)).is_file(), 'Missing review page artifact: '+link
    rows = list(csv.DictReader((batch/'tables/ALL_per_worm.csv').open(encoding='utf-8-sig',newline='')))
    # Inclusive drawing fills 21 columns of the intersecting rectangle inside
    # W01's 120x20 ImageJ ROI: (1859*42 + 420*75 + 121*93) / 2400.
    expected = {'DEMO_001_W01':(2400,50.34625),'DEMO_001_W02':(1700,75.0),'DEMO_002_W01':(330,42.0)}
    for row in rows:
        area, mean = expected[row['WormID']]
        assert int(row['Area_px']) == area and abs(float(row['Mean'])-mean)<1e-8, row
    preserved = {p.name:sha(p) for p in (batch/'tables').glob('*.csv')}
    repeat = run('measure','--run',batch)
    assert repeat == dict(measured_images=0,cache_hits=3,pending_images=[])
    assert preserved == {p.name:sha(p) for p in (batch/'tables').glob('*.csv')}
    run('review','--run',batch,'--images','DEMO_001','DEMO_002','DEMO_003','--decision','accept',
        '--evidence','SYNTHETIC fixture only; not real worm boundary acceptance')
    accepted = run('export','--run',batch)
    assert accepted['boundary_accepted_images'] == 3
    candidates = run('crops','--run',batch,'--padding',0)
    assert candidates['ready'] == 1
    crop = next(item for item in read(batch/'training/crop_manifest.json') if item['SourceImageID']=='DEMO_002')
    assert any(item['Status']=='BLOCKED_PARTIAL_LABEL' for item in read(batch/'training/crop_manifest.json'))
    run('crop-review','--run',batch,'--crop-ids',crop['CropID'],'--decision','revise','--coverage','incomplete',
        '--evidence','SYNTHETIC explicit revocation')
    assert not list((batch/'training/crops_ready/images').glob('*.png'))
    selections = read(batch/'selections.json')
    selections[0]['worms'][0]['points_original'][0][0] -= 1
    write(batch/'selections.json',selections)
    changed = run('measure','--run',batch)
    assert changed == dict(measured_images=1,cache_hits=2,pending_images=[])
    changed_export = run('export','--run',batch,'--audit')
    assert changed_export['boundary_accepted_images'] == 2
    selections[2]['worms'] = [dict(worm_id='W01',points_original=[[80,100],[110,100],[110,129],[80,129]])]
    write(batch/'selections.json',selections)
    run('measure','--run',batch)
    run('export','--run',batch,'--audit')
    qc = list(csv.DictReader((batch/'tables/qc_flags.csv').open(encoding='utf-8-sig',newline='')))
    assert next(row for row in qc if row['ImageID']=='DEMO_003')['TouchesImageBorder'] == 'True'
    from PIL import Image
    import numpy as np
    mask=np.array(Image.open(batch/'imagej/DEMO/DEMO_003/DEMO_003_W01_ImageJ_mask.png'))>0
    assert not (mask[-1].any() or mask[0].any() or mask[:,0].any() or mask[:,-1].any())
    assert sha(prefs) == prefs_before
    result=dict(ok=True,synthetic_not_experimental=True,actual_ImageJ_initial_images=3,initial_worms=3,
        fixed_expected_values_checked=True,independent_original_pixel_audit=True,overlapping_ROIs_preserved=True,
        unconfirmed_rules_rejected=True,cache_repeat=repeat,local_change=changed,acceptance_invalidated_on_change=True,
        partial_label_crop_blocked=True,crop_revocation_removes_ready=True,contour_border_regression_passed=True,
        configured_preferences_unchanged=True,isolated_preferences_loading_checked=True,
        review_page_local_links_valid=True,real_worm_accuracy_evaluated=False)
    write(output/'verification.json',result)
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    try:
        print(json.dumps(verify(args.config.resolve(),args.output),ensure_ascii=False))
    except Exception as error:
        print(json.dumps(dict(ok=False,error=str(error),error_type=type(error).__name__),ensure_ascii=False),file=sys.stderr)
        raise SystemExit(1)
