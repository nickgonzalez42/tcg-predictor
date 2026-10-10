import { useState } from "react";

// Weekly-report email capture (2026-10-09). Collects addresses only — the
// Friday send job ships separately once the sending domain is verified.
export default function NewsletterSignup({ source }: { source: string }) {
    const [email, setEmail] = useState("");
    const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");

    const submit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!email || state === "busy") return;
        setState("busy");
        try {
            const r = await fetch(`${import.meta.env.VITE_API_URL}/newsletter/subscribe`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email, source }),
            });
            setState(r.ok ? "done" : "error");
        } catch {
            setState("error");
        }
    };

    if (state === "done") {
        return (
            <p className="newsletter newsletter--done">
                You're on the list — the next report lands Friday morning.
            </p>
        );
    }
    return (
        <form className="newsletter" onSubmit={submit}>
            <div className="newsletter__copy">
                <strong>Get the weekly report by email</strong>
                <span>One email every Friday — the market story, the movers and the
                    model's record. Nothing else.</span>
            </div>
            <div className="newsletter__row">
                <input type="email" required placeholder="you@example.com"
                    value={email} onChange={e => setEmail(e.target.value)}
                    aria-label="Email address" />
                <button type="submit" disabled={state === "busy"}>
                    {state === "busy" ? "…" : "Subscribe"}
                </button>
            </div>
            {state === "error" &&
                <span className="newsletter__err">Something went wrong — try again.</span>}
        </form>
    );
}
