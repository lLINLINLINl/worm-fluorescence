"""Per-batch, user-confirmed scientific choices; no automatic contour changes."""
import json

PROFILE = 'RGB8_unweighted_original_pixels_no_background'
CHOICES = {
    'border_policy': {'visible_if_near_complete', 'complete_only', 'ask'},
    'crossing_policy': {'independent_shared_pixels', 'exclude', 'ask'},
    'small_worm_policy': {'include_unless_abnormal', 'exclude', 'ask'},
}


def template():
    return dict(schema_version=1,
        measurement=dict(profile=PROFILE, confirmed=False),
        inclusion=dict(confirmed=False, border_policy=None, crossing_policy=None,
                       small_worm_policy=None, minimum_visible_fraction=None),
        evidence=dict(user_quote='', scope=''))


def validate(document, require_confirmed=False):
    if not isinstance(document, dict) or document.get('schema_version') != 1:
        raise ValueError('Rules require schema_version=1')
    measurement = document.get('measurement', {})
    inclusion = document.get('inclusion', {})
    evidence = document.get('evidence', {})
    if not all(isinstance(item, dict) for item in (measurement, inclusion, evidence)):
        raise ValueError('Rules measurement/inclusion/evidence must be objects')
    if measurement.get('profile') != PROFILE:
        raise ValueError('Only RGB8 unweighted original brightness without background subtraction is implemented; '
                         'other channels or profiles need separate validation')
    for section in (measurement, inclusion):
        if type(section.get('confirmed')) is not bool:
            raise ValueError('Rule confirmation flags must be booleans')
    for key, choices in CHOICES.items():
        if inclusion.get(key) is not None and inclusion[key] not in choices:
            raise ValueError('Invalid inclusion choice: ' + key)
    fraction = inclusion.get('minimum_visible_fraction')
    if fraction is not None and (type(fraction) not in (int, float) or not 0 < fraction <= 1):
        raise ValueError('minimum_visible_fraction must be null or a number in (0,1]')
    confirmed = measurement['confirmed'] and inclusion['confirmed']
    if inclusion['confirmed'] and any(inclusion.get(key) is None for key in CHOICES):
        raise ValueError('Confirmed inclusion requires all policy choices')
    if confirmed and (not isinstance(evidence.get('user_quote'), str) or not evidence['user_quote'].strip()
                      or not isinstance(evidence.get('scope'), str) or not evidence['scope'].strip()):
        raise ValueError('Confirmed rules require the user instruction and its scope')
    if require_confirmed and not confirmed:
        raise ValueError('Confirm this batch measurement and inclusion rules before measuring; '
                         'see analysis_rules.json and scripts/workflow.py rules')
    return confirmed


def load(run, require_confirmed=False):
    path = run / 'analysis_rules.json'
    if not path.is_file():
        if require_confirmed:
            raise ValueError('Missing analysis_rules.json; record this batch user-confirmed rules first')
        return template()
    document = json.loads(path.read_text(encoding='utf-8-sig'))
    validate(document, require_confirmed)
    return document
