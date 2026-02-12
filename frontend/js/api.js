/**
 * API Wrapper for Blind Hour Reveal Backend
 */
export class API {
    static async getPhase() {
        const res = await fetch('/api/phase');
        return await res.json();
    }

    static async startContest(contestId, freezeMinutes, pollInterval) {
        const res = await fetch('/api/start', {
            method: 'POST',
            body: JSON.stringify({ contestId, freezeMinutes, pollInterval })
        });
        return await res.json();
    }

    static async freezeContest() {
        const res = await fetch('/api/freeze', { method: 'POST' });
        return await res.json();
    }

    static async revealContest() {
        const res = await fetch('/api/reveal', { method: 'POST' });
        return await res.json();
    }

    static async resetContest() {
        const res = await fetch('/api/reset', { method: 'POST' });
        return await res.json();
    }

    static async getStandings() {
        const res = await fetch('/api/standings');
        return await res.json();
    }

    static async getDemoData() {
        const res = await fetch('/api/demo');
        return await res.json();
    }

    // New API Endpoints

    static async fetchScoreboard(contestId) {
        const res = await fetch(`/api/scoreboard/fetch?contestId=${contestId}`);
        if (!res.ok) throw new Error(`API Error: ${res.statusText}`);
        return await res.json();
    }

    static async getReplayData(contestId, freezeMinutes) {
        const res = await fetch(`/api/scoreboard/replay?contestId=${contestId}&freezeMinutes=${freezeMinutes}`);
        if (!res.ok) throw new Error(`API Error: ${res.statusText}`);
        return await res.json();
    }

    static async getStateAtTime(contestId, timestamp) {
        const res = await fetch(`/api/scoreboard/stateAtTime?contestId=${contestId}&timestamp=${timestamp}`);
        if (!res.ok) throw new Error(`API Error: ${res.statusText}`);
        return await res.json();
    }
}
