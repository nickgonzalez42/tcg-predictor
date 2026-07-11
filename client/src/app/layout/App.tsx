import NavBar from "./NavBar";
import { Outlet, ScrollRestoration, useLocation } from "react-router-dom";

function App() {
  const { pathname } = useLocation();

  // The market ticker rides under the navbar on the landing + catalog screens.
  const showTicker = pathname === "/" || pathname === "/catalog";

  return (
    <div className={`app-shell${showTicker ? " has-ticker" : ""}`}>
      <ScrollRestoration />
      <NavBar showTicker={showTicker} />
      <main className="container page grid-box">
        <Outlet />
      </main>
    </div>
  );
}

export default App;
