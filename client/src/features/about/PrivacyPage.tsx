import { Link } from "react-router-dom";
import { usePageMeta } from "../../lib/usePageMeta";

// Privacy policy — required for AdSense approval (ads/cookies disclosure)
// and good practice regardless. Plain content page on the standard grid.
export default function PrivacyPage() {
    usePageMeta("Privacy Policy", "How CardStock handles your data, cookies, and advertising.");

    return (
        <div className="full-span legal">
            <h1>Privacy Policy</h1>
            <p className="est-note">Last updated: July 14, 2026</p>

            <h2>What we collect</h2>
            <p>
                If you create an account, we store your email address and the cards you
                track (portfolio and watchlist entries, including quantities, grades, and
                any purchase prices or notes you enter). That's it. We don't collect
                names, addresses, or payment details.
            </p>

            <h2>How it's used</h2>
            <p>
                Your tracked cards exist solely to power your portfolio and watchlist.
                We don't sell or share your personal data with third parties.
            </p>

            <h2>Cookies</h2>
            <p>
                We use a session cookie to keep you signed in. If advertising is enabled,
                Google AdSense and its partners may set cookies to serve ads, including
                personalized ads. Third-party vendors, including Google, use cookies to
                serve ads based on your prior visits to this and other websites. You can
                opt out of personalized advertising at{' '}
                <a href="https://www.google.com/settings/ads" rel="noreferrer" target="_blank">
                    Google Ads Settings
                </a>.
            </p>

            <h2>Price data</h2>
            <p>
                Card prices and market data shown on this site are aggregated from public
                sources and are informational only. Nothing here is financial advice.
            </p>

            <h2>Contact</h2>
            <p>
                Questions about this policy? Use our <Link to="/contact">contact page</Link>{' '}
                or email <a href="mailto:hello@cardstock.guide">hello@cardstock.guide</a>.
            </p>
        </div>
    );
}
