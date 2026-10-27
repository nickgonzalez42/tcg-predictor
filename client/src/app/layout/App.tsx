import { useEffect } from "react";
import NavBar from "./NavBar";
import Footer from "./Footer";
import ConsentBanner from "../shared/components/ConsentBanner";
import ReportProblem from "../../features/report/ReportProblem";
import { Outlet, ScrollRestoration, useLocation } from "react-router-dom";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { ScrollSmoother } from "gsap/ScrollSmoother";

gsap.registerPlugin(ScrollTrigger, ScrollSmoother);

function App() {
  const { pathname } = useLocation();

  // The market ticker rides under the navbar on the landing + catalog screens.
  const showTicker = pathname === "/" || pathname === "/catalog";
  // Pages whose dot-and-glow field runs the whole page length rather than
  // just the hero (portfolio, user request 2026-10-24 — the cut-off below
  // its tall hero looked awkward). The class lives on the page wrapper so
  // the field can span every section.
  const pageField = pathname === "/portfolio";

  // GSAP smooth scrolling: the fixed header lives OUTSIDE the smoothed
  // wrapper (transformed ancestors break position: fixed). Touch devices
  // keep native scrolling (smoothTouch off). Body scroll locks
  // (modal-open / filters-open) still work — the native scrollbar remains
  // the source of truth and overflow: hidden removes it.
  useEffect(() => {
    const smoother = ScrollSmoother.create({
      wrapper: "#smooth-wrapper",
      content: "#smooth-content",
      smooth: 0.9,
      effects: false,
      smoothTouch: 0,
    });
    return () => smoother.kill();
  }, []);

  return (
    <div className={`app-shell${showTicker ? " has-ticker" : ""}`}>
      <ScrollRestoration />
      <NavBar showTicker={showTicker} />
      <div id="smooth-wrapper">
        <div id="smooth-content">
          <main className={`container page grid-box${pageField ? " page--field" : ""}`}>
            <Outlet />
          </main>
          {/* In-flow at the bottom of the page, above the footer (and so above
              the fixed crawl). The modal it opens portals to <body>. */}
          <ReportProblem />
          <Footer />
        </div>
      </div>
      {/* position: fixed must live OUTSIDE the smoothed content (a transformed
          ancestor turns fixed into absolute): inside, the bar sat at the very
          bottom of the page on desktop and nobody saw it (2026-10-26). */}
      <ConsentBanner />
    </div>
  );
}

export default App;
