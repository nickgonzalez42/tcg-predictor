import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <div className="panel center-box">
      <div className="display" style={{ fontSize: '64px', lineHeight: 1 }}>404</div>
      <h3>We couldn't find that page.</h3>
      <Link className="btn" to='/catalog'>Back to the catalog</Link>
    </div>
  )
}
