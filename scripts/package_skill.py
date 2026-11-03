"""Build and verify a skill archive from an explicit public text-file allowlist."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import re
import zipfile

ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = 'release_files.json'
TEXT_SUFFIXES = {'.md', '.py', '.json', '.yaml', '.yml', '.txt'}
SENSITIVE_PATTERNS = {
    'private_home_path': re.compile(r'/(?:home|Users)/[A-Za-z0-9_.-]+/'),
    'credential_token': re.compile(r'(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})'),
    'private_key': re.compile(r'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----'),
    'credential_literal': re.compile(r'''(?i)\b(?:password|passwd|api[_-]?key|access[_-]?token|smtp[_-]?pass)\b["']?\s*[:=]\s*["']([^\s"']{8,})["']'''),
}


def approved_names(root):
    document = json.loads((root / ALLOWLIST).read_text(encoding='utf-8-sig'))
    names = document.get('files')
    if document.get('schema_version') != 1 or not isinstance(names, list) or not names:
        raise ValueError('Invalid release file allowlist')
    if any(not isinstance(n, str) for n in names) or len(set(names)) != len(names):
        raise ValueError('Release allowlist contains duplicate/invalid names')
    if not {ALLOWLIST, 'SKILL.md', 'LICENSE'} <= set(names):
        raise ValueError('Release allowlist must include itself, SKILL.md and LICENSE')
    for name in names:
        p = PurePosixPath(name)
        parts = [s.casefold() for s in p.parts]
        if (not name or '\\' in name or ':' in name or p.is_absolute() or p.as_posix() != name
                or '..' in parts or any(s.startswith('.') or s.startswith('local_') for s in parts)
                or (p.suffix.lower() not in TEXT_SUFFIXES and name != 'LICENSE')):
            raise ValueError('Non-public or non-text allowlist entry: ' + name)
        candidate = root / name
        for component in [candidate, *candidate.parents]:
            if component == root: break
            if component.is_symlink() or getattr(component, 'is_junction', lambda: False)():
                raise ValueError('Linked release entry: ' + name)
        if not candidate.resolve().is_relative_to(root.resolve()) or not candidate.is_file():
            raise ValueError('Missing/outside approved file: ' + name)
    return sorted(names)


def scan_text(name, raw):
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValueError('Approved file is not UTF-8 text: ' + name) from exc
    normalized = text.replace('\\\\', '/').replace('\\', '/')
    for number, line in enumerate(normalized.splitlines(), 1):
        # The generic Windows system-font directory is public configuration code.
        for match in re.finditer(r'(?i)\b[A-Z]:/[^\s"\'<>`]*', line):
            if match.group().casefold().split('/')[:2] != ['c:', 'windows']:
                raise ValueError(f'Absolute machine path in {name}:{number}')
        for label, pattern in SENSITIVE_PATTERNS.items():
            if pattern.search(line):
                raise ValueError(f'{label} in {name}:{number}')
    return text


def public_files(root=None):
    root = Path(root) if root else ROOT
    names = approved_names(root)
    payload = {}
    for name in names:
        raw = (root / name).read_bytes()
        scan_text(name, raw)
        payload[name] = raw
    return payload


def verify_archive(archive, manifest):
    expected = manifest['files_sha256']
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        wanted = {'worm-fluorescence/' + n for n in expected}
        if len(names) != len(set(names)) or set(names) != wanted:
            raise ValueError('Archive files differ from the audited allowlist')
        for name, sha in expected.items():
            raw = z.read('worm-fluorescence/' + name)
            if hashlib.sha256(raw).hexdigest() != sha:
                raise ValueError('Archive hash mismatch: ' + name)
            scan_text(name, raw)
    return dict(files_verified=len(expected), hashes_match=True, content_scan_passed=True)


def build(output):
    output = Path(output).resolve()
    if output.exists() or output.is_relative_to(ROOT.resolve()):
        raise ValueError('Use a new sharing directory outside the skill folder')
    payload = public_files()
    version_match = re.search(r'version: "([^"\n]+)"', payload['SKILL.md'].decode('utf-8-sig'))
    if not version_match or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?', version_match[1]):
        raise ValueError('Invalid skill release version')
    version = version_match[1]
    target = output / 'worm-fluorescence'
    target.mkdir(parents=True)
    for name, raw in payload.items():
        destination = target / name
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(raw)
    manifest = dict(skill='worm-fluorescence',version=version,license='MIT',
        files_sha256={n: hashlib.sha256(b).hexdigest() for n,b in payload.items()},
        packaging_policy='explicit_text_file_allowlist',content_scan_passed=True,
        installation_requires_local_configuration=True,accuracy_claim='not established',
        privacy_scope='Approved UTF-8 text files; automated checks are not a general privacy guarantee.')
    archive = output / ('worm-fluorescence-v' + version + '.skill.zip')
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as handle:
        for name,raw in payload.items():handle.writestr('worm-fluorescence/'+name,raw)
    manifest['archive_verification'] = verify_archive(archive,manifest)
    manifest['archive_sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output/'package_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return dict(folder=str(target),archive=str(archive),files=len(payload),
                manifest=str(output/'package_manifest.json'),published=False,**manifest['archive_verification'])


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    print(json.dumps(build(parser.parse_args().output),ensure_ascii=False))
