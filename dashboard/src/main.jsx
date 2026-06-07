import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import DecisionJourney from "./DecisionJourney.jsx";

// Tiny hash router — keeps the landing page (App) untouched and mounts the
// Decision Journey view at #/journey. No react-router dependency needed.
function Router() {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const on = () => setHash(window.location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash.startsWith("#/journey") ? <DecisionJourney /> : <App />;
}

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <Router />
  </StrictMode>
);
