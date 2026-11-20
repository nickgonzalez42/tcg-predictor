import { Link } from "react-router-dom";
import TcgPromo from "../shared/components/TcgPromo";

// Minimal site footer — just the info/legal links, on every page. In-flow at
// the bottom (not fixed); how visitors and ad-network reviewers reach these.
export default function Footer() {
    return (
        <footer className="site-footer">
            <TcgPromo />
            <nav className="site-footer__links container" aria-label="Footer">
                <Link to="/about">About</Link>
                <Link to="/contact">Contact</Link>
                <Link to="/privacy">Privacy</Link>
                <Link to="/terms">Terms</Link>
            </nav>
        </footer>
    );
}
