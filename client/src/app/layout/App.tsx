import { Suspense, useEffect } from "react";
import NavBar from "./NavBar";
import Footer from "./Footer";
import CardLoader from "../shared/components/CardLoader";
import { trackPageView } from "../../lib/pageview";
import ReportProblem from "../../features/report/ReportProblem";
import { Outlet, ScrollRestoration, useLocation } from "react-router-dom";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { ScrollSmoother } from "gsap/ScrollSmoother";

gsap.registerPlugin(ScrollTrigger, ScrollSmoother);

function App() {
  const { pathname } = useLocation();

  // Count this view (see lib/pageview.ts): the search string goes along on
  // the first view only, so a campaign tag is recorded without turning every
  // filter change into a separate page.
  useEffect(() => {
    trackPageView(pathname);
  }, [pathname]);

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
            {/* Pages other than home are lazy chunks (Routes.tsx); the
                loader holds their place for the one round trip it takes. */}
            <Suspense fallback={<CardLoader />}>
              <Outlet />
            </Suspense>
          </main>
          {/* In-flow at the bottom of the page, above the footer (and so above
              the fixed crawl). The modal it opens portals to <body>. */}
          <ReportProblem />
          <Footer />
        </div>
      </div>
    </div>
  );
}

export default App;
