#!/usr/bin/env python3
"""Make dist\\SmashUltimateHDR-Setup.exe: installer/hdr_setup.py frozen with PyInstaller, the port's source code inside.

    python installer/build_exe.py [--out dist]          (needs: pip install pyinstaller zstandard)

source.zip = the files git knows in this checkout (tracked + new, not ignored), as they are in the working tree; its zip
comment names the commit ("<branch> <sha>[-dirty]"), which the setup writes to <install>\\src\\.release. Before the zip
is made every file is checked: nothing generated from the game may be in it (no lifted C, no .elf / .nso / .nro / .arc,
no build folder, no site log with instruction words).
"""
import argparse
import os
import re
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BAD_EXT = ('.elf', '.nso', '.nro', '.arc', '.nca', '.nsp', '.xci', '.dll', '.exe', '.obj', '.lib', '.keys')
BAD_TEXT = [(re.compile(rb'(?m)(?:^void [fu]_7[0-9a-f]{9}\(Ctx \*c[^;\n]*\{[ \t]*$[\s\S]*?){20}'), ('.c', '.h', '.inc')),     # lifted C (20+ lifted function bodies)
            (re.compile(rb'(?m)^patch [0-9a-f]{10} [0-9a-f]{8} [0-9a-f]{8}'), ('.txt', '.log', '.sites'))]                      # a recorded site list (game words)


def git(*a):
    return subprocess.run(['git', '-C', REPO] + list(a), capture_output=True, text=True, check=True).stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(REPO, 'dist'))
    ap.add_argument('--work', default=os.path.join(REPO, 'build_exe_tmp'))
    a = ap.parse_args()
    files = sorted(set(git('ls-files', '-co', '--exclude-standard').splitlines()))
    files = [f for f in files if os.path.isfile(os.path.join(REPO, f)) and not f.startswith(('build/', 'dist/', 'build_exe_tmp/', 'deps/', 'sd/'))]
    sha = git('rev-parse', '--short=10', 'HEAD').strip()
    branch = git('rev-parse', '--abbrev-ref', 'HEAD').strip()
    dirty = bool(git('status', '--porcelain', '--untracked-files=no').strip())
    comment = '%s %s%s' % (branch, sha, '-dirty' if dirty else '')
    os.makedirs(a.work, exist_ok=True)
    zpath = os.path.join(a.work, 'source.zip')
    bad = []
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in files:
            data = open(os.path.join(REPO, f), 'rb').read()
            if f.lower().endswith(BAD_EXT):
                bad.append('%s: file type not allowed' % f)
            for rx, exts in BAD_TEXT:
                if f.lower().endswith(exts) and rx.search(data):
                    bad.append('%s: matches %s' % (f, rx.pattern[:40]))
            z.writestr(f, data)
        z.comment = comment.encode()
    if bad:
        os.remove(zpath)
        sys.exit('refused, the source holds files it must not:\n  ' + '\n  '.join(bad))
    print('source.zip: %d files, %.1f MB, %s' % (len(files), os.path.getsize(zpath) / 1048576.0, comment))
    cmd = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--console', '--name', 'SmashUltimateHDR-Setup',
           '--distpath', a.out, '--workpath', os.path.join(a.work, 'pyi'), '--specpath', a.work,
           '--add-data', zpath + os.pathsep + '.', '--hidden-import', 'zstandard', os.path.join(HERE, 'hdr_setup.py')]
    subprocess.run(cmd, check=True)
    exe = os.path.join(a.out, 'SmashUltimateHDR-Setup.exe')
    print('%s: %.1f MB' % (exe, os.path.getsize(exe) / 1048576.0))


if __name__ == '__main__':
    main()
