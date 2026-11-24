import { Link } from "react-router-dom";
import { usePageMeta } from "../../lib/usePageMeta";

// The 404 page. A page whose own subject is missing (a card, an article, a
// report) shows it in place with its own words and way back, so the address
// stays as typed and Back still works.
export default function NotFound({
  message = "We couldn't find that page.", to = '/catalog', linkText = 'Back to the catalog',
}: { message?: string; to?: string; linkText?: string }) {
  // The server answers 200 for any address the app might own, so this is the
  // only place that knows the page does not exist: say so to crawlers.
  usePageMeta("Page not found", undefined, { noindex: true });
  return (
    <div className="panel center-box full-span">
      <div className="display" style={{ fontSize: '64px', lineHeight: 1 }}>404</div>
      <h3>{message}</h3>
      <Link className="btn" to={to}>{linkText}</Link>
    </div>
  )
}
