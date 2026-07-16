import { useState } from "react";
import Modal from "../../app/shared/components/Modal";
import { useSubmitReportMutation } from "./reportApi";
import { useUserInfoQuery } from "../account/accountApi";

// Small fixed tab (bottom-right) that opens a modal to report an issue. The
// current page URL is captured automatically; email is optional. Signed-in
// users only (each report notifies the site owner, so it's kept accountable).
export default function ReportProblem() {
    const { data: user } = useUserInfoQuery();
    const [open, setOpen] = useState(false);
    const [message, setMessage] = useState('');
    const [email, setEmail] = useState('');
    const [sent, setSent] = useState(false);
    const [hint, setHint] = useState<string | null>(null);
    const [submit, { isLoading, error }] = useSubmitReportMutation();
    const apiError = (error as { data?: string } | undefined)?.data;

    const send = async () => {
        if (message.trim().length < 5) {
            setHint('Add a few more words so we can act on it.');
            return;
        }
        setHint(null);
        try {
            await submit({
                message: message.trim(),
                email: email.trim() || undefined,
                pageUrl: window.location.href,
            }).unwrap();
            setSent(true);
        } catch { /* apiError renders below */ }
    };

    const close = () => {
        setOpen(false);
        // Reset once the modal is gone so a reopened form is fresh.
        setTimeout(() => { setSent(false); setMessage(''); setEmail(''); setHint(null); }, 200);
    };

    if (!user) return null;

    return (
        <>
            <div className="report-bar">
                <button className="report-tab" onClick={() => setOpen(true)}
                    title="Report a problem" aria-label="Report a problem">
                    <span className="report-tab__icon" aria-hidden="true">⚠</span>
                    <span className="report-tab__label">Report</span>
                </button>
            </div>

            {open && (
                <Modal title="Report a problem" onClose={close}>
                    {sent ? (
                        <p>
                            Thanks, your report came through.
                            {email.trim() && " We'll follow up if we need more detail."}
                        </p>
                    ) : (
                        <>
                            <p className="est-note">
                                Found a bug or something off? Tell us what happened. The page
                                you're on is included automatically.
                            </p>
                            <textarea className="input" rows={5} maxLength={4000} autoFocus
                                placeholder="What went wrong?"
                                value={message}
                                onChange={e => {
                                    setMessage(e.target.value);
                                    if (e.target.value.trim().length >= 5) setHint(null);
                                }} />
                            <label className="field-label" htmlFor="report-email">Email (optional)</label>
                            <input id="report-email" className="input" type="email"
                                placeholder="you@example.com for a reply"
                                value={email} onChange={e => setEmail(e.target.value)} />
                            {(hint || apiError) && <p className="comment-error">{hint ?? String(apiError)}</p>}
                            <div className="modal__actions">
                                <button className="btn btn--outline" onClick={close}>Cancel</button>
                                <button className="btn" disabled={isLoading}
                                    onClick={send}>Send report</button>
                            </div>
                        </>
                    )}
                </Modal>
            )}
        </>
    );
}
