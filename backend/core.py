import time
import requests
from typing import Any

CF_API = "https://codeforces.com/api"
WA_PENALTY_MINUTES = 20

# Verdicts that count as "wrong attempt" for ICPC penalty
COUNTED_WA_VERDICTS = {
    "WRONG_ANSWER", "TIME_LIMIT_EXCEEDED", "MEMORY_LIMIT_EXCEEDED",
    "RUNTIME_ERROR", "PRESENTATION_ERROR", "IDLENESS_LIMIT_EXCEEDED",
}

IGNORED_VERDICTS = {
    "TESTING", "SKIPPED", "COMPILATION_ERROR",
    "HACKED", "CHALLENGED",
}

def api_get(method: str, params: dict | None = None) -> dict:
    """Call a Codeforces API method with basic retry logic."""
    url = f"{CF_API}/{method}"
    for attempt in range(4):
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "OK":
                raise RuntimeError(f"CF API error: {data.get('comment', 'unknown')}")
            return data["result"]
        except (requests.RequestException, RuntimeError) as exc:
            if attempt == 3:
                raise
            wait = 2 * (attempt + 1)
            print(f"  ⚠ Attempt {attempt+1} failed ({exc}), retrying in {wait}s…")
            time.sleep(wait)
    return {}

def fetch_standings(contest_id: int, unofficial: bool = False) -> dict:
    """Fetch final standings with pagination."""
    all_rows = []
    page_size = 500
    page = 1
    contest_info = None
    problems_info = None

    while True:
        result = api_get("contest.standings", {
            "contestId": contest_id,
            "from": (page - 1) * page_size + 1,
            "count": page_size,
            "showUnofficial": unofficial,
        })
        if contest_info is None:
            contest_info = result["contest"]
            problems_info = result["problems"]

        rows = result.get("rows", [])
        if not rows:
            break

        all_rows.extend(rows)
        # If we got fewer rows than page_size, we are done
        if len(rows) < page_size:
            break
        page += 1
        time.sleep(0.5)

    return {"contest": contest_info, "problems": problems_info, "rows": all_rows}

def fetch_submissions(contest_id: int) -> list[dict]:
    """Fetch all submissions with pagination."""
    all_subs = []
    page_size = 10000
    page = 1

    while True:
        start = (page - 1) * page_size + 1
        result = api_get("contest.status", {
            "contestId": contest_id,
            "from": start,
            "count": page_size,
        })
        if not result:
            break
        all_subs.extend(result)
        if len(result) < page_size:
            break
        page += 1
        time.sleep(0.5)

    return all_subs

def get_handle(party: dict) -> str:
    """Display name for a party (team name or first member handle)."""
    team = party.get("teamName")
    if team:
        return team
    members = party.get("members", [])
    return members[0]["handle"] if members else "unknown"


def compute_standings_at_time(submissions: list[dict], rows: list[dict], problems: list[dict], time_seconds: int) -> list[dict]:
    """
    Compute the standings at a specific relative time based on submissions.
    This reconstructs the scoreboard state from scratch using the submissions log.
    
    Args:
        submissions: List of submission dicts from CF API.
        rows: Rows from fetch_standings (used to get the list of official contestants).
        problems: Problem metadata.
        time_seconds: The relative time in seconds to compute standings for.
        
    Returns:
        List of contestant dicts with 'rank', 'solved', 'penalty', and 'problemResults'.
    """
    # 1. Filter official contestants
    official_handles = set()
    for row in rows:
        official_handles.add(get_handle(row["party"]))

    # 2. Sort submissions chronologically
    submissions = sorted(submissions, key=lambda s: s.get("relativeTimeSeconds", 0))

    # 3. Contestant state tracking
    # contestant_state[handle][problem_index] = { "solved": bool, "penalty": int, "rejected": int, "time": int }
    problem_indices = [p["index"] for p in problems]
    contestant_state = {
        handle: {
            pid: {"solved": False, "penalty": 0, "rejected": 0, "time": 0} 
            for pid in problem_indices
        }
        for handle in official_handles
    }

    for sub in submissions:
        rel_time = sub.get("relativeTimeSeconds", 0)
        
        # Stop processing if we passed the target time
        if rel_time > time_seconds:
            break
            
        party = sub.get("author", {})
        handle = get_handle(party)
        
        # Only process official contestants
        if handle not in official_handles:
            continue
            
        # Only CONTESTANT type
        if party.get("participantType") != "CONTESTANT":
            continue
            
        verdict = sub.get("verdict")
        if verdict in IGNORED_VERDICTS:
            continue
            
        prob_idx = sub["problem"]["index"]
        # If problem index is unknown (e.g. from a removed problem), skip
        if prob_idx not in problem_indices:
             continue
             
        p_state = contestant_state[handle][prob_idx]
        
        # If already solved, ignore further submissions for this problem
        if p_state["solved"]:
            continue
            
        if verdict == "OK":
            p_state["solved"] = True
            p_state["time"] = rel_time
            # Penalty = time in minutes + 20 mins * rejected attempts
            # Note: ICPC rules usually truncate seconds for penalty calculation (minutes)
            p_state["penalty"] = int(rel_time / 60) + (WA_PENALTY_MINUTES * p_state["rejected"])
        elif verdict in COUNTED_WA_VERDICTS:
            # Codeforces/ICPC rule: if failed on test 1 (passed=0), no penalty
            if sub.get("passedTestCount", 0) > 0:
                p_state["rejected"] += 1
            
    # 4. Aggregate results
    standings = []
    for handle, problems_data in contestant_state.items():
        solved_count = 0
        total_penalty = 0
        
        formatted_problems = {}
        
        for pid, p_data in problems_data.items():
            if p_data["solved"]:
                solved_count += 1
                total_penalty += p_data["penalty"]
                
            formatted_problems[pid] = {
                "solved": p_data["solved"],
                "time": p_data["time"],
                "rejectedAttempts": p_data["rejected"]
            }
            
        standings.append({
            "handle": handle,
            "solved": solved_count,
            "penalty": total_penalty,
            "problemResults": formatted_problems
        })
        
    # 5. Sort by solved (desc), penalty (asc)
    standings.sort(key=lambda x: (-x["solved"], x["penalty"]))
    
    # 6. Assign ranks
    # Handle ties? Standard ICPC rank assignment (same rank for ties, but gaps)
    # For now, simple 1..N ranking
    for i, c in enumerate(standings):
        c["rank"] = i + 1
        
    return standings


def build_reveal_data(contest_id: int, freeze_minutes: int) -> dict:
    """
    Build the full data payload needed for the reveal phase.
    This reconstructs the scoreboard at freeze time and identifies blind-hour submissions.
    """
    print(f"📡  Fetching data for contest {contest_id}...")
    standings_data = fetch_standings(contest_id)
    all_subs = fetch_submissions(contest_id)

    contest_meta = standings_data["contest"]
    problems = standings_data["problems"]
    rows = standings_data["rows"]

    contest_duration_sec = contest_meta.get("durationSeconds", 300 * 60)
    freeze_sec = freeze_minutes * 60
    contest_name = contest_meta.get("name", f"Contest {contest_id}")

    problem_indices = [p["index"] for p in problems]

    # Map handle -> row
    handle_row = {}
    for row in rows:
        handle = get_handle(row["party"])
        handle_row[handle] = row

    # Sort submissions chronologically
    all_subs.sort(key=lambda s: s.get("relativeTimeSeconds", 0))

    # Tracking Structures
    # pre_freeze_accepted[handle][prob] = { "time": int, "waCount": int, "submissionId": int }
    pre_freeze_accepted = {}
    # pre_freeze_wa_count[handle][prob] = int
    pre_freeze_wa_count = {}

    # blind_hour_accepted[handle] = set(prob_indices)
    blind_hour_accepted = {}
    # blind_hour_wa_count[handle][prob] = int
    blind_hour_wa_count = {}

    blind_hour_subs = []

    for sub in all_subs:
        party = sub.get("author", {})
        members = party.get("members", [])
        if not members:
            continue

        handle = get_handle(party)
        if handle not in handle_row:
            continue

        if party.get("participantType") != "CONTESTANT":
            continue

        verdict = sub.get("verdict", "")
        if verdict in IGNORED_VERDICTS:
            continue

        rel_time = sub.get("relativeTimeSeconds", 0)
        problem_idx = sub["problem"]["index"]
        
        # Ensure we initialize dicts
        if handle not in pre_freeze_wa_count: pre_freeze_wa_count[handle] = {}
        if handle not in pre_freeze_accepted: pre_freeze_accepted[handle] = {}
        if handle not in blind_hour_accepted: blind_hour_accepted[handle] = set()
        if handle not in blind_hour_wa_count: blind_hour_wa_count[handle] = {}

        if rel_time < freeze_sec:
            # --- Pre-Freeze ---
            if problem_idx in pre_freeze_accepted[handle]:
                continue

            if verdict == "OK":
                wa_before = pre_freeze_wa_count[handle].get(problem_idx, 0)
                pre_freeze_accepted[handle][problem_idx] = {
                    "time": rel_time,
                    "waCount": wa_before,
                    "submissionId": sub["id"]
                }
            elif verdict in COUNTED_WA_VERDICTS:
                if sub.get("passedTestCount", 0) > 0:
                    pre_freeze_wa_count[handle][problem_idx] = \
                        pre_freeze_wa_count[handle].get(problem_idx, 0) + 1
        else:
            # --- Blind Hour ---
            # If already accepted before freeze, ignore
            if problem_idx in pre_freeze_accepted[handle]:
                continue
            
            # If already accepted in blind hour, ignore
            if problem_idx in blind_hour_accepted[handle]:
                continue

            wa_before_freeze = pre_freeze_wa_count[handle].get(problem_idx, 0)
            wa_during_blind = blind_hour_wa_count[handle].get(problem_idx, 0)

            blind_hour_subs.append({
                "handle": handle,
                "problemIndex": problem_idx,
                "problemName": sub["problem"].get("name", problem_idx),
                "verdict": verdict,
                "relativeTimeSec": rel_time,
                "submissionId": sub["id"],
                "wrongAttemptsBefore": wa_before_freeze + wa_during_blind,
            })

            if verdict == "OK":
                blind_hour_accepted[handle].add(problem_idx)
            elif verdict in COUNTED_WA_VERDICTS:
                if sub.get("passedTestCount", 0) > 0:
                    blind_hour_wa_count[handle][problem_idx] = wa_during_blind + 1

    # Build contestant list
    contestants = []
    for row in rows:
        handle = get_handle(row["party"])

        # API Final Results (authoritative)
        api_final = {}
        for i, pr in enumerate(row.get("problemResults", [])):
            idx = problem_indices[i] if i < len(problem_indices) else str(i)
            api_final[idx] = {
                "solved": pr.get("points", 0) > 0,
                "time": pr.get("bestSubmissionTimeSeconds", 0),
                "rejectedAttempts": pr.get("rejectedAttemptCount", 0),
            }

        # Reconstruct Freeze State
        valid_freeze_solved = {}
        # Checks if it was solved pre-freeze AND matches API final (not hacked)
        # Note: Codeforces standings API reflects current state. If a sub was hacked,
        # it won't appear as solved in api_final or will have different time if re-solved.
        # We trust pre_freeze_accepted IF it matches api_final conditions mostly,
        # but to be safe we check if api_final says it is solved and time is < freeze_sec.
        
        solved_at_freeze_candidate = pre_freeze_accepted.get(handle, {})
        
        for idx, info in solved_at_freeze_candidate.items():
            final = api_final.get(idx, {})
            # If final says solved and time is consistent with freeze time
            if final.get("solved") and final.get("time", 0) < freeze_sec:
                valid_freeze_solved[idx] = info

        freeze_solved_count = len(valid_freeze_solved)
        freeze_penalty = sum(
            int(info["time"] / 60) + (WA_PENALTY_MINUTES * info["waCount"])
            for info in valid_freeze_solved.values()
        )

        problem_results_freeze = {}
        for idx in problem_indices:
            if idx in valid_freeze_solved:
                problem_results_freeze[idx] = {
                    "solved": True,
                    "time": valid_freeze_solved[idx]["time"],
                    "waCount": valid_freeze_solved[idx]["waCount"]
                }
            else:
                wa = pre_freeze_wa_count.get(handle, {}).get(idx, 0)
                problem_results_freeze[idx] = {
                    "solved": False,
                    "time": 0,
                    "waCount": wa
                }

        contestants.append({
            "handle": handle,
            "freezeSolvedCount": freeze_solved_count,
            "freezePenalty": freeze_penalty,
            "freezeRank": 0, # To be filled after sort
            "finalPoints": row.get("points", 0),
            "finalPenalty": row.get("penalty", 0),
            "finalRank": row.get("rank", 0),
            "problemResultsAtFreeze": problem_results_freeze,
            "problemResultsFinal": api_final
        })

    # Sort contestants by freeze score
    contestants.sort(key=lambda c: (-c["freezeSolvedCount"], c["freezePenalty"]))
    for i, c in enumerate(contestants):
        c["freezeRank"] = i + 1

    # Sort blind hour subs
    blind_hour_subs.sort(key=lambda s: s["relativeTimeSec"])

    return {
        "contest": {
            "id": contest_id,
            "name": contest_name,
            "durationSeconds": contest_duration_sec,
            "freezeTimeSeconds": freeze_sec,
        },
        "problems": [
            {"index": p["index"], "name": p.get("name", p["index"])}
            for p in problems
        ],
        "contestants": contestants,
        "blindHourSubmissions": blind_hour_subs,
    }
