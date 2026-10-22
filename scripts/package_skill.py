"""Build a sanitized skill ZIP; publishing is a separate operation."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import shutil
import zipfile

ROOT = Path(__file__).resolve().parent.parent
TOP = {'SKILL.md','README.md','INSTALL.md','LICENSE','NOTICE.md','CONTRIBUTING.md',
       'CHANGELOG.md','requirements.txt','requirements-tested.txt'}
DIRECTORIES = {'scripts','references','agents','examples','tests','docs'}


def build(output):
    output = Path(output).resolve()
    if output.exists() or output.is_relative_to(ROOT):
        raise ValueError('Use a new sharing directory outside the skill folder')
    target = output / 'worm-fluorescence'
    target.mkdir(parents=True)
    files = {}
    for path in ROOT.rglob('*'):
        if not path.is_file(): continue
        relative = path.relative_to(ROOT)
        if '__pycache__' in relative.parts or path.suffix == '.pyc': continue
        if relative.as_posix() == 'references/local_environment.json': continue
        if relative.parts[0] not in DIRECTORIES and relative.as_posix() not in TOP: continue
        if relative.parts[0] == 'docs' and path.suffix.lower() not in {'.md','.png','.json'}: continue
        destination = target / relative
        destination.parent.mkdir(parents=True,exist_ok=True)
        raw = path.read_bytes()
        if path.suffix.lower() != '.png':
            decoded = raw.decode('utf-8-sig')
            if str(Path.home()) in decoded or Path.home().as_posix() in decoded:
                raise ValueError('Personal path remains in ' + relative.as_posix())
        shutil.copy2(path,destination)
        files[relative.as_posix()] = hashlib.sha256(raw).hexdigest()
    version = re.search(r'version: "([^"\n]+)"',(target/'SKILL.md').read_text(encoding='utf-8')).group(1)
    manifest = dict(skill='worm-fluorescence',version=version,files_sha256=files,license='MIT',
        includes_experimental_images=False,includes_personal_configuration=False,includes_model_weights=False,
        installation_requires_local_configuration=True,training_started=False,accuracy_claim='not established')
    (output/'package_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    archive = output / ('worm-fluorescence-v' + version + '.skill.zip')
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(target.rglob('*')):
            if path.is_file(): handle.write(path,path.relative_to(output).as_posix())
    return dict(folder=str(target),archive=str(archive),files=len(files),private_data_included=False,published=False)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    print(json.dumps(build(parser.parse_args().output),ensure_ascii=False))
