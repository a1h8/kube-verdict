import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import DecisionJourney from "./DecisionJourney.jsx";

// Mock the API client so the component test never touches the network.
vi.mock("./api.js", () => ({
  getToken: () => "",
  setToken: vi.fn(),
  getState: vi.fn(),
  investigate: vi.fn(),
  loadSample: vi.fn(),
  reviewSession: vi.fn(),
}));
import { loadSample, investigate, reviewSession } from "./api.js";

const SAMPLE = {
  session_id: "smp",
  status: "AWAITING_REVIEW",
  confidence: "HIGH",
  verdict: "HUMAN_REVIEW",
  verdict_reasons: ["namespace 'production' is production — always HUMAN_REVIEW minimum"],
  current_hypothesis: "PVC payment-data is Pending",
  blast_radius: { risk: "MEDIUM" },
  reasoning_history: [
    { step: 1, hypothesis: "OOMKilled", confidence: "LOW", retry_count: 1, summary: "switched" },
  ],
  edge_log: [
    { router: "confidence", edge_taken: "next_path", reason: "LOW×2", snapshot: { confidence: "LOW" } },
    { router: "policy", edge_taken: "review", reason: "production", snapshot: { score: 0.85 } },
  ],
  report: { root_cause: "No PV matches storageClass", remediation: ["kubectl apply -f pv.yaml"] },
  review_payload: {
    summary: "Review before applying remediation.",
    root_cause: "No PV matches storageClass",
    remediation: ["kubectl apply -f pv.yaml"],
  },
};

beforeEach(() => vi.clearAllMocks());

describe("DecisionJourney", () => {
  it("renders the run controls initially, no results", () => {
    render(<DecisionJourney />);
    expect(screen.getByText("Decision Journey")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Investigate/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Load sample/ })).toBeInTheDocument();
    expect(screen.queryByText("HUMAN REVIEW")).not.toBeInTheDocument();
  });

  it("renders verdict, paths, timeline and root cause after Load sample", async () => {
    loadSample.mockResolvedValue({ session_id: "smp", state: SAMPLE });
    render(<DecisionJourney />);

    fireEvent.click(screen.getByRole("button", { name: /Load sample/ }));

    expect(await screen.findByText("HUMAN REVIEW")).toBeInTheDocument();
    // gate score from edge_log, not recomputed (shown in banner + timeline)
    expect(screen.getAllByText(/0\.85/).length).toBeGreaterThan(0);
    // blast radius
    expect(screen.getByText("MEDIUM")).toBeInTheDocument();
    // chosen path + eliminated path
    expect(screen.getByText(/Chosen/)).toBeInTheDocument();
    expect(screen.getAllByText(/eliminated/).length).toBeGreaterThan(0);
    // timeline routers
    expect(screen.getByText(/Decision timeline/)).toBeInTheDocument();
    // root cause
    expect(screen.getAllByText(/No PV matches storageClass/).length).toBeGreaterThan(0);
  });

  it("renders the B9 beam-search tree and collector fallback badges", async () => {
    loadSample.mockResolvedValue({
      session_id: "smp",
      state: {
        ...SAMPLE,
        ingestion_stats: {
          k8s: { fallback: false },
          prometheus: { fallback: true, error: "no Prometheus endpoint configured" },
        },
      },
    });
    render(<DecisionJourney />);

    fireEvent.click(screen.getByRole("button", { name: /Load sample/ }));

    // beam-search tree (SVG dag)
    expect(await screen.findByText(/Beam-search tree/)).toBeInTheDocument();
    // collector fallback overlay — OK vs FALLBACK badges
    expect(screen.getByText(/Collector status/)).toBeInTheDocument();
    expect(screen.getByText(/k8s: OK/)).toBeInTheDocument();
    expect(screen.getByText(/prometheus: FALLBACK/)).toBeInTheDocument();
  });

  it("shows an error banner when the API call fails", async () => {
    loadSample.mockRejectedValue(new Error("401 — bearer token required or invalid"));
    render(<DecisionJourney />);

    fireEvent.click(screen.getByRole("button", { name: /Load sample/ }));

    expect(await screen.findByText(/401/)).toBeInTheDocument();
    expect(investigate).not.toHaveBeenCalled();
  });

  it("submits approve from the review gate and renders the completed state", async () => {
    investigate.mockResolvedValue({ session_id: "s1", state: SAMPLE });
    reviewSession.mockResolvedValue({
      session_id: "s1",
      state: {
        ...SAMPLE,
        status: "COMPLETED",
        review_payload: null,
        verdict: "AUTO",
      },
    });
    render(<DecisionJourney />);

    fireEvent.click(screen.getByRole("button", { name: /Investigate/ }));
    expect(await screen.findByText(/Human review/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Approve remediation/ }));

    await waitFor(() => {
      expect(reviewSession).toHaveBeenCalledWith("s1", "approve", { onTick: expect.any(Function) });
    });
    expect(await screen.findByText("AUTO")).toBeInTheDocument();
  });

  // ── decision process ─────────────────────────────────────────────────────
  it("renders the reasoning process: eliminated hypotheses with their reason, then the chosen path", async () => {
    loadSample.mockResolvedValue({ session_id: "smp", state: SAMPLE });
    render(<DecisionJourney />);

    fireEvent.click(screen.getByRole("button", { name: /Load sample/ }));
    await screen.findByText("HUMAN REVIEW");

    // eliminated hypothesis + why it was archived
    expect(screen.getAllByText(/OOMKilled/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/switched/).length).toBeGreaterThan(0);
    // the path that was selected
    expect(screen.getByText(/Chosen/)).toBeInTheDocument();
  });

  it("renders the routing timeline — each decision with its edge and reason", async () => {
    loadSample.mockResolvedValue({ session_id: "smp", state: SAMPLE });
    render(<DecisionJourney />);

    fireEvent.click(screen.getByRole("button", { name: /Load sample/ }));
    await screen.findByText(/Decision timeline/);

    expect(screen.getByText("next_path")).toBeInTheDocument();   // early path switch
    expect(screen.getAllByText(/review/).length).toBeGreaterThan(0); // human gate edge
    expect(screen.getByText(/LOW×2/)).toBeInTheDocument();        // switch reason
  });

  it("renders a NO-GO verdict with its blocking reason", async () => {
    loadSample.mockResolvedValue({
      session_id: "smp",
      state: {
        ...SAMPLE,
        verdict: "NO_GO",
        verdict_reasons: ["rollback_available=False — no safe recovery path"],
      },
    });
    render(<DecisionJourney />);

    fireEvent.click(screen.getByRole("button", { name: /Load sample/ }));

    expect(await screen.findByText("NO-GO")).toBeInTheDocument();
    expect(screen.getByText(/no safe recovery path/)).toBeInTheDocument();
  });
});
