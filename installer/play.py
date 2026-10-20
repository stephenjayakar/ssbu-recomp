#!/usr/bin/env python3
"""Start Smash Ultimate with HewDraw Remix (the build installer/hdr_setup.py made). "Smash Ultimate HDR.bat" runs this.

    python play.py [--build <install>\\build] [--userdata <install>\\userdata] [--log <install>\\play.log] [--exe ssbu_native.exe]
                   [--env NAME=VALUE ...] [--seconds N]

The game reads data.arc from the dump folder the installer was given (build\\state.json), the SD card folder (HDR,
ARCropolis, smashline ...) from <install>\\sd, settings from build\\config\\graphics.ini. Saves go to userdata\\.
The last three logs are kept (play.log.1 = the run before) and the game's exit code is appended to the log.
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--build', default=os.path.join(HERE, 'build'))
    ap.add_argument('--userdata', default=os.path.join(HERE, 'userdata'))
    ap.add_argument('--log', default=os.path.join(HERE, 'play.log'))
    ap.add_argument('--exe', default='ssbu_native.exe')
    ap.add_argument('--seconds', type=int, default=0)
    ap.add_argument('--env', action='append', default=[])
    a = ap.parse_args()
    with open(os.path.join(a.build, 'state.json')) as f:
        state = json.load(f)
    work = os.path.join(a.build, 'work')
    exe = os.path.join(work, a.exe)
    romfs = state['dump']['romfs']
    if not os.path.isfile(os.path.join(romfs, 'data.arc')):
        print('The game files are not where they were at the install: %s\\data.arc is missing.\n'
              'Put the dump folder back there (or edit "romfs" in %s).' % (romfs, os.path.join(a.build, 'state.json')))
        return 3
    env = {k: v for k, v in os.environ.items() if not k.startswith(('RECOMP_', 'SSBU_'))}
    env['SSBU_ROMFS'] = romfs
    env['SSBU_USERDATA'] = os.path.abspath(a.userdata)
    for sub, var in (('fonts', 'SSBU_FONTS'), ('zoneinfo', 'SSBU_TZDIR')):
        p = os.path.join(a.build, 'sysdata', sub)
        if os.path.isdir(p):
            env[var] = p
    if a.exe.lower() not in ('ssbu_native.exe', 'ssbu_user.exe'):       # other names start hidden and muted (test exes)
        env['SSBU_SHOW'], env['SSBU_VOLUME'] = '1', '100'
    for kv in a.env:
        k, _, v = kv.partition('=')
        env[k] = v
    os.makedirs(a.userdata, exist_ok=True)
    os.makedirs(os.path.join(a.build, 'cache'), exist_ok=True)
    if a.log and os.path.exists(a.log):                                  # keep play.log.1 .. .3
        for i in (2, 1):
            if os.path.exists('%s.%d' % (a.log, i)):
                os.replace('%s.%d' % (a.log, i), '%s.%d' % (a.log, i + 1))
        os.replace(a.log, a.log + '.1')
    cmd = [exe, os.path.join(a.build, 'binaries', '13.0.4', 'main.elf'), str(a.seconds)]
    t0 = time.time()
    out = open(a.log, 'wb') if a.log else None
    p = subprocess.run(cmd, cwd=work, env=env, stdout=out, stderr=subprocess.STDOUT if out else None)
    print('game exited with code %d after %.0f s' % (p.returncode, time.time() - t0))
    if out:
        out.close()
        with open(a.log, 'a') as f:
            f.write('\nplay.py: game exited with code %d (0x%08x) after %.0f s\n' % (p.returncode, p.returncode & 0xffffffff, time.time() - t0))
    return p.returncode


if __name__ == '__main__':
    sys.exit(main())
