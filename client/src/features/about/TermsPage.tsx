import { Link } from "react-router-dom";
import { usePageMeta } from "../../lib/usePageMeta";

// Terms of Service. Plain-English; the "not financial advice" section matters
// most given the site publishes price forecasts.
export default function TermsPage() {
    usePageMeta("Terms of Service", "The terms for using cardstock.");

    return (
        <div className="full-span legal">
            <h1>Terms of Service</h1>
            <p className="est-note">Last updated: July 15, 2026</p>

            <p>
                By using cardstock (the "Service," at cardstock.guide) you agree to these
                terms. If you don't agree, please don't use the Service.
            </p>

            <h2>What cardstock is</h2>
            <p>
                cardstock is an informational tool for tracking trading-card prices and
                viewing machine-generated price forecasts, provided for research and
                entertainment.
            </p>

            <h2>Not financial advice</h2>
            <p>
                Nothing on cardstock is financial, investment, or trading advice. Price
                forecasts are model estimates that are frequently wrong, and past
                performance does not predict future results. Any decision to buy, sell, or
                hold a card is yours alone. Do your own research.
            </p>

            <h2>Accounts</h2>
            <p>
                You're responsible for activity under your account and for keeping your
                login secure. Provide a valid email. We may suspend or remove accounts that
                abuse the Service or violate these terms.
            </p>

            <h2>Your content</h2>
            <p>
                You keep ownership of comments and other content you post, and grant us a
                non-exclusive license to display it on the Service. Don't post content that
                is illegal, hateful, harassing, deceptive, infringing, spam, or otherwise
                objectionable. We may remove content or hide it via automated moderation at
                our discretion.
            </p>

            <h2>Acceptable use</h2>
            <p>
                Don't scrape, bulk-download, overload, reverse-engineer, or attempt to
                disrupt the Service, and don't use it to break the law or infringe others'
                rights.
            </p>

            <h2>Data and accuracy</h2>
            <p>
                Prices, histories, and forecasts are aggregated from third-party and public
                sources and are provided "as is." They may be delayed, incomplete, or
                inaccurate. Card names, images, and trademarks belong to their respective
                owners; cardstock is not affiliated with or endorsed by any card publisher.
            </p>

            <h2>Intellectual property</h2>
            <p>
                The site design, original text, and the forecasting models are ours. You
                may use the Service for personal, non-commercial purposes.
            </p>

            <h2>Disclaimers</h2>
            <p>
                The Service is provided "as is" and "as available," without warranties of
                any kind, express or implied, including merchantability, fitness for a
                particular purpose, and non-infringement.
            </p>

            <h2>Limitation of liability</h2>
            <p>
                To the maximum extent permitted by law, cardstock and its operators are not
                liable for any indirect, incidental, or consequential damages, or for any
                losses arising from your use of the Service or reliance on its data or
                forecasts.
            </p>

            <h2>Changes</h2>
            <p>
                We may update these terms; continued use after changes means you accept
                them. Material changes are reflected in the "last updated" date above.
            </p>

            <h2>Contact</h2>
            <p>
                Questions about these terms? Reach us via the <Link to="/contact">contact
                page</Link>.
            </p>
        </div>
    );
}
