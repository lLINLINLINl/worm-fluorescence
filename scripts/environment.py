"""Configure and diagnose local dependencies without installing or changing ImageJ preferences."""
from pathlib import Path
import argparse
import importlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
PROFILE = "RGB8_unweighted_original_pixels_no_background"
PACKAGES = {"numpy": "numpy", "Pillow": "PIL", "opencv-python": "cv2",
            "roifile": "roifile", "shapely": "shapely"}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def load_config(path=None):
    chosen = path or os.environ.get("WORM_FLUORESCENCE_CONFIG")
    target = Path(chosen).expanduser() if chosen else ROOT / "references/local_environment.json"
    if not target.is_file():
        raise ValueError("No local configuration. Run scripts/environment.py configure --fiji FIJI_FOLDER; "
                         "then doctor. Do not reuse another computer's measurement confirmation.")
    return read_json(target)


def preferences(path):
    result = {}
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def discover_font():
    candidates = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc",
                  Path("/System/Library/Fonts/PingFang.ttc"),
                  Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
                  Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    return next((str(p.resolve()) for p in candidates if p.is_file()), "")


def configure(args):
    fiji = Path(args.fiji).expanduser().resolve() if args.fiji else None
    java = args.java
    if not java and fiji and (fiji / "java").is_dir():
        options = sorted((p for p in (fiji / "java").rglob("java.exe") if p.parent.name == "bin"))
        if not options:
            options = sorted(p for p in (fiji / "java").rglob("java") if p.parent.name == "bin")
        java = str(options[0]) if options else None
    java = java or shutil.which("java") or ""
    ij = args.ij_jar
    if not ij and fiji:
        jars = list((fiji / "jars").glob("ij-*.jar"))
        jars.sort(key=lambda p: tuple(int(n) for n in re.findall(r"\d+", p.name)))
        ij = str(jars[-1]) if jars else (str(fiji / "ij.jar") if (fiji / "ij.jar").is_file() else "")
    prefs = args.prefs or str(Path.home() / ".imagej/IJ_Prefs.txt")
    result = dict(java=java, ij_jar=ij or "", prefs=prefs, font=args.font or discover_font(),
                  profile=PROFILE, expected_measurements=19, expected_precision=3,
                  settings_user_confirmed_for_current_batch=False)
    missing = [k for k in ("java", "ij_jar", "prefs", "font") if not result[k] or not Path(result[k]).is_file()]
    if missing:
        raise ValueError("Missing paths: " + ", ".join(missing) +
                         ". Supply explicit options; open ImageJ and save the agreed measurement settings first. "
                         "No preferences were changed.")
    result.update({k: str(Path(result[k]).expanduser().resolve()) for k in ("java", "ij_jar", "prefs", "font")})
    target = Path(args.output).expanduser().resolve() if args.output else ROOT / "references/local_environment.json"
    if target.exists():
        raise ValueError("Configuration already exists; choose --output NEW_FILE to preserve it.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return dict(config=str(target),next="Run doctor --config THIS_FILE. For workflow init use --config THIS_FILE.",
                preferences_changed=False, experimental_settings_confirmed=False)


def doctor(args):
    checks = {"python": {"version": platform.python_version(), "ok": sys.version_info >= (3, 10)}}
    for distribution, module in PACKAGES.items():
        try:
            importlib.import_module(module)
            try:
                version = importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError:
                version = getattr(sys.modules[module], "__version__", "unknown")
            checks[distribution] = dict(ok=True, distribution_version=version, module_version=getattr(sys.modules[module], "__version__", "unknown"))
        except Exception as exc:
            checks[distribution] = dict(ok=False, error=str(exc))
    try:
        config = load_config(args.config)
        for name in ("java", "ij_jar", "prefs", "font"):
            value = config.get(name)
            checks[name] = dict(ok=bool(value and Path(value).is_file()), path=value)
        if checks["java"]["ok"]:
            process = subprocess.run([config["java"], "-version"], capture_output=True, text=True, timeout=15)
            checks["java"]["ok"] = process.returncode == 0
            checks["java"]["version"] = (process.stderr or process.stdout).splitlines()[:1]
        if checks["prefs"]["ok"]:
            prefs = preferences(config["prefs"])
            checks["measurement_profile"] = dict(
                ok=config.get("profile") == PROFILE and int(prefs.get("measurements", -1)) == 19
                and int(prefs.get("precision", -1)) == 3 and not (int(prefs.get("prefs.options", 0)) & 256),
                measurements=prefs.get("measurements"), precision=prefs.get("precision"),
                unweighted_rgb=not bool(int(prefs.get("prefs.options", 0)) & 256))
        else:
            checks["measurement_profile"] = dict(ok=False, reason="Preferences unavailable")
    except Exception as exc:
        checks["configuration"] = dict(ok=False, error=str(exc))
    return dict(ok=all(v["ok"] for v in checks.values()), checks=checks, preferences_changed=False,
                scope="Local environment only. Does not confirm acquisition comparability or boundary accuracy.",
                validated_image_type="single-frame RGB8", validated_platform="Windows; other platforms require a sample measurement")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("configure")
    for name in ("fiji", "java", "ij-jar", "prefs", "font", "output"):
        p.add_argument("--" + name)
    p = commands.add_parser("doctor")
    p.add_argument("--config")
    args = parser.parse_args()
    try:
        result = configure(args) if args.command == "configure" else doctor(args)
    except Exception as exc:
        print(json.dumps(dict(ok=False, error=str(exc), preferences_changed=False), ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False))
    if result.get("ok") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
