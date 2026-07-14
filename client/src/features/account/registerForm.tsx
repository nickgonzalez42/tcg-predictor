import { zodResolver } from "@hookform/resolvers/zod";
import { registerSchema } from "../../lib/schemas/registerSchema";
import { useRegisterMutation } from "./accountApi"
import { useForm } from "react-hook-form";
import { Link } from "react-router-dom";
import { usePageMeta } from "../../lib/usePageMeta";

export default function registerForm() {
    usePageMeta("Create account");
    const [registerUser] = useRegisterMutation();
    const { register, handleSubmit, setError, formState: { errors, isValid, isLoading } } = useForm<registerSchema>({
        mode: 'onTouched',
        resolver: zodResolver(registerSchema)
    })

    const onSubmit = async (data: registerSchema) => {
        try {
            await registerUser(data).unwrap();
        } catch (error) {
            const apiError = error as { message: string };
            if (apiError.message && typeof apiError.message === 'string') {
                const errorArray = apiError.message.split(',');

                errorArray.forEach(e => {
                    if (e.includes('Password')) {
                        setError('password', { message: e })
                    } else if (e.includes('Email')) {
                        setError('email', { message: e })
                    }
                })
            }
        }
    }

    return (
        <div className="panel auth-card">
            <h3>🔒 Register</h3>
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
                <button className="btn btn--block" disabled={isLoading || !isValid} type="submit">
                    Register
                </button>
                <p style={{ textAlign: 'center' }}>
                    Already have an account? <Link to='/login'>Login</Link>
                </p>
            </form>
        </div>
    )
}
