import z from "zod";

// At least 6 chars with one digit, lower, upper, and special; no whitespace.
// (An earlier version had HTML-escaped entities inside the character class —
// letters like "a" and "q" counted as "special" — and capped length at 10.)
const passwordValidation =
    /(?=^.{6,}$)(?=.*\d)(?=.*[a-z])(?=.*[A-Z])(?=.*[!@#$%^&*()_+}{":;'?/>.<,])(?!.*\s).*$/;

export const registerSchema = z.object({
    email: z.string().email(),
    password: z.string().regex(passwordValidation, {
        message: 'Password must contain 1 lowercase, 1 uppercase, 1 number, 1 special character and be at least 6 characters long'
    })
})

export type RegisterSchema = z.infer<typeof registerSchema>