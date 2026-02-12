import { API } from './api.js';
import { fireConfetti } from './confetti.js';

// ═══════════════════════════════════════════════════════════════════════
//  CONSTANTS & STATE
// ═══════════════════════════════════════════════════════════════════════
const ROW_H = 88;
const PENDING_MS = 2000;
const VERDICT_MS = 1200;
const CLIMB_STEP_MS = 180;
const WA_PENALTY_MINUTES = 20;

function fmtTime(sec) {
    const h = String(Math.floor(sec / 3600)).padStart(2, '0');
    const m = String(Math.floor((sec % 3600) / 60)).padStart(2, '0');
    return h + ':' + m;
}

let currentPhase = 'setup';
let data = null;
let problems = [];
let state = [];
let queue = [];
let revealed = 0;
let acceptedCount = 0;
let rankChangeCount = 0;
let autoTimer = null;
let playing = false;
let processing = false;
let mvp = { handle: '', delta: 0 };
let lastMinuteHero = { handle: '', problemIndex: '', problemName: '', time: 0 };
let submissionCounts = {};  // { 'handle|problemIndex': count }
let mostPersistent = { handle: '', problemIndex: '', problemName: '', count: 0 };
let revealFinished = false;
let livePoller = null;
let contestName = '';

// Timer state
let contestDurationSec = 0;   // Total contest duration in seconds
let freezeAtSec = 0;          // Seconds from start when freeze happens
let contestStartTimestamp = 0;  // When contest started (Date.now() - elapsed)
let timerInterval = null;
let timeScale = 1; // For simulation: 60 means 60x speed
let isFrozen = false;

const el = id => document.getElementById(id);
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ═══════════════════════════════════════════════════════════════════════
//  AUDIO
// ═══════════════════════════════════════════════════════════════════════
let ctx = null;
function audio() { if (!ctx) ctx = new (window.AudioContext || window.webkitAudioContext)(); return ctx; }

function snd(type) {
    if (!el('sound-toggle')?.checked) return;
    const c = audio(), t = c.currentTime;
    const make = (waveform, freq, start, dur, vol) => {
        const o = c.createOscillator(), g = c.createGain();
        o.type = waveform; o.frequency.setValueAtTime(freq, start);
        o.connect(g); g.connect(c.destination);
        g.gain.setValueAtTime(vol, start);
        g.gain.exponentialRampToValueAtTime(0.001, start + dur);
        o.start(start); o.stop(start + dur);
        return o;
    };

    if (type === 'pending') {
        const o = make('sine', 220, t, 2, 0.05);
        o.frequency.linearRampToValueAtTime(330, t + 1.8);
    } else if (type === 'accepted') {
        [523, 659, 784].forEach((f, i) => make('sine', f, t + i * 0.07, 0.5, 0.1));
    } else if (type === 'rejected') {
        const o = make('sawtooth', 200, t, 0.35, 0.06);
        o.frequency.exponentialRampToValueAtTime(80, t + 0.35);
    } else if (type === 'climb') {
        [880, 1047, 1319].forEach((f, i) => make('triangle', f, t + i * 0.08, 0.6, 0.08));
    } else if (type === 'fanfare') {
        [523, 659, 784, 1047].forEach((f, i) => make('sine', f, t + i * 0.1, 1.8, 0.08));
    } else if (type === 'freeze') {
        // Ice-like sound
        [1200, 900, 600].forEach((f, i) => make('sine', f, t + i * 0.12, 0.8, 0.06));
    } else if (type === 'end') {
        // Deep gong
        make('sine', 220, t, 2.5, 0.12);
        make('sine', 110, t, 3, 0.08);
    }
}

// ═══════════════════════════════════════════════════════════════════════
//  TIMER
// ═══════════════════════════════════════════════════════════════════════

function formatTime(seconds) {
    if (seconds < 0) seconds = 0;
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function startContestTimer(durationSec, freezeAtSecond, elapsedSec = 0, scale = 1) {
    contestDurationSec = durationSec;
    freezeAtSec = freezeAtSecond;
    contestStartTimestamp = Date.now() - (elapsedSec * 1000 / scale);
    isFrozen = false;
    timeScale = scale;

    el('banner-timer').style.display = 'block';
    updateTimerDisplay();

    if (timerInterval) clearInterval(timerInterval);
    timerInterval = setInterval(updateTimerDisplay, 1000);
}

function stopContestTimer() {
    if (timerInterval) {
        clearInterval(timerInterval);
        timerInterval = null;
    }
}

function updateTimerDisplay() {
    const elapsedMs = (Date.now() - contestStartTimestamp) * timeScale;
    const elapsedSec = Math.floor(elapsedMs / 1000);
    const remaining = Math.max(0, contestDurationSec - elapsedSec);
    const remainingUntilFreeze = Math.max(0, freezeAtSec - elapsedSec);

    // Update clock
    el('timer-display').textContent = formatTime(remaining);

    // Color transitions
    const timerEl = el('banner-timer');
    timerEl.className = 'banner-timer';

    if (remaining <= 0) {
        timerEl.classList.add('timer--ended');
    } else if (isFrozen) {
        timerEl.classList.add('timer--frozen');
    } else if (remainingUntilFreeze <= 300 && remainingUntilFreeze > 0) {
        timerEl.classList.add('timer--critical');
    }
    // else default

    // Auto-freeze transition
    if (!isFrozen && elapsedSec >= freezeAtSec && currentPhase === 'live') {
        isFrozen = true;
        enterFrozenPhase();
    }

    // Auto-end transition
    if (remaining <= 0 && (currentPhase === 'frozen' || currentPhase === 'live')) {
        stopContestTimer();
        enterContestEndedPhase();
    }
}

// ═══════════════════════════════════════════════════════════════════════
//  SETUP & PHASE TRANSITIONS
// ═══════════════════════════════════════════════════════════════════════

async function startSimulation() {
    showLoading('Generating simulation data…');
    hideSetupError();

    try {
        const res = await fetch('/api/simulate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ seed: Math.floor(Math.random() * 10000) }),
        });
        const result = await res.json();
        if (result.error) throw new Error(result.error);

        const standingsData = await API.getStandings();
        if (standingsData.data) {
            contestName = result.contestName || 'Simulation';

            const totalDuration = standingsData.data.contest.durationSeconds || 14400;
            const freezeMin = 60; // last hour is blind
            const freezeAtSecond = totalDuration - (freezeMin * 60);
            const elapsed = standingsData.data.contest.relativeTimeSeconds || 0;

            // In simulation mode, use compression ratio: 4h -> 4min = 60:1
            const simScale = totalDuration / 240;  // 14400 / 240 = 60
            enterLivePhase(standingsData.data, 1, totalDuration, freezeAtSecond, elapsed, simScale);
        } else {
            throw new Error('No standings data received');
        }
    } catch (err) {
        hideLoading();
        showSetupError(err.message);
    }
}

async function startLive() {
    const contestId = el('setup-contest-id').value.trim();
    const durationMin = parseInt(el('setup-duration').value) || 240;
    const freezeMin = parseInt(el('setup-freeze').value) || 60;
    const pollSec = parseInt(el('setup-poll').value) || 30;

    if (!contestId) {
        showSetupError('Please enter a Contest ID');
        return;
    }

    showLoading('Connecting to Codeforces API…');
    hideSetupError();

    try {
        const result = await API.startContest(parseInt(contestId), freezeMin, pollSec);
        if (result.error) throw new Error(result.error);

        const standingsData = await API.getStandings();
        if (standingsData.data) {
            contestName = result.contestName || 'Contest ' + contestId;

            // Calculate elapsed time from CF API if available
            const cfDuration = standingsData.data?.contest?.durationSeconds || (durationMin * 60);
            const cfPhase = standingsData.data?.contest?.phase;

            // Use CF's duration if available, otherwise user input
            const totalDuration = cfDuration || (durationMin * 60);
            const freezeAtSecond = totalDuration - (freezeMin * 60);

            // Determine elapsed time
            let elapsed = 0;
            if (cfPhase === 'FINISHED') {
                // Contest is already over — go straight to ended
                elapsed = totalDuration;
            } else if (standingsData.data?.contest?.relativeTimeSeconds) {
                elapsed = standingsData.data.contest.relativeTimeSeconds;
            }

            enterLivePhase(standingsData.data, pollSec, totalDuration, freezeAtSecond, elapsed);
        } else {
            throw new Error('No standings data received');
        }
    } catch (err) {
        hideLoading();
        showSetupError(err.message);
    }
}

async function loadDemoMode() {
    showLoading('Loading demo data…');
    try {
        const demoData = await API.getDemoData();
        data = demoData;
        contestName = demoData.contest?.name || 'Demo Contest';
        enterRevealPhase(demoData);
    } catch (err) {
        hideLoading();
        showSetupError('Demo data error: ' + err.message);
    }
}

function loadFileMode() {
    const input = el('setup-file-input');
    input.onchange = async (e) => {
        const f = e.target.files[0]; if (!f) return;
        showLoading('Loading ' + f.name + '…');
        try {
            const fileData = JSON.parse(await f.text());
            data = fileData;
            contestName = fileData.contest?.name || f.name;
            if (fileData.blindHourSubmissions) {
                enterRevealPhase(fileData);
            } else {
                throw new Error('Invalid format: missing blindHourSubmissions');
            }
        } catch (err) {
            hideLoading();
            showSetupError('Invalid JSON: ' + err.message);
        }
    };
    input.click();
}

function enterLivePhase(standingsData, pollInterval, durationSec, freezeAtSecond, elapsedSec, scale = 1) {
    currentPhase = 'live';
    el('setup-screen').style.display = 'none';
    hideLoading();

    el('banner').style.display = '';
    el('banner').className = 'banner banner--live';
    el('live-dot').style.display = '';
    el('banner-icon').style.display = 'none';
    el('banner-text').textContent = 'LIVE — SCOREBOARD UPDATING IN REAL TIME';

    el('contest-title').textContent = contestName;
    el('contest-subtitle').textContent = 'Live Scoreboard';

    el('app').style.display = 'block';
    document.body.classList.add('fullscreen');
    startAutoScroll();

    el('controls-live').style.display = 'flex';
    el('controls-frozen').style.display = 'none';
    el('controls-ended').style.display = 'none';
    el('controls-reveal').style.display = 'none';
    el('progress-track').style.display = 'none';
    el('mvp-section').style.display = 'none';

    el('poll-status').textContent = 'Auto-refresh: ' + pollInterval + 's';
    el('s-subs-label').textContent = 'Problems';
    el('s-rc-label').textContent = 'Phase';

    renderLiveStandings(standingsData);

    // Start timer
    if (durationSec && durationSec > 0) {
        startContestTimer(durationSec, freezeAtSecond, elapsedSec, scale);

        // Check if we should already be frozen or ended
        if (elapsedSec >= durationSec) {
            // Contest already finished
            stopContestTimer();
            enterContestEndedPhase();
            return;
        } else if (elapsedSec >= freezeAtSecond) {
            // Already in freeze period
            isFrozen = true;
            enterFrozenPhase();
            return;
        }
    }

    if (livePoller) clearInterval(livePoller);
    livePoller = setInterval(() => pollLiveStandings(), pollInterval * 1000);
}

function enterFrozenPhase() {
    if (currentPhase === 'frozen') return; // prevent double-entry
    currentPhase = 'frozen';
    isFrozen = true;

    // Stop live polling only if not in sim mode (sim needs to keep polling to detect contest end)
    if (livePoller && timeScale <= 1) { clearInterval(livePoller); livePoller = null; }

    el('banner').className = 'banner banner--freeze';
    el('live-dot').style.display = 'none';
    el('banner-icon').style.display = '';
    el('banner-text').textContent = 'SCOREBOARD FROZEN — BLIND HOUR IN PROGRESS';
    el('contest-subtitle').textContent = 'Scoreboard Frozen';

    // Ensure auto-scroll continues in frozen phase
    startAutoScroll();

    el('controls-live').style.display = 'none';
    el('controls-frozen').style.display = 'flex';
    el('controls-ended').style.display = 'none';
    el('controls-reveal').style.display = 'none';

    snd('freeze');
    toast('⏸ Scoreboard frozen! Blind hour has begun.', 'pending');
}

function enterContestEndedPhase() {
    currentPhase = 'ended';
    stopContestTimer();

    el('banner').className = 'banner banner--ended';
    el('live-dot').style.display = 'none';
    el('banner-icon').style.display = '';
    el('banner-text').textContent = 'CONTEST ENDED — READY FOR BLIND HOUR REVEAL';
    el('contest-subtitle').textContent = 'Contest Ended — Ready for Reveal';

    // Show ended time
    el('timer-display').textContent = '00:00:00';
    el('banner-timer').className = 'banner-timer timer--ended';

    el('controls-live').style.display = 'none';
    el('controls-frozen').style.display = 'none';
    el('controls-ended').style.display = 'flex';
    el('controls-reveal').style.display = 'none';

    snd('end');
    toast('🏁 Contest has ended! Click "Start Blind Hour Reveal" to begin.', 'accepted');
}

function enterRevealPhase(revealData) {
    currentPhase = 'reveal';
    data = revealData;
    el('setup-screen').style.display = 'none';
    hideLoading();
    stopContestTimer();
    stopAutoScroll();
    document.body.classList.remove('fullscreen'); // Reveal phase uses standard layout or focused layout? 
    // User wants "keep doing that until the end of the contest". Reveal is post-contest.
    // Let's keep fullscreen off for reveal as it's interactive.

    // Hide timer during reveal
    el('banner-timer').style.display = 'none';

    el('banner').style.display = '';
    el('banner').className = 'banner banner--freeze';
    el('live-dot').style.display = 'none';
    el('banner-icon').style.display = '';
    el('banner-text').textContent = 'SCOREBOARD FROZEN — BLIND HOUR AWAITS';

    el('contest-title').textContent = revealData.contest?.name || contestName;
    el('contest-subtitle').textContent = 'Blind Hour Reveal';
    el('awards-heading').textContent = revealData.contest?.name || contestName;

    el('app').style.display = 'block';

    el('controls-live').style.display = 'none';
    el('controls-frozen').style.display = 'none';
    el('controls-ended').style.display = 'none';
    el('controls-reveal').style.display = 'flex';
    el('progress-track').style.display = '';
    el('mvp-section').style.display = 'none';
    el('s-subs-label').textContent = 'Blind Hour Subs';
    el('s-rc-label').textContent = 'Rank Changes';

    resetReveal();
}

// ═══════════════════════════════════════════════════════════════════════
//  LIVE PHASE ACTIONS
// ═══════════════════════════════════════════════════════════════════════

async function pollLiveStandings() {
    try {
        const result = await API.getStandings();

        // Handle ENDED phase transition
        if (result.phase === 'ended' || result.phase === 'finished') {
            if (livePoller) clearInterval(livePoller);
            livePoller = null;
            enterContestEndedPhase();
            return;
        }

        if (result.phase === 'frozen') {
            // In sim mode, keep polling to detect contest end
            if (timeScale <= 1) {
                if (livePoller) clearInterval(livePoller);
                livePoller = null;
            }
            if (currentPhase !== 'frozen') {
                enterFrozenPhase();
            }
            // Update standings even while frozen (for accepted count etc)
            if (result.data) {
                renderLiveStandings(result.data);
            }
            return;
        }
        if (result.phase === 'live' && result.data) {
            renderLiveStandings(result.data);
        }
    } catch (err) {
        console.warn('Poll error:', err);
    }
}

async function manualRefresh() {
    toast('Refreshing standings…', 'pending');
    await pollLiveStandings();
    toast('Standings updated', 'accepted');
}

async function doFreeze() {
    showLoading('Freezing scoreboard…');
    try {
        await API.freezeContest();
        if (livePoller) clearInterval(livePoller);
        livePoller = null;
        hideLoading();
        enterFrozenPhase();
    } catch (err) {
        hideLoading();
        toast('Freeze failed: ' + err.message, 'rejected');
    }
}

async function doReset() {
    if (!confirm("Reset everything and go back to setup?")) return;
    stopContestTimer();
    try {
        await API.resetContest();
        location.reload();
    } catch (err) {
        console.error(err);
        location.reload();
    }
}

async function doStartReveal() {
    showLoading('Building reveal data... This may take a min.');
    try {
        await API.revealContest();
        const standingsData = await API.getStandings();
        if (standingsData.phase === 'reveal' && standingsData.data) {
            enterRevealPhase(standingsData.data);
        } else {
            throw new Error('Reveal data not ready');
        }
    } catch (err) {
        hideLoading();
        toast('Reveal failed: ' + err.message, 'rejected');
    }
}

// ═══════════════════════════════════════════════════════════════════════
//  RENDERING & REVEAL ENGINE
// ═══════════════════════════════════════════════════════════════════════

function renderLiveStandings(standingsData) {
    if (!standingsData || !standingsData.contestants) return;
    problems = standingsData.problems || [];
    const contestants = standingsData.contestants;

    el('s-contestants').textContent = contestants.length;
    el('s-subs').textContent = problems.length;
    el('s-accepted').textContent = contestants.reduce((sum, c) => sum + c.solved, 0);
    el('s-rankchanges').textContent = standingsData.contest?.phase || 'LIVE';

    const colP = el('col-problems');
    colP.innerHTML = '';
    const probW = problems.length * 90;
    document.documentElement.style.setProperty('--prob-cols-width', probW + 'px');
    problems.forEach(p => {
        const s = document.createElement('span');
        s.style.cssText = 'display:inline-block;width:84px;text-align:center;font-size:21px;';
        s.textContent = p.index; s.title = p.name;
        colP.appendChild(s);
    });

    const body = el('board-body');
    body.innerHTML = '';
    body.style.height = (contestants.length * ROW_H) + 'px';
    document.documentElement.style.setProperty('--row-h', ROW_H + 'px');

    contestants.forEach((c, idx) => {
        const div = document.createElement('div');
        div.className = 'row';
        div.style.transform = 'translateY(' + (idx * ROW_H) + 'px)';
        div.innerHTML = liveRowHTML(c, idx + 1);
        body.appendChild(div);
    });
}

function liveRowHTML(c, displayRank) {
    const rank = c.rank || displayRank;
    const rCls = rank <= 3 ? 'rank-badge--' + rank : 'rank-badge--default';
    const badge = `<div class="rank-badge ${rCls}">${rank}</div>`;
    const delta = '<div class="delta delta--none">&mdash;</div>';

    let probsHtml = '<div class="prob-cells">';
    problems.forEach(p => {
        const pr = c.problemResults?.[p.index];
        let cls = 'prob--empty', label = '\u00B7';
        if (pr?.solved) {
            cls = 'prob--solved';
            label = fmtTime(pr.time);
        } else if (pr?.rejectedAttempts > 0) {
            cls = 'prob--failed';
            label = '-' + pr.rejectedAttempts;
        }
        probsHtml += `<div class="prob ${cls}">${label}</div>`;
    });
    probsHtml += '</div>';

    return `<div>${badge}</div><div>${delta}</div><div><span class="handle">${c.handle}</span></div>` +
        `<div><span class="solved-val">${c.solved}</span></div><div><span class="penalty-val">${c.penalty}</span></div>` +
        `<div>${probsHtml}</div>`;
}

const rowEls = {};

function resetReveal() {
    stopAuto(); processing = false;
    if (!data) return;

    problems = data.problems || [];
    state = data.contestants.map(c => ({
        ...c,
        rank: c.freezeRank,
        solved: c.freezeSolvedCount,
        penalty: c.freezePenalty,
        probs: JSON.parse(JSON.stringify(c.problemResultsAtFreeze)),
        delta: 0,
    }));

    queue = [...data.blindHourSubmissions];
    revealed = 0; acceptedCount = 0; rankChangeCount = 0;
    mvp = { handle: '', delta: 0 };
    lastMinuteHero = { handle: '', problemIndex: '', problemName: '', time: 0 };
    submissionCounts = {};
    mostPersistent = { handle: '', problemIndex: '', problemName: '', count: 0 };
    revealFinished = false;
    el('awards-overlay').classList.remove('show');

    const acc = queue.filter(s => s.verdict === 'OK').length;
    el('s-contestants').textContent = state.length;
    el('s-subs').textContent = queue.length;
    el('s-accepted').textContent = acc;
    el('s-rankchanges').textContent = '\u2014';

    const colP = el('col-problems');
    colP.innerHTML = '';
    const probW = problems.length * 77;
    document.documentElement.style.setProperty('--prob-cols-width', probW + 'px');
    problems.forEach(p => {
        const s = document.createElement('span');
        s.style.cssText = 'display:inline-block;width:72px;text-align:center;font-size:21px;';
        s.textContent = p.index; s.title = p.name;
        colP.appendChild(s);
    });

    buildRows();

    el('banner').style.display = '';
    el('banner').className = 'banner banner--freeze';
    el('banner-text').textContent = 'SCOREBOARD FROZEN — BLIND HOUR AWAITS';
    el('progress').style.width = '0';
    el('mvp-section').style.display = 'none';

    el('btn-start').disabled = false;
    el('btn-next').disabled = true;
    el('btn-auto').disabled = true;
    el('btn-all').disabled = true;
}

function buildRows() {
    const body = el('board-body');
    body.innerHTML = '';
    body.style.height = (state.length * ROW_H) + 'px';
    document.documentElement.style.setProperty('--row-h', ROW_H + 'px');

    const sorted = [...state].sort((a, b) => a.rank - b.rank);
    Object.keys(rowEls).forEach(k => delete rowEls[k]);

    sorted.forEach(c => {
        const div = document.createElement('div');
        div.className = 'row';
        div.id = 'row-' + c.handle;
        div.style.transform = 'translateY(' + ((c.rank - 1) * ROW_H) + 'px)';
        div.innerHTML = rowHTML(c);
        body.appendChild(div);
        rowEls[c.handle] = div;
    });
}

function rowHTML(c, pendingProb) {
    const rCls = c.rank <= 3 ? 'rank-badge--' + c.rank : 'rank-badge--default';
    const rank = `<div class="rank-badge ${rCls}" id="badge-${c.handle}">${c.rank}</div>`;

    let delta;
    if (c.delta > 0) delta = `<div class="delta delta--up">▲ ${c.delta}</div>`;
    else if (c.delta < 0) delta = `<div class="delta delta--down">▼ ${Math.abs(c.delta)}</div>`;
    else delta = `<div class="delta delta--none">&mdash;</div>`;

    let probsHtml = '<div class="prob-cells">';
    problems.forEach(p => {
        let cls = 'prob--empty', label = '\u00B7';
        if (pendingProb === p.index) {
            cls = 'prob--judging'; label = '...';
        } else if (c.probs[p.index]?.solved) {
            cls = 'prob--solved'; label = fmtTime(c.probs[p.index].time);
        } else if (c.probs[p.index]?.failed) {
            cls = 'prob--failed'; label = '-';
        }
        probsHtml += `<div class="prob ${cls}" id="p-${c.handle}-${p.index}">${label}</div>`;
    });
    probsHtml += '</div>';

    return `<div>${rank}</div><div>${delta}</div><div><span class="handle">${c.handle}</span></div>` +
        `<div><span class="solved-val">${c.solved}</span></div><div><span class="penalty-val">${c.penalty}</span></div>` +
        `<div>${probsHtml}</div>`;
}

function updateRow(handle, pendingProb) {
    const c = state.find(x => x.handle === handle);
    if (!c || !rowEls[handle]) return;
    rowEls[handle].innerHTML = rowHTML(c, pendingProb);
}

function updateAllRows() {
    state.forEach(c => {
        if (rowEls[c.handle]) rowEls[c.handle].innerHTML = rowHTML(c);
    });
}

function repositionAll() {
    state.forEach(c => {
        if (rowEls[c.handle]) rowEls[c.handle].style.transform = 'translateY(' + ((c.rank - 1) * ROW_H) + 'px)';
    });
}

function startRevealAnimation() {
    el('banner').className = 'banner banner--reveal';
    el('banner-text').textContent = 'BLIND HOUR REVEAL IN PROGRESS';
    el('btn-start').disabled = true;
    el('btn-next').disabled = false;
    el('btn-auto').disabled = false;
    el('btn-all').disabled = false;
    snd('climb');
}

async function nextSubmission() {
    if (processing) return;
    if (revealFinished) { showAwards(); return; }
    if (queue.length === 0) { finishReveal(); return; }

    processing = true;
    lockUI(true);

    const sub = queue.shift();
    revealed++;
    el('progress').style.width = (revealed / (revealed + queue.length) * 100) + '%';

    const c = state.find(x => x.handle === sub.handle);
    if (!c) { processing = false; lockUI(false); return; }

    submissionCounts[sub.handle + '|' + sub.problemIndex] = (submissionCounts[sub.handle + '|' + sub.problemIndex] || 0) + 1;
    const thisCount = submissionCounts[sub.handle + '|' + sub.problemIndex];
    if (thisCount > mostPersistent.count) {
        mostPersistent = { handle: sub.handle, problemIndex: sub.problemIndex, problemName: sub.problemName, count: thisCount };
    }

    // PENDING
    snd('pending');
    toast(`${sub.handle} submitted ${sub.problemIndex} (${sub.problemName}) — Judging`, 'pending');
    updateRow(c.handle, sub.problemIndex);
    rowEls[c.handle].classList.add('row--pending');
    scrollTo(c.handle);

    await sleep(PENDING_MS);
    rowEls[c.handle].classList.remove('row--pending');

    // VERDICT
    if (sub.verdict === 'OK') {
        acceptedCount++;
        c.probs[sub.problemIndex] = { solved: true, time: sub.relativeTimeSec };
        c.solved++;
        const waBefore = sub.wrongAttemptsBefore || 0;
        c.penalty += Math.floor(sub.relativeTimeSec / 60) + WA_PENALTY_MINUTES * waBefore;

        snd('accepted');
        toast(`${sub.handle} — ${sub.problemIndex}: Accepted ✓`, 'accepted');
        lastMinuteHero = { handle: sub.handle, problemIndex: sub.problemIndex, problemName: sub.problemName, time: sub.relativeTimeSec };

        updateRow(c.handle);
        rowEls[c.handle].classList.add('row--accepted');
        const cell = document.getElementById('p-' + c.handle + '-' + sub.problemIndex);
        if (cell) cell.classList.add('pop-in');

        await sleep(VERDICT_MS);
        rowEls[c.handle].classList.remove('row--accepted');

        // CLIMBING
        const oldRank = c.rank;
        recalcRanks();
        const newRank = c.rank;
        const climb = oldRank - newRank;

        if (climb > 0) {
            rankChangeCount++;
            if (c.delta > mvp.delta) mvp = { handle: c.handle, delta: c.delta };
            snd('climb');
            toast(`${sub.handle} — Rank ${oldRank} → ${newRank}`, 'accepted');
            rowEls[c.handle].classList.add('row--climbing');

            for (let step = 0; step < climb; step++) {
                const intermediateRank = oldRank - step - 1;
                rowEls[c.handle].style.transform = 'translateY(' + ((intermediateRank - 1) * ROW_H) + 'px)';
                const displaced = state.find(x => x.handle !== c.handle && x.rank === intermediateRank);
                if (displaced && rowEls[displaced.handle]) {
                    rowEls[displaced.handle].style.transform = 'translateY(' + (intermediateRank * ROW_H) + 'px)';
                }
                await sleep(CLIMB_STEP_MS);
            }

            updateAllRows();
            repositionAll();
            rowEls[c.handle].classList.remove('row--climbing');
            const badge = document.getElementById('badge-' + c.handle);
            if (badge) { badge.classList.add('pop'); setTimeout(() => badge.classList.remove('pop'), 600); }
            await sleep(400);
        } else {
            updateAllRows();
            repositionAll();
        }
        scrollTo(c.handle);
    } else {
        // REJECTED
        if (!c.probs[sub.problemIndex]?.solved) {
            c.probs[sub.problemIndex] = { solved: false, failed: true, time: 0 };
        }
        snd('rejected');
        toast(`${sub.handle} — ${sub.problemIndex}: Rejected`, 'rejected');
        updateRow(c.handle);
        rowEls[c.handle].classList.add('row--rejected');
        const cell = document.getElementById('p-' + c.handle + '-' + sub.problemIndex);
        if (cell) cell.classList.add('shake');
        await sleep(800);
        rowEls[c.handle].classList.remove('row--rejected');
    }

    el('s-rankchanges').textContent = rankChangeCount || '\u2014';
    processing = false;
    lockUI(false);
}

function lockUI(locked) {
    el('btn-next').style.opacity = locked ? '.35' : '';
    el('btn-next').style.pointerEvents = locked ? 'none' : '';
}

function recalcRanks() {
    const sorted = [...state].sort((a, b) => b.solved !== a.solved ? b.solved - a.solved : a.penalty - b.penalty);
    sorted.forEach((c, i) => { c.rank = i + 1; c.delta = c.freezeRank - c.rank; });
}

function scrollTo(handle) {
    const r = rowEls[handle];
    if (r) r.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function autoPlay() {
    if (playing) { stopAuto(); return; }
    playing = true;
    el('btn-auto').innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><rect x="5" y="4" width="4" height="16"/><rect x="15" y="4" width="4" height="16"/></svg> Pause';
    el('btn-auto').classList.remove('btn--ghost'); el('btn-auto').classList.add('btn--primary');
    autoTick();
}

async function autoTick() {
    if (!playing || queue.length === 0) { stopAuto(); if (queue.length === 0) finishReveal(); return; }
    await nextSubmission();
    if (!playing) return;
    autoTimer = setTimeout(autoTick, parseInt(el('speed-slider').value));
}

function stopAuto() {
    playing = false; clearTimeout(autoTimer);
    el('btn-auto').innerHTML = '<svg viewBox="0 0 24 24"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" fill="currentColor"/></svg> Auto Play';
    el('btn-auto').classList.remove('btn--primary'); el('btn-auto').classList.add('btn--ghost');
}

async function revealAll() {
    stopAuto();
    if (!confirm('Are you sure you want to skip all animations and show final results?')) return;

    try {
        // Snapshot old ranks for MVP tracking
        const oldRanks = {};
        state.forEach(c => { oldRanks[c.handle] = c.rank; });

        let processed = 0;
        while (queue.length > 0) {
            const sub = queue.shift();
            processed++;
            revealed++;
            const c = state.find(x => x.handle === sub.handle);
            if (!c) {
                console.warn('Contestant not found for handle:', sub.handle);
                continue;
            }

            // Track submission counts for "Mr. Not Give Up"
            const key = sub.handle + '|' + sub.problemIndex;
            submissionCounts[key] = (submissionCounts[key] || 0) + 1;
            if (submissionCounts[key] > mostPersistent.count) {
                mostPersistent = { handle: sub.handle, problemIndex: sub.problemIndex, problemName: sub.problemName, count: submissionCounts[key] };
            }

            if (sub.verdict === 'OK') {
                c.probs[sub.problemIndex] = { solved: true, time: sub.relativeTimeSec };
                c.solved++;
                const wa = sub.wrongAttemptsBefore || 0;
                c.penalty += Math.floor(sub.relativeTimeSec / 60) + WA_PENALTY_MINUTES * wa;
                acceptedCount++;

                // Track "Last-Minute Hero"
                if (sub.relativeTimeSec > lastMinuteHero.time) {
                    lastMinuteHero = { handle: sub.handle, problemIndex: sub.problemIndex, problemName: sub.problemName, time: sub.relativeTimeSec };
                }
            }
        }

        recalcRanks();

        // Calculate MVP / Hill Climber and count rank changes
        state.forEach(c => {
            const delta = (oldRanks[c.handle] || c.rank) - c.rank;
            if (delta !== 0) rankChangeCount++;
            if (delta > mvp.delta) {
                mvp = { handle: c.handle, delta };
            }
        });

        buildRows();
        finishReveal();
    } catch (e) {
        console.error('revealAll error:', e);
        alert('Error revealing all: ' + e.message);
    }
}

function finishReveal() {
    revealFinished = true;
    el('banner').className = 'banner banner--final';
    el('banner-text').textContent = 'CONTEST FINISHED — FINAL RESULTS';
    el('btn-auto').disabled = true;
    el('btn-next').disabled = false;
    el('btn-next').innerHTML = '🏆 Show Awards';

    // Update stats display
    if (el('s-subs')) el('s-subs').textContent = revealed;
    if (el('s-accepted')) el('s-accepted').textContent = acceptedCount;
    if (el('s-rankchanges')) el('s-rankchanges').textContent = rankChangeCount || '\u2014';

    toast('Reveal complete! Click "Show Awards" to see podium.', 'accepted');
    snd('fanfare');
    fireConfetti();

    if (mvp.handle && mvp.delta > 0) {
        el('mvp-section').style.display = 'block';
        el('mvp-name').textContent = mvp.handle;
        el('mvp-detail').textContent = `Rocketed up ${mvp.delta} ranks during blind hour!`;
    }
}

function showAwards() {
    el('awards-overlay').classList.add('show');
    snd('fanfare');
    fireConfetti();

    const top3 = [...state].sort((a, b) => a.rank - b.rank).slice(0, 3);
    const podiumFn = (c, i) => `
        <div class="podium-slot">
            <div class="podium-avatar">${c.handle.substring(0, 2).toUpperCase()}</div>
            <div class="podium-name">${c.handle}</div>
            <div class="podium-stats">${c.solved} solved / ${c.penalty}</div>
            <div class="podium-bar podium-bar--${i + 1}">${i + 1}</div>
        </div>`;

    let html = '';
    if (top3[1]) html += podiumFn(top3[1], 1); // 2nd place
    if (top3[0]) html += podiumFn(top3[0], 0); // 1st place
    if (top3[2]) html += podiumFn(top3[2], 2); // 3rd place

    el('podium').innerHTML = html;

    // Build honorable mention badges
    let badgesHtml = '';

    // Hill Climber — biggest rank jump
    if (mvp.handle && mvp.delta > 0) {
        badgesHtml += `
        <div class="award-card award-card--climb">
            <div class="award-card-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="var(--badge-accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/>
                    <polyline points="17 6 23 6 23 12"/>
                </svg>
            </div>
            <div class="award-card-ribbon"><span></span><span></span></div>
            <div class="award-card-text">
                <div class="award-card-label">Hill Climber</div>
                <div class="award-card-name">${mvp.handle}</div>
                <div class="award-card-detail">Leaped up ${mvp.delta} ranks</div>
            </div>
        </div>`;
    }

    // Mr. Not Give Up — most submissions on a single problem
    if (mostPersistent.handle && mostPersistent.count > 1) {
        badgesHtml += `
        <div class="award-card award-card--grit">
            <div class="award-card-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="var(--badge-accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2z"/>
                    <path d="M12 6v6l4.5 2.5"/>
                </svg>
            </div>
            <div class="award-card-ribbon"><span></span><span></span></div>
            <div class="award-card-text">
                <div class="award-card-label">Mr. Not Give Up</div>
                <div class="award-card-name">${mostPersistent.handle}</div>
                <div class="award-card-detail">${mostPersistent.count} attempts on ${mostPersistent.problemIndex} (${mostPersistent.problemName})</div>
            </div>
        </div>`;
    }

    // Last-Minute Hero — last person to solve a problem
    if (lastMinuteHero.handle) {
        const timeStr = fmtTime(lastMinuteHero.time);
        badgesHtml += `
        <div class="award-card award-card--hero">
            <div class="award-card-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="var(--badge-accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
                </svg>
            </div>
            <div class="award-card-ribbon"><span></span><span></span></div>
            <div class="award-card-text">
                <div class="award-card-label">Last-Minute Hero</div>
                <div class="award-card-name">${lastMinuteHero.handle}</div>
                <div class="award-card-detail">Solved ${lastMinuteHero.problemIndex} at ${timeStr}</div>
            </div>
        </div>`;
    }

    el('awards-badges').innerHTML = badgesHtml;
}

function closeAwards() {
    el('awards-overlay').classList.remove('show');
}

// ═══════════════════════════════════════════════════════════════════════
//  UI HELPERS
// ═══════════════════════════════════════════════════════════════════════

let scrollInterval = null;
let scrollPausing = false;

function startAutoScroll() {
    if (scrollInterval) return;
    const body = el('board-body');
    const speed = 1; // px per tick
    const tick = 30; // ms

    scrollInterval = setInterval(() => {
        if (scrollPausing) return;

        // If content fits, no scroll needed
        if (body.scrollHeight <= body.clientHeight) return;

        body.scrollTop += speed;

        // Check if reached bottom with buffer
        if (body.scrollTop + body.clientHeight >= body.scrollHeight - 2) {
            scrollPausing = true;
            setTimeout(() => {
                // Jump to top
                body.scrollTo({ top: 0, behavior: 'smooth' });
                // pause at top
                setTimeout(() => { scrollPausing = false; }, 4000);
            }, 2000);
        }
    }, tick);
}

function stopAutoScroll() {
    if (scrollInterval) {
        clearInterval(scrollInterval);
        scrollInterval = null;
    }
}

function showLoading(msg) {
    el('loading-text').innerText = msg;
    el('loading').style.display = 'flex';
}
function hideLoading() {
    el('loading').style.display = 'none';
}
function showSetupError(msg) {
    const e = el('setup-error'); e.textContent = msg; e.style.display = 'block';
}
function hideSetupError() {
    el('setup-error').style.display = 'none';
}
function toast(msg, type = 'accepted') {
    const t = el('toast');
    t.className = `toast toast--${type} show`;
    t.innerHTML = `<svg viewBox="0 0 24 24" class="toast-icon">
        <circle cx="12" cy="12" r="10" fill="currentColor" fill-opacity=".2"/>
        <path d="M12 6v6l4 2" stroke="currentColor" stroke-width="2" fill="none"/>
    </svg> ${msg}`;
    setTimeout(() => t.classList.remove('show'), 3500);
}

// ═══════════════════════════════════════════════════════════════════════
//  INIT & EXPORTS
// ═══════════════════════════════════════════════════════════════════════

// Attach global functions for HTML event handlers
window.startSimulation = startSimulation;
window.startLive = startLive;
window.loadDemoMode = loadDemoMode;
window.loadFileMode = loadFileMode;
window.manualRefresh = manualRefresh;
window.doFreeze = doFreeze;
window.doReset = doReset;
window.doStartReveal = doStartReveal;
window.startRevealAnimation = startRevealAnimation;
window.nextSubmission = nextSubmission;
window.autoPlay = autoPlay;
window.revealAll = revealAll;
window.resetReveal = resetReveal;
window.closeAwards = closeAwards;

// Initial Setup
const speedSlider = el('speed-slider');
if (speedSlider) {
    speedSlider.oninput = () => el('speed-val').textContent = (speedSlider.value / 1000) + 's';
}

// Check if we are already in a phase
(async () => {
    try {
        const p = await API.getPhase();
        if (p.phase === 'live') {
            const s = await API.getStandings();
            contestName = p.contestName;
            const durationSec = s.data?.contest?.durationSeconds || 0;
            const freezeMin = p.freezeMinutes || 60;
            const freezeAt = durationSec - (freezeMin * 60);
            const elapsed = s.data?.contest?.relativeTimeSeconds || 0;
            enterLivePhase(s.data, p.pollInterval, durationSec, freezeAt, elapsed);
        } else if (p.phase === 'frozen') {
            const s = await API.getStandings();
            contestName = p.contestName;
            // Enter frozen with timer showing remaining
            const durationSec = s.data?.contest?.durationSeconds || 0;
            const freezeMin = p.freezeMinutes || 60;
            const freezeAt = durationSec - (freezeMin * 60);
            const elapsed = s.data?.contest?.relativeTimeSeconds || durationSec;
            enterLivePhase(s.data, p.pollInterval || 30, durationSec, freezeAt, elapsed);
        } else if (p.phase === 'reveal') {
            const s = await API.getStandings();
            enterRevealPhase(s.data);
        }
    } catch (e) {
        console.log("No active session or API error", e);
    }
})();
