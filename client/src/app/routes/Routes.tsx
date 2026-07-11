import { createBrowserRouter, Navigate } from "react-router-dom";
import App from "../layout/App";
import HomePage from "../../features/home/HomePage";
import Catalog from "../../features/catalog/Catalog";
import CardDetails from "../../features/catalog/CardDetails";
import AboutPage from "../../features/about/AboutPage";
import ContactPage from "../../features/contact/ContactPage";
import ServerError from "../errors/ServerError";
import NotFound from "../errors/NotFound";
import LoginForm from "../../features/account/loginForm";
import RegisterForm from "../../features/account/registerForm";
import RequireAuth from "./RequireAuth";
import Portfolio from "../../features/watchlist/Portfolio";
import Wishlist from "../../features/watchlist/Wishlist";

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
                    // Old bookmarks from when the page was called "Wishlist".
                    {path: '/wishlist', element: <Navigate replace to='/watchlist' />},
                ]
            },
            {path: '', element: <HomePage />},
            {path: '/catalog', element: <Catalog />},
            {path: '/catalog/:game/:id', element: <CardDetails />},
            {path: '/about', element: <AboutPage />},
            {path: '/contact', element: <ContactPage />},
            {path: '/not-found', element: <NotFound />},
            {path: '/server-error', element: <ServerError />},
            {path: '/login', element: <LoginForm />},
            {path: '/register', element: <RegisterForm />},
            {path: '*', element: <Navigate replace to='/not-found' />}
        ]
    }
])