"""Explicit, boundary/rule-bound statistical decisions and final exports."""
from pathlib import Path
import csv
import html
import os
import statistics
import time
import zipfile

from PIL import Image, ImageDraw, ImageEnhance

import boundary_questions
import rules
import scoped_reviews
from file_operations import replace_with_retry

STATUSES = {'include', 'exclude', 'pending'}
IDENTITIES = {'worm', 'worm_fragment', 'uncertain'}
VISIBLE = {'complete', 'near_complete', 'long_body', 'short_fragment', 'uncertain'}
FIELDS = ['Group', 'ImageID', 'SourceFile', 'Label', 'Area', 'Mean', 'Min', 'Max', 'AreaUnit', 'Area_px']
DECISION_FIELDS = ['Group', 'ImageID', 'WormID', 'SourceFile', 'ObjectType', 'VisibleClass', 'StatisticsStatus',
                   'Reason', 'Evidence', 'DecisionSource', 'BoundaryAccepted', 'DecisionCurrent']


def validate_row(row):
    if not isinstance(row, dict) or row.get('status') not in STATUSES or row.get('object_type') not in IDENTITIES or row.get('visible_class') not in VISIBLE:
        raise ValueError('Invalid eligibility status/object_type/visible_class')
    if row['status'] != 'pending':
        if any(not isinstance(row.get(k), str) or not row[k].strip() for k in ('reason', 'evidence')):
            raise ValueError('Resolved eligibility requires actual reason/evidence')
        if row.get('decision_source') not in {'user_decision', 'agent_applied_rule'}:
            raise ValueError('Distinguish a user decision from application of the batch rule')
    if row['status'] == 'include' and (row['object_type'] == 'uncertain' or row['visible_class'] == 'uncertain'):
        raise ValueError('Uncertain identity/visibility cannot enter final statistics')


def publish(wf, run, stage):
    final = wf.inside(run, run / 'final_statistics')
    previous = None
    if final.exists():
        previous = wf.inside(run, run / 'history' / ('final_statistics_' + wf.stamp()))
        previous.parent.mkdir(exist_ok=True)
        replace_with_retry(final, previous)
    try:
        replace_with_retry(stage, final)
    except OSError:
        if previous is not None:
            replace_with_retry(previous, final)
        raise
    return final


def current(wf, run):
    inventory, selections = wf.data(run)
    policy_key = wf.digest(rules.load(run))
    expected = {}
    for source in inventory:
        selection = selections.get(source['image_id'])
        if selection:
            for worm in selection['worms']:
                expected[(source['image_id'], worm['worm_id'])] = dict(
                    source=source, selection=selection, worm=worm, rules_key=policy_key,
                    boundary_key=scoped_reviews.worm_key(wf, source, selection, worm))
    return inventory, selections, expected


def resolve(wf, run):
    inventory, selections, expected = current(wf, run)
    document = wf.read(run / 'statistical_eligibility.json', {'schema_version': 1, 'worms': []})
    if not isinstance(document, dict) or document.get('schema_version') != 1 or not isinstance(document.get('worms'), list):
        raise ValueError('Malformed stored statistical eligibility')
    rows = {}
    for row in document.get('worms', []):
        validate_row(row)
        key = (row['image_id'], row['worm_id'])
        if key in rows:
            raise ValueError('Duplicate eligibility decision: ' + str(key))
        rows[key] = row
    resolved = {}
    for key, context in expected.items():
        row = rows.get(key)
        valid = bool(row and all(row.get(k) == context[k] for k in ('boundary_key', 'rules_key')))
        resolved[key] = dict(row or {}, image_id=key[0], worm_id=key[1], decision_current=valid,
                             effective_status=row['status'] if valid else 'pending')
    return inventory, selections, expected, resolved


def command(wf, run, args):
    inventory, selections, expected = current(wf, run)
    path = run / 'statistical_eligibility.json'
    if args.file:
        document = wf.read(Path(args.file))
        if not isinstance(document, dict) or document.get('schema_version') != 1 or not isinstance(document.get('worms'), list):
            raise ValueError('Eligibility requires schema_version=1 and a worms list')
        old = wf.read(path, {'schema_version': 1, 'worms': []})
        by_key = {(r['image_id'], r['worm_id']): r for r in old['worms']}
        seen = set()
        for row in document['worms']:
            if not isinstance(row, dict):
                raise ValueError('Each eligibility row must be an object')
            key = (row.get('image_id'), row.get('worm_id'))
            if key in seen or key not in expected:
                raise ValueError('Duplicate or unknown eligibility ID: ' + str(key))
            seen.add(key)
            context = expected[key]
            if any(row.get(k) != context[k] for k in ('boundary_key', 'rules_key')):
                raise ValueError('Stale/missing boundary or rule fingerprint: ' + str(key) + '; generate a new template and review')
            validate_row(row)
            by_key[key] = dict(row)
        if path.exists():
            wf.write(run / 'history' / ('eligibility_' + wf.stamp() + '.json'), old)
        wf.write(path, dict(schema_version=1, worms=list(by_key.values())))
    inventory, selections, expected, resolved = resolve(wf, run)
    template = []
    impacted = []
    for key, context in expected.items():
        row = resolved[key]
        if row['decision_current']:
            proposed = {k: v for k, v in row.items() if k not in ('effective_status', 'decision_current')}
        else:
            proposed = dict(image_id=key[0], worm_id=key[1], status='pending', object_type='uncertain',
                            visible_class='uncertain', reason='', evidence='', decision_source='',
                            boundary_key=context['boundary_key'], rules_key=context['rules_key'])
            if 'status' in row:
                impacted.append(dict(image_id=key[0], worm_id=key[1], previous_status=row['status'],
                                     reason='boundary_or_rules_changed'))
        template.append(proposed)
    wf.write(run / 'statistical_eligibility_template.json', dict(schema_version=1, worms=template))
    wf.write(run / 'eligibility_affected.json', dict(affected=impacted, no_automatic_reinterpretation=True))
    unresolved = [dict(image_id=k[0], worm_id=k[1]) for k, r in resolved.items() if r['effective_status'] == 'pending']
    return dict(file=str(path), template=str(run / 'statistical_eligibility_template.json'),
                total_regions=len(expected), pending=unresolved, affected=impacted,
                measurement_changed=False, automatic_length_threshold=False)


def finalize(wf, run, args):
    rules.load(run, require_confirmed=True)
    inventory, selections, expected, resolved = resolve(wf, run)
    missing = [s['image_id'] for s in inventory if selections.get(s['image_id'], {}).get('status') != 'ready']
    pending = [key for key, r in resolved.items() if r['effective_status'] == 'pending']
    if missing or pending:
        raise ValueError('Final statistics blocked by unready images or pending/stale eligibility: '
                         + str(dict(images=missing[:25], regions=pending[:25])))
    profile = wf.profile(wf.read(run / 'workflow.json'))
    cache = wf.read(run / 'cache.json', {})
    native, precise, decisions, excluded, cards, source_files = [], [], [], [], [], {}
    stage = wf.inside(run, run / '.staging' / ('final_statistics_' + wf.stamp()))
    stage.mkdir(parents=True)
    font = boundary_questions.font_for(run, wf)
    with zipfile.ZipFile(stage / 'included_ROIs.zip', 'w', compression=zipfile.ZIP_DEFLATED) as selected_zip, \
            zipfile.ZipFile(stage / 'all_ROIs.zip', 'w', compression=zipfile.ZIP_DEFLATED) as all_zip:
        for source in inventory:
            identifier, selection = source['image_id'], selections[source['image_id']]
            wf.check_source(source)
            if not wf.cache_ok(run, cache.get(identifier), wf.calculation_key(selection, source, profile)):
                raise ValueError('Missing/stale original measurement: ' + identifier + '; run measure/export')
            if args.audit:
                wf.audit_image(run, source, selection)
            directory = wf.image_dir(run, source)
            values = {v['WormID']: v for v in wf.read(directory / 'verified_measurements.json')}
            with (directory / 'ImageJ_Results.csv').open(encoding='utf-8-sig', newline='') as handle:
                raw_rows = list(csv.DictReader(handle))
            rows = {r['Label']: r for r in raw_rows}
            labels = {identifier + '_' + w['worm_id'] for w in selection['worms']}
            if set(rows) != labels or len(raw_rows) != len(labels) or set(values) != labels:
                raise ValueError('Native measurement IDs do not match contours: ' + identifier)
            contours, colors, lines = [], {}, []
            for worm in selection['worms']:
                key = (identifier, worm['worm_id'])
                label = identifier + '_' + worm['worm_id']
                record = resolved[key]
                included = record['effective_status'] == 'include'
                boundary_ok = scoped_reviews.worm_accepted(wf, run, source, selection, worm)
                status = dict(Group=source['group'], ImageID=identifier, WormID=worm['worm_id'],
                              SourceFile=source['source_file'], ObjectType=record['object_type'],
                              VisibleClass=record['visible_class'], StatisticsStatus=record['effective_status'],
                              Reason=record['reason'], Evidence=record['evidence'], DecisionSource=record['decision_source'],
                              BoundaryAccepted=boundary_ok, DecisionCurrent=True)
                decisions.append(status)
                value, raw = values[label], rows[label]
                roi = directory / (label + '.roi')
                all_zip.write(roi, identifier + '/' + roi.name)
                source_files[roi.relative_to(run).as_posix()] = wf.sha(roi)
                if included:
                    native.append(dict(Group=source['group'], ImageID=identifier, SourceFile=source['source_file'],
                                       **{k: raw[k] for k in ('Label', 'Area', 'Mean', 'Min', 'Max')},
                                       AreaUnit=value['AreaUnit'], Area_px=value['Area_px']))
                    precise.append(dict(value, BoundaryVerified=boundary_ok))
                    selected_zip.write(roi, identifier + '/' + roi.name)
                else:
                    excluded.append(status)
                contours.append((worm['worm_id'], wf.points(selection, source, worm)))
                colors[worm['worm_id']] = '#40edb3' if included else '#c1c1c1'
                lines.append('<li>' + html.escape(worm['worm_id'] + ('：纳入 Mean=' + raw['Mean'] if included else '：排除强度统计')
                                                     + '；' + record['reason']) + '</li>')
            for name in ('ImageJ_Results.csv', 'verified_measurements.json'):
                path = directory / name
                source_files[path.relative_to(run).as_posix()] = wf.sha(path)
            with Image.open(source['source_path']) as original:
                preview = boundary_questions.overlay(ImageEnhance.Brightness(original).enhance(3), contours, font, colors)
            legend = Image.new('RGB', (preview.width, preview.height + 38), '#111820')
            legend.paste(preview, (0, 0))
            ImageDraw.Draw(legend).text((10, preview.height + 6), '纳入 Include = green; 排除 Exclude = gray', fill='#ffffff', font=font)
            (stage / 'full_frames').mkdir(exist_ok=True)
            legend.save(stage / 'full_frames' / (identifier + '.png'))
            cards.append('<article><h2>' + html.escape(identifier + ' / ' + source['source_file']) + '</h2><img src="full_frames/'
                         + identifier + '.png"><ul>' + ''.join(lines) + '</ul></article>')
    wf.csvwrite(stage / 'included_ImageJ_Results.csv', native, FIELDS)
    wf.csvwrite(stage / 'included_precise.csv', precise, list(precise[0]) if precise else ['Group', 'ImageID', 'WormID', 'Mean'])
    wf.csvwrite(stage / 'all_decisions.csv', decisions, DECISION_FIELDS)
    wf.csvwrite(stage / 'excluded_no_intensity.csv', excluded, DECISION_FIELDS)
    summary = []
    for group in sorted({s['group'] for s in inventory}):
        rows = [r for r in precise if r['Group'] == group]
        means = [float(r['Mean']) for r in rows]
        summary.append(dict(Group=group, IncludedRegions=len(rows), Images=len({r['ImageID'] for r in rows}),
                            Mean=statistics.mean(means) if means else '', Median=statistics.median(means) if means else '',
                            SD_regions=statistics.stdev(means) if len(means) > 1 else ''))
    wf.csvwrite(stage / 'group_descriptive.csv', summary,
                ['Group', 'IncludedRegions', 'Images', 'Mean', 'Median', 'SD_regions'])
    page = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>最终统计与完整图复核</title>'
            '<style>body{background:#171b21;color:#eee;max-width:1200px;margin:24px auto;font:17px sans-serif}'
            'article{border:1px solid #777;padding:16px;margin:24px 0}img{width:100%}a{color:#86d3ff}</style>'
            '<h1>最终统计与完整图复核</h1><p>绿色纳入，灰色排除；全部轮廓保留。统计资格与边界认可分别记录。</p>'
            '<p>Mean为原始RGB非加权亮度、不扣背景；Area_px为像素面积。区域数不代表独立动物或生物学重复。</p>'
            '<p><a href="included_ImageJ_Results.csv">纳入统计表</a> · <a href="all_decisions.csv">全部资格</a> · '
            '<a href="excluded_no_intensity.csv">排除清单（无强度值）</a> · <a href="included_ROIs.zip">纳入ROI</a> · '
            '<a href="all_ROIs.zip">全部ROI</a></p>' + ''.join(cards) + '</html>')
    (stage / 'index.html').write_text(page, encoding='utf-8')
    source_files.update({name: wf.sha(run / name) for name in
                         ('workflow.json', 'source_inventory.json', 'selections.json', 'statistical_eligibility.json', 'analysis_rules.json')})
    # Input-state provenance includes all scoped acceptance records when present.
    for name in ('reviews.json', 'scoped_reviews.json'):
        if (run / name).is_file():
            source_files[name] = wf.sha(run / name)
    result = dict(status='COMPLETE', source_images=len(inventory), included_regions=len(native),
                  excluded_regions=len(excluded), pending_regions=0, measurement_changed=False,
                  boundary_acceptance_required_for_statistics=False, training_started=False,
                  measurement_profile_fingerprint=profile,
                  source_files_sha256=source_files,
                  note='Raw imagej/ and tables/ retain all measured regions; final_statistics/ only includes eligible regions.')
    wf.write(stage / 'provenance.json', result)
    final = publish(wf, run, stage)
    result['page'] = str(final / 'index.html')
    return result
