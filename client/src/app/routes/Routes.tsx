import { createBrowserRouter, Navigate } from "react-router-dom";
import App from "../layout/App";
import HomePage from "../../features/home/HomePage";
import RequireAuth from "./RequireAuth";
// Every other page is a lazy chunk; see pages.ts for why.
import {
    Catalog, CardDetails, PrivacyPage, AccountSettingsPage, AboutPage, ContactPage,
    TermsPage, ServerError, NotFound, LoginForm, RegisterForm, Portfolio, ScanPage,
    Wishlist, ReportsPage, ReportPage,
} from "./pages";

export const router = createBrowserRouter([
    {
        path: '/',
        element: <App />,
        children: [
            {
                element: <RequireAuth />,
                children: [
                    {path: '/portfolio', element: <Portfolio />},
                    {path: '/scan', element: <ScanPage />},
                    {path: '/watchlist', element: <Wishlist />},
                    {path: '/settings', element: <AccountSettingsPage />},
                    {path: '/settings/profile', element: <Navigate replace to='/settings' />},
                    // Old bookmarks from when the page was called "Wishlist".
                    {path: '/wishlist', element: <Navigate replace to='/watchlist' />},
                ]
            },
            {path: '', element: <HomePage />},
            {path: '/catalog', element: <Catalog />},
            {path: '/catalog/:game/:id', element: <CardDetails />},
            {path: '/reports', element: <ReportsPage />},
            {path: '/reports/:slug', element: <ReportPage />},
            {path: '/about', element: <AboutPage />},
            {path: '/privacy', element: <PrivacyPage />},
            {path: '/terms', element: <TermsPage />},
            {path: '/contact', element: <ContactPage />},
            {path: '/not-found', element: <NotFound />},
            {path: '/server-error', element: <ServerError />},
            {path: '/login', element: <LoginForm />},
            {path: '/register', element: <RegisterForm />},
            {path: '*', element: <Navigate replace to='/not-found' />}
        ]
    }
])
