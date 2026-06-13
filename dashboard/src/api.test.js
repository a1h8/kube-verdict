import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createSession, getState, pollState, investigate, loadSample, reviewSession, setToken,
} from "./api.js";

function okJson(body, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
}

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("api client", () => {
  it("sends Authorization header when a token is set", async () => {
    setToken("s3cret");
    const fetchMock = vi.fn(() => okJson({ session_id: "abc" }, 201));
    vi.stubGlobal("fetch", fetchMock);

    await createSession();

    const [, opts] = fetchMock.mock.calls[0];
    expect(opts.headers.Authorization).toBe("Bearer s3cret");
    expect(opts.method).toBe("POST");
  });

  it("omits Authorization header when no token", async () => {
    const fetchMock = vi.fn(() => okJson({ session_id: "abc" }, 201));
    vi.stubGlobal("fetch", fetchMock);

    await createSession();

    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBeUndefined();
  });

  it("surfaces a clear error on 401", async () => {
    vi.stubGlobal("fetch", vi.fn(() => okJson({ detail: "nope" }, 401)));
    await expect(getState("x")).rejects.toThrow(/401/);
  });

  it("pollState stops at a terminal status and reports each tick", async () => {
    const states = [
      { status: "RUNNING" },
      { status: "RUNNING" },
      { status: "AWAITING_REVIEW", verdict: "HUMAN_REVIEW" },
    ];
    let i = 0;
    vi.stubGlobal("fetch", vi.fn(() => okJson(states[Math.min(i++, states.length - 1)])));
    const ticks = [];

    const final = await pollState("id", { interval: 0, onTick: (s) => ticks.push(s.status) });

    expect(final.status).toBe("AWAITING_REVIEW");
    expect(ticks).toEqual(["RUNNING", "RUNNING", "AWAITING_REVIEW"]);
  });

  it("investigate chains create → run → poll", async () => {
    const calls = [];
    vi.stubGlobal("fetch", vi.fn((url, opts) => {
      calls.push(`${opts?.method || "GET"} ${url}`);
      if (url.endsWith("/sessions")) return okJson({ session_id: "s1" }, 201);
      if (url.endsWith("/run")) return okJson({ status: "RUNNING" });
      return okJson({ status: "COMPLETED", session_id: "s1" });
    }));

    const { session_id, state } = await investigate({ query: "x" }, { onTick: () => {} });

    expect(session_id).toBe("s1");
    expect(state.status).toBe("COMPLETED");
    expect(calls[0]).toBe("POST /api/v1/sessions");
    expect(calls[1]).toBe("POST /api/v1/sessions/s1/run");
  });

  it("loadSample returns the terminal sample state without polling", async () => {
    const fetchMock = vi.fn(() => okJson({ session_id: "smp", status: "AWAITING_REVIEW", verdict: "HUMAN_REVIEW" }, 201));
    vi.stubGlobal("fetch", fetchMock);

    const { state } = await loadSample();

    expect(state.verdict).toBe("HUMAN_REVIEW");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/sessions/sample");
  });

  it("loadSample falls back to the bundled sample when there is no backend (405)", async () => {
    vi.stubGlobal("fetch", vi.fn(() => okJson({ detail: "method not allowed" }, 405)));

    const { state } = await loadSample();

    // bundled client-side snapshot renders the full journey without an API
    expect(state.verdict).toBe("HUMAN_REVIEW");
    expect(state.status).toBe("AWAITING_REVIEW");
    expect(state.reasoning_history.length).toBeGreaterThan(0);
    expect(state.ingestion_stats).toBeTruthy();
  });

  it("surfaces a 'no API backend' message on 405", async () => {
    vi.stubGlobal("fetch", vi.fn(() => okJson({}, 405)));
    await expect(getState("x")).rejects.toThrow(/No API backend reachable/);
  });

  it("reviewSession sends feedback then polls to completion", async () => {
    const calls = [];
    vi.stubGlobal("fetch", vi.fn((url, opts) => {
      calls.push(`${opts?.method || "GET"} ${url}`);
      if (url.endsWith("/feedback")) return okJson({ status: "RUNNING" });
      return okJson({ status: "COMPLETED", session_id: "s1" });
    }));

    const { session_id, state } = await reviewSession("s1", "approve", { onTick: () => {} });

    expect(session_id).toBe("s1");
    expect(state.status).toBe("COMPLETED");
    expect(calls[0]).toBe("POST /api/v1/sessions/s1/feedback");
  });
});
