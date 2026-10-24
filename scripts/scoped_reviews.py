"""User acceptance at frame, worm, or feature scope; never promotes a feature."""
from pathlib import Path
import time


def worm_key(wf, source, selection, worm):
    return wf.digest(dict(source=source['source_sha256'], size=[source['width'], source['height']],
                          worm=worm['worm_id'], points=wf.points(selection, source, worm).tolist()))


def matching(wf, run, source, selection):
    worms = {w['worm_id']: w for w in selection['worms']}
    return [r for r in wf.read(run / 'scoped_reviews.json', [])
            if r['image_id'] == source['image_id'] and r['worm_id'] in worms
            and r['boundary_key'] == worm_key(wf, source, selection, worms[r['worm_id']])]


def frame_accepted(wf, run, source, selection):
    review = wf.read(run / 'reviews.json', {}).get(source['image_id'], {})
    if review.get('decision') != 'accept' or review.get('boundary_key') != wf.boundary_key(selection, source):
        return False
    return not any(r['decision'] == 'revise' and r.get('recorded_at_ns', 0) > review.get('recorded_at_ns', 0)
                   for r in matching(wf, run, source, selection))


def worm_accepted(wf, run, source, selection, worm):
    rows = [r for r in matching(wf, run, source, selection) if r['worm_id'] == worm['worm_id']]
    whole = [r for r in rows if r['scope'] == 'worm']
    frame = wf.read(run / 'reviews.json', {}).get(source['image_id'], {})
    if frame.get('boundary_key') == wf.boundary_key(selection, source):
        whole.append(frame)
    if not whole:
        return False
    review = max(whole, key=lambda r: r.get('recorded_at_ns', 0))
    return review['decision'] == 'accept' and not any(
        r['decision'] == 'revise' and r.get('recorded_at_ns', 0) > review.get('recorded_at_ns', 0) for r in rows)


def record(wf, run, args):
    inventory, selections = wf.data(run)
    sources = {s['image_id']: s for s in inventory}
    scope = getattr(args, 'scope', 'frame')
    targets = getattr(args, 'worms', None)
    feature = getattr(args, 'feature', None)
    if not args.evidence.strip():
        raise ValueError('Actual user evidence and its scope are required')
    if scope == 'frame' and (targets or feature):
        raise ValueError('Frame acceptance cannot be combined with --worms/--feature')
    if scope != 'frame' and not targets:
        raise ValueError('Worm/feature review requires --worms')
    if (scope == 'feature') != bool(feature):
        raise ValueError('Only feature review requires --feature (for example tail_endpoint_B)')
    if len(set(args.images)) != len(args.images) or targets and len(set(targets)) != len(targets):
        raise ValueError('Duplicate image/worm IDs')
    records = []
    now = time.time_ns()
    for identifier in args.images:
        if identifier not in sources or identifier not in selections:
            raise ValueError('No contour: ' + identifier)
        source, selection = sources[identifier], selections[identifier]
        wf.check_source(source)
        if scope == 'frame':
            records.append(dict(image_id=identifier, boundary_key=wf.boundary_key(selection, source)))
        else:
            worms = {w['worm_id']: w for w in selection['worms']}
            if set(targets) - worms.keys():
                raise ValueError('Unknown worm for ' + identifier)
            for target in targets:
                records.append(dict(image_id=identifier, worm_id=target, scope=scope, feature=feature,
                                    boundary_key=worm_key(wf, source, selection, worms[target])))
    path = run / ('reviews.json' if scope == 'frame' else 'scoped_reviews.json')
    old = wf.read(path, {} if scope == 'frame' else [])
    if path.exists():
        wf.write(run / 'history' / (path.stem + '_' + wf.stamp() + '.json'), old)
    common = dict(decision=args.decision, evidence=args.evidence, recorded_at_ns=now,
                  meaning='user_acceptance_of_assistant_contours_not_independent_test_ground_truth')
    if scope == 'frame':
        for row in records:
            old[row['image_id']] = dict(common, scope=scope, boundary_key=row['boundary_key'])
    else:
        replaced = {(r['image_id'], r['worm_id'], scope, feature) for r in records}
        old = [r for r in old if (r['image_id'], r['worm_id'], r['scope'], r.get('feature')) not in replaced]
        old.extend(dict(row, **common) for row in records)
    wf.write(path, old)
    return dict(recorded=len(records), scope=scope, decision=args.decision,
                frame_acceptance_granted=scope == 'frame' and args.decision == 'accept')
