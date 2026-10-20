"""Build steps of the HDR setup (installer/hdr_setup.py) that run inside build\\work with the private Python.

    python build_steps.py main <jobs>        gen_fl -> obj_fl23 -> ssbu_game_fl23.dll    (clang 23 -O2, build_opt.py's ladder)
    python build_steps.py nro-lift <jobs>    every script module of ro_study\\nro_map.json lifted into gen_nro_eh_o2\\<module>
    python build_steps.py nro <jobs>         gen_nro_fl23 -> obj_nro_fl23 -> nro_bin_fl23\\<module>.dll

The compiler, -O level and target come from the environment exactly as build_opt.py reads them (RECOMP_CLANG,
RECOMP_OPT, RECOMP_CFLAGS, RECOMP_GEN / RECOMP_OBJ / RECOMP_DLL, RECOMP_NRO_SUFFIX). What this file adds is memory care
for a PC that also runs other programs: a compile starts only while enough physical memory is free
(HDR_MIN_FREE_GB, default 3.5), and when free memory falls under 1 GB the largest running compile is stopped; every
file that failed that way is compiled again afterwards with one job (the build tools only redo what is missing). A file
that fails even alone (clang running out of memory: the largest lifted functions, compiled `optnone` inside an -O2 file,
can take more than 30 GB in clang 23's code generator) is compiled as a whole at -O0, which takes about 1.5 GB; the
report line says so ("WHOLE FILE at -O0"), and relift_sites.py compiles that file the same way.
"""
import ctypes
import os
import subprocess
import sys
import threading
import time

HERE = os.getcwd()
sys.path.insert(0, HERE)

MIN_FREE = float(os.environ.get('HDR_MIN_FREE_GB', '3.5'))


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong), ('ullTotalPhys', ctypes.c_ulonglong),
                ('ullAvailPhys', ctypes.c_ulonglong), ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong), ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]


def free_gb():
    m = MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullAvailPhys / 2.0 ** 30


class PMC(ctypes.Structure):
    _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong), ('PeakWorkingSetSize', ctypes.c_size_t),
                ('WorkingSetSize', ctypes.c_size_t), ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]


def private_bytes(p):
    c = PMC()
    c.cb = ctypes.sizeof(c)
    if not ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(int(p._handle)), ctypes.byref(c), c.cb):
        return 0
    return c.PagefileUsage


_lock = threading.Lock()
_running = {}          # Popen -> start time
_last_start = [0.0]
_stopped = [0]
_solo = [False]          # the retry with one job: a compile that fails now gets the -O0 fallback
_capped = set()          # pids stopped for going over the per-compile cap: their file goes straight to -O0


def gate():
    """Wait until a compile may start: enough free memory, and 3 s after the previous start (its memory shows by then)."""
    waited = False
    while True:
        with _lock:
            ok = time.time() - _last_start[0] >= 3.0 and (free_gb() >= MIN_FREE or not _running)
            if ok:
                _last_start[0] = time.time()
                return
        if not waited:
            waited = True
        time.sleep(1.0)


def cap_gb():
    """one compile may not use more than this (default 60% of the physical memory): above it, the file is hopeless at -O2 here"""
    if os.environ.get('HDR_COMPILE_CAP_GB'):
        return float(os.environ['HDR_COMPILE_CAP_GB'])
    m = MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return 0.6 * m.ullTotalPhys / 2.0 ** 30


def watchdog():
    cap = cap_gb()
    while True:
        time.sleep(1.0)
        with _lock:
            procs = list(_running)
        for p in procs:
            if private_bytes(p) / 2.0 ** 30 > cap:
                print('  a compile went over %.0f GB: stopped, its file is compiled at -O0' % cap, flush=True)
                _capped.add(p.pid)
                subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'], capture_output=True)
        if free_gb() >= 1.0:
            continue
        with _lock:
            procs = list(_running)
        if not procs or (len(procs) < 2 and not _solo[0]):
            continue
        victim = max(procs, key=private_bytes)
        print('  memory under 1 GB: stopping the largest compile (%s)' % ('it gets the -O0 fallback' if _solo[0] else 'it is compiled again alone later'), flush=True)
        _stopped[0] += 1
        subprocess.run(['taskkill', '/PID', str(victim.pid), '/T', '/F'], capture_output=True)


def run_limited(cmd, limit):
    """build_opt.run_limited with the memory gate and the watchdog's registry."""
    gate()
    t = time.time()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    with _lock:
        _running[p] = t
    try:
        out, _ = p.communicate(timeout=limit)
        out = out.decode('latin-1')[-2000:]
        if p.pid in _capped:
            out += ' HDR_SETUP_CAPPED'
        return p.returncode, out, time.time() - t
    except subprocess.TimeoutExpired:
        subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'], capture_output=True)
        p.communicate()
        return None, '', time.time() - t
    finally:
        with _lock:
            _running.pop(p, None)


def patched_build_opt():
    import build_opt
    build_opt.run_limited = run_limited
    orig = build_opt.compile_file

    def compile_file(src, obj, base_flags, time_limit=None):
        code, out, secs, note = orig(src, obj, base_flags, time_limit)
        capped = 'HDR_SETUP_CAPPED' in out
        if code and (_solo[0] or capped):
            cmd = [build_opt.CLANG, '-c', '-O0'] + build_opt.EXTRA + base_flags + [src, '-o', obj]
            print('  %s %s: compiled as a whole at -O0' % (os.path.basename(src), 'needs more memory than the cap at -O2' if capped else
                                                           'failed alone (%s)' % ('out of memory' if 'out of memory' in out else 'exit %s' % code)), flush=True)
            gate()
            r = subprocess.run(cmd, capture_output=True, text=True)
            return r.returncode, (r.stdout + r.stderr)[-2000:], secs, 'WHOLE FILE at -O0 (memory: %s)' % ('over the cap at -O2' if capped else 'failed at -O2 alone')
        return code, out, secs, note
    build_opt.compile_file = compile_file
    threading.Thread(target=watchdog, daemon=True).start()
    return build_opt


def cmd_main(jobs):
    build_opt = patched_build_opt()
    rc = build_opt.cmd_main(jobs)
    if rc:
        print('some files failed (memory or time): compiling what is missing with one job', flush=True)
        _solo[0] = True
        rc = build_opt.cmd_main(1)
    return rc


def cmd_nro(jobs):
    build_opt = patched_build_opt()
    rc = build_opt.cmd_nro(jobs, [])
    if rc:
        print('some modules failed (memory or time): building what is missing with one job', flush=True)
        _solo[0] = True
        rc = build_opt.cmd_nro(1, [])
    return rc


def cmd_nro_lift(jobs):
    """build_nro.build_module's lift step only, for every module, into gen_nro_eh_o2 (flags_local.py makes gen_nro_fl23 of it)."""
    import build_nro
    entries = build_nro.load_map()
    build_nro.write_registry(entries)
    out_root = os.path.join(HERE, 'gen_nro_eh_o2')
    failed = []
    t0 = time.time()
    for i, e in enumerate(entries):
        name = e['name']
        gen = os.path.join(out_root, name)
        os.makedirs(gen, exist_ok=True)
        nro = os.path.join(build_nro.RO, 'nro', e['file'])
        stamp = os.path.join(gen, 'table.c')
        extra = os.path.join(build_nro.RO, 'nro_entries.json')
        deps = [nro, build_nro.LIFTER, os.path.join(build_nro.RO, 'nro_eh.py')] + ([extra] if os.path.exists(extra) else [])
        if os.path.exists(stamp) and os.path.getmtime(stamp) >= max(os.path.getmtime(p) for p in deps):
            print('[%d/%d] %s: lifted earlier' % (i + 1, len(entries), name), flush=True)
            continue
        r = subprocess.run([sys.executable, build_nro.LIFTER, 'full', nro, gen, str(min(jobs, 16)), e['base']], capture_output=True, text=True)
        tail = [l for l in r.stdout.splitlines() if l.startswith('functions:')]
        if r.returncode or not tail:
            print('%s: LIFT FAILED\n%s\n%s' % (name, r.stdout[-3000:], r.stderr[-3000:]), flush=True)
            failed.append(name)
            continue
        print('[%d/%d] %s: lifted  %s' % (i + 1, len(entries), name, tail[-1]), flush=True)
    print('modules lifted in %.0fs, %d failed' % (time.time() - t0, len(failed)), flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    cmd = sys.argv[1]
    jobs = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    sys.exit({'main': cmd_main, 'nro': cmd_nro, 'nro-lift': cmd_nro_lift}[cmd](jobs))
