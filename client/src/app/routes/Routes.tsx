import { createBrowserRouter, Navigate } from "react-router-dom";
import App from "../layout/App";
import HomePage from "../../features/home/HomePage";
import Catalog from "../../features/catalog/Catalog";
import CardDetails from "../../features/catalog/CardDetails";
import PrivacyPage from '../../features/about/PrivacyPage';
import ProfileSettingsPage from '../../features/social/ProfileSettingsPage';
import PublicProfilePage from '../../features/social/PublicProfilePage';
import AboutPage from "../../features/about/AboutPage";
import ContactPage from "../../features/about/ContactPage";
import TermsPage from "../../features/about/TermsPage";
import ServerError from "../errors/ServerError";
import NotFound from "../errors/NotFound";
import LoginForm from "../../features/account/LoginForm";
import RegisterForm from "../../features/account/RegisterForm";
import RequireAuth from "./RequireAuth";
import Portfolio from "../../features/watchlist/Portfolio";
import Wishlist from "../../features/watchlist/Wishlist";
import NotificationsPage from "../../features/watchlist/NotificationsPage";
import ReportsPage from "../../features/reports/ReportsPage";
import ReportPage from "../../features/reports/ReportPage";

export const router = createBrowserRouter([
    {
        path: '/',
        element: <App />,
        children: [
            {
                element: <RequireAuth />,
                children: [
                    {path: '/portfolio', element: <Portfolio />},
                    {path: '/watchlist', element: <Wishlist />},
                    {path: '/notifications', element: <NotificationsPage />},
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
            {path: '/settings/profile', element: <ProfileSettingsPage />},
            {path: '/u/:handle', element: <PublicProfilePage />},
            {path: '/not-found', element: <NotFound />},
            {path: '/server-error', element: <ServerError />},
            {path: '/login', element: <LoginForm />},
            {path: '/register', element: <RegisterForm />},
            {path: '*', element: <Navigate replace to='/not-found' />}
        ]
    }
])