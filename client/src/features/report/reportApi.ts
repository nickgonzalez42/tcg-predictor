import { createApi } from "@reduxjs/toolkit/query/react";
import { baseQueryWithErrorHandling } from "../../app/api/baseApi";

export type ReportBody = { message: string; pageUrl?: string; email?: string };

export const reportApi = createApi({
    reducerPath: 'reportApi',
    baseQuery: baseQueryWithErrorHandling,
    endpoints: (builder) => ({
        submitReport: builder.mutation<{ ok: boolean }, ReportBody>({
            query: (body) => ({ url: 'reports', method: 'POST', body }),
        }),
    }),
});

export const { useSubmitReportMutation } = reportApi;
