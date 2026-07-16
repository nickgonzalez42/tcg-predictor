import { useState } from "react";
import { usePageMeta } from "../../lib/usePageMeta";
import { useSubmitReportMutation } from "../report/reportApi";

// Contact form — posts to the same /api/reports endpoint the "report a problem"
// tab uses, so messages land in one place (and notify once SNS is configured).
export default function ContactPage() {
    usePageMeta("Contact", "Get in touch with cardstock: questions, feedback, corrections, or bugs.");
    const [message, setMessage] = useState('');
    const [email, setEmail] = useState('');
    const [sent, setSent] = useState(false);
    const [submit, { isLoading, error }] = useSubmitReportMutation();
    const apiError = (error as { data?: string } | undefined)?.data;

    const send = async () => {
        if (message.trim().length < 5) return;
        try {
            await submit({
                message: message.trim(),
                email: email.trim() || undefined,
                pageUrl: 'contact-form',
            }).unwrap();
            setSent(true);
        } catch { /* apiError renders below */ }
    };

    return (
        <div className="full-span legal">
            <h1>Contact</h1>
            {sent ? (
                <p>
                    Thanks, your message came through.
                    {email.trim() && " We'll reply to the address you gave if a response is needed."}
                </p>
            ) : (
                <>
                    <p>
                        Questions, feedback, a data correction, or a bug? Send us a note below
                        and it goes straight to the team. Add your email if you'd like a reply.
                    </p>

                    <label className="field-label" htmlFor="c-msg">Message</label>
                    <textarea id="c-msg" className="input" rows={6} maxLength={4000}
                        placeholder="How can we help?"
                        value={message} onChange={e => setMessage(e.target.value)} />

                    <label className="field-label" htmlFor="c-email">Email (optional)</label>
                    <input id="c-email" className="input" type="email" placeholder="you@example.com"
                        value={email} onChange={e => setEmail(e.target.value)} />

                    {apiError && <p className="profile-settings__error">{String(apiError)}</p>}

                    <p style={{marginTop: "20px"}}>
                        <button className="btn" disabled={isLoading || message.trim().length < 5}
                            onClick={send}>Send message</button>
                    </p>
                </>
            )}
        </div>
    );
}
