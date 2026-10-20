#!/usr/bin/env python3
"""Smash Ultimate HDR setup: builds the native Windows port of Super Smash Bros. Ultimate 13.0.4 with HewDraw Remix
(HDR) v0.49.11-beta on YOUR PC, from YOUR game dump and YOUR HDR download, and ends with a game you can start.

    hdr_setup.exe                                    a window asks for the three folders
    hdr_setup.exe --no-gui --dump D --hdr H --install I [--jobs N] [--exe-name X.exe] [--no-shortcut | --shortcut-dir DIR]
                  [--keep-build-files] [--no-dpi-flag] [--downloads DIR] [--any-hdr] [--test-guards]

Nothing of the game, of Nintendo's software or of HDR is inside this program: it holds the port's own source code
(stephenjayakar/ssbu-recomp, branch hdr-release, GPL-3.0) and downloads open-source tools from their official places,
each checked against a SHA-256 written below. Everything derived from the game is made on this PC, inside the install
folder, and stays there.

Steps (each one is skipped when it finished before, so starting again after a stop resumes; state in
<install>\\hdr_setup_state.json, logs in <install>\\logs and <install>\\build\\logs):
  check      this PC: Windows x64, memory, disk, Vulkan, Microsoft C++ Build Tools (offered through winget when missing)
  dump       your dump: exefs\\main and sdk must be Smash Ultimate 13.0.4 (build ids checked), romfs\\data.arc complete;
             your HDR download: its 7 plugin files must be those of v0.49.11-beta (checked before any download)
  source     the port's source code into <install>\\src
  python     a private Python 3.13 (python.org embeddable package) + numpy lz4 zstandard Pillow unicorn, in <install>\\deps
  tools      LLVM/clang 23.1.2, SDL3, Vulkan-Headers, glslang, two files of suyu (self-test data), libopus
  setup     scripts/setup.py: convert, modules, tables, lift, sdk (the game code translated to C, on this PC)
  flags      tools/lifter/flags_local.py (the play build's form of the lifted code)
  compile    the game DLL: clang 23 -O2 for this PC's CPU (the longest step)
  nro        the game's script modules, the same way
  record_exe the host runtime linked against the plain game DLL (ssbu_record.exe), libopus beside it
  sd         your HDR download unpacked into <install>\\sd (the plugin files checked against v0.49.11-beta)
  plugins    the Skyline plugins (HDR, ARCropolis, smashline ...) lifted and compiled (tools/nro/build_plugins.py)
  record     one hidden, muted boot with the plugins: they write every code change they make to skyline_sites.log
  sites      that list + installer/hdr_sites_addr_v0.49.11.txt (addresses only) -> build\\work\\hdr_sites.txt
  relift     tools/lifter/relift_sites.py: the game DLL and the common fighter module re-lifted with those changes compiled in
  link       the exe of the play build, linked against ssbu_game_hdr.dll
  verify     one hidden, muted boot of that exe: the plugins load, the sites are compiled in
  finish     graphics.ini (borderless, 2.0x = 4K, 120 Hz), "Smash Ultimate HDR.bat", desktop shortcut, clean-up
"""
import argparse
import ctypes
import hashlib
import io
import json
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import traceback
import urllib.request
import zipfile

RELEASE = 'hdr-v1'
GAME_VERSION = '13.0.4'
HDR_VERSION = 'v0.49.11-beta'
HDR_URL = 'https://github.com/HDR-Development/HDR-Releases/releases/tag/v0.49.11'
TITLE = 'Smash Ultimate HDR setup (%s)' % RELEASE

# --- the game: build ids of the ExeFS files (NSO header +0x40, the first 20 bytes); same for every copy of 13.0.4 ---
NSO_BUILD_IDS = {'main': '3eae0063b12fd81e99fb5980ba56297049ea94f9', 'sdk': '5fd4f531c1195e9e5510c9e73f22cd39612839f8'}
# --- HDR v0.49.11-beta (switch-package.zip of HDR-Development/HDR-Releases): the plugin files the build was made for ---
HDR_PLUGINS = {
    'ultimate/mods/hdr/plugin.nro': 'c0652fb3fe09d0ea314b4b055a5e7801a6c23fc18e7ea3a0208ada0814fe6566',
    'atmosphere/contents/01006A800016E000/romfs/skyline/plugins/hdr-launcher.nro': '6b7cac9f9fa101f0be2f834875452a5d5305745ee317fe7adfca5f9f0e729144',
    'atmosphere/contents/01006A800016E000/romfs/skyline/plugins/libarcropolis.nro': '49b6f634039f0b2f9bd9e28cf0d04bfda16ad5fbef67b3994032cc43d80441a7',
    'atmosphere/contents/01006A800016E000/romfs/skyline/plugins/libnro_hook.nro': 'ca7aec03aee49fea539dcb92569fea5bc5389f77377ba2a30e4d0b7ebd0255e6',
    'atmosphere/contents/01006A800016E000/romfs/skyline/plugins/libsmashline_plugin.nro': 'd37b4724d0c74b2d63648a7373f3649c2098aba531b34576c2e13c197b14fcd9',
    'atmosphere/contents/01006A800016E000/romfs/skyline/plugins/libstage_alts.nro': '4953ed0785b6b72d5f6f109dd5ebcdf7c68773a76b9a7532f755db2a4c8ec75e',
    'atmosphere/contents/01006A800016E000/romfs/skyline/plugins/libstage_config.nro': 'a081a2bacf90306f9c8d5c9936d415d4900dbc94e831169fd61c736f25702658',
}
HDR_ZIP_SHA = 'b7ca9333b0309f0f0893051d68b456a1bb3a8792a0eb1a16df9856d010ac7232'     # switch-package.zip, for the log only

# --- downloads: (url, sha256) ---
PY_VERSION = '3.13.16'
DL = {
    'python': ('https://www.python.org/ftp/python/%s/python-%s-embed-amd64.zip' % (PY_VERSION, PY_VERSION),
               '97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297'),
    'pip': ('https://files.pythonhosted.org/packages/f3/6e/1736e5b4ae2b778ef2f81c47d797de9f891d4d8acb047a24ca37a60294dd/pip-26.2.1-py3-none-any.whl',
            '71138adf1f4ca900cdb7d289c21b7494329f2332b6d85f0e1c42108c0384ed3e'),
    'llvm': ('https://github.com/llvm/llvm-project/releases/download/llvmorg-23.1.2/clang+llvm-23.1.2-x86_64-pc-windows-msvc.tar.zst',
             'ceaee048142fece144752c6f6431cb0905a7a6160f78ab8cf5cf0b6216f99418'),
    'sdl3': ('https://github.com/libsdl-org/SDL/releases/download/release-3.4.18/SDL3-devel-3.4.18-VC.zip',
             '78d84602ae616cfe26b33a73b7d3b9b6b56e8a6650e53d92c9c6ca938f747acd'),
    'vulkan-headers': ('https://github.com/KhronosGroup/Vulkan-Headers/archive/refs/tags/v1.4.365.zip',
                       '767d80b0c6506f90b85e1515eb5998299aecd0d16e3c5a373246efede770ac6f'),
    'glslang': ('https://github.com/KhronosGroup/glslang/releases/download/16.6.0/glslang-16.6.0-windows-x86_64-release.zip',
                '82bf434e69b9bb4829de7e2b4bc2c5e7a7861e53d66cf75e5cc70f5f694a8d9b'),
    'opus': ('https://repo.msys2.org/mingw/ucrt64/mingw-w64-ucrt-x86_64-opus-1.6.1-2-any.pkg.tar.zst',
             'bb114bcf26225f50f874497ebfd6b8224fff2a20bda3f8ee45cd07ce5f02657b'),
}
OPUS_DLL_SHA = 'e747edad6bf02cf5cdcce417b95be3b7ba108f93df1b024778be5d73fa6dae39'
SUYU_BASE = 'https://raw.githubusercontent.com/suyu-emu/suyu-v0.0.4/c0dd2ff554327657f0c6318f29b740ac7e329ea0/'
SUYU_FILES = {'src/core/hle/service/mii/types/raw_data.cpp': '0f5b10dcde4038024fffd6079d75cd4bad8d72486efcf1ffa06e6c779803c486',
              'src/core/hle/service/mii/mii_types.h': 'cf2e0c990b881c238f5b7fd823f33fe2c0a8d0846cdbf012ec6f702b1647f3e1'}
PACKAGES = ['numpy==2.5.4', 'lz4==4.4.5', 'zstandard==0.25.0', 'Pillow==13.0.0', 'unicorn==2.1.4']
MSVC_WINGET = ('winget install --id Microsoft.VisualStudio.2022.BuildTools -e --accept-package-agreements --accept-source-agreements '
               '--override "--passive --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"')
HOST_WARN = '-Wno-error=incompatible-function-pointer-types -Wno-error=implicit-function-declaration -Wno-error=int-conversion'

STEPS = ['check', 'dump', 'source', 'python', 'tools', 'setup', 'flags', 'compile', 'nro', 'record_exe', 'sd', 'plugins',
         'record', 'sites', 'relift', 'link', 'verify', 'finish']
TITLES = {'check': 'Check this PC', 'source': 'Source code', 'python': 'Private Python', 'tools': 'Compiler and libraries',
          'dump': 'Check your game dump and HDR download', 'setup': 'Translate the game code', 'flags': 'Prepare the translated code',
          'compile': 'Compile the game (longest step)', 'nro': 'Compile the script modules', 'record_exe': 'Link a first exe',
          'sd': 'Unpack HDR', 'plugins': 'Compile the HDR plugins', 'record': 'Record HDR\'s code changes (hidden boot)',
          'sites': 'Site list', 'relift': 'Re-lift the game with HDR\'s changes', 'link': 'Link the HDR exe',
          'verify': 'Test boot (hidden)', 'finish': 'Settings, shortcut, clean-up'}
# rough minutes on an 8-core / 16-thread PC with 32 GB (measured on the release test), scaled by jobs at run time
EST_MIN = {'check': 0.2, 'source': 0.2, 'python': 1, 'tools': 1, 'dump': 0.2, 'setup': 2, 'flags': 7, 'compile': 120, 'nro': 45,
           'record_exe': 1, 'sd': 2, 'plugins': 4, 'record': 2, 'sites': 0.1, 'relift': 17, 'link': 1, 'verify': 3, 'finish': 1}
USER_GAME_EXES = ('ssbu_native.exe', 'ssbu_user.exe')
RECORD_EXE, CHECK_EXE = 'ssbu_record.exe', 'ssbu_check.exe'


class Stop(Exception):
    def __init__(self, msg, action=''):
        Exception.__init__(self, msg)
        self.msg, self.action = msg, action


# ------------------------------------------------------------------------------------------------ small helpers
class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong), ('ullTotalPhys', ctypes.c_ulonglong),
                ('ullAvailPhys', ctypes.c_ulonglong), ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong), ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]


def memory():
    """(total physical, available physical, available commit) in GB"""
    m = MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    g = 2.0 ** 30
    return m.ullTotalPhys / g, m.ullAvailPhys / g, m.ullAvailPageFile / g


def disk_free_gb(path):
    while path and not os.path.exists(path):
        path = os.path.dirname(path)
    return shutil.disk_usage(path).free / 2.0 ** 30


def folder_gb(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / 2.0 ** 30


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def fmt_secs(s):
    s = int(s)
    return '%dh%02dm' % (s // 3600, s % 3600 // 60) if s >= 3600 else '%dm%02ds' % (s // 60, s % 60)


def running_exes():
    try:
        out = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True, text=True, creationflags=0x08000000).stdout
    except OSError:
        return set()
    return set(l.split('","')[0].strip('"').lower() for l in out.splitlines() if l.startswith('"'))


def kill_tree(p):
    try:
        subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'], capture_output=True, creationflags=0x08000000)
    except OSError:
        pass


# ------------------------------------------------------------------------------------------------ the installer
class Installer:
    def __init__(self, a, out):
        self.a = a
        self.out = out                          # callable(kind, payload): 'log' text, 'step' (id, state, msg), 'prog' (done, total, text)
        self.inst = os.path.abspath(a.install)
        self.src = os.path.join(self.inst, 'src')
        self.deps = os.path.join(self.inst, 'deps')
        self.build = os.path.join(self.inst, 'build')
        self.work = os.path.join(self.build, 'work')
        self.sd = os.path.join(self.inst, 'sd')
        self.logs = os.path.join(self.inst, 'logs')
        self.tmp = os.path.join(self.inst, 'tmp')
        self.dl_dir = os.path.abspath(a.downloads) if a.downloads else os.path.join(self.inst, 'downloads')
        self.py = os.path.join(self.deps, 'python', 'python.exe')
        self.clang = os.path.join(self.deps, 'llvm', 'bin', 'clang.exe')
        self.state_file = os.path.join(self.inst, 'hdr_setup_state.json')
        self.state = {'done': {}, 'release': RELEASE}
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    self.state.update(json.load(f))
            except (OSError, ValueError):
                pass
        total, _, _ = memory()
        self.jobs = a.jobs or max(2, min(os.cpu_count() or 4, int(total / 4)))
        self.children = []
        self.cancelled = False
        self.logf = None
        self.exe_name = a.exe_name or 'ssbu_native.exe'
        self.cur = None

    # -------------------------------------------------------------------------------------------- output
    def log(self, text=''):
        for line in str(text).splitlines() or ['']:
            self.out('log', line)
            if self.logf:
                self.logf.write(line + '\n')
                self.logf.flush()

    def save(self):
        os.makedirs(self.inst, exist_ok=True)
        tmp = self.state_file + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, self.state_file)

    def env(self, **extra):
        e = {k: v for k, v in os.environ.items() if not k.startswith(('RECOMP_', 'SSBU_', 'PYTHON'))}
        e['PATH'] = os.pathsep.join(p for p in e.get('PATH', '').split(os.pathsep) if p and 'python' not in p.lower())
        e['TEMP'] = e['TMP'] = self.tmp          # big temporary files on the install drive, not on C:
        e['PYTHONIOENCODING'] = 'utf-8'
        e['PYTHONUNBUFFERED'] = '1'
        e['PIP_CACHE_DIR'] = os.path.join(self.dl_dir, 'pip')
        e['RECOMP_LOCK'] = ''                    # the development PC's shared build lock: not here
        e.update(extra)
        return e

    def build_env(self, **extra):
        """what the compile steps read (build.py / build_opt.py / build_nro.py / build_plugins.py / relift_sites.py)"""
        e = dict(RECOMP_CLANG=self.clang, RECOMP_OPT='-O2', RECOMP_CFLAGS='-march=' + self.march, RECOMP_MARCH=self.march,
                 RECOMP_GEN='gen_fl', RECOMP_OBJ='obj_fl23', RECOMP_DLL='ssbu_game_fl23.dll',
                 RECOMP_HOST_OPT='-O2 -march=%s %s' % (self.march, HOST_WARN),
                 SDL3_ROOT=os.path.join(self.deps, 'SDL3'), VULKAN_HEADERS=os.path.join(self.deps, 'Vulkan-Headers'),
                 GLSLANG_ROOT=os.path.join(self.deps, 'glslang'), SUYU_ROOT=os.path.join(self.deps, 'suyu'),
                 HDR_MIN_FREE_GB=str(self.a.min_free_gb))
        e.update(extra)
        return self.env(**e)

    @property
    def march(self):
        return self.a.march or 'native'

    def run(self, argv, cwd=None, env=None, logname=None, on_line=None, ok=(0,)):
        """Run a child; its output goes to the window / console and to logs\\<logname>.log. Raises Stop on a bad exit."""
        os.makedirs(self.logs, exist_ok=True)
        path = os.path.join(self.logs, (logname or self.cur or 'run') + '.log')
        with open(path, 'a', encoding='utf-8', errors='replace') as lf:
            lf.write('\n$ %s\n' % ' '.join(argv))
            p = subprocess.Popen(argv, cwd=cwd or self.work, env=env or self.env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding='utf-8', errors='replace', bufsize=1, creationflags=0x08000000)
            self.children.append(p)
            tail = []
            try:
                for line in p.stdout:
                    line = line.rstrip('\r\n')
                    lf.write(line + '\n')
                    self.out('log', '    ' + line)
                    tail.append(line)
                    del tail[:-30]
                    if on_line:
                        on_line(line)
                    if self.cancelled:
                        kill_tree(p)
                        break
                rc = p.wait()
            finally:
                self.children.remove(p)
        if self.cancelled:
            raise Stop('stopped')
        if rc not in ok:
            raise Stop('%s failed (exit code %s); the last lines:\n%s\n(whole log: %s)' % (os.path.basename(argv[1] if argv[0] == self.py and len(argv) > 1 else argv[0]),
                                                                                      rc, '\n'.join(tail[-12:]), path))
        return tail

    def pyrun(self, script_args, **kw):
        return self.run([self.py] + script_args, **kw)

    # -------------------------------------------------------------------------------------------- downloads
    def download(self, key, url=None, sha=None, name=None):
        url, sha = (url, sha) if url else DL[key]
        os.makedirs(self.dl_dir, exist_ok=True)
        dst = os.path.join(self.dl_dir, name or os.path.basename(url))
        if os.path.exists(dst) and sha256_file(dst) == sha:
            self.log('  %s: already downloaded' % os.path.basename(dst))
            return dst
        self.log('  downloading %s' % url)
        part = dst + '.part'
        req = urllib.request.Request(url, headers={'User-Agent': 'ssbu-hdr-setup'})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as r, open(part, 'wb') as f:
                    total = int(r.headers.get('Content-Length') or 0)
                    got, last = 0, 0
                    while True:
                        b = r.read(1 << 20)
                        if not b:
                            break
                        f.write(b)
                        got += len(b)
                        if time.time() - last > 0.5:
                            last = time.time()
                            self.out('prog', (got, total, '%s  %.0f / %.0f MB' % (os.path.basename(dst), got / 1048576.0, total / 1048576.0)))
                        if self.cancelled:
                            raise Stop('stopped')
                break
            except (OSError, urllib.error.URLError) as e:
                if attempt == 2:
                    raise Stop('could not download %s: %s\nCheck the internet connection (or a firewall blocking github.com / python.org) and start again.' % (url, e))
                self.log('  download error (%s), trying again' % e)
                time.sleep(3)
        got = sha256_file(part)
        if got != sha:
            os.remove(part)
            raise Stop('%s does not match its SHA-256 (got %s, expected %s): refused' % (os.path.basename(dst), got, sha))
        os.replace(part, dst)
        return dst

    def unzip_to(self, zpath, dst, strip_single=True):
        if os.path.isdir(dst):
            shutil.rmtree(dst, ignore_errors=True)
        tmp = dst + '.tmp'
        shutil.rmtree(tmp, ignore_errors=True)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(tmp)
        inner = os.listdir(tmp)
        if strip_single and len(inner) == 1 and os.path.isdir(os.path.join(tmp, inner[0])):
            shutil.move(os.path.join(tmp, inner[0]), dst)
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            os.replace(tmp, dst)

    # -------------------------------------------------------------------------------------------- steps
    def step_check(self):
        if struct_size() != 8 or platform.machine().upper() not in ('AMD64', 'X86_64'):
            raise Stop('this build needs 64-bit Windows on an x64 CPU')
        total, avail, commit = memory()
        self.log('  memory: %.1f GB (%.1f GB free now), %d CPU threads; compile jobs: %d' % (total, avail, os.cpu_count() or 0, self.jobs))
        if total < 11.5:
            raise Stop('this PC has %.1f GB of memory: the build needs at least 16 GB (one file of the game takes about 11 GB to compile)' % total)
        if total < 23:
            self.log('  note: with less than 32 GB the compile runs with fewer jobs and takes longer; close other programs')
        need = 0 if self.state['done'].get('compile') else 22
        free = disk_free_gb(self.inst)
        self.log('  disk: %.0f GB free at %s' % (free, self.inst))
        if free < need:
            raise Stop('%.0f GB free at %s, the build needs about %d GB while it runs (about 5 GB stay after the clean-up)' % (free, self.inst, need))
        root = os.environ.get('SystemRoot', r'C:\Windows')
        if not os.path.exists(os.path.join(root, 'System32', 'vulkan-1.dll')):
            self.log('  WARNING: Vulkan (vulkan-1.dll) was not found: install the current driver of your graphics card (NVIDIA / AMD / Intel)')
        vs = vswhere()
        if not vs:
            raise Stop('the Microsoft C++ Build Tools are missing (clang needs their linker and the Windows SDK).\n'
                       'Install them (free, about 3-4 GB, 10-20 minutes, asks for administrator rights) with:\n  %s\n'
                       'or from https://visualstudio.microsoft.com/downloads/ ("Build Tools for Visual Studio", workload '
                       '"Desktop development with C++"), then start this setup again.' % MSVC_WINGET, action='msvc')
        self.log('  Microsoft C++ Build Tools: %s' % vs)
        codecs = store_codecs()
        if codecs is not None and codecs:
            self.log('  WARNING: the opening movie needs these free Microsoft Store add-ons: %s' % ', '.join(codecs))
        for d in (self.inst, self.tmp, self.logs):
            os.makedirs(d, exist_ok=True)
        if ' ' in self.inst or not all(ord(ch) < 128 for ch in self.inst):
            self.log('  note: the install path has spaces or non-ASCII characters; if a step fails, try a plain path like D:\\SmashHDR')

    def step_source(self):
        z = source_zip()
        marker = os.path.join(self.src, '.release')
        if os.path.exists(marker) and open(marker).read().strip() == source_id(z):
            self.log('  source already there')
            return
        shutil.rmtree(self.src, ignore_errors=True)
        with zipfile.ZipFile(z) as zf:
            zf.extractall(self.src)
        with open(marker, 'w') as f:
            f.write(source_id(z))
        self.log('  source of %s unpacked into %s' % (source_id(z), self.src))

    def step_python(self):
        pydir = os.path.join(self.deps, 'python')
        if not os.path.exists(self.py):
            z = self.download('python')
            self.unzip_to(z, pydir, strip_single=False)
        pth = [n for n in os.listdir(pydir) if n.startswith('python') and n.endswith('._pth')]
        if not pth:
            raise Stop('python._pth not found in the embeddable package')
        p = os.path.join(pydir, pth[0])
        txt = open(p).read()
        txt = re.sub(r'(?m)^\s*#\s*import site', 'import site', txt)
        open(p, 'w').write(txt)
        site = os.path.join(pydir, 'Lib', 'site-packages')
        os.makedirs(site, exist_ok=True)
        if not os.path.isdir(os.path.join(site, 'pip')):
            whl = self.download('pip')
            with zipfile.ZipFile(whl) as zf:
                zf.extractall(site)
        # the embeddable Python leaves the script's folder off sys.path; the build scripts import their neighbours
        with open(os.path.join(site, 'ssbu_scriptdir.pth'), 'w') as f:
            f.write("import sys, os; a = sys.argv[0] if sys.argv else ''; (a and os.path.isfile(a) and sys.path.insert(0, os.path.dirname(os.path.abspath(a))))\n")
        probe = subprocess.run([self.py, '-c', 'import numpy, lz4, zstandard, PIL, unicorn'], capture_output=True, env=self.env(), creationflags=0x08000000)
        if probe.returncode:
            self.pyrun(['-m', 'pip', 'install', '--only-binary=:all:', '--no-warn-script-location', '--disable-pip-version-check'] + PACKAGES,
                       cwd=self.inst)
        self.log('  private Python %s with %s' % (PY_VERSION, ' '.join(PACKAGES)))

    def step_tools(self):
        import zstandard
        llvm = os.path.join(self.deps, 'llvm')
        if not all(os.path.exists(os.path.join(llvm, 'bin', b)) for b in ('clang.exe', 'lld-link.exe', 'llvm-objdump.exe')):
            arc = self.download('llvm')
            self.log('  unpacking clang (bin\\clang.exe, lld-link.exe, llvm-objdump.exe, lib\\clang)')
            shutil.rmtree(llvm, ignore_errors=True)
            want_bin = {'clang.exe', 'lld-link.exe', 'llvm-objdump.exe'}      # clang 23 links with lld-link; llvm-objdump: hook_inline_check.py
            n = 0
            with open(arc, 'rb') as f:
                tr = tarfile.open(fileobj=zstandard.ZstdDecompressor(max_window_size=2 ** 31).stream_reader(f), mode='r|')
                for m in tr:
                    parts = m.name.split('/')
                    rel = parts[1:]
                    if not rel or not (m.isfile() or m.issym() or m.islnk()):
                        continue
                    keep = (rel[0] == 'bin' and len(rel) == 2 and rel[1] in want_bin) or \
                           (rel[0] == 'lib' and len(rel) > 2 and rel[1] == 'clang') or (rel[0] == 'LICENSE.TXT')
                    if not keep or not m.isfile():
                        continue
                    out = os.path.join(llvm, *rel)
                    os.makedirs(os.path.dirname(out), exist_ok=True)
                    with tr.extractfile(m) as src, open(out, 'wb') as dst:
                        shutil.copyfileobj(src, dst, 1 << 20)
                    n += 1
            self.log('  clang: %d files' % n)
        ver = subprocess.run([self.clang, '--version'], capture_output=True, text=True, creationflags=0x08000000).stdout.splitlines()
        self.log('  %s' % (ver[0] if ver else 'clang --version printed nothing'))
        for key, folder, probe in (('sdl3', 'SDL3', os.path.join('lib', 'x64', 'SDL3.lib')),
                                   ('vulkan-headers', 'Vulkan-Headers', os.path.join('include', 'vulkan', 'vulkan.h')),
                                   ('glslang', 'glslang', os.path.join('lib', 'glslang.lib'))):
            dst = os.path.join(self.deps, folder)
            if os.path.exists(os.path.join(dst, probe)):
                continue
            self.unzip_to(self.download(key), dst)
            if not os.path.exists(os.path.join(dst, probe)):
                raise Stop('%s: %s is missing after unpacking' % (folder, probe))
        for rel, sha in SUYU_FILES.items():
            out = os.path.join(self.deps, 'suyu', *rel.split('/'))
            if os.path.exists(out) and sha256_file(out) == sha:
                continue
            got = self.download('suyu', SUYU_BASE + rel, sha, name='suyu_' + os.path.basename(rel))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.copyfile(got, out)
        opus = os.path.join(self.deps, 'opus', 'libopus-0.dll')
        if not (os.path.exists(opus) and sha256_file(opus) == OPUS_DLL_SHA):
            arc = self.download('opus')
            os.makedirs(os.path.dirname(opus), exist_ok=True)
            with open(arc, 'rb') as f:
                tr = tarfile.open(fileobj=zstandard.ZstdDecompressor(max_window_size=2 ** 31).stream_reader(f), mode='r|')
                for m in tr:
                    if m.name in ('ucrt64/bin/libopus-0.dll', 'ucrt64/share/licenses/opus/COPYING'):
                        with tr.extractfile(m) as src, open(os.path.join(os.path.dirname(opus), os.path.basename(m.name)), 'wb') as dst:
                            shutil.copyfileobj(src, dst)
            if sha256_file(opus) != OPUS_DLL_SHA:
                raise Stop('libopus-0.dll does not match its SHA-256: refused')
        self.log('  SDL3, Vulkan-Headers, glslang, suyu self-test files, libopus: ready')

    def step_dump(self):
        d = self.dump_paths()
        for name, want in NSO_BUILD_IDS.items():
            p = os.path.join(d['exefs'], name)
            if not os.path.isfile(p):
                raise Stop('%s is missing: the dump folder must hold exefs\\ (main, sdk, main.npdm ...) and romfs\\ (data.arc ...)' % p)
            with open(p, 'rb') as f:
                head = f.read(0x60)
            if head[:4] == b'\x7fELF':
                raise Stop('%s is an ELF, not the NSO file of the ExeFS: dump the ExeFS files as they are' % p)
            if head[:4] != b'NSO0':
                raise Stop('%s is not an NSO file (an ExeFS dump has main, sdk, rtld, subsdk0, main.npdm)' % p)
            got = head[0x40:0x54].hex()
            if got != want:
                raise Stop('%s is not Super Smash Bros. Ultimate %s (build id %s, expected %s).\n'
                           'This build needs exactly version %s: the base game with the 13.0.4 update applied, ExeFS and RomFS '
                           'taken with the update applied. 13.0.5 and older versions are not supported by this release.'
                           % (p, GAME_VERSION, got, want, GAME_VERSION))
        if not os.path.isfile(os.path.join(d['exefs'], 'main.npdm')):
            raise Stop('exefs\\main.npdm is missing: dump the whole ExeFS (main, sdk, rtld, subsdk0, main.npdm)')
        arc = os.path.join(d['romfs'], 'data.arc')
        if not arc_complete(arc):
            raise Stop('%s is missing or incomplete (a dump cut short?): dump the RomFS again (with the 13.0.4 update applied)' % arc)
        self.check_hdr_input()
        fonts = os.path.join(d['dump'], 'sysdata', 'fonts')
        self.log('  exefs: main and sdk are 13.0.4 (build ids match); romfs: data.arc %.1f GB, complete%s' % (
            os.path.getsize(arc) / 2.0 ** 30, '; system fonts: yes' if os.path.isdir(fonts) else
            '; no sysdata\\fonts (optional: without the console\'s shared fonts some text has no font)'))

    def check_hdr_input(self):
        """the 7 plugin files of the HDR download, read straight from the zip (or the folder): wrong versions stop here,
        before anything is downloaded or unpacked"""
        src = os.path.abspath(self.a.hdr)
        found = {}
        if os.path.isfile(src):
            if not zipfile.is_zipfile(src):
                raise Stop("%s is not a zip file: give HDR's switch-package.zip or an SD card folder" % src)
            with zipfile.ZipFile(src) as z:
                names = z.namelist()
                root = hdr_root_in_zip(names)
                if root is None:
                    raise Stop("%s does not look like HDR's switch-package.zip (no atmosphere and ultimate folders inside)" % src)
                for rel in HDR_PLUGINS:
                    if root + rel in names:
                        found[rel] = hashlib.sha256(z.read(root + rel)).hexdigest()
        elif os.path.isdir(src):
            root = src if os.path.isdir(os.path.join(src, 'atmosphere')) else os.path.join(src, 'sd')
            for rel in HDR_PLUGINS:
                p = os.path.join(root, *rel.split('/'))
                if os.path.isfile(p):
                    found[rel] = sha256_file(p)
        else:
            raise Stop('HDR download not found: %s' % src)
        bad = ['%s %s' % (rel, 'missing' if rel not in found else 'is another build') for rel, sha in HDR_PLUGINS.items() if found.get(rel) != sha]
        if bad and not self.a.any_hdr:
            raise Stop('this release was built and tested with HDR %s; your HDR download does not match it:\n  %s\nGet switch-package.zip of '
                       '%s from %s (or start with --any-hdr to try anyway: the plugin patch lists will most likely refuse another build).'
                       % (HDR_VERSION, '\n  '.join(bad), HDR_VERSION, HDR_URL))
        self.log('  HDR download: the 7 plugin files are those of %s%s' % (HDR_VERSION, '' if not bad else ' NOT (--any-hdr)'))

    def dump_paths(self):
        dump = os.path.abspath(self.a.dump)
        return {'dump': dump, 'exefs': os.path.join(dump, 'exefs'), 'romfs': os.path.join(dump, 'romfs')}

    def setup_cmd(self, step):
        a = [os.path.join(self.src, 'scripts', 'setup.py'), '--dump', os.path.abspath(self.a.dump), '--build', self.build,
             '--llvm', os.path.join(self.deps, 'llvm'), '--sdl3', os.path.join(self.deps, 'SDL3'),
             '--vulkan-headers', os.path.join(self.deps, 'Vulkan-Headers'), '--glslang', os.path.join(self.deps, 'glslang'),
             '--suyu', os.path.join(self.deps, 'suyu'), '--jobs', str(self.jobs), '--only', step]
        return a

    def step_setup(self):
        steps = ['stage', 'convert', 'modules', 'tables', 'lift', 'sdk']
        sub = self.state.setdefault('setup_done', [])
        for i, s in enumerate(steps):
            if s in sub and s != 'stage':
                continue
            self.out('prog', (i, len(steps), 'setup.py %s' % s))
            self.pyrun(self.setup_cmd(s), cwd=self.src, env=self.env(), logname='setup_' + s)
            if s not in sub:
                sub.append(s)
            self.save()

    def step_flags(self):
        self.pyrun(['flags_local.py', 'gen_eh_o2', 'gen_fl'], env=self.build_env())

    def counter(self, total_re, step_re):
        """progress from lines: total_re gives the total, step_re the count done"""
        st = {'total': 0}

        def on(line):
            m = re.search(total_re, line)
            if m:
                st['total'] = int(m.group(1))
                self.out('prog', (0, st['total'], line.strip()))
                return
            m = re.search(step_re, line)
            if m and st['total']:
                self.out('prog', (int(m.group(1)), st['total'], line.strip()[:100]))
        return on

    def step_compile(self):
        script = os.path.join(self.src, 'installer', 'build_steps.py')
        self.pyrun([script, 'main', str(self.jobs)], env=self.build_env(),
                   on_line=self.counter(r'to compile: (\d+)', r'^\s+(\d+)/\d+\s+\d+s elapsed'))
        if not os.path.exists(os.path.join(self.work, 'ssbu_game_fl23.dll')):
            raise Stop('ssbu_game_fl23.dll was not produced (see logs\\compile.log)')

    def step_nro(self):
        script = os.path.join(self.src, 'installer', 'build_steps.py')
        with open(os.path.join(self.work, 'ro_study', 'nro_map.json')) as f:
            total = len(json.load(f))
        sub = self.state.setdefault('nro_done', [])
        if 'lift' not in sub:                   # (both sub-steps are long on a hard disk: not repeated on a resume)
            self.out('prog', (0, total, 'lifting the script modules'))
            self.pyrun([script, 'nro-lift', str(self.jobs)], env=self.build_env(), on_line=self.counter(r'^$^', r'^\[(\d+)/\d+\]'))
            sub.append('lift')
            self.save()
        if 'flags' not in sub:
            self.pyrun(['flags_local.py', 'gen_nro_eh_o2', 'gen_nro_fl23'], env=self.build_env())
            sub.append('flags')
            self.save()
        done = {'n': 0}

        def on(line):
            if re.match(r'^\S+: \d+ C files', line):
                done['n'] += 1
                self.out('prog', (done['n'], total, line[:100]))
        self.pyrun([script, 'nro', str(self.jobs)], env=self.build_env(RECOMP_NRO_SUFFIX='_fl23'), on_line=on)
        n = len([f for f in os.listdir(os.path.join(self.work, 'nro_bin_fl23')) if f.endswith('.dll')])
        if n < total:
            raise Stop('only %d of %d script modules were built (see logs\\nro.log)' % (n, total))

    def step_record_exe(self):
        self.run([os.environ.get('COMSPEC', 'cmd.exe'), '/c', os.path.join(self.work, 'build_glslang.bat')], env=self.build_env())
        self.pyrun(['build.py', 'link'], env=self.build_env(RECOMP_EXE=RECORD_EXE))
        shutil.copyfile(os.path.join(self.deps, 'opus', 'libopus-0.dll'), os.path.join(self.work, 'libopus-0.dll'))
        for f in (RECORD_EXE, 'SDL3.dll', 'ssbu_glslang.dll', 'libopus-0.dll'):
            if not os.path.exists(os.path.join(self.work, f)):
                raise Stop('%s was not produced (see logs\\record_exe.log)' % f)

    def step_sd(self):
        src = os.path.abspath(self.a.hdr)
        marker = os.path.join(self.sd, '.from')
        if os.path.exists(marker) and open(marker).read() == src and self.check_hdr(quiet=True):
            self.log('  %s already unpacked' % src)
            return
        tmp = self.sd + '.tmp'
        shutil.rmtree(tmp, ignore_errors=True)
        if os.path.isfile(src):
            if not zipfile.is_zipfile(src):
                raise Stop('%s is not a zip file: give HDR\'s switch-package.zip or an SD card folder' % src)
            with zipfile.ZipFile(src) as z:
                names = z.infolist()
                root = hdr_root_in_zip([i.filename for i in names])
                if root is None:
                    raise Stop('%s does not look like HDR\'s switch-package.zip (no atmosphere\\ and ultimate\\ folders inside)' % src)
                for k, i in enumerate(names):
                    if not i.filename.startswith(root) or i.is_dir():
                        continue
                    rel = i.filename[len(root):]
                    out = os.path.join(tmp, *rel.split('/'))
                    os.makedirs(os.path.dirname(out), exist_ok=True)
                    with z.open(i) as s, open(out, 'wb') as d:
                        shutil.copyfileobj(s, d, 1 << 20)
                    if k % 200 == 0:
                        self.out('prog', (k, len(names), 'unpacking %s' % rel[:80]))
                    if self.cancelled:
                        raise Stop('stopped')
        elif os.path.isdir(src):
            root = src
            if not os.path.isdir(os.path.join(root, 'atmosphere')) and os.path.isdir(os.path.join(root, 'sd', 'atmosphere')):
                root = os.path.join(root, 'sd')
            if not (os.path.isdir(os.path.join(root, 'atmosphere')) and os.path.isdir(os.path.join(root, 'ultimate'))):
                raise Stop('%s is not an SD card folder with HDR (atmosphere\\ and ultimate\\ inside)' % src)
            self.log('  copying %s' % root)
            shutil.copytree(root, tmp)
        else:
            raise Stop('HDR download not found: %s' % src)
        shutil.rmtree(self.sd, ignore_errors=True)
        os.replace(tmp, self.sd)
        with open(marker, 'w') as f:
            f.write(src)
        self.check_hdr()

    def check_hdr(self, quiet=False):
        bad = []
        for rel, sha in HDR_PLUGINS.items():
            p = os.path.join(self.sd, *rel.split('/'))
            if not os.path.isfile(p):
                bad.append('%s missing' % rel)
            elif sha256_file(p) != sha:
                bad.append('%s is another build' % rel)
        ver = os.path.join(self.sd, 'ultimate', 'mods', 'hdr', 'ui', 'hdr_version.txt')
        version = open(ver).read().strip() if os.path.exists(ver) else '?'
        if quiet:
            return not bad
        if bad and not self.a.any_hdr:
            raise Stop('this release was built and tested with HDR %s; your download is %s:\n  %s\nGet switch-package.zip of %s from %s '
                       '(or start with --any-hdr to try anyway: the plugin patch lists will most likely refuse another build).'
                       % (HDR_VERSION, version, '\n  '.join(bad), HDR_VERSION, HDR_URL))
        self.log('  HDR %s: the 7 plugin files match%s' % (version, '' if not bad else ' NOT (--any-hdr): ' + '; '.join(bad)))
        return True

    def step_plugins(self):
        total = sum(1 for r in HDR_PLUGINS)
        done = {'n': 0}

        def on(line):
            if re.match(r'^\S+: \d+ C files', line):
                done['n'] += 1
                self.out('prog', (done['n'], total, line[:100]))
        self.pyrun(['build_plugins.py', '--sd', self.sd, '--jobs', str(max(1, min(self.jobs, 6)))], cwd=os.path.join(self.work, 'ro_study'),
                   env=self.build_env(), on_line=on)
        with open(os.path.join(self.work, 'plugin_bin', 'plugins.txt')) as f:
            n = len([l for l in f if l.strip() and not l.startswith('#')])
        if n < total:
            raise Stop('only %d of %d plugins were built (see logs\\plugins.log)' % (n, total))

    # ---------------------------------------------------------------------------- game runs (hidden, muted)
    def game_env(self, **extra):
        st = json.load(open(os.path.join(self.build, 'state.json')))
        e = self.env(SSBU_ROMFS=st['dump']['romfs'], SSBU_USERDATA=os.path.join(self.build, 'setup_userdata'), SSBU_SD=self.sd,
                     SSBU_AUDIO_MUTE='1', SSBU_SKIP_MOVIES='1', SSBU_VK_PREP='0')
        for sub, var in (('fonts', 'SSBU_FONTS'), ('zoneinfo', 'SSBU_TZDIR')):
            p = os.path.join(self.build, 'sysdata', sub)
            if os.path.isdir(p):
                e[var] = p
        e.update(extra)
        return e

    def wait_to_launch(self):
        """No game of the user's running (the test boots would compete with it), and enough memory."""
        min_avail, min_commit = (6.0, 12.0) if self.a.test_guards else (3.0, 4.0)
        said = set()
        while True:
            if self.cancelled:
                raise Stop('stopped')
            users = running_exes() & set(USER_GAME_EXES)
            _, avail, commit = memory()
            if users:
                if 'u' not in said:
                    self.log('  waiting: %s is running (close the game; the setup goes on by itself)' % ', '.join(sorted(users)))
                    said.add('u')
            elif avail < min_avail or commit < min_commit:
                if 'm' not in said:
                    self.log('  waiting for memory: %.1f GB free (%.0f wanted), commit %.1f GB free (%.0f wanted); close other programs'
                             % (avail, min_avail, commit, min_commit))
                    said.add('m')
            else:
                return
            time.sleep(5)

    def boot(self, exe, logname, done_when, max_secs, extra_env=None):
        """Start exe hidden and muted, stop it when done_when(log text) holds (plus a little time) or after max_secs.
        Returns the log text."""
        self.wait_to_launch()
        os.makedirs(os.path.join(self.build, 'setup_userdata'), exist_ok=True)
        os.makedirs(os.path.join(self.build, 'cache'), exist_ok=True)
        path = os.path.join(self.logs, logname + '.log')
        main_elf = os.path.join(self.build, 'binaries', GAME_VERSION, 'main.elf')
        env = self.game_env(**(extra_env or {}))
        min_avail = 3.0 if self.a.test_guards else 1.0
        with open(path, 'wb') as lf:
            p = subprocess.Popen([os.path.join(self.work, exe), main_elf, str(max_secs)], cwd=self.work, env=env, stdout=lf,
                                 stderr=subprocess.STDOUT, creationflags=0x08000000)
        self.children.append(p)
        t0, hit, why = time.time(), None, ''
        try:
            while p.poll() is None:
                time.sleep(1.0)
                el = time.time() - t0
                self.out('prog', (min(el, max_secs), max_secs, '%s running hidden: %.0f s' % (exe, el)))
                if self.cancelled:
                    why = 'stopped'
                    break
                if running_exes() & set(USER_GAME_EXES):
                    why = 'the game (ssbu_native.exe) was started: the hidden boot is stopped and tried again later'
                    break
                if memory()[1] < min_avail:
                    why = 'memory ran low (%.1f GB free): the hidden boot was stopped' % memory()[1]
                    break
                text = open(path, 'rb').read().decode('utf-8', 'replace')
                if hit is None and done_when(text):
                    hit = time.time()
                    self.log('  %s reached its goal after %.0f s; letting it run 15 s more' % (exe, el))
                if hit and time.time() - hit > 15:
                    break
                if el > max_secs + 30:
                    why = 'no result after %d s' % max_secs
                    break
        finally:
            if p.poll() is None:
                kill_tree(p)
                p.wait()
            self.children.remove(p)
        text = open(path, 'rb').read().decode('utf-8', 'replace')
        if why == 'stopped':
            raise Stop('stopped')
        if hit is None:
            raise Stop('the hidden boot of %s did not get far enough (%s; exit code %s). The game log is %s; its last lines:\n%s'
                       % (exe, why or 'it ended', p.returncode, path, '\n'.join(text.splitlines()[-15:])))
        return text

    def step_record(self):
        def done(t):
            return 'plugins loaded' in t and re.search(r'LoadModule: lua2cpp_common at', t) is not None
        text = self.boot(RECORD_EXE, 'record_boot', done, int(self.a.boot_secs))
        src = os.path.join(self.work, 'skyline_sites.log')
        if not os.path.exists(src):
            raise Stop('the hidden boot wrote no skyline_sites.log (did the plugins load? see logs\\record_boot.log)')
        shutil.copyfile(src, os.path.join(self.work, 'hdr_recorded_sites.log'))
        n = sum(1 for l in open(src) if l.strip() and not l.startswith('#'))
        self.log('  %d site lines recorded; %s' % (n, next((l.strip() for l in text.splitlines() if 'plugins loaded' in l), '')))

    def step_sites(self):
        rec = parse_sites(os.path.join(self.work, 'hdr_recorded_sites.log'))
        sup = parse_sites(os.path.join(self.src, 'installer', 'hdr_sites_addr_v0.49.11.txt'))
        lo, hi = 0x7100000000, 0x7100000000 + 0x39c8000            # main's .text (13.0.4)
        chains, sites = {}, {}
        for kind, a, args in rec:
            if not lo <= a < hi or kind == 'data':
                continue
            if kind == 'patch':
                if args[0] != args[1]:
                    chains.setdefault(a, []).append(args)
            else:
                sites[(('noinline' if kind == 'hook' else kind), a)] = args
        added = 0
        for kind, a, args in sup:
            if (kind, a) not in sites:
                sites[(kind, a)] = args
                added += 1
        out = os.path.join(self.work, 'hdr_sites.txt')
        with open(out, 'w', newline='\n') as f:
            f.write('# hdr_setup.py: sites recorded on this PC (hdr_recorded_sites.log) + addresses of installer/hdr_sites_addr_v0.49.11.txt not seen in that run\n')
            for a in sorted(chains):
                for o, n in chains[a]:
                    f.write('patch %x %s %s\n' % (a, o, n))
            for (kind, a), args in sorted(sites.items(), key=lambda kv: (kv[0][1], kv[0][0])):
                f.write('%s %x %s\n' % (kind, a, ' '.join(args)))
        self.log('  %d patch sites and %d other sites recorded in main, %d added from the address list -> %s'
                 % (sum(len(v) for v in chains.values()), len(sites) - added, added, out))

    def step_relift(self):
        common = [l for l in open(os.path.join(self.work, 'hdr_recorded_sites.log')) if '# lua2cpp_common+' in l]
        jobs = str(max(1, self.jobs))          # relift_sites.py starts a compile only with --min-free-gb free
        free = str(max(2.0, self.a.min_free_gb))
        self.pyrun(['relift_sites.py', 'build', 'hdr_sites.txt', '--jobs=' + jobs, '--min-free-gb=' + free], env=self.build_env(), logname='relift_main')
        if not os.path.exists(os.path.join(self.work, 'ssbu_game_hdr.dll')):
            raise Stop('ssbu_game_hdr.dll was not produced (logs\\relift_main.log)')
        self.log('  %d recorded lines are in lua2cpp_common' % len(common))
        self.pyrun(['relift_sites.py', 'build', 'hdr_recorded_sites.log', '--module=common', '--jobs=' + jobs, '--min-free-gb=' + free],
                   env=self.build_env(), logname='relift_common')
        if not os.path.exists(os.path.join(self.work, 'nro_bin_hdr', 'lua2cpp_common.dll')):
            raise Stop('nro_bin_hdr\\lua2cpp_common.dll was not produced (logs\\relift_common.log)')

    def step_link(self):
        exe = os.path.join(self.work, self.exe_name)
        self.pyrun(['build.py', 'link'], env=self.build_env(RECOMP_DLL='ssbu_game_hdr.dll', RECOMP_EXE=self.exe_name))
        if not os.path.exists(exe):
            raise Stop('%s was not produced (logs\\link.log)' % self.exe_name)

    def step_verify(self):
        shutil.copyfile(os.path.join(self.work, self.exe_name), os.path.join(self.work, CHECK_EXE))
        need = (r'ssbu_game_hdr\.dll has (\d+) plugin sites compiled in', r'lua2cpp_common has (\d+) plugin sites compiled in')

        def done(t):
            return 'plugins loaded' in t and all(re.search(rx, t) for rx in need)
        try:
            text = self.boot(CHECK_EXE, 'verify_boot', done, int(self.a.boot_secs))
        finally:
            try:
                os.remove(os.path.join(self.work, CHECK_EXE))
            except OSError:
                pass
        n_main, n_common = (int(re.search(rx, text).group(1)) for rx in need)
        inactive = [l for l in text.splitlines() if 'NOT ACTIVE' in l]
        self.log('  plugins load; %d sites compiled into the game DLL, %d into the common module; %d hooks NOT ACTIVE%s'
                 % (n_main, n_common, len(inactive), ' (expected: 2, smashline\'s hooks in the SDK)' if len(inactive) <= 2 else ''))
        for l in inactive[:10]:
            self.log('    ' + l.strip())

    def step_finish(self):
        cfg = os.path.join(self.build, 'config', 'graphics.ini')
        if os.path.exists(cfg):
            txt = open(cfg, encoding='utf-8', errors='replace').read()
            for key, val in (('window', 'borderless'), ('resolution_scale', self.a.scale), ('frame_rate', self.a.frame_rate)):
                txt, n = re.subn(r'(?m)^%s\s*=.*$' % key, '%s = %s' % (key, val), txt)
                if not n:
                    txt += '\n%s = %s\n' % (key, val)
            open(cfg, 'w', encoding='utf-8', newline='\r\n').write(txt.replace('\r\n', '\n'))
        else:
            os.makedirs(os.path.dirname(cfg), exist_ok=True)
            with open(cfg, 'w') as f:
                f.write('window = borderless\nresolution_scale = %s\nframe_rate = %s\n' % (self.a.scale, self.a.frame_rate))
        self.log('  graphics.ini: borderless, resolution_scale %s, frame_rate %s (%s)' % (self.a.scale, self.a.frame_rate, cfg))
        shutil.copyfile(os.path.join(self.src, 'installer', 'play.py'), os.path.join(self.inst, 'play.py'))
        os.makedirs(os.path.join(self.inst, 'userdata'), exist_ok=True)
        bat = os.path.join(self.inst, 'Smash Ultimate HDR.bat')
        with open(bat, 'w') as f:
            f.write('@echo off\r\nrem Super Smash Bros. Ultimate 13.0.4 + HewDraw Remix %s, native build (%s).\r\n'
                    'rem Saves: userdata\\  Settings: build\\config\\graphics.ini  Log: play.log\r\n'
                    'cd /d "%%~dp0"\r\n"%%~dp0deps\\python\\python.exe" "%%~dp0play.py" --exe %s\r\n'
                    'if errorlevel 1 pause\r\n' % (HDR_VERSION, RELEASE, self.exe_name))
        self.log('  start the game with: %s' % bat)
        if not self.a.no_dpi_flag:          # a high-DPI monitor otherwise gets a stretched, lower-resolution picture
            subprocess.run(['reg', 'add', r'HKCU\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers', '/v',
                            os.path.join(self.work, self.exe_name), '/t', 'REG_SZ', '/d', '~ HIGHDPIAWARE', '/f'], capture_output=True,
                           creationflags=0x08000000)
        if not self.a.no_shortcut:
            where = self.a.shortcut_dir or desktop_dir()
            lnk = os.path.join(where, 'Smash Ultimate HDR.lnk')
            ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}'); $s.TargetPath = '{bat}'; "
                  "$s.WorkingDirectory = '{inst}'; $s.IconLocation = '{exe},0'; $s.Description = 'Super Smash Bros. Ultimate + HDR (native build)'; "
                  "$s.WindowStyle = 7; $s.Save()").format(lnk=lnk.replace("'", "''"), bat=bat.replace("'", "''"), inst=self.inst.replace("'", "''"),
                                                         exe=os.path.join(self.work, self.exe_name).replace("'", "''"))
            r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps], capture_output=True, text=True, creationflags=0x08000000)
            self.log('  shortcut: %s%s' % (lnk, '' if r.returncode == 0 else ' (FAILED: %s)' % r.stderr.strip()[:200]))
        if not self.a.keep_build_files:
            self.clean_up()
        self.log('  install folder: %.1f GB' % folder_gb(self.inst))

    def clean_up(self):
        before = folder_gb(self.inst)
        w = self.work
        names = ['gen_eh_o2', 'gen_fl', 'gen_hdr', 'gen_nro_eh_o2', 'gen_nro_fl23', 'gen_nro_hdr', 'gen_plugin', 'gen_plugin_fl23',
                 'obj_eh_o1', 'obj_fl23', 'obj_hdr', 'obj_nro_fl23', 'obj_nro_hdr', 'obj_plugin_fl23', 'obj_host',
                 os.path.join('ro_study', 'nro'), 'tests', 'fighter_sdk']
        for n in names:
            shutil.rmtree(os.path.join(w, n), ignore_errors=True)
        for n in os.listdir(w):
            low = n.lower()
            if low.startswith('ssbu_game_fl23.') or low.startswith('ssbu_record.') or low.endswith(('.rsp', '.exp', '.pdb')) \
                    or (low.startswith('ssbu_game_hdr.') and not low.endswith('.dll')):
                try:
                    os.remove(os.path.join(w, n))
                except OSError:
                    pass
        for d in (self.tmp, os.path.join(self.deps, 'llvm'), os.path.join(self.build, 'setup_userdata')):
            shutil.rmtree(d, ignore_errors=True)
        if not self.a.downloads:
            shutil.rmtree(self.dl_dir, ignore_errors=True)
        self.log('  build files removed: %.1f GB freed (keep them next time with --keep-build-files)' % (before - folder_gb(self.inst)))

    # -------------------------------------------------------------------------------------------- driver
    def run_all(self):
        os.makedirs(self.logs, exist_ok=True)
        self.logf = open(os.path.join(self.logs, 'hdr_setup.log'), 'a', encoding='utf-8')
        self.log('=== %s, %s' % (TITLE, time.strftime('%Y-%m-%d %H:%M:%S')))
        self.log('dump: %s\nHDR: %s\ninstall: %s\njobs: %d, CPU target: %s' % (self.a.dump, self.a.hdr, self.inst, self.jobs, self.march))
        self.state['inputs'] = {'dump': os.path.abspath(self.a.dump), 'hdr': os.path.abspath(self.a.hdr)}
        self.save()
        t_all = time.time()
        if self.state['done'].get('finish'):
            self.log('This install is complete (%s). Start the game with %s. To build it again, delete %s and start the setup again.'
                     % (self.state['done']['finish'].get('at', ''), os.path.join(self.inst, 'Smash Ultimate HDR.bat'), self.state_file))
            for sid in STEPS:
                self.out('step', (sid, 'skip', 'done'))
            return None
        for sid in STEPS:
            done = self.state['done'].get(sid)
            if done and sid not in ('check', 'source', 'tools'):          # cheap; they check what later steps rely on
                self.out('step', (sid, 'skip', 'done earlier (%s)' % fmt_secs(done['secs'])))
                continue
            self.cur = sid
            self.out('step', (sid, 'run', ''))
            self.log('[%d/%d] %s ...' % (STEPS.index(sid) + 1, len(STEPS), TITLES[sid]))
            t0 = time.time()
            try:
                getattr(self, 'step_' + sid)()
            except Stop as e:
                self.spent(sid, time.time() - t0)
                self.out('step', (sid, 'fail', e.msg))
                self.log('FAILED at "%s": %s' % (TITLES[sid], e.msg))
                self.log('Fix the problem and start the setup again with the same install folder: finished steps are skipped.')
                return e
            except Exception as e:      # an unexpected error: the traceback goes to the log
                self.spent(sid, time.time() - t0)
                self.out('step', (sid, 'fail', str(e)))
                self.log(traceback.format_exc())
                self.log('FAILED at "%s" (unexpected error above). Start the setup again to resume.' % TITLES[sid])
                return Stop(str(e))
            secs = time.time() - t0 + self.state.setdefault('spent', {}).pop(sid, 0)       # earlier, stopped tries of this step count too
            self.state['done'][sid] = {'secs': round(secs, 1), 'at': time.strftime('%Y-%m-%d %H:%M')}
            self.save()
            self.out('step', (sid, 'done', fmt_secs(secs)))
            self.log('[%d/%d] %s: done in %s' % (STEPS.index(sid) + 1, len(STEPS), TITLES[sid], fmt_secs(secs)))
        self.log('')
        self.log('All done in %s. Start the game with "Smash Ultimate HDR" (desktop) or %s' % (
            fmt_secs(time.time() - t_all), os.path.join(self.inst, 'Smash Ultimate HDR.bat')))
        self.log('The first matches stutter while the shaders of this PC are made (once; they are kept in build\\cache).')
        return None

    def spent(self, sid, secs):
        sp = self.state.setdefault('spent', {})
        sp[sid] = round(sp.get(sid, 0) + secs, 1)
        self.save()

    def cancel(self):
        self.cancelled = True
        for p in list(self.children):
            kill_tree(p)


# ------------------------------------------------------------------------------------------------ module helpers
def struct_size():
    return ctypes.sizeof(ctypes.c_void_p)


def vswhere():
    vsw = os.path.join(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'), 'Microsoft Visual Studio', 'Installer', 'vswhere.exe')
    if not os.path.exists(vsw):
        return ''
    r = subprocess.run([vsw, '-latest', '-products', '*', '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64',
                        '-property', 'installationPath'], capture_output=True, text=True, creationflags=0x08000000)
    path = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ''
    if not path:
        return ''
    kits = os.path.join(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'), 'Windows Kits', '10', 'Lib')
    if not os.path.isdir(kits) or not any(os.path.exists(os.path.join(kits, v, 'um', 'x64', 'kernel32.lib')) for v in os.listdir(kits)):
        return ''
    return path


def store_codecs():
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', 'Get-AppxPackage | ForEach-Object { $_.Name }'],
                           capture_output=True, text=True, timeout=40, creationflags=0x08000000)
    except (OSError, subprocess.TimeoutExpired):
        return None
    names = (r.stdout or '').lower()
    if not names.strip():
        return None
    miss = []
    if 'vp9videoextensions' not in names:
        miss.append('VP9 Video Extensions (https://apps.microsoft.com/detail/9n4d0msmp0pt)')
    if 'webmediaextensions' not in names:
        miss.append('Web Media Extensions (https://apps.microsoft.com/detail/9n5tdp8vcmhs)')
    return miss


def desktop_dir():
    buf = ctypes.create_unicode_buffer(1024)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0:      # CSIDL_DESKTOPDIRECTORY
        return buf.value
    return os.path.join(os.path.expanduser('~'), 'Desktop')


def arc_complete(path):
    import struct
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            magic, stream, files, shared, fs, search, _ = struct.unpack('<7Q', f.read(0x38))
            if magic != 0xABCDEF9876543210 or max(stream, files, shared, fs, search) + 16 > size:
                return False
            f.seek(fs)
            m, _, comp, _ = struct.unpack('<4I', f.read(16))
            return m == 0x10 and fs + 16 + comp <= size
    except (OSError, struct.error):
        return False


def hdr_root_in_zip(names):
    for n in names:
        parts = n.split('/')
        for i, p in enumerate(parts[:-1]):
            if p == 'atmosphere':
                root = '/'.join(parts[:i])
                root = root + '/' if root else ''
                if any(x.startswith(root + 'ultimate/') for x in names):
                    return root
    return None


def parse_sites(path):
    out = []
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            t = line.split('#', 1)[0].split()
            if not t:
                continue
            try:
                out.append((t[0], int(t[1], 16), tuple(t[2:4])))
            except (IndexError, ValueError):
                continue
    return out


def resource(name):
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def source_zip():
    z = resource('source.zip')
    if os.path.exists(z):
        return z
    raise Stop('source.zip is not inside this setup program (run installer/build_exe.py to make the exe)')


def source_id(z):
    with zipfile.ZipFile(z) as zf:
        c = zf.comment.decode('ascii', 'replace').strip()
    return '%s (source.zip %s)' % (c or '?', sha256_file(z)[:12])


# ------------------------------------------------------------------------------------------------ window
def gui(a):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    root = tk.Tk()
    root.title(TITLE)
    root.geometry('980x720')
    q = queue.Queue()
    last = {}
    try:
        with open(os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ssbu-hdr-setup.json')) as f:
            last = json.load(f)
    except (OSError, ValueError):
        pass
    v_dump = tk.StringVar(value=a.dump or last.get('dump', ''))
    v_hdr = tk.StringVar(value=a.hdr or last.get('hdr', ''))
    v_inst = tk.StringVar(value=a.install or last.get('install', r'C:\Games\SmashHDR'))
    v_short = tk.BooleanVar(value=not a.no_shortcut)
    v_clean = tk.BooleanVar(value=not a.keep_build_files)
    total, _, _ = memory()
    v_jobs = tk.IntVar(value=a.jobs or max(2, min(os.cpu_count() or 4, int(total / 4))))

    intro = ('Builds Super Smash Bros. Ultimate %s with HewDraw Remix %s as a native Windows game, on this PC, from your own '
             'game dump and your own HDR download. Nothing of the game or of HDR is in this program.\n'
             'Needs: the Microsoft C++ Build Tools, 16 GB of memory (32 GB recommended), about 22 GB of free disk while it '
             'builds (about 5 GB stay), an internet connection for the open-source tools, and 3 to 4 hours (it can be '
             'stopped and resumed).' % (GAME_VERSION, HDR_VERSION))
    tk.Label(root, text=intro, wraplength=950, justify='left').pack(fill='x', padx=10, pady=(8, 4))
    form = tk.Frame(root)
    form.pack(fill='x', padx=10)

    def row(r, label, var, buttons):
        tk.Label(form, text=label, anchor='w').grid(row=r, column=0, sticky='w')
        tk.Entry(form, textvariable=var, width=80).grid(row=r, column=1, sticky='we', padx=4)
        for i, (txt, cmd) in enumerate(buttons):
            tk.Button(form, text=txt, command=cmd).grid(row=r, column=2 + i, padx=2)
    row(0, 'Game dump folder (exefs + romfs, %s):' % GAME_VERSION, v_dump,
        [('Folder...', lambda: v_dump.set(filedialog.askdirectory(title='Your dump: the folder with exefs\\ and romfs\\ inside') or v_dump.get()))])
    row(1, 'HDR %s (switch-package.zip or SD folder):' % HDR_VERSION, v_hdr,
        [('Zip...', lambda: v_hdr.set(filedialog.askopenfilename(title='HDR switch-package.zip', filetypes=[('zip', '*.zip')]) or v_hdr.get())),
         ('Folder...', lambda: v_hdr.set(filedialog.askdirectory(title='SD card folder with atmosphere\\ and ultimate\\') or v_hdr.get()))])
    row(2, 'Install folder:', v_inst, [('Folder...', lambda: v_inst.set(filedialog.askdirectory(title='Install folder') or v_inst.get()))])
    form.columnconfigure(1, weight=1)
    opts = tk.Frame(root)
    opts.pack(fill='x', padx=10, pady=4)
    tk.Checkbutton(opts, text='Desktop shortcut "Smash Ultimate HDR"', variable=v_short).pack(side='left')
    tk.Checkbutton(opts, text='Delete the build files when done (frees about 12 GB)', variable=v_clean).pack(side='left', padx=12)
    tk.Label(opts, text='Compile jobs:').pack(side='left')
    tk.Spinbox(opts, from_=1, to=64, textvariable=v_jobs, width=4).pack(side='left')
    tk.Label(root, text='HDR download: %s (asset switch-package.zip)' % HDR_URL, fg='#555').pack(anchor='w', padx=10)

    mid = tk.Frame(root)
    mid.pack(fill='both', expand=True, padx=10, pady=4)
    steps = tk.Listbox(mid, width=46, font=('Consolas', 9))
    steps.pack(side='left', fill='y')
    status = {s: '' for s in STEPS}

    def draw_steps():
        steps.delete(0, 'end')
        marks = {'': '   ', 'run': '>> ', 'done': 'ok ', 'skip': 'ok ', 'fail': '!! '}
        for s in STEPS:
            steps.insert('end', '%s%s' % (marks.get(status[s], '   '), TITLES[s]))
    draw_steps()
    logbox = tk.Text(mid, font=('Consolas', 8), wrap='none')
    sb = tk.Scrollbar(mid, command=logbox.yview)
    logbox.configure(yscrollcommand=sb.set)
    sb.pack(side='right', fill='y')
    logbox.pack(side='left', fill='both', expand=True)
    bar = ttk.Progressbar(root, mode='determinate', maximum=1000)
    bar.pack(fill='x', padx=10)
    v_now = tk.StringVar(value='')
    tk.Label(root, textvariable=v_now, anchor='w').pack(fill='x', padx=10)
    btns = tk.Frame(root)
    btns.pack(fill='x', padx=10, pady=6)
    worker = {'inst': None, 'thread': None, 't0': 0}

    def out(kind, payload):
        q.put((kind, payload))

    def start():
        if worker['thread'] and worker['thread'].is_alive():
            return
        for v, what in ((v_dump, 'game dump folder'), (v_hdr, 'HDR download'), (v_inst, 'install folder')):
            if not v.get().strip():
                messagebox.showerror(TITLE, 'Choose the %s first.' % what)
                return
        a.dump, a.hdr, a.install = v_dump.get().strip(), v_hdr.get().strip(), v_inst.get().strip()
        a.no_shortcut, a.keep_build_files, a.jobs = not v_short.get(), not v_clean.get(), int(v_jobs.get())
        try:
            with open(os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ssbu-hdr-setup.json'), 'w') as f:
                json.dump({'dump': a.dump, 'hdr': a.hdr, 'install': a.install}, f)
        except OSError:
            pass
        inst = Installer(a, out)
        worker['inst'], worker['t0'] = inst, time.time()

        def body():
            err = inst.run_all()
            out('end', err)
        worker['thread'] = threading.Thread(target=body, daemon=True)
        worker['thread'].start()
        b_start.configure(state='disabled')

    def open_folder():
        p = v_inst.get().strip()
        if p and os.path.isdir(p):
            os.startfile(p)

    def quit_():
        if worker['thread'] and worker['thread'].is_alive():
            if not messagebox.askyesno(TITLE, 'The build is running. Stop it? (Starting the setup again later resumes it.)'):
                return
            worker['inst'].cancel()
        root.destroy()
    b_start = tk.Button(btns, text='Start / Resume', width=16, command=start)
    b_start.pack(side='left')
    tk.Button(btns, text='Open install folder', command=open_folder).pack(side='left', padx=6)
    tk.Button(btns, text='Quit', width=10, command=quit_).pack(side='right')
    root.protocol('WM_DELETE_WINDOW', quit_)

    def poll():
        n = 0
        while n < 500:
            try:
                kind, p = q.get_nowait()
            except queue.Empty:
                break
            n += 1
            if kind == 'log':
                print(p, flush=True)
                logbox.insert('end', p + '\n')
                if int(logbox.index('end-1c').split('.')[0]) > 6000:
                    logbox.delete('1.0', '2000.0')
                logbox.see('end')
            elif kind == 'step':
                sid, st, msg = p
                status[sid] = st
                draw_steps()
                if st == 'run':
                    bar['value'] = 0
                    v_now.set('%s ... (about %d min on a fast PC)' % (TITLES[sid], max(1, round(EST_MIN.get(sid, 1)))))
            elif kind == 'prog':
                done, total_, text = p
                bar['value'] = int(1000 * done / total_) if total_ else 0
                el = time.time() - worker['t0']
                v_now.set('%s   (total time so far %s)' % (text, fmt_secs(el)))
            elif kind == 'end':
                b_start.configure(state='normal')
                if p is None:
                    messagebox.showinfo(TITLE, 'Done. Start the game with "Smash Ultimate HDR" on the desktop or "Smash Ultimate HDR.bat" in %s.\n'
                                        'The first matches stutter while this PC makes its shaders (once).' % a.install)
                elif p.msg != 'stopped':
                    if p.action == 'msvc' and messagebox.askyesno(TITLE, p.msg + '\n\nInstall the C++ Build Tools now with winget? '
                                                                  '(A console window opens; Windows asks for administrator rights. '
                                                                  'Press "Start / Resume" here when it has finished.)'):
                        subprocess.Popen(['cmd', '/c', 'start', 'C++ Build Tools', 'cmd', '/k', MSVC_WINGET])
                    else:
                        messagebox.showerror(TITLE, p.msg[:3000])
        root.after(150, poll)
    root.after(150, poll)
    root.mainloop()


def main():
    ap = argparse.ArgumentParser(description=TITLE)
    ap.add_argument('--dump', help='your dump folder: exefs\\ and romfs\\ (13.0.4) inside')
    ap.add_argument('--hdr', help="HDR's switch-package.zip (v0.49.11-beta) or an SD card folder with HDR")
    ap.add_argument('--install', help='install folder')
    ap.add_argument('--no-gui', action='store_true', help='no window: everything from the options, output on the console')
    ap.add_argument('--jobs', type=int, help='parallel compiler jobs (default: CPU threads, at most one per 4 GB of memory)')
    ap.add_argument('--march', help='CPU target of the compiled code (default native = this PC\'s CPU)')
    ap.add_argument('--exe-name', help='name of the game exe (default ssbu_native.exe)')
    ap.add_argument('--no-shortcut', action='store_true')
    ap.add_argument('--shortcut-dir', help='make the shortcut there instead of on the desktop')
    ap.add_argument('--keep-build-files', action='store_true', help='keep the intermediate build files (about 12 GB)')
    ap.add_argument('--no-dpi-flag', action='store_true', help='do not mark the exe as DPI-aware in the registry (HKCU)')
    ap.add_argument('--downloads', help='folder for the downloaded archives (default <install>\\downloads, removed at the end)')
    ap.add_argument('--any-hdr', action='store_true', help='go on with another HDR version (untested)')
    ap.add_argument('--scale', default='2.0', help='resolution_scale written to graphics.ini (2.0 = 3840x2160)')
    ap.add_argument('--frame-rate', default='120', help='frame_rate written to graphics.ini (60 or 120)')
    ap.add_argument('--min-free-gb', type=float, default=3.5, help='start a compile only with this much free memory')
    ap.add_argument('--boot-secs', type=int, default=900, help='time limit of each hidden boot')
    ap.add_argument('--test-guards', action='store_true', help='stricter memory limits for the hidden boots (test machines)')
    a = ap.parse_args()
    if not a.no_gui:
        try:
            return gui(a)
        except ImportError:
            pass
    for k in ('dump', 'hdr', 'install'):
        if not getattr(a, k):
            ap.error('--%s is required with --no-gui' % k)

    def out(kind, payload):
        if kind == 'log':
            print(payload, flush=True)
        elif kind == 'step':
            sid, st, msg = payload
            if st in ('skip',):
                print('[%s] %s: %s' % (sid, TITLES[sid], msg), flush=True)
        elif kind == 'prog':
            done, total, text = payload
            now = time.time()
            if now - out.last > 30:
                out.last = now
                print('    .. %s%s' % (text, ' (%d/%d)' % (done, total) if total and not text.endswith('MB') else ''), flush=True)
    out.last = 0
    inst = Installer(a, out)
    err = inst.run_all()
    return 0 if err is None else 1


if __name__ == '__main__':
    sys.exit(main())
