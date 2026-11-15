import { Link } from "react-router-dom";

// The 404 page. A page whose own subject is missing (a card, an article, a
// report) shows it in place with its own words and way back, so the address
// stays as typed and Back still works.
export default function NotFound({
  message = "We couldn't find that page.", to = '/catalog', linkText = 'Back to the catalog',
}: { message?: string; to?: string; linkText?: string }) {
  return (
    <div className="panel center-box full-span">
      <div className="display" style={{ fontSize: '64px', lineHeight: 1 }}>404</div>
      <h3>{message}</h3>
      <Link className="btn" to={to}>{linkText}</Link>
    </div>
  )
}
