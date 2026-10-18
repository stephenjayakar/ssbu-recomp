/* The 120 Hz mode: graphics.ini frame_rate = 120 (env SSBU_FRAME_RATE=120 wins).
 *
 * The engine already draws between its steps: in a global slow motion (the final hit of a match, a fighter's "whole"
 * slow) it runs the battle with a divisor N: logic (status scripts, hit boxes, every frame counter) on one update in N,
 * and the N - 1 updates between drawn by the engine itself at fractional motion frames. This mode uses exactly that:
 * during a battle the refresh clock runs at 120 Hz (display.c) and the divisor is doubled, so logic still runs 60 times
 * per second and every second presented frame is the engine's own picture between two steps. Nothing is blended or
 * guessed, and a slow motion of the game (N = 3) keeps its real length (N = 6 at 120 Hz).
 *
 * Main 13.0.4 [SEEN in the binary unless marked]:
 *   0x710135ca00  the battle's per-update step (vtable 0x71050603f0, slot 6). It keeps the slow state at 0x71052c4184
 *                 (u32 cycle counter, u8 N of the cycle at +4, u8 N asked for at +5, u8 update within the cycle at +6,
 *                 f32 1 / N at +0xc) and then updates every battle object (0x710137b3f0) with a per-update record
 *                 built from it (0x71003ac660: logic on update 0 of a cycle, the others through the motion module's
 *                 sub-frame slots).
 *   0x71014a2cd0  the largest N of the slow request table *(0x71052c2610) + 0x20.. (enabled, N byte pairs; at least 1).
 *                 The step calls it when the table's dirty flag (+0x40) is set, stores the clamped result as the N
 *                 asked for, and takes it as the cycle's N at the next cycle boundary (at once when N is 1).
 * The hook on 0x71014a2cd0 doubles its result in a battle; the hook on the step marks the battle as running and sets the
 * dirty flag whenever the N asked for is 1 (the battle's set-up writes 1 there), so the doubled N is taken even when no
 * request changes (not for a few steps after a slow motion of the game ends: see g_hold). Every other value (the cycle,
 * the rate, the per-update records) is the game's own work. Not done: writing N after the step returns (the objects have
 * been updated inside it by then: a cycle-end update ran with N = 1 and the next one reset each motion to a saved frame
 * that was never written: frozen fighters, measured 2026-10-15).
 * The step runs only in a battle: the refresh clock is 120 Hz while it has run in the last quarter second, else 60 Hz, so
 * the menus keep their console speed.
 *
 * At frame_rate = 60 nothing here runs: no hooks, a 60 Hz clock. */
#include <windows.h>
#include <stdio.h>
#include "runtime.h"
#include "hostrt.h"
#include "gfx_options.h"

#define FN_SLOW_STEP 0x710135ca00ULL
#define FN_SLOW_MAX 0x71014a2cd0ULL
#define SLOW_TABLE_PTR 0x71052c2610ULL                 /* -> the slow request table; + 0x40 = its dirty flag */
#define SLOW 0x71052c4184ULL                           /* the slow state: + 4 N of the cycle, + 6 update within the cycle */
/* The update rate, [SEEN] 0x710523c004 (app::sv_information::get_delta_frame returns it; about 45 readers compute 60 / rate
 * frames per update: the match timer, the battle's set-up and entrance sequencing, ...). Those run on every update, outside
 * the slow divisor: at 120 updates per second they would run twice as fast (measured: the match timer at 1.8x real time).
 * In a battle with doubled cycles it is set to 119: 60 / 119 = 0.504 frames per update, 0.84 % fast. Not 120: the code
 * that tests rate / 60 >= 2 (the engine's own 2x replay speed: Brave::get_special_lw_param_frame, shiokarazu
 * get_slow_rate, 0x71006b92c0) would take that branch. The main loop [SEEN 0x7103749b70..0x7103749bd8] counts updates
 * per picture from the same value: acc -= rate, then one update per 60 that brings acc above 0, so rate / 60 updates
 * per picture; hfr_picture adds rate - 60 to acc once per picture to keep that at one (the updates for refreshes the
 * game missed, 0x710593a44c, are added by the game apart from this count and still are). */
#define UPDATE_RATE 0x710523c004ULL
#define UPDATE_ACC 0x710593a454ULL
#define HFR_RATE 119
/* The match timer: the battle rule component (vtable 0x710506fbf0) keeps the remaining frames at +0x3d4 [SEEN: read by
 * sv_information::get_remaining_time_as_frame through slot 0x128, set from the rule's time by slot 0x120 = 0x710157c8f0].
 * MEASURED (SSBU_HFR_LOG): it drops by 1 inside every battle step, the game's catch-up updates included; it does not read
 * the update rate. The component is taken from its init, and an added update gives its frame back. */
#define FN_TIMER_INIT 0x710157c8f0ULL
#define CATCH_UP 0x710593a44cULL                       /* the game's count of catch-up updates in this picture (only logged) */
#define RNG_ROOT 0x71052c25b0ULL                      /* -> -> -> the 9 script random generators of app::sv_math::rand, 20 bytes each (fighter_sdk\fighter_host.c) */
int hook_install(uint64_t addr, void (*repl)(Ctx *, void *), void *user, ImportFn *orig);      /* fighter_sdk\hook_host.c (in nnro.c) */

static int g_setting;                                  /* 60 or 120 once hfr_init has run */
static unsigned g_rate = HFR_RATE;                    /* TEST: SSBU_HFR_RATE */
/* The end of a slow motion of the game. Code of the game takes N != 1 (or N > 1) as "a slow motion runs" [SEEN: 0x7101481058,
 * 0x71014b63c0, 0x71014b6cb0, 0x7101510978, 0x71017532e4 read N of the cycle]; the end of a stock match waits for N to be
 * 1 again after the final hit's slow motion (N = 4) before it starts the "GAME" sequence. With N doubled the game would
 * never see 1: the match stayed on its final hit forever (measured 2026-10-17 at 120 Hz, every stock match). So when the
 * game's request maximum falls back to 1 after a request above 1, the hook returns 1, not 2, until the step has taken
 * N = 1 as the cycle's N and kept it for HOLD_STEPS steps that ran with the stop word clear (a hit stop that freezes the
 * battle often follows the final hit: the step does not take a new N while it lasts, measured 95 updates); a new request
 * above 1 ends the hold at once. Those steps run at 60 Hz (the clock follows g_doubled_now). MEASURED (run30, 3 stock
 * fights): N 8 -> stop 93 updates -> N 1 for 8 steps -> N 2, then the "GAME" request (90, falling to 1) and the results,
 * every fight. Since the end of the match below, this hold is left for slow motions within a match (a hard hit, a
 * fighter's special: N 16 -> 1 for 8 steps -> 2 measured with Ness and Captain Falcon, the fight went on). */
#define STOP_WORD 0x71052b82e8ULL                     /* nonzero: the battle is stopped (the step skips its slow bookkeeping) */
#define HOLD_STEPS 8
static unsigned g_last_req = 1;                        /* the game's own request maximum at its last call (battle thread) */
static int g_hold, g_hold_done;                        /* holding N at 1; steps of the hold with N = 1 and no stop */
static int g_hold_steps = HOLD_STEPS;                  /* TEST: SSBU_HFR_HOLD */
static LONG64 g_holds;
/* The end of the match. Once the FighterManager's is_ready_go (impl + 0xd2) has been set in this battle and is clear again
 * (the final hit, the end of the time, a quit from the pause menu) the divisor is not doubled any more: whatever the game
 * does from there to the end of the battle scene (the final hit's slow motion, "GAME", the way to the results or back to
 * the select screen) runs as on the console, at 60 Hz. A quit from the pause menu ends the match without any slow request,
 * so the hold above never starts there [the user's report 2026-10-18: quitting left the game in the battle scene at 120
 * Hz; reproduced: runQ9off stayed on NO CONTEST, runQ9on went back to the select screen 3 times of 3]. With the hold alone
 * the stock matches did end (run30), but this is simpler and the same for every way a match ends. MEASURED: stock matches
 * through "GAME" to the results (runA2, runB, runC, runT2: 20 fights), a time-out through sudden death (run32). A sudden
 * death sets is_ready_go again and runs at 120 Hz. TEST: SSBU_HFR_OVER=0 turns this off. */
static int g_go_seen, g_over, g_over_on = 1;
static uint64_t g_fm_inst;                             /* &lib::Singleton<app::FighterManager>::instance_ */
uint64_t hook_address(const char *name);              /* fighter_sdk\hook_host.c (included by nnro.c) */
static int g_trace;                                    /* TEST ONLY, SSBU_HFR_TRACE=1: changes of the slow state, requests above 1, the holds */
static ImportFn g_orig_step, g_orig_max, g_orig_timer_init;
static volatile uint64_t g_timer_comp;                  /* the rule component, from its init */
static volatile LONG64 g_step_qpc;                     /* QueryPerformanceCounter at the last battle step */
static volatile LONG g_doubled_now;                    /* the current cycle runs with the doubled divisor */
static LONG64 g_qpf, g_steps, g_battles, g_doubled, g_added, g_timer;
static LONG64 g_upd_ticks[2], g_upd_n[2], g_last_step_qpc;   /* time from one battle step to the next: [0] after a console update, [1] after an added one */
static int g_last_added;

int hfr_setting(void) { return g_setting == 120 ? 120 : 60; }
/* TEST ONLY (SSBU_SHOT_BATTLE lines): the update within the slow cycle as the render thread sees it, -1 at 60 Hz. */
int hfr_shot_phase(void) { return g_setting == 120 ? (int)rd8(SLOW + 6) : -1; }
static int battle_running(LONG64 now) { return g_step_qpc && now - g_step_qpc < g_qpf / 4; }
/* Refreshes per second of the clock now: 120 in a battle with frame_rate = 120, else 60. Any thread. */
int hfr_rate(void) {
    if (g_setting != 120) return 60;
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    return battle_running(now.QuadPart) && g_doubled_now ? 120 : 60;      /* 120 only once the cycles are doubled: before, every update is a console update */
}

static void slow_step_hook(Ctx *c, void *user) {
    (void)user;
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    if (!battle_running(now.QuadPart)) { g_battles++; g_hold = 0; g_last_req = 1; g_go_seen = g_over = 0; }
    if (!g_fm_inst) g_fm_inst = hook_address("_ZN3lib9SingletonIN3app14FighterManagerEE9instance_E");
    uint64_t fm = g_fm_inst ? rd64(g_fm_inst) : 0, fmi = fm ? rd64(fm) : 0;
    if (fmi && rd8(fmi + 0xd2)) {
        if (g_over && g_trace) printf("hfr-trace: is_ready_go set again at update %lld: doubling\n", (long long)g_steps);
        g_go_seen = 1; g_over = 0;
    } else if (fmi && g_go_seen && !g_over && g_over_on) {
        g_over = 1;
        if (g_trace) printf("hfr-trace: the match is over at update %lld (is_ready_go clear): no doubling\n", (long long)g_steps);
    }
    if (rd8(SLOW + 5) < 2 || g_over) {                           /* N asked for is 1: never a doubled value, so the game set it (a battle's set-up does, after
                                                        * its first updates: measured 2026-10-15); have the step ask the request maximum again */
        uint64_t t = rd64(SLOW_TABLE_PTR);
        if (t) wr8(t + 0x40, 1);
    }
    InterlockedExchange64(&g_step_qpc, now.QuadPart);
    g_steps++;
    /* The updates this mode adds are the odd ones of a cycle of 2N (the even ones are the console's updates of a cycle of
     * N). Effects draw from the script random generators on every update, so the added ones would move the generators
     * that the game logic also draws from, and the fight would take another course than at 60 Hz. An added update gets
     * the generators back as they were before it: the logic sees the same random numbers as at 60 Hz. */
    unsigned n = rd8(SLOW + 4), next = rd8(SLOW + 6) + 1u;
    uint64_t rb = rd64(RNG_ROOT);
    if (rb) rb = rd64(rb);
    if (rb) rb = rd64(rb);
    int added = n > 1 && next < n && (next & 1) && rb;
    if (g_last_step_qpc && now.QuadPart - g_last_step_qpc < g_qpf / 10) { g_upd_ticks[g_last_added] += now.QuadPart - g_last_step_qpc; g_upd_n[g_last_added]++; }
    g_last_step_qpc = now.QuadPart; g_last_added = added;
    uint8_t keep[180];
    if (added) memcpy(keep, (void *)(uintptr_t)rb, sizeof keep);
    uint64_t tc = g_timer_comp;
    int before = tc ? (int)rd32(tc + 0x3d4) : -1;
    g_orig_step(c);
    {   /* TEST ONLY, SSBU_HFR_TRACE=1: every change of the slow state */
        static unsigned last = 0xffffffff; static int shown2;
        int tr = g_trace;
        unsigned cur = rd8(SLOW + 4) | rd8(SLOW + 5) << 8 | (rd32(STOP_WORD) ? 1u << 16 : 0);
        if (tr && cur != last && shown2 < 400) { shown2++; last = cur; printf("hfr-trace: update %lld N %u asked %u phase %u counter %u inv %f stop %u\n", (long long)g_steps, rd8(SLOW + 4), rd8(SLOW + 5), rd8(SLOW + 6), rd32(SLOW), (double)*(float *)(uintptr_t)(SLOW + 0xc), rd32(STOP_WORD)); }
    }
    if (g_hold && rd8(SLOW + 4) == 1 && !rd32(STOP_WORD) && ++g_hold_done >= g_hold_steps) {
        g_hold = 0;                                    /* the next request maximum doubles again */
        if (g_trace) printf("hfr-trace: hold ends at update %lld\n", (long long)g_steps);
    }
    InterlockedExchange(&g_doubled_now, rd8(SLOW + 4) >= 2);
    if (g_doubled_now) wr32(UPDATE_RATE, g_rate);   /* the battle's managers: 60 / rate frames per update (set-up writes 60 here again) */
    else if (rd32(UPDATE_RATE) == g_rate) wr32(UPDATE_RATE, 60);   /* N = 1 (a hold): console updates at a 60 Hz clock */
    if (added) {
        memcpy((void *)(uintptr_t)rb, keep, sizeof keep);
        g_added++;
        if (tc && (int32_t)rd32(tc + 0x3d4) >= 0 && (int)rd32(tc + 0x3d4) == before - 1) { wr32(tc + 0x3d4, (uint32_t)before); g_timer++; }
    }
    {   /* TEST ONLY, SSBU_HFR_LOG=1: the slow state and the match timer for 40 updates (from the 600th) */
        static int log = -1, shown;
        if (log < 0) log = getenv("SSBU_HFR_LOG") && atoi(getenv("SSBU_HFR_LOG")) != 0;
        if (log && tc && shown < 40 && g_steps > 600) { shown++; printf("hfr-log: update %lld N %u phase-next %u added %d rate %u: timer %d -> %d (catch-up %u)\n", (long long)g_steps, rd8(SLOW + 4), next, added, rd32(UPDATE_RATE), before, (int)rd32(tc + 0x3d4), rd32(CATCH_UP)); }
    }
}
static void slow_max_hook(Ctx *c, void *user) {
    (void)user;
    g_orig_max(c);
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    if (!battle_running(now.QuadPart)) return;
    unsigned n = (unsigned)(c->x[0] & 0xff);
    {   static int shown3;
        if (g_trace && n != 1 && shown3 < 200) { shown3++; printf("hfr-trace: request maximum %u (x0 %llx) at update %lld\n", n, (unsigned long long)c->x[0], (long long)g_steps); }
    }
    if (!n) n = 1;
    if (n == 1 && g_last_req > 1 && g_hold_steps > 0) {
        g_hold = 1; g_hold_done = 0; g_holds++;
        if (g_trace) printf("hfr-trace: hold starts at update %lld\n", (long long)g_steps);
    }
    if (n > 1) g_hold = 0;
    g_last_req = n;
    if (g_hold || g_over) { c->x[0] = n; return; }
    c->x[0] = n * 2 > 100 ? 100 : n * 2;              /* the step clamps to 1..100 as well */
    g_doubled++;
}

static void timer_init_hook(Ctx *c, void *user) {
    (void)user;
    g_timer_comp = c->x[0];
    /* TEST ONLY, SSBU_HFR_TEST_TIME=<seconds>: a match without a time limit gets one (the rule's frames at *(component + 0x10)
     * + 0x48, which the init copies to the remaining frames [SEEN 0x710157c8fc..0x710157c92c]): a test of the end by time. */
    uint64_t rule = rd64(c->x[0] + 0x10);
    const char *tt = getenv("SSBU_HFR_TEST_TIME");
    if (tt && rule && !rd32(rule + 0x48)) wr32(rule + 0x48, (uint32_t)atoi(tt) * 60u);
    g_orig_timer_init(c);
    if (g_trace) printf("hfr-trace: match timer init: rule %llx time %u frames, remaining %d\n", (unsigned long long)rule, rule ? rd32(rule + 0x48) : 0, (int)rd32(c->x[0] + 0x3d4));
}

/* boot.c, after the static constructors, before nnMain. */
void hfr_init(void) {
    LARGE_INTEGER f;
    QueryPerformanceFrequency(&f);
    g_qpf = f.QuadPart;
    g_setting = 60;
    if (gfx_options()->frame_rate != 120) return;
    int a = hook_install(FN_SLOW_STEP, slow_step_hook, NULL, &g_orig_step), b = a ? hook_install(FN_SLOW_MAX, slow_max_hook, NULL, &g_orig_max) : 0;
    if (!a || !b) { printf("hfr: frame_rate = 120 asked for, but a hook failed (step %d, request maximum %d): 60 is used\n", a, b); return; }
    hook_install(FN_TIMER_INIT, timer_init_hook, NULL, &g_orig_timer_init);
    if (getenv("SSBU_HFR_HOLD")) g_hold_steps = atoi(getenv("SSBU_HFR_HOLD"));      /* TEST ONLY */
    if (getenv("SSBU_HFR_OVER")) g_over_on = atoi(getenv("SSBU_HFR_OVER")) != 0;      /* TEST ONLY */
    g_trace = getenv("SSBU_HFR_TRACE") && atoi(getenv("SSBU_HFR_TRACE")) != 0;
    if (getenv("SSBU_HFR_RATE")) g_rate = (unsigned)atoi(getenv("SSBU_HFR_RATE"));
    g_setting = 120;
    printf("hfr: frame_rate = 120: battles at 120 Hz (the engine's slow-motion divisor doubled, logic 60 steps per second), menus at 60 Hz\n");
}

/* display.c display_clock_sample, once per picture (the game's frame end, before its next count of updates): one update per
 * picture while the update rate is ours (see UPDATE_RATE). */
void hfr_picture(void) {
    if (g_setting != 120 || rd32(UPDATE_RATE) != HFR_RATE) return;
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    if (!battle_running(now.QuadPart)) { wr32(UPDATE_RATE, 60); return; }     /* the battle has stopped (a quit from the pause menu ends it
                                                                              * without the set-up of another one): the console's rate again */
    wr32(UPDATE_ACC, rd32(UPDATE_ACC) + (HFR_RATE - 60));
}

void hfr_report(FILE *out) {
    if (g_setting == 120) fprintf(out, "hfr: %lld battles, %lld battle updates (%lld of them added between two of the console's), slow divisor doubled %lld times, match timer frames given back %lld, ends of the game's slow motions held at N = 1 %lld\n", (long long)g_battles, (long long)g_steps, (long long)g_added, (long long)g_doubled, (long long)g_timer, (long long)g_holds);
    if (g_setting == 120 && g_upd_n[0] && g_upd_n[1])
        fprintf(out, "hfr: time from one battle step to the next: after a console update %.2f ms (%lld), after an added update %.2f ms (%lld)\n",
                (double)g_upd_ticks[0] * 1000.0 / (double)g_qpf / (double)g_upd_n[0], (long long)g_upd_n[0], (double)g_upd_ticks[1] * 1000.0 / (double)g_qpf / (double)g_upd_n[1], (long long)g_upd_n[1]);
}
