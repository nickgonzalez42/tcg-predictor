import { useLocation } from "react-router-dom";

export default function ServerError() {
    const { state } = useLocation();

    return (
        <div className="panel">
            {state?.error ? (
                <>
                    <h3 style={{ color: 'var(--secondary)' }}>{state.error.title}</h3>
                    <hr className="divider" />
                    <p>{state.error.detail}</p>
                </>
            ) : (
                <h3>Server error</h3>
            )}
        </div>
    )
}
