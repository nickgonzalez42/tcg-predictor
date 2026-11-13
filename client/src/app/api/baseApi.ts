import { fetchBaseQuery, type BaseQueryApi, type FetchArgs } from "@reduxjs/toolkit/query";
import { toast } from "react-toastify";
import { router } from "../routes/Routes";

const customBaseQuery = fetchBaseQuery({
    baseUrl: import.meta.env.VITE_API_URL,
    credentials: 'include'
});

type ErrorResponse = | string | {title: string} | {errors: string[]};

// A deploy restarts the API, and for a moment the server answers 502/503/504
// or nothing at all (2026-11-13: a portfolio page click landed in that window
// and the pager looked dead). Reads are safe to repeat, so a GET that meets a
// restart waits and tries again for up to about a minute before the page sees
// an error. Writes are never repeated.
const RESTART_WAITS_MS = [1500, 3000, 5000, 8000, 12000, 15000, 15000];

const isRead = (args: string | FetchArgs) =>
    typeof args === 'string' || !args.method || args.method.toUpperCase() === 'GET';

const isRestart = (status: unknown) =>
    status === 502 || status === 503 || status === 504 || status === 'FETCH_ERROR';

export const baseQueryWithErrorHandling = async (
    args: string | FetchArgs,
    api: BaseQueryApi,
    extraOptions: object) => {
        let result = await customBaseQuery(args, api, extraOptions);
        if (isRead(args)) {
            for (const wait of RESTART_WAITS_MS) {
                const e = result.error;
                if (!e || !isRestart(e.status === 'PARSING_ERROR' ? e.originalStatus : e.status)) break;
                await new Promise(resolve => setTimeout(resolve, wait));
                if (api.signal.aborted) break;
                result = await customBaseQuery(args, api, extraOptions);
            }
        }
        if (result.error) {
            const originalStatus = result.error.status === 'PARSING_ERROR' && result.error.originalStatus
                ? result.error.originalStatus
                : result.error.status;

            const responseData = result.error.data as ErrorResponse;
            
            switch (originalStatus) {
                case 400:
                    if (typeof responseData === 'string') {
                        toast.error(responseData);
                    } else if (responseData && 'errors' in responseData) {
                        throw Object.values(responseData.errors).flat().join(', ');
                    } else if (responseData && 'title' in responseData) {
                        toast.error(responseData.title)
                    }
                    break;
                case 401:
                    if (responseData && typeof responseData === 'object' && 'title' in responseData)
                    {
                        toast.error(responseData.title);
                    }
                    break;
                case 404:
                    if (responseData && typeof responseData === 'object' && 'title' in responseData)
                    {
                        router.navigate('/not-found')
                    }
                    break;
                case 500:
                    if (responseData && typeof responseData === 'object')
                    {
                        router.navigate('/server-error', {state: {error: responseData}})
                    }
                    break;
                default:
                    break;
            }
        }

        return result;
    }