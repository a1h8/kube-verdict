import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import App from "./App.jsx";
import ROADMAP_DATA from "./roadmap.json";

describe("App — landing page (IHM)", () => {
  it("renders the hero with a link to the Decision Journey", () => {
    render(<App />);
    expect(screen.getAllByText(/KubeVerdict/).length).toBeGreaterThan(0);
    const journey = screen.getByRole("link", { name: /Decision Journey/ });
    expect(journey.getAttribute("href")).toBe("#/journey");
  });

  it("embeds the decision-walkthrough video and the fixed demo GIF", () => {
    const { container } = render(<App />);
    // jsDelivr-hosted walkthrough video plays inline
    const source = container.querySelector("video source");
    expect(source.getAttribute("src")).toContain("demo-decision-thresholds.mp4");
    // demo GIF uses the correct filename (regression: was demo_kubeWhisperer.gif)
    const gif = screen.getByAltText("KubeVerdict demo");
    expect(gif.getAttribute("src")).toContain("demo_kubeVerdict.gif");
    expect(gif.getAttribute("src")).not.toContain("kubeWhisperer");
  });

  it("renders all five landing sections", () => {
    render(<App />);
    for (const heading of [
      "Why it matters",
      "Validated scenarios",
      "How it works",
      "Current limitations",
      "Roadmap",
    ]) {
      expect(screen.getAllByText(heading).length).toBeGreaterThan(0);
    }
  });

  it("lists all ten validated scenarios (h001–h010)", () => {
    render(<App />);
    for (let i = 1; i <= 10; i++) {
      const id = `h0${String(i).padStart(2, "0")}`;
      expect(screen.getAllByText(new RegExp(id)).length).toBeGreaterThan(0);
    }
  });

  it("renders every roadmap bloc from roadmap.json", () => {
    render(<App />);
    for (const bloc of ROADMAP_DATA.blocs) {
      expect(screen.getAllByText(new RegExp(bloc.id)).length).toBeGreaterThan(0);
    }
  });
});
