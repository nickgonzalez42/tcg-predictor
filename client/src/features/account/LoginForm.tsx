import { Link, useLocation, useNavigate } from "react-router-dom";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { loginSchema, type LoginSchema } from "../../lib/schemas/loginSchema";
import GoogleSignInButton from "./GoogleSignInButton";
import { useLazyUserInfoQuery, useLoginMutation } from "./accountApi";
import { useFetchMoversQuery } from "../catalog/catalogApi";
import { usePageMeta } from "../../lib/usePageMeta";

export default function LoginForm() {
    usePageMeta("Sign in");
    // Playful teaser under the title, powered by the movers we already cache.
    const { data: movers } = useFetchMoversQuery({ count: 12 });
    const teaserPct = movers?.length
        ? movers.reduce((s, m) => s + (m.fcst12Pct ?? 0), 0) / movers.length
        : null;
    const [login, { isLoading }] = useLoginMutation();
    const [fetchUserInfo] = useLazyUserInfoQuery();
    const location = useLocation();
    const { register, handleSubmit, formState: { errors } } = useForm<LoginSchema>({
        mode: 'onTouched',
        resolver: zodResolver(loginSchema)
    });
    const navigate = useNavigate();

    const onSubmit = async (data: LoginSchema) => {
        // unwrap so a failed login stays on this page (the 401 toast comes
        // from baseApi's error handler) instead of bouncing to /portfolio.
        try {
            await login(data).unwrap();
        } catch {
            return;
        }
        await fetchUserInfo();
        navigate(location.state?.from || '/portfolio');
    }

    return (
        <div className="panel auth-card">
            <h3>Sign in</h3>
            {teaserPct != null && (
                <p className="mono auth-card__teaser">
                    markets don't sleep, top movers point{' '}
                    <span className={teaserPct >= 0 ? 'auth-teaser--up' : 'auth-teaser--down'}>
                        {teaserPct >= 0 ? '+' : '−'}{Math.abs(teaserPct).toFixed(1)}%
                    </span>{' '}
                    over 1 year
                </p>
            )}
            <form onSubmit={handleSubmit(onSubmit)}>
                <div className="field">
                    <label htmlFor="email">Email</label>
                    <input id="email" className="input" autoFocus {...register('email')} />
                    {errors.email && <span className="field__error">{errors.email.message}</span>}
                </div>
                <div className="field">
                    <label htmlFor="password">Password</label>
                    <input id="password" className="input" type="password" {...register('password')} />
                    {errors.password && <span className="field__error">{errors.password.message}</span>}
                </div>
                <button className="btn btn--block" disabled={isLoading} type="submit">
                    Sign in
                </button>
                <div className="auth-divider"><span>or</span></div>
                <GoogleSignInButton />
                <p style={{ textAlign: 'center' }}>
                    Don't have an account? <Link to='/register'>Sign up</Link>
                </p>
            </form>
        </div>
    )
}
