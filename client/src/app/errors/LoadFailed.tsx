// A page whose own data didn't arrive (offline, a timeout): not a 404, since
// the thing may well exist, so it says so and offers another try.
export default function LoadFailed({ what, onRetry }: { what: string; onRetry: () => void }) {
  return (
    <div className="panel center-box full-span">
      <h3>We couldn't load this {what}.</h3>
      <p className="est-note">Check your connection and try again in a moment.</p>
      <button type="button" className="btn" onClick={onRetry}>Try again</button>
    </div>
  )
}
