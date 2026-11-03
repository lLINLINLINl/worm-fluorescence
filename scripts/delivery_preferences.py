"""Optional local output naming; excluded from sharing packages."""
from pathlib import Path
import json

LOCAL = Path(__file__).resolve().parents[1] / 'references/local_preferences.json'


def load(path=None):
    path = Path(path) if path else LOCAL
    value = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    name = value.get('precise_csv_name')
    if name is not None and (not isinstance(name, str) or not name or
                             any(c in name for c in '/\\:<>"|?*') or any(ord(c) < 32 for c in name) or
                             not name.endswith('.csv') or name.casefold() in {
                                 'included_imagej_results.csv', 'all_decisions.csv',
                                 'excluded_no_intensity.csv', 'group_descriptive.csv'}):
        raise ValueError('precise_csv_name must be a safe CSV basename distinct from other tables')
    if name and name.casefold() == 'included_precise.csv':
        name = 'included_precise.csv'
    return dict(precise_csv_name=name)
