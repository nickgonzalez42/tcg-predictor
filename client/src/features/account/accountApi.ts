import { createApi } from "@reduxjs/toolkit/query/react";
import { baseQueryWithErrorHandling } from "../../app/api/baseApi";
import type { User } from "../../app/models/user";

export type AccountSettings = {
    email: string; alertEmails: boolean; hasPassword: boolean; providers: string[]; createdAt: string;
};
import type { LoginSchema } from "../../lib/schemas/loginSchema";
import type { RegisterSchema } from "../../lib/schemas/registerSchema";
import { router } from "../../app/routes/Routes";
import { toast } from "react-toastify";
import { watchlistApi } from "../watchlist/watchlistApi";

export const accountApi = createApi({
    reducerPath: 'accountApi',
    baseQuery: baseQueryWithErrorHandling,
    tagTypes: ['UserInfo', 'Settings'],
    endpoints: (builder) => ({
        login: builder.mutation<void, LoginSchema>({
            query: (creds) => {
                return {
                    url: 'login?useCookies=true',
                    method: 'POST',
                    body: creds
                }
            },
            async onQueryStarted(_, {dispatch, queryFulfilled}) {
                try{
                    await queryFulfilled;
                    dispatch(accountApi.util.invalidateTags(['UserInfo']));
                } catch(error) {
                    console.log(error);
                }
            }
        }),
        register: builder.mutation<void, RegisterSchema>({
            query: (creds) => {
                return {
                    url: 'account/register',
                    method: 'POST',
                    body: creds
                }
            },
            async onQueryStarted(_, {queryFulfilled}) {
                try {
                    await queryFulfilled;
                    toast.success('Registration successful');
                    router.navigate('/login');
                } catch (error) {
                    console.log(error);
                    throw error;
                }
            }
        }),
        userInfo: builder.query<User, void>({
            query: () => 'account/user-info',
            providesTags: ['UserInfo']
        }),
        // ----- Account settings (2026-10-25): the minimal self-service surface -----
        settings: builder.query<AccountSettings, void>({
            query: () => 'account/settings',
            providesTags: ['Settings'],
        }),
        updateSettings: builder.mutation<void, { alertEmails: boolean }>({
            query: (body) => ({ url: 'account/settings', method: 'PUT', body }),
            invalidatesTags: ['Settings'],
        }),
        changePassword: builder.mutation<void, { currentPassword?: string; newPassword: string }>({
            query: (body) => ({ url: 'account/password', method: 'POST', body }),
            invalidatesTags: ['Settings'],
        }),
        deleteAccount: builder.mutation<void, void>({
            query: () => ({ url: 'account', method: 'DELETE', body: { confirm: 'DELETE' } }),
            async onQueryStarted(_, { dispatch, queryFulfilled }) {
                try {
                    await queryFulfilled;
                    toast.success('Your account has been deleted');
                } finally {
                    dispatch(accountApi.util.invalidateTags(['UserInfo']));
                    dispatch(watchlistApi.util.resetApiState());
                    router.navigate('/');
                }
            },
        }),
        logout: builder.mutation<void, void>({
            query: () => ({
                url: 'account/logout',
                method: 'POST'
            }),
            async onQueryStarted(_, {dispatch, queryFulfilled}) {
                // Even if the server call fails (e.g. session already gone),
                // clear the local session: user-scoped caches must not survive
                // into the next login.
                try {
                    await queryFulfilled;
                } finally {
                    dispatch(accountApi.util.invalidateTags(['UserInfo']));
                    dispatch(watchlistApi.util.resetApiState());
                    router.navigate('/');
                }
            }

        })
    })
})

export const {useLoginMutation, useRegisterMutation, useLogoutMutation, useUserInfoQuery, useLazyUserInfoQuery,
    useSettingsQuery, useUpdateSettingsMutation, useChangePasswordMutation, useDeleteAccountMutation} = accountApi;