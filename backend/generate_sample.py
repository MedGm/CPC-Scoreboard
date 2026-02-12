#!/usr/bin/env python3
"""
Generate realistic sample contest data for the Blind Hour Reveal demo.
No Codeforces API needed — this creates a plausible contest scenario
with dramatic rank changes during the blind hour.

Usage:
    python generate_sample.py [--output ../frontend/data/contest_demo.json]
    python generate_sample.py --contestants 40 --duration 240 --freeze 180
"""

import argparse
import json
import random
from pathlib import Path

# ── Algerian-flavored handles for realism ───────────────────────────────

HANDLES = [
    "Yassine_CP", "Amine_DZ", "Fatima_Code", "Khaled_ACM",
    "Meriem_Algo", "Raouf_Master", "Sara_Dev", "Mourad_IOI",
    "Lina_Solver", "Nabil_Pro", "Amira_DZ", "Zakaria_CP",
    "Houssem_Bit", "Djamila_X", "Bilal_Hash",
    "Imane_Tree", "Walid_Graph", "Noura_DP", "Sofiane_Seg",
    "Chaima_Math", "Abdelkader_FF", "Hadjer_BFS", "Oussama_Greedy",
    "Asma_Binary", "Mehdi_Trie", "Rania_Flow", "Aymen_Suffix",
    "Lamia_Stack", "Ismail_Queue", "Yasmine_FFT",
    "Ilyes_MST", "Wissam_LCA", "Hanane_Lazy", "Farid_Sparse",
    "Ikram_Centroid", "Djamel_HLD", "Samira_BIT", "Tarek_DSU",
    "Mouna_Sweep", "Nassim_Convex", "Salima_Mo", "Rami_Sqrt",
    "Amel_Z", "Karim_Aho", "Sihem_KMP",
]

ALL_PROBLEMS = [
    {"index": "A", "name": "Array Warmup"},
    {"index": "B", "name": "Binary Beauty"},
    {"index": "C", "name": "Counting Paths"},
    {"index": "D", "name": "Dynamic Grid"},
    {"index": "E", "name": "Edge Coloring"},
    {"index": "F", "name": "Flow Network"},
    {"index": "G", "name": "Graph Decomposition"},
]

WA_PENALTY_MINUTES = 20       # ICPC: +20 min per wrong attempt before AC


def generate_sample_data(seed: int = 42, n_contestants: int = 40,
                         duration_min: int = 240, freeze_min: int = 180,
                         n_problems: int = 7) -> dict:
    random.seed(seed)

    contest_duration = duration_min * 60
    freeze_time = freeze_min * 60

    problems = ALL_PROBLEMS[:n_problems]
    handles = HANDLES[:n_contestants]

    n = len(handles)
    # Skill levels: higher = solves more problems, faster
    skills = sorted([random.uniform(0.3, 1.0) for _ in range(n)], reverse=True)

    contestants = []
    blind_subs = []
    # All submissions (for progressive simulation)
    all_subs = []

    for i, handle in enumerate(handles):
        skill = skills[i]

        # Determine which problems solved before freeze
        pre_freeze_solved = {}  # idx -> {solved, time, waCount}
        pre_freeze_wa = {}      # idx -> wa count (for unsolved-at-freeze problems)
        for j, prob in enumerate(problems):
            difficulty = (j + 1) / len(problems)
            # Probability of solving before freeze
            prob_solve = max(0, min(1, skill - difficulty * 0.6 + 0.3))
            # Generate some wrong attempts before AC
            wa_count = random.randint(0, 2) if random.random() < 0.4 else 0
            if random.random() < prob_solve:
                solve_time = int(
                    random.uniform(5, 40 + j * 25) * 60  # in seconds
                )
                solve_time = min(solve_time, freeze_time - 60)

                # Generate wrong attempts as individual submissions
                for w in range(wa_count):
                    wrong_time = int(random.uniform(max(60, solve_time - 1800), solve_time - 30))
                    wrong_time = max(60, wrong_time)
                    all_subs.append({
                        "handle": handle,
                        "problemIndex": prob["index"],
                        "problemName": prob["name"],
                        "verdict": random.choice(["WRONG_ANSWER", "TIME_LIMIT_EXCEEDED", "RUNTIME_ERROR"]),
                        "relativeTimeSec": wrong_time,
                        "submissionId": random.randint(100000, 999999),
                        "wrongAttemptsBefore": w,
                    })

                pre_freeze_solved[prob["index"]] = {
                    "solved": True,
                    "time": solve_time,
                    "waCount": wa_count,
                }
                # AC submission
                all_subs.append({
                    "handle": handle,
                    "problemIndex": prob["index"],
                    "problemName": prob["name"],
                    "verdict": "OK",
                    "relativeTimeSec": solve_time,
                    "submissionId": random.randint(100000, 999999),
                    "wrongAttemptsBefore": wa_count,
                })
            else:
                # Track WA for unsolved problems (may be attempted during blind hour)
                pre_freeze_wa[prob["index"]] = wa_count

                # Add failed attempts as submissions too
                for w in range(wa_count):
                    fail_time = int(random.uniform(60, freeze_time - 120))
                    all_subs.append({
                        "handle": handle,
                        "problemIndex": prob["index"],
                        "problemName": prob["name"],
                        "verdict": random.choice(["WRONG_ANSWER", "TIME_LIMIT_EXCEEDED"]),
                        "relativeTimeSec": fail_time,
                        "submissionId": random.randint(100000, 999999),
                        "wrongAttemptsBefore": w,
                    })

        # Penalty at freeze (ICPC: solve_time_min + 20 * wa_count)
        freeze_penalty = sum(
            v["time"] // 60 + WA_PENALTY_MINUTES * v["waCount"]
            for v in pre_freeze_solved.values()
        )

        # Problem results at freeze
        problem_results_freeze = {}
        for prob in problems:
            idx = prob["index"]
            if idx in pre_freeze_solved:
                problem_results_freeze[idx] = pre_freeze_solved[idx]
            else:
                problem_results_freeze[idx] = {
                    "solved": False, "time": 0,
                    "waCount": pre_freeze_wa.get(idx, 0),
                }

        # ── Blind hour: some contestants solve extra problems ────────
        final_solved = dict(pre_freeze_solved)

        for j, prob in enumerate(problems):
            idx = prob["index"]
            if idx in final_solved:
                continue  # already solved
            difficulty = (j + 1) / len(problems)
            # Lower chance during blind hour, but clutch solves happen
            prob_blind = max(0, min(0.6, skill - difficulty * 0.5 + 0.1))
            if random.random() < prob_blind:
                solve_time = int(
                    random.uniform(freeze_time + 60, contest_duration - 120)
                )
                # Wrong attempts from pre-freeze
                wa_from_freeze = pre_freeze_wa.get(idx, 0)

                # Add some wrong attempts during blind hour (for drama)
                n_wrong = random.randint(0, 3)
                wa_during_blind = 0
                for _ in range(n_wrong):
                    wrong_time = int(random.uniform(freeze_time + 30, solve_time - 30))
                    if wrong_time > freeze_time:
                        sub = {
                            "handle": handle,
                            "problemIndex": idx,
                            "problemName": prob["name"],
                            "verdict": random.choice(["WRONG_ANSWER", "TIME_LIMIT_EXCEEDED", "RUNTIME_ERROR"]),
                            "relativeTimeSec": wrong_time,
                            "submissionId": random.randint(100000, 999999),
                            "wrongAttemptsBefore": wa_from_freeze + wa_during_blind,
                        }
                        blind_subs.append(sub)
                        all_subs.append(sub)
                        wa_during_blind += 1

                # The AC submission
                total_wa = wa_from_freeze + wa_during_blind
                final_solved[idx] = {
                    "solved": True,
                    "time": solve_time,
                    "waCount": total_wa,
                }
                ac_sub = {
                    "handle": handle,
                    "problemIndex": idx,
                    "problemName": prob["name"],
                    "verdict": "OK",
                    "relativeTimeSec": solve_time,
                    "submissionId": random.randint(100000, 999999),
                    "wrongAttemptsBefore": total_wa,
                }
                blind_subs.append(ac_sub)
                all_subs.append(ac_sub)

        # Final problem results
        problem_results_final = {}
        for prob in problems:
            idx = prob["index"]
            if idx in final_solved:
                wa = final_solved[idx].get("waCount", 0)
                problem_results_final[idx] = {
                    "solved": True,
                    "time": final_solved[idx]["time"],
                    "rejectedAttempts": wa,
                }
            else:
                problem_results_final[idx] = {
                    "solved": False,
                    "time": 0,
                    "rejectedAttempts": pre_freeze_wa.get(idx, 0),
                }

        # ICPC penalty: sum(solve_time_min + 20 * wa_count)
        final_penalty = sum(
            v["time"] // 60 + WA_PENALTY_MINUTES * v.get("waCount", 0)
            for v in final_solved.values()
        )

        contestants.append({
            "handle": handle,
            "freezeSolvedCount": len(pre_freeze_solved),
            "freezePenalty": freeze_penalty,
            "finalPoints": len(final_solved),
            "finalPenalty": final_penalty,
            "finalRank": 0,  # computed below
            "freezeRank": 0,  # computed below
            "problemResultsAtFreeze": problem_results_freeze,
            "problemResultsFinal": problem_results_final,
        })

    # Sort by freeze standing
    contestants.sort(key=lambda c: (-c["freezeSolvedCount"], c["freezePenalty"]))
    for i, c in enumerate(contestants):
        c["freezeRank"] = i + 1

    # Sort by final standing
    contestants.sort(key=lambda c: (-c["finalPoints"], c["finalPenalty"]))
    for i, c in enumerate(contestants):
        c["finalRank"] = i + 1

    # Re-sort by freeze rank for display
    contestants.sort(key=lambda c: c["freezeRank"])

    # Sort all subs by time
    blind_subs.sort(key=lambda s: s["relativeTimeSec"])
    all_subs.sort(key=lambda s: s["relativeTimeSec"])

    return {
        "contest": {
            "id": 0,
            "name": "CPC Contest Simulation — 4h Edition",
            "durationSeconds": contest_duration,
            "freezeTimeSeconds": freeze_time,
        },
        "problems": problems,
        "contestants": contestants,
        "blindHourSubmissions": blind_subs,
        "allSubmissions": all_subs,
    }


def main():
    parser = argparse.ArgumentParser(description="Generate sample Blind Hour data")
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output JSON path (default: ../frontend/data/contest_demo.json)"
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--contestants", type=int, default=40, help="Number of contestants")
    parser.add_argument("--duration", type=int, default=240, help="Contest duration in minutes")
    parser.add_argument("--freeze", type=int, default=180, help="Freeze time in minutes")
    parser.add_argument("--problems", type=int, default=7, help="Number of problems (max 7)")
    args = parser.parse_args()

    output_path = args.output
    if output_path is None:
        out_dir = Path(__file__).parent.parent / "frontend" / "data"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(out_dir / "contest_demo.json")

    data = generate_sample_data(args.seed, args.contestants, args.duration, args.freeze, args.problems)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    n = len(data["contestants"])
    b = len(data["blindHourSubmissions"])
    accepted = len([s for s in data["blindHourSubmissions"] if s["verdict"] == "OK"])
    print(f"✅ Generated demo data: {n} contestants, {b} blind-hour subs ({accepted} accepted)")
    print(f"📁 Saved to: {output_path}")


if __name__ == "__main__":
    main()
