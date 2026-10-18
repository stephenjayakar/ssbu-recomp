"""The first HDR site list for relift_sites.py, from the static plugin inventory (ssbu-hdr\\RECON_HDR.md, tools\\out\\*.tsv).

    python hdr_sites_from_inventory.py [--inv=E:\\BigData\\games\\ssbu-hdr\\tools\\out] [--out=hdr_sites.txt]
                                       [--report=hdr_sites_report.txt] [--obj=obj_fl23] [--gen=gen_fl]

Writes (in the work folder by default):
  hdr_sites.txt         every text patch with its exact word (HDR, ARCropolis, smashline: what the plugin writes, stale
                        offsets and the 8-byte branch write included), every inline hook (HDR, smashline, ARCropolis,
                        stage_alts; those at a function start too: relift_sites.py skips them, the runtime hooks the entry),
                        the two mid-function function hooks of HDR, Wolf's run-time immediate, stage_config's jump table,
                        and `noinline` for every hook-table target that hook_inline_check.py finds inlined / cloned.
  hdr_sites_report.txt  what is not in the list and why (run-time computed text, data writes), the per-target
                        hook_inline_check.py results and the entry-mover census of the hooked entries.
The list is in relift_sites.py's format; a list recorded from the plugins at run time replaces it unchanged.
"""
import collections
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NOP = 0xD503201F

# ARCropolis 4.0.7 src\fixes.rs ADDED_COLOR_PATCHES (hardcoded, "13.0.2" offsets, valid for 13.0.4: RECON_HDR.md 4.2);
# 0x18352bc is stale (a b.eq, the intended cmp is at 0x18352ac): written anyway, as on hardware.
ARC_WORDS = {
    0x18355fc: 0xF104027F, 0x18352bc: 0xF104027F, 0x1835618: 0xF104011F, 0x18359e8: 0xF10402FF, 0x1835e7c: 0xF104011F,
    0x18365d4: 0xF104027F, 0x18368f0: 0xF104013F, 0x1a1cdf0: 0xF104011F, 0x1a1ce34: 0xF104011F, 0x14de31c: 0x7104013F,
    0x14e1624: 0x710402FF, 0x14e17b0: 0x7104011F, 0x1a56084: 0x52800020, 0x1a561b4: 0xF104011F, 0x1a55a74: 0x7104015F,
    0x1a55b0c: 0x7104001F, 0x1a55b38: 0xF104001F, 0x1a55aa0: 0xF104001F, 0x1a55ad4: 0x7104017F,
    0x35bbf54: NOP, 0x35bbf58: NOP,                    # inkling_patch, +4: nop
    0x372c530: NOP,                                    # lua_magic_check: nop (HDR nops it too)
    # BranchBuilder::branch().branch_offset(inkling_c10plus = 0xb0b614).branch_to_offset(+0x38).replace(): skyline-rs
    # writes a u64, so the next word becomes 0 as well (RECON_HDR.md 4.2)
    0xb0b614: 0x1400000E, 0xb0b618: 0x00000000,
}

# HDR hooks `replace = <path>` on main functions by symbol (RECON_HDR.md Appendix F, module main): the mangled names
# the plugin imports (plugin_imports.txt, demangled and matched one to one)
HDR_SYMBOL_HOOKS = [
    ('KineticModule::change_kinetic', '_ZN3app8lua_bind34KineticModule__change_kinetic_implEPNS_26BattleObjectModuleAccessorEi'),
    ('sv_animcmd::FOOT_EFFECT', '_ZN3app10sv_animcmd11FOOT_EFFECTEP9lua_State'),
    ('sv_animcmd::FOOT_EFFECT_FLIP', '_ZN3app10sv_animcmd16FOOT_EFFECT_FLIPEP9lua_State'),
    ('sv_animcmd::LANDING_EFFECT', '_ZN3app10sv_animcmd14LANDING_EFFECTEP9lua_State'),
    ('sv_animcmd::LANDING_EFFECT_FLIP', '_ZN3app10sv_animcmd19LANDING_EFFECT_FLIPEP9lua_State'),
    ('sv_animcmd::DOWN_EFFECT', '_ZN3app10sv_animcmd11DOWN_EFFECTEP9lua_State'),
    ('sv_animcmd::EFFECT_GLOBAL_BACK_GROUND_CUT_IN_CENTER_POS', '_ZN3app10sv_animcmd43EFFECT_GLOBAL_BACK_GROUND_CUT_IN_CENTER_POSEP9lua_State'),
    ('EffectModule::preset_lifetime_rate_partial', '_ZN3app8lua_bind47EffectModule__preset_lifetime_rate_partial_implEPNS_26BattleObjectModuleAccessorEf'),
    ('EffectModule::get_dead_effect_scale', '_ZN3app8lua_bind40EffectModule__get_dead_effect_scale_implEPNS_26BattleObjectModuleAccessorERKN3phx8Vector3fEfb'),
    ('sv_module_access::effect', '_ZN3app16sv_module_access6effectEP9lua_State'),
    ('FighterUtil::get_just_shield_se', '_ZN3app11FighterUtil18get_just_shield_seEi'),
    ('FighterSpecializer_Brave::get_special_lw_command_sp_cost', '_ZN3app24FighterSpecializer_Brave30get_special_lw_command_sp_costERKNS_26BattleObjectModuleAccessorENS_28FighterBraveSpecialLwCommandEb'),
    ('GroundModule::correct', '_ZN3app8lua_bind26GroundModule__correct_implEPNS_26BattleObjectModuleAccessorENS_17GroundCorrectKindE'),
    ('get_ground_correct_kind_air_trans', '_ZN3app11FighterUtil33get_ground_correct_kind_air_transERNS_26BattleObjectModuleAccessorEi'),
    ('GroundModule::can_entry_cliff', '_ZN3app8lua_bind34GroundModule__can_entry_cliff_implEPNS_26BattleObjectModuleAccessorE'),
    ('MotionModule::change_motion', '_ZN3app8lua_bind32MotionModule__change_motion_implEPNS_26BattleObjectModuleAccessorEN3phx6Hash40Effbfbb'),
    ('MotionModule::change_motion_inherit_frame', '_ZN3app8lua_bind46MotionModule__change_motion_inherit_frame_implEPNS_26BattleObjectModuleAccessorEN3phx6Hash40Efffbb'),
    ('MotionModule::change_motion_inherit_frame_keep_rate', '_ZN3app8lua_bind56MotionModule__change_motion_inherit_frame_keep_rate_implEPNS_26BattleObjectModuleAccessorEN3phx6Hash40Efff'),
    ('MotionModule::change_motion_force_inherit_frame', '_ZN3app8lua_bind52MotionModule__change_motion_force_inherit_frame_implEPNS_26BattleObjectModuleAccessorEN3phx6Hash40Efff'),
    ('MotionModule::change_motion_kind', '_ZN3app8lua_bind37MotionModule__change_motion_kind_implEPNS_26BattleObjectModuleAccessorEN3phx6Hash40E'),
    ('StatusModule::init_settings', '_ZN3app8lua_bind32StatusModule__init_settings_implEPNS_26BattleObjectModuleAccessorENS_13SituationKindEijNS_20GroundCliffCheckKindEbiiii'),
    ('StatusModule::change_status_request', '_ZN3app8lua_bind40StatusModule__change_status_request_implEPNS_26BattleObjectModuleAccessorEib'),
    ('StatusModule::change_status_request_from_script', '_ZN3app8lua_bind52StatusModule__change_status_request_from_script_implEPNS_26BattleObjectModuleAccessorEib'),
    ('WorkModule::is_flag', '_ZN3app8lua_bind24WorkModule__is_flag_implEPNS_26BattleObjectModuleAccessorEi'),
    ('FighterSpecializer_Jack::add_rebel_gauge', '_ZN3app23FighterSpecializer_Jack15add_rebel_gaugeERNS_26BattleObjectModuleAccessorENS_14FighterEntryIDEf'),
    ('sv_animcmd::PLAY_SEQUENCE', '_ZN3app10sv_animcmd13PLAY_SEQUENCEEP9lua_State'),
    ('WorkModule::is_enable_transition_term', '_ZN3app8lua_bind42WorkModule__is_enable_transition_term_implEPNS_26BattleObjectModuleAccessorEi'),
    ('WorkModule::enable_transition_term', '_ZN3app8lua_bind39WorkModule__enable_transition_term_implEPNS_26BattleObjectModuleAccessorEi'),
    ('FighterSpecializer_Brave::special_lw_open_command', '_ZN3app24FighterSpecializer_Brave23special_lw_open_commandERNS_7FighterE'),
    ('FighterSpecializer_Rockman::set_leafshield', '_ZN3app26FighterSpecializer_Rockman14set_leafshieldERNS_26BattleObjectModuleAccessorEb'),
    ('ControlModule::get_down_stand_fb_kind', '_ZN3app8lua_bind42ControlModule__get_down_stand_fb_kind_implEPNS_26BattleObjectModuleAccessorE'),
    ('TeamModule::set_team', '_ZN3app8lua_bind25TeamModule__set_team_implEPNS_26BattleObjectModuleAccessorEib'),
]

MIDHOOKS = {0x9a5090: 'HDR duckhunt_on_attack (fc/fh/vtables/duckhunt.rs:18), 0x9a5070+0x20',
            0x376d000: 'HDR set_next_button (src/lua.rs:318), 0x376cf50+0xb0'}
WOLF = 0x12c29c0             # HDR wolf on_start: `cmp w0,#<SPECIAL_S_RUSH>` (0x7100001F | id << 10) at every Wolf start
JTABLE_FN, JTABLE_ADRP = 0x2633c90, 0x2633d28     # stage_config: create_stage's table 0x4505df0, fn 0x2633c90


def tsv(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        return [line.rstrip('\r\n').split('\t') for line in f if line.strip()]


def main():
    opts = dict(a[2:].split('=', 1) if '=' in a else (a[2:], '1') for a in sys.argv[1:] if a.startswith('--'))
    inv = opts.get('inv', r'E:\BigData\games\ssbu-hdr\tools\out')
    out_path = os.path.join(HERE, opts.get('out', 'hdr_sites.txt'))
    rep_path = os.path.join(HERE, opts.get('report', 'hdr_sites_report.txt'))
    import recomp
    import hook_inline_check
    B = recomp.BASE

    def word(off):
        return recomp.word(B + off)
    lines, report = [], []
    patches = collections.OrderedDict()           # offset -> (new word, source)
    inlines = collections.OrderedDict()           # offset -> source
    entry_targets = collections.OrderedDict()     # offset -> source (hook table)
    not_precomputed, data_writes = [], []

    def add_patch(off, new, src):
        if off in patches and patches[off][0] != new:
            raise SystemExit('two different words for %x: %08x (%s) and %08x (%s)' % (off, patches[off][0], patches[off][1], new, src))
        if off in patches:
            patches[off] = (new, patches[off][1] + ' + ' + src)
        else:
            patches[off] = (new, src)

    # ---- HDR text / data patches
    rows = tsv(os.path.join(inv, 'hdr_patches_annot.tsv'))
    hdr = rows[0]
    col = {n: i for i, n in enumerate(hdr)}
    for r in rows[1:]:
        r += [''] * (len(hdr) - len(r))
        if r[col['state']] != 'active':
            continue
        src = 'HDR %s:%s' % (r[col['file']].replace('fighters/common/src/function_hooks/', 'fc/fh/'), r[col['line']])
        region, act = r[col['region2']] or r[col['region']], r[col['action']]
        off = int(r[col['resolved']], 16) if r[col['resolved']] else None
        if region != 'text':
            data_writes.append('%s %s %s: %s (data, no re-lift: the runtime must let sky_memcpy write it)' % (
                r[col['resolved']] or '?', region, act, src))
            continue
        if act.startswith('nop()') or act == 'data(KILL_ZOOM_DATA)':
            new = NOP
        elif act == 'data(instruction)':
            not_precomputed.append('%x HDR wolf special_s.rs:421 `cmp w0,#SPECIAL_S_RUSH` written at every Wolf start; '
                                   'immediate = smashline-assigned status id: compiled as dynimm (the immediate is read at run time)' % off)
            continue
        else:
            m = re.match(r'^data\((0x[0-9a-fA-F]+)(?:u32)?\)$', act)
            if not m:
                raise SystemExit('unparsed HDR text patch %s %s' % (r[col['resolved']], act))
            new = int(m.group(1), 16)
        add_patch(off, new, src)

    # ---- HDR hooks
    rows = tsv(os.path.join(inv, 'hdr_hooks_annot.tsv'))
    hdr = rows[0]
    col = {n: i for i, n in enumerate(hdr)}
    for r in rows[1:]:
        r += [''] * (len(hdr) - len(r))
        if r[col['state']] != 'active' or r[col['installed']] != 'main' or not r[col['resolved']]:
            continue
        off = int(r[col['resolved']], 16)
        src = 'HDR %s %s:%s' % (r[col['target_expr']], r[col['file']].replace('fighters/common/src/function_hooks/', 'fc/fh/'), r[col['line']])
        if r[col['kind']] == 'inline':
            inlines[off] = src
            if r[col['pos']] == 'entry':
                entry_targets[off] = src + ' (inline hook at a function start)'
        elif r[col['kind']] == 'entry':
            if r[col['pos']] == 'entry':
                entry_targets[off] = src
            elif off not in MIDHOOKS:
                raise SystemExit('HDR entry hook at a non-entry %x not expected' % off)
    # by symbol
    syms = {}
    nsym = (recomp.STRTAB - recomp.SYMTAB) // 24
    for i in range(nsym):
        name_off, info, other, shndx, value, size = struct.unpack_from('<IBBHQQ', recomp.IMG, recomp.FILE_OFF + recomp.SYMTAB + i * 24)
        if shndx:
            p = recomp.FILE_OFF + recomp.STRTAB + name_off
            syms[recomp.IMG[p:recomp.IMG.index(b'\0', p)].decode('latin-1')] = value
    for written, mangled in HDR_SYMBOL_HOOKS:
        if mangled not in syms:
            raise SystemExit('main does not export %s' % mangled)
        entry_targets.setdefault(syms[mangled], 'HDR by symbol %s' % written)

    # ---- smashline / ARCropolis / stage_alts / stage_config
    for r in tsv(os.path.join(inv, 'other_sites_annot.tsv')):
        plugin, kind, off, fn, pos = r[0], r[1], int(r[2], 16), r[3], r[4]
        src = '%s %s' % (plugin, kind)
        if kind in ('inline', 'inline+nop', 'inline-opt'):
            inlines.setdefault(off, src + (' (optional: ARCropolis skip_title_scene / skip_cutscene)' if kind == 'inline-opt' else ''))
            if kind == 'inline+nop':
                add_patch(off, NOP, plugin + ' nop before its inline hook')
            if pos == 'entry':
                entry_targets.setdefault(off, src + ' (inline hook at a function start)')
        elif kind == 'patch':
            if plugin != 'arcropolis' or off not in ARC_WORDS:
                raise SystemExit('no word for the %s patch at %x' % (plugin, off))
            add_patch(off, ARC_WORDS[off], 'ARCropolis fixes.rs' + (' (stale 13.0.2 offset: overwrites a b.eq, as on hardware)' if off == 0x18352bc else
                                                                   ' (8-byte branch write: the second word)' if off == 0xb0b618 else ''))
        elif kind == 'entry':
            entry_targets.setdefault(off, '%s function hook' % plugin)
    for off in ARC_WORDS:
        if off not in patches:
            raise SystemExit('ARCropolis patch %x missing from the inventory' % off)

    # ---- stage_config's jump table: the br after the adrp/add of the table in fn 0x2633c90
    br = next(o for o in range(JTABLE_ADRP, JTABLE_ADRP + 0x40, 4) if (word(o) & 0xfffffc1f) == 0xd61f0000)

    # ---- noinline analysis of every hook-table target
    targets = sorted(B + o for o in entry_targets)
    res = hook_inline_check.check(targets, os.path.join(HERE, opts.get('gen', 'gen_fl')), os.path.join(HERE, opts.get('obj', 'obj_fl23')))
    # a file the other sites re-lift is compiled again anyway, and its new code may inline a target that was not inlined
    # before (a patch can shrink a function below the inliner's threshold): every target in such a file gets noinline too
    import relift_sites
    rl = relift_sites.Relifter(os.path.join(HERE, opts.get('gen', 'gen_fl')))
    code_sites = set(patches) | set(o for o in inlines if B + o not in rl.starts_set) | set(MIDHOOKS) | {WOLF}
    relifted = set(rl.bucket_of(e) for o in code_sites for e in rl.entries_holding(B + o)) | set(rl.bucket_of(B + o) for o in MIDHOOKS)
    relifted |= set(res[t]['bucket'] for t in targets if res[t]['verdict'] not in ('OK', 'NOT AN ENTRY'))     # files compiled again for a noinline

    # ---- the list
    L = lines.append
    L('# HDR site list for relift_sites.py, generated by hdr_sites_from_inventory.py from %s' % inv)
    L('# (HDR v0.49.11 + smashline 1.6.5 + ARCropolis 4.0.7 + stage_alts 0.1.8 + stage_config; game 13.0.4). Static inventory:')
    L('# a list recorded from the plugins at run time (same format) supersedes it.')
    L('')
    L('# ---- text patches: <addr> <word of the binary> <word the plugin writes>')
    for off, (new, src) in sorted(patches.items()):
        L('patch    0x%x %08x %08x    # %s' % (B + off, word(off), new, src))
    L('')
    L('# ---- inline hooks (callout before the instruction; at a function start relift_sites.py compiles none: hook table)')
    for off, src in sorted(inlines.items()):
        L('inline   0x%x    # %s%s' % (B + off, src, '  [also patched]' if off in patches else ''))
    L('')
    L('# ---- function hooks written at a non-entry address (HDR offsets that are stale in 13.0.4; installed on hardware)')
    for off, src in sorted(MIDHOOKS.items()):
        L('midhook  0x%x    # %s' % (B + off, src))
    L('')
    L('# ---- run-time immediates')
    L('dynimm   0x%x    # HDR wolf on_start: cmp w0,#<SPECIAL_S_RUSH status id> (fighters/wolf/src/status/special_s.rs:419)' % (B + WOLF))
    L('')
    L('# ---- jump tables patched at run time')
    L('jtable   0x%x    # stage_config: create_stage switch of fn 0x%x, table 0x4505df0 + 4*stage_id (6 entries rewritten)' % (B + br, JTABLE_FN))
    L('')
    L('# ---- hook-table targets that some caller in the same file inlines (hook_inline_check.py on obj_fl23)')
    nbad = 0
    nextra = 0
    for t in targets:
        r = res[t]
        if r['verdict'] not in ('OK', 'NOT AN ENTRY'):
            nbad += 1
            L('noinline 0x%x    # %s: %s' % (t, r['verdict'], entry_targets[t - B]))
        elif r['verdict'] == 'OK' and r['bucket'] in relifted:
            nextra += 1
            L('noinline 0x%x    # OK in obj_fl23, its file is re-lifted for other sites: %s' % (t, entry_targets[t - B]))
    with open(out_path, 'w', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')

    # ---- report
    R = report.append
    R('HDR site list report (hdr_sites_from_inventory.py)')
    R('')
    R('site list: %s' % out_path)
    R('  %d text patches, %d inline hooks (%d at function starts, no callout), %d midhook, 1 dynimm, 1 jtable, %d noinline'
      % (len(patches), len(inlines), sum(1 for o in inlines if o in entry_targets), len(MIDHOOKS), nbad + nextra))
    R('  noinline: %d targets inlined / cloned in obj_fl23 + %d not inlined there but in a file re-lifted for other sites' % (nbad, nextra))
    R('  re-lifted files for patches / inline / midhook / dynimm: %d' % len(relifted))
    R('')
    R('TEXT WRITES NOT PRECOMPUTABLE (computed at run time):')
    for x in not_precomputed:
        R('  ' + x)
    R('  stage_config: 6 i32 entries of the .rodata jump table 0x4505df0 (config dependent): data, the lifted br reads the table at run time (jtable line)')
    R('')
    R('HOOK TARGETS KNOWN ONLY AT RUN TIME (not analysed): HDR fighter / weapon / item destructor and deleter hooks (vtable+0x340 / +0x348, read when the')
    R('  first vtable-swap inline hook runs); nnSdk set_interval_1 (sdk+0x429d60) and the plugin-module targets are not in main.')
    R('')
    R('DATA / RODATA WRITES (no re-lift needed):')
    for x in data_writes:
        R('  ' + x)
    R('')
    R('HOOK-TABLE TARGETS (%d): verdict, bucket, entry mover, callers in the same file (compiled call+jump sites / reachable lifted sites, + dead lifted sites)' % len(targets))
    for t in targets:
        r = res[t]
        callers = ', '.join(hook_inline_check.fmt_caller(*c) for c in r['callers']) or 'none in its file'
        R('  %x %-9s %s  entry %s  callers: %s  [%s]' % (t, r['verdict'], ('c_%04x' % r['bucket']) if r['bucket'] is not None else '------', r['entry'], callers, entry_targets[t - B]))
    v = collections.Counter(res[t]['verdict'] for t in targets)
    R('  summary: ' + ', '.join('%s %d' % kv for kv in sorted(v.items())))
    refused = [t for t in targets if res[t]['entry'].startswith('REFUSED')]
    R('  entry mover refusals among them: %d %s' % (len(refused), ' '.join('%x' % t for t in refused)))
    with open(rep_path, 'w', newline='\n') as f:
        f.write('\n'.join(report) + '\n')
    print('\n'.join(report))
    print('wrote %s (%d lines)' % (out_path, len(lines)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
