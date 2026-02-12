#!/usr/bin/env python3
"""
Blind Hour Reveal — Flask Application
=======================================
A real-time competitive-programming scoreboard with three operational phases:

  Phase 1 — LIVE:    Real-time scoreboard, polls Codeforces API periodically.
  Phase 2 — FROZEN:  Board locks at freeze time, shows "FROZEN" banner.
  Phase 3 — REVEAL:  The existing Blind Hour Reveal engine takes over.

Usage:
    python app.py                          # start on port 5000
    python app.py --port 8080              # custom port
    python app.py --debug                  # Flask debug mode
"""

import argparse
import json
import threading
import time as _time
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory, abort

# Import core logic from backend package
from backend import core
from backend.generate_sample import generate_sample_data

# ═══════════════════════════════════════════════════════════════════════
#  PHASE STATE MACHINE
# ═══════════════════════════════════════════════════════════════════════

class ContestState:
    """
    Manages the lifecycle of a tracked contest:
      setup → live → frozen → reveal

    The background poller thread updates standings during LIVE phase.
    """

    PHASES = ("setup", "live", "frozen", "reveal")

    def __init__(self):
        self.phase: str = "setup"
        self.contest_id: int | None = None
        self.contest_name: str = ""
        self.freeze_minutes: int = 60
        self.poll_interval: int = 30       # seconds between API polls
        self.problems: list[dict] = []
        self.duration_seconds: int = 0

        # Live standings (updated by poller)
        self.live_standings: dict = {}
        self.last_poll_time: float = 0
        self.poll_error: str | None = None

        # Reveal data (built once when entering reveal phase)
        self.reveal_data: dict | None = None
        
        # Cache for simple stateAtTime requests (optional enhancement)
        # For now we will fetch submissions on demand or use cached ones if already fetching
        self.cached_submissions: list[dict] = []
        self.cached_rows: list[dict] = []

        # Background poller
        self._poller_thread: threading.Thread | None = None
        self._poller_stop = threading.Event()
        self._lock = threading.Lock()

        # ── Simulation state ──────────────────────────────────────────
        self.sim_mode: bool = False
        self.sim_data: dict | None = None        # full generated data
        self.sim_start_real: float = 0            # real-time when sim started
        self.sim_compression: float = 60.0        # ratio: 60 sim-sec per 1 real-sec
        self.sim_all_subs: list[dict] = []        # all submissions sorted by time
        self.sim_freeze_sec: int = 0              # freeze time in sim seconds

    def start_contest(self, contest_id: int, freeze_minutes: int, poll_interval: int = 30):
        """Initialize tracking for a contest → enter LIVE phase."""
        self.contest_id = contest_id
        self.freeze_minutes = freeze_minutes
        self.poll_interval = max(15, poll_interval)  # minimum 15s
        self.phase = "live"
        self.reveal_data = None
        self.poll_error = None

        # Do an initial fetch using core logic
        try:
            # We use fetch_standings from core to get live data
            standings_data = core.fetch_standings(contest_id)
            
            # Helper to format live standings similar to what frontend expects
            # In live phase, we just want current rank/score.
            # We can reuse build_reveal_data logic BUT with freeze_time = duration (effectively no freeze)
            # OR just parse the standings manually. 
            # Let's stick to the simple parsing for live view as it is faster (no submission replay needed).
             
            self._update_live_state(standings_data)
            
        except Exception as e:
            self.poll_error = str(e)
            print(f"❌ Initial fetch failed: {e}")

        # Start background poller
        self._poller_stop.clear()
        self._poller_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poller_thread.start()
        print(f"🟢 LIVE — tracking contest {contest_id} (poll every {self.poll_interval}s)")

    def _update_live_state(self, standings_data: dict):
        """Update internal state from raw standings data."""
        contest_meta = standings_data["contest"]
        problems = standings_data["problems"]
        rows = standings_data["rows"]
        
        with self._lock:
            self.contest_name = contest_meta.get("name", f"Contest {self.contest_id}")
            self.duration_seconds = contest_meta.get("durationSeconds", 0)
            self.problems = problems
            
            # Format rows for frontend "live" view
            # The frontend expects structure similar to 'contestants' in reveal data
            # but we can simplify since we don't need replay details yet.
            
            problem_indices = [p["index"] for p in problems]
            contestants = []
            for row in rows:
                handle = core.get_handle(row["party"])
                
                # Build simple problem results
                formatted_probs = {}
                for i, pr in enumerate(row.get("problemResults", [])):
                    idx = problem_indices[i] if i < len(problem_indices) else str(i)
                    formatted_probs[idx] = {
                        "solved": pr.get("points", 0) > 0,
                        "time": pr.get("bestSubmissionTimeSeconds", 0),
                        "rejectedAttempts": pr.get("rejectedAttemptCount", 0),
                    }
                
                contestants.append({
                    "handle": handle,
                    "rank": row.get("rank", 0),
                    "solved": row.get("points", 0), # codeforces api returns points=solved_count for ICPC usually? 
                    # actually for ICPC 'points' in CF API usually matches solved count, but let's re-calculate to be safe if needed.
                    # Actually standard CF API for ICPC returns points = solved count.
                    "penalty": row.get("penalty", 0),
                    "problemResults": formatted_probs
                })

            self.live_standings = {
                "contest": {
                    "id": self.contest_id,
                    "name": self.contest_name,
                    "durationSeconds": self.duration_seconds,
                    "phase": contest_meta.get("phase", ""),
                    "relativeTimeSeconds": contest_meta.get("relativeTimeSeconds", 0),
                },
                "problems": [
                    {"index": p["index"], "name": p.get("name", p["index"])}
                    for p in problems
                ],
                "contestants": contestants,
            }
            self.last_poll_time = _time.time()
            
            # Cache rows for other queries
            self.cached_rows = rows


    def freeze(self):
        """Transition to FROZEN phase — stop polling, lock the board."""
        if self.phase != "live":
            return
        self.phase = "frozen"
        self._stop_poller()
        print("🔒 FROZEN — scoreboard locked")

    def start_reveal(self):
        """Transition to REVEAL phase — build reveal data from CF API."""
        if self.phase not in ("frozen", "live"):
            return

        self._stop_poller()
        self.phase = "reveal"
        self.poll_error = None  # Clear any previous polling errors
        print("🎬 REVEAL — building reveal data…")

        try:
            self.reveal_data = core.build_reveal_data(self.contest_id, self.freeze_minutes)
            
            # Also cache submissions/rows if we have them now, for stateAtTime to use efficiently
            # build_reveal_data fetches them internally, but we might want them available on self.
            # For now, let's just let stateAtTime refetch or we can optimize later.
            
            print(f"  ✓ Reveal data ready: {len(self.reveal_data['contestants'])} contestants, "
                  f"{len(self.reveal_data['blindHourSubmissions'])} blind-hour subs")
        except Exception as e:
            self.poll_error = str(e)
            print(f"❌ Reveal data build failed: {e}")

    def reset(self):
        """Go back to setup phase."""
        self._stop_poller()
        self.phase = "setup"
        self.live_standings = {}
        self.reveal_data = None
        self.poll_error = None
        self.cached_rows = []
        self.cached_submissions = []
        self.sim_mode = False
        self.sim_data = None
        self.sim_all_subs = []
        print("⏹ RESET — back to setup")

    # ── Simulation ────────────────────────────────────────────────────

    def start_simulation(self, seed: int = 42):
        """Start a simulated contest: 40 contestants, 4h, last 1h blind."""
        self.reset()
        self.sim_mode = True

        # Generate data
        data = generate_sample_data(seed=seed, n_contestants=40,
                                    duration_min=240, freeze_min=180, n_problems=7)
        self.sim_data = data
        self.contest_id = 0
        self.contest_name = data["contest"]["name"]
        self.duration_seconds = data["contest"]["durationSeconds"]
        self.freeze_minutes = 180
        self.sim_freeze_sec = data["contest"]["freezeTimeSeconds"]
        self.problems = data["problems"]
        self.sim_all_subs = data.get("allSubmissions", [])

        # Compression: 4h (14400s) -> 4min (240s real)
        self.sim_compression = self.duration_seconds / 240.0
        self.sim_start_real = _time.time()

        # Build initial standings (empty — no solves yet)
        self._sim_update_standings(0)

        self.phase = "live"
        print(f"🎮 SIMULATION — {len(data['contestants'])} contestants, "
              f"{len(data['problems'])} problems, {self.duration_seconds}s contest")
        print(f"   Compression: {self.sim_compression:.0f}:1 → "
              f"{self.duration_seconds / self.sim_compression:.0f}s real time")

        # Start simulation ticker (updates standings every 1s real time)
        self._poller_stop.clear()
        self._poller_thread = threading.Thread(target=self._sim_loop, daemon=True)
        self._poller_thread.start()

    def _sim_elapsed(self) -> int:
        """Return simulated elapsed seconds based on real clock."""
        real_elapsed = _time.time() - self.sim_start_real
        return int(real_elapsed * self.sim_compression)

    def _sim_update_standings(self, sim_time: int):
        """Rebuild standings based on submissions up to sim_time."""
        contestants_data = self.sim_data["contestants"]
        problems = self.sim_data["problems"]
        freeze_sec = self.sim_freeze_sec

        # Build per-handle state
        results = {}  # handle -> {prob_idx -> {solved, time, rejectedAttempts}}
        for c in contestants_data:
            results[c["handle"]] = {}
            for p in problems:
                results[c["handle"]][p["index"]] = {
                    "solved": False, "time": 0, "rejectedAttempts": 0
                }

        # Replay submissions up to sim_time
        for sub in self.sim_all_subs:
            if sub["relativeTimeSec"] > sim_time:
                break
            h = sub["handle"]
            pi = sub["problemIndex"]
            if h not in results:
                continue
            pr = results[h][pi]
            if pr["solved"]:
                continue  # already solved
            if sub["verdict"] == "OK":
                pr["solved"] = True
                pr["time"] = sub["relativeTimeSec"]
            else:
                pr["rejectedAttempts"] += 1

        # If past freeze time, hide blind hour results (show freeze-state only)
        if sim_time >= freeze_sec:
            visible_time = freeze_sec
        else:
            visible_time = sim_time

        # Build visible results (only show solves up to visible_time)
        visible_results = {}
        for c in contestants_data:
            visible_results[c["handle"]] = {}
            for p in problems:
                full = results[c["handle"]][p["index"]]
                if full["solved"] and full["time"] <= visible_time:
                    visible_results[c["handle"]][p["index"]] = dict(full)
                else:
                    # Count WAs only up to visible time
                    wa = 0
                    for sub in self.sim_all_subs:
                        if sub["relativeTimeSec"] > visible_time:
                            break
                        if sub["handle"] == c["handle"] and sub["problemIndex"] == p["index"] and sub["verdict"] != "OK":
                            wa += 1
                    visible_results[c["handle"]][p["index"]] = {
                        "solved": False, "time": 0, "rejectedAttempts": wa
                    }

        # Build contestant list with rank
        contestants = []
        for c in contestants_data:
            vr = visible_results[c["handle"]]
            solved = sum(1 for pr in vr.values() if pr["solved"])
            penalty = sum(
                pr["time"] // 60 + 20 * pr["rejectedAttempts"]
                for pr in vr.values() if pr["solved"]
            )
            contestants.append({
                "handle": c["handle"],
                "rank": 0,
                "solved": solved,
                "penalty": penalty,
                "problemResults": vr,
            })

        # Sort and assign ranks
        contestants.sort(key=lambda x: (-x["solved"], x["penalty"]))
        for i, c in enumerate(contestants):
            c["rank"] = i + 1

        # Determine phase string
        remaining = self.duration_seconds - sim_time
        if sim_time >= self.duration_seconds:
            cf_phase = "FINISHED"
        elif sim_time >= freeze_sec:
            cf_phase = "FROZEN"
        else:
            cf_phase = "CODING"

        with self._lock:
            self.live_standings = {
                "contest": {
                    "id": 0,
                    "name": self.contest_name,
                    "durationSeconds": self.duration_seconds,
                    "phase": cf_phase,
                    "relativeTimeSeconds": sim_time,
                },
                "problems": [{"index": p["index"], "name": p["name"]} for p in problems],
                "contestants": contestants,
            }
            self.last_poll_time = _time.time()

    def _sim_loop(self):
        """Background thread: tick simulation clock and update standings."""
        while not self._poller_stop.is_set():
            sim_time = self._sim_elapsed()

            # Auto-freeze at blind hour start
            if sim_time >= self.sim_freeze_sec and self.phase == "live":
                print(f"❄️  Auto-freeze at sim time {sim_time}s "
                      f"(freeze={self.sim_freeze_sec}s)")
                self.phase = "frozen"

            # Contest ended
            if sim_time >= self.duration_seconds:
                self._sim_update_standings(self.duration_seconds)
                # Ensure we mark it as ended so pollers see it
                self.phase = "ended"
                print("🏁 Simulation contest ended — ready for reveal")
                break

            self._sim_update_standings(sim_time)
            self._poller_stop.wait(1.0)  # tick every 1s real time

    def start_reveal_sim(self):
        """Transition to REVEAL phase using simulation data."""
        self._stop_poller()
        self.phase = "reveal"
        self.poll_error = None
        print("🎬 REVEAL — using simulation reveal data…")

        # Build reveal data from sim_data (same format as core.build_reveal_data)
        self.reveal_data = {
            "contest": self.sim_data["contest"],
            "problems": self.sim_data["problems"],
            "contestants": self.sim_data["contestants"],
            "blindHourSubmissions": self.sim_data["blindHourSubmissions"],
        }
        n = len(self.reveal_data["contestants"])
        b = len(self.reveal_data["blindHourSubmissions"])
        print(f"  ✓ Reveal data ready: {n} contestants, {b} blind-hour subs")

    def get_live_data(self) -> dict:
        """Thread-safe read of live standings."""
        with self._lock:
            return dict(self.live_standings)

    def _poll_loop(self):
        """Background thread: periodically fetch standings from CF."""
        while not self._poller_stop.is_set():
            self._poller_stop.wait(self.poll_interval)
            if self._poller_stop.is_set():
                break

            try:
                standings = core.fetch_standings(self.contest_id)
                self._update_live_state(standings)
                self.poll_error = None

                # Auto-detect contest end → auto-freeze
                cf_phase = standings.get("contest", {}).get("phase", "")
                if cf_phase in ("FINISHED", "SYSTEM_TEST", "PENDING_SYSTEM_TEST"):
                    if self.phase == "live":
                        print("⏰ Contest ended on CF — auto-freezing")
                        self.freeze()
                        break

            except Exception as e:
                with self._lock:
                    self.poll_error = str(e)
                print(f"  ⚠ Poll error: {e}")

    def _stop_poller(self):
        """Signal the poller thread to stop."""
        self._poller_stop.set()
        if self._poller_thread and self._poller_thread.is_alive():
            # Don't join if we are checking from the poller thread itself
            if self._poller_thread != threading.current_thread():
                self._poller_thread.join(timeout=5)
        self._poller_thread = None


# ═══════════════════════════════════════════════════════════════════════
#  FLASK APP
# ═══════════════════════════════════════════════════════════════════════

app = Flask(__name__, static_folder=None)
contest_state = ContestState()

FRONTEND_DIR = Path(__file__).parent / "frontend"


# ── Serve frontend ────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:filename>")
def static_files(filename):
    """Serve any file from the frontend directory."""
    file_path = FRONTEND_DIR / filename
    if file_path.is_file():
        return send_from_directory(FRONTEND_DIR, filename)
    abort(404)


# ── API routes ────────────────────────────────────────────────────────

@app.route("/api/phase", methods=["GET"])
def get_phase():
    """Return current phase and metadata."""
    return jsonify({
        "phase": contest_state.phase,
        "contestId": contest_state.contest_id,
        "contestName": contest_state.contest_name,
        "freezeMinutes": contest_state.freeze_minutes,
        "pollInterval": contest_state.poll_interval,
        "durationSeconds": contest_state.duration_seconds,
        "lastPollTime": contest_state.last_poll_time,
        "error": contest_state.poll_error,
        "simMode": contest_state.sim_mode,
    })


@app.route("/api/start", methods=["POST"])
def start_contest():
    """Start tracking a contest → enter LIVE phase."""
    body = request.get_json(force=True)
    contest_id = body.get("contestId")
    freeze_minutes = body.get("freezeMinutes", 60)
    poll_interval = body.get("pollInterval", 30)

    if not contest_id:
        return jsonify({"error": "contestId is required"}), 400

    try:
        contest_id = int(contest_id)
        freeze_minutes = int(freeze_minutes)
        poll_interval = int(poll_interval)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid numeric parameters"}), 400

    # Run in a thread to not block the request
    def do_start():
        contest_state.start_contest(contest_id, freeze_minutes, poll_interval)

    t = threading.Thread(target=do_start, daemon=True)
    t.start()
    t.join(timeout=60)  # wait up to 60s for initial fetch

    if contest_state.poll_error:
        return jsonify({"error": contest_state.poll_error}), 500

    return jsonify({
        "status": "ok",
        "phase": contest_state.phase,
        "contestName": contest_state.contest_name,
    })


@app.route("/api/freeze", methods=["POST"])
def freeze_contest():
    """Freeze the scoreboard."""
    contest_state.freeze()
    return jsonify({"status": "ok", "phase": contest_state.phase})


@app.route("/api/reveal", methods=["POST"])
def reveal_contest():
    """Start the reveal phase — builds reveal data from CF API or sim data."""
    if contest_state.sim_mode:
        contest_state.start_reveal_sim()
        return jsonify({"status": "ok", "phase": contest_state.phase})

    def do_reveal():
        contest_state.start_reveal()

    t = threading.Thread(target=do_reveal, daemon=True)
    t.start()
    t.join(timeout=120)  # can take a while for large contests

    if contest_state.poll_error:
        return jsonify({"error": contest_state.poll_error}), 500

    return jsonify({"status": "ok", "phase": contest_state.phase})


@app.route("/api/simulate", methods=["POST"])
def simulate_contest():
    """Start a simulated 4h contest with 40 contestants."""
    body = request.get_json(force=True) if request.data else {}
    seed = int(body.get("seed", 42))

    contest_state.start_simulation(seed=seed)

    return jsonify({
        "status": "ok",
        "phase": contest_state.phase,
        "contestName": contest_state.contest_name,
        "simMode": True,
    })


@app.route("/api/reset", methods=["POST"])
def reset_contest():
    """Reset back to setup phase."""
    contest_state.reset()
    return jsonify({"status": "ok", "phase": contest_state.phase})


@app.route("/api/standings", methods=["GET"])
def get_standings():
    """
    Return standings for the current phase (Legacy/Simple endpoint).
    """
    phase = contest_state.phase

    if phase == "setup":
        return jsonify({"phase": "setup", "data": None})

    if phase in ("live", "frozen"):
        data = contest_state.get_live_data()
        return jsonify({"phase": phase, "data": data})

    if phase == "reveal":
        return jsonify({"phase": "reveal", "data": contest_state.reveal_data})

    return jsonify({"phase": phase, "data": None})


# ── New API Endpoints ─────────────────────────────────────────────────

@app.route("/api/scoreboard/fetch", methods=["GET"])
def api_fetch_scoreboard():
    """
    GET /api/scoreboard/fetch?contestId=123
    Returns current live standings.
    """
    cid = request.args.get("contestId")
    if not cid:
        return jsonify({"error": "contestId required"}), 400
    
    try:
        # If matches current tracked contest, use cache
        if contest_state.contest_id == int(cid) and contest_state.live_standings:
            return jsonify(contest_state.get_live_data())
            
        # Otherwise fetch fresh
        data = core.fetch_standings(int(cid))
        # We need to format it to match our frontend expectation if we want to reuse frontend logic directly
        # But let's return raw-ish data and let frontend adapt or return the same structure as update_live_state
        
        # NOTE: For consistency, let's just use the logic in ContestState._update_live_state to format it,
        # but we can't easily call that method on a temporary object.
        # Let's just return the raw data and let the frontend handle it or standard format.
        # Actually, the requirement was "The backend can load Codeforces contest data... and expose API".
        # Let's reuse the format we use in live_standings for consistency.
        
        # Reuse logic by creating a temporary ContestState-like structure or just copy-paste formatting logic?
        # Copy-paste for now to keep it isolated or move formatting to core.
        # Moving formatting to core is cleaner. But core is pure logic.
        
        # Let's just do it here briefly.
        contest_meta = data["contest"]
        problems = data["problems"]
        rows = data["rows"]
        problem_indices = [p["index"] for p in problems]
        
        contestants = []
        for row in rows:
            handle = core.get_handle(row["party"])
            formatted_probs = {}
            for i, pr in enumerate(row.get("problemResults", [])):
                idx = problem_indices[i] if i < len(problem_indices) else str(i)
                formatted_probs[idx] = {
                    "solved": pr.get("points", 0) > 0,
                    "time": pr.get("bestSubmissionTimeSeconds", 0),
                    "rejectedAttempts": pr.get("rejectedAttemptCount", 0),
                }
            contestants.append({
                "handle": handle,
                "rank": row.get("rank", 0),
                "solved": row.get("points", 0),
                "penalty": row.get("penalty", 0),
                "problemResults": formatted_probs
            })
            
        response = {
            "contest": {
                "id": int(cid),
                "name": contest_meta.get("name", ""),
                "durationSeconds": contest_meta.get("durationSeconds", 0),
                "phase": contest_meta.get("phase", ""),
            },
            "problems": [{"index": p["index"], "name": p.get("name", p["index"])} for p in problems],
            "contestants": contestants
        }
        return jsonify(response)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scoreboard/replay", methods=["GET"])
def api_replay_scoreboard():
    """
    GET /api/scoreboard/replay?contestId=123
    Returns the reveal data (freeze state + blind subs).
    """
    cid = request.args.get("contestId")
    if not cid:
        return jsonify({"error": "contestId required"}), 400
    
    try:
        # If matches current tracked contest state
        if contest_state.contest_id == int(cid) and contest_state.reveal_data:
            return jsonify(contest_state.reveal_data)
            
        data = core.build_reveal_data(int(cid), request.args.get("freezeMinutes", 60, type=int))
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scoreboard/stateAtTime", methods=["GET"])
def api_state_at_time():
    """
    GET /api/scoreboard/stateAtTime?contestId=123&timestamp=3600
    Returns standings at specific relative time (seconds).
    """
    cid = request.args.get("contestId")
    timestamp = request.args.get("timestamp", type=int)
    
    if not cid or timestamp is None:
        return jsonify({"error": "contestId and timestamp required"}), 400
        
    try:
        cid = int(cid)
        # Optimization: cache submissions/rows if possible
        # For now, simplistic approach: fetch everytime (slow but stateless)
        # OR check if we have them in contest_state
        
        rows = []
        submissions = []
        problems = []
        
        if contest_state.contest_id == cid and contest_state.cached_rows:
             rows = contest_state.cached_rows
             problems = contest_state.problems # format differs slightly but core.compute needs problem list
             # contest_state.problems is list of dicts with index/name, exactly what we need
             # But we need submissions.
             if contest_state.cached_submissions:
                 submissions = contest_state.cached_submissions
             else:
                 submissions = core.fetch_submissions(cid)
                 contest_state.cached_submissions = submissions
        else:
            # Full fetch
            standings_data = core.fetch_standings(cid)
            rows = standings_data["rows"]
            problems = standings_data["problems"]
            submissions = core.fetch_submissions(cid)
            
        # Compute
        result = core.compute_standings_at_time(submissions, rows, problems, timestamp)
        
        return jsonify({
            "contestId": cid,
            "relativeTimeSeconds": timestamp,
            "standings": result
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/demo", methods=["GET"])
def get_demo_data():
    """Return demo data for offline testing."""
    demo_path = FRONTEND_DIR / "data" / "contest_demo.json"
    if demo_path.exists():
        with open(demo_path, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    else:
        return jsonify({"error": "Demo data not found. Run generate_sample.py first."}), 404


# ═══════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Blind Hour Reveal — Flask Server")
    parser.add_argument("--port", type=int, default=5000, help="Port (default: 5000)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host (default: 0.0.0.0)")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    args = parser.parse_args()

    print("═══════════════════════════════════════════════════════")
    print("  Blind Hour Reveal — Server")
    print(f"  http://localhost:{args.port}")
    print("═══════════════════════════════════════════════════════")

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
