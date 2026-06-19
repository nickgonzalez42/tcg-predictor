import { Link, useLocation, useNavigate } from "react-router-dom";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { loginSchema, type LoginSchema } from "../../lib/schemas/loginSchema";
import { useLazyUserInfoQuery, useLoginMutation } from "./accountApi";

export default function loginForm() {
    const [login, { isLoading }] = useLoginMutation();
    const [fetchUserInfo] = useLazyUserInfoQuery();
    const location = useLocation();
    const { register, handleSubmit, formState: { errors } } = useForm<LoginSchema>({
        mode: 'onTouched',
        resolver: zodResolver(loginSchema)
    });
    const navigate = useNavigate();

    const onSubmit = async (data: LoginSchema) => {
        await login(data);
        await fetchUserInfo();
        navigate(location.state?.from || '/catalog');
    }

    return (
        <div className="panel auth-card">
            <h3>🔒 Sign in</h3>
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
                <p style={{ textAlign: 'center' }}>
                    Don't have an account? <Link to='/register'>Sign up</Link>
                </p>
            </form>
        </div>
    )
}
