import { useState } from "react";
import Modal from "../../app/shared/components/Modal";
import { useSubmitReportMutation } from "./reportApi";

// Small fixed tab (bottom-right) that opens a modal to report an issue. The
// current page URL is captured automatically; email is optional. Available to
// everyone, signed in or not.
export default function ReportProblem() {
    const [open, setOpen] = useState(false);
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
                pageUrl: window.location.href,
            }).unwrap();
            setSent(true);
        } catch { /* apiError renders below */ }
    };

    const close = () => {
        setOpen(false);
        // Reset once the modal is gone so a reopened form is fresh.
        setTimeout(() => { setSent(false); setMessage(''); setEmail(''); }, 200);
    };

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
                                value={message} onChange={e => setMessage(e.target.value)} />
                            <label className="field-label" htmlFor="report-email">Email (optional)</label>
                            <input id="report-email" className="input" type="email"
                                placeholder="you@example.com for a reply"
                                value={email} onChange={e => setEmail(e.target.value)} />
                            {apiError && <p className="comment-error">{String(apiError)}</p>}
                            <div className="modal__actions">
                                <button className="btn btn--outline" onClick={close}>Cancel</button>
                                <button className="btn" disabled={isLoading || message.trim().length < 5}
                                    onClick={send}>Send report</button>
                            </div>
                        </>
                    )}
                </Modal>
            )}
        </>
    );
}
