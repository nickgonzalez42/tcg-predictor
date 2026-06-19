import NavBar from "./NavBar";
import { Outlet, ScrollRestoration } from "react-router-dom";
import { useAppSelector } from "../store/store";

function App() {
  const { darkMode } = useAppSelector(state => state.ui);

  return (
    <div className={`app-shell ${darkMode ? "" : "theme-light"}`}>
      <ScrollRestoration />
      <NavBar />
      <main className="container page">
        <Outlet />
      </main>
    </div>
  );
}

export default App;
