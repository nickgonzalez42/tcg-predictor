import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <div className="panel center-box">
      <div style={{ fontSize: '4rem' }}>🔍</div>
      <h3>Could not find what you were looking for.</h3>
      <Link className="btn" to='/catalog'>Go back to catalog</Link>
    </div>
  )
}
