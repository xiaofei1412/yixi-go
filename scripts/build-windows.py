"""Build the product only; never copy a user's database or training artifacts."""
import hashlib
from importlib import metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desktop_launcher import VERSION

OUTPUT = ROOT/'dist'/f'YixiGo-v{VERSION}-windows-x64'
WORK = ROOT/'build'/f'desktop-v{VERSION}'
MODEL = 'kata1-b28c512nbt-s12192929536-d5655876072.bin.gz'
ENGINE_FILES = ['katago.exe', MODEL, 'product_analysis.cfg', 'README.txt', 'cacert.pem',
                'libcrypto-3-x64.dll', 'libssl-3-x64.dll', 'libz.dll', 'libzip.dll',
                'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
                'msvcp140_atomic_wait.dll', 'msvcp140_codecvt_ids.dll',
                'vcruntime140.dll', 'vcruntime140_1.dll']


def sha256(path):
    with path.open('rb') as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def licenses(destination):
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT/'packaging/KataGo-network-LICENSE.txt', destination)
    shutil.copy2(Path(sys.base_prefix)/'LICENSE.txt', destination/'Python-LICENSE.txt')
    # Installed distribution metadata contains each package's own license files.
    for dist in metadata.distributions():
        for file in dist.files or []:
            if any(term in file.name.lower() for term in ('license', 'copying', 'notice', 'copyright')):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    target = destination/'python'/dist.metadata['Name']/str(file)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    base = 'https://raw.githubusercontent.com/lightvector/KataGo/v1.16.4/'
    sources = {p: base+p for p in (
        'LICENSE', 'cpp/external/clblast/LICENSE', 'cpp/external/filesystem-1.5.8/LICENSE',
        'cpp/external/half-2.2.0/LICENSE.txt', 'cpp/external/httplib/LICENSE',
        'cpp/external/mozilla-cacerts/LICENSE', 'cpp/external/tclap-1.2.2/COPYING',
        'cpp/core/sha2.cpp', 'cpp/external/nlohmann_json/json.hpp')}
    sources.update({
        'OpenSSL-LICENSE.txt': 'https://raw.githubusercontent.com/openssl/openssl/openssl-3.0.16/LICENSE.txt',
        'libzip-LICENSE': 'https://raw.githubusercontent.com/nih-at/libzip/v1.11.3/LICENSE',
        'zlib-LICENSE': 'https://raw.githubusercontent.com/madler/zlib/v1.3.1/LICENSE',
        'Tcl-LICENSE': 'https://raw.githubusercontent.com/tcltk/tcl/core-8-6-12/license.terms',
        'Tk-LICENSE': 'https://raw.githubusercontent.com/tcltk/tk/core-8-6-12/license.terms'})
    cache = ROOT/'build/license-cache'
    for name, url in sources.items():
        cached = cache/name
        if not cached.exists():
            cached.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(url, timeout=60) as response:
                cached.write_bytes(response.read())
        target = destination/'KataGo'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cached, target)
    (destination/'SOURCES.txt').write_text(
        '\n'.join(f'{name}: {url}' for name, url in sources.items()) +
        '\nKataGo Windows runtime DLLs are redistributed unchanged from its upstream release.\n'
        'Microsoft Visual C++ runtime: https://visualstudio.microsoft.com/license-terms/vs2022-cruntime/\n'
        'Python runtime includes Tcl/Tk; their notices are included here as Tcl-LICENSE and Tk-LICENSE.\n', encoding='utf-8')


def main():
    if sys.platform != 'win32' or sys.maxsize <= 2**32:
        raise SystemExit('Build on 64-bit Windows using the dedicated build environment.')
    for name in ENGINE_FILES:
        if not (ROOT/'katago_engine'/name).is_file():
            raise SystemExit(f'Missing engine resource: {name}')
    WORK.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
               '--onedir', '--windowed', '--name', 'YixiGo',
               '--distpath', str(OUTPUT.parent), '--workpath', str(WORK/'pyinstaller'),
               '--specpath', str(WORK), '--collect-submodules', 'uvicorn']
    for source, target in [('index.html', '.'), ('app.js', '.'), ('style.css', '.'),
                           ('examples/study-demo.sgf', 'examples')]:
        command.extend(['--add-data', f'{ROOT/source};{target}'])
    command.append(str(ROOT/'desktop_launcher.py'))
    subprocess.run(command, cwd=ROOT, check=True)
    staging = OUTPUT.parent/'YixiGo'
    # Only replace this script's own versioned output, never the project build tree.
    if OUTPUT.exists():
        if OUTPUT.resolve().parent != (ROOT/'dist').resolve():
            raise RuntimeError('Unexpected output path')
        shutil.rmtree(OUTPUT)
    staging.rename(OUTPUT)
    engine = OUTPUT/'katago_engine'
    engine.mkdir()
    for name in ENGINE_FILES:
        shutil.copy2(ROOT/'katago_engine'/name, engine/name)
    shutil.copy2(ROOT/'packaging/使用说明.txt', OUTPUT)
    licenses(OUTPUT/'THIRD_PARTY')
    files = sorted(path for path in OUTPUT.rglob('*') if path.is_file())
    forbidden = {'.db', '.sqlite', '.sqlite3', '.log', '.pt', '.pth'}
    assert not any(path.suffix.lower() in forbidden for path in files), 'Private/build data found'
    assert not any(part in ('product_data', 'train_v2_runs', '.git') for path in files for part in path.parts)
    manifest = {'version': VERSION, 'python': sys.version.split()[0],
                'katago': '1.16.4 OpenCL', 'model': MODEL,
                'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'source_dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip()),
                'files': {p.relative_to(OUTPUT).as_posix(): sha256(p) for p in files}}
    (OUTPUT/'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    archive = Path(str(OUTPUT)+'.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(OUTPUT.rglob('*')):
            if path.is_file():
                bundle.write(path, path.relative_to(OUTPUT.parent))
    (OUTPUT.parent/'SHA256SUMS.txt').write_text(f'{sha256(archive)}  {archive.name}\n', encoding='ascii')
    print(f'BUILT {archive} ({archive.stat().st_size / 1024**2:.1f} MiB)', flush=True)


if __name__ == '__main__':
    main()
