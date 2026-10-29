import { lazy } from "react";

// Route-level code splitting (2026-10-29). Every page used to ship in the
// one entry chunk, so a first visit to the home page downloaded the card
// chart library, the scanner, the portfolio and every settings form before
// it could paint. Only the home page and the layout stay in the entry
// bundle; each page here is its own chunk, fetched the first time its route
// renders. App.tsx holds the Suspense boundary around the outlet.
export const Catalog = lazy(() => import("../../features/catalog/Catalog"));
export const CardDetails = lazy(() => import("../../features/catalog/CardDetails"));
export const PrivacyPage = lazy(() => import("../../features/about/PrivacyPage"));
export const AccountSettingsPage = lazy(() => import("../../features/account/AccountSettingsPage"));
export const AboutPage = lazy(() => import("../../features/about/AboutPage"));
export const ContactPage = lazy(() => import("../../features/about/ContactPage"));
export const TermsPage = lazy(() => import("../../features/about/TermsPage"));
export const ServerError = lazy(() => import("../errors/ServerError"));
export const NotFound = lazy(() => import("../errors/NotFound"));
export const LoginForm = lazy(() => import("../../features/account/LoginForm"));
export const RegisterForm = lazy(() => import("../../features/account/RegisterForm"));
export const Portfolio = lazy(() => import("../../features/watchlist/Portfolio"));
export const ScanPage = lazy(() => import("../../features/scan/ScanPage"));
export const Wishlist = lazy(() => import("../../features/watchlist/Wishlist"));
export const ReportsPage = lazy(() => import("../../features/reports/ReportsPage"));
export const ReportPage = lazy(() => import("../../features/reports/ReportPage"));
