import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useUserInfoQuery } from "../../features/account/accountApi"
import CardLoader from "../shared/components/CardLoader";

export default function RequireAuth() {
    const {data: user, isLoading} = useUserInfoQuery();
    const location = useLocation();

    if (isLoading) return <CardLoader />

    if (!user) {
        return <Navigate to='/login' state={{from: location}} />
    }

    return (
        <Outlet />
    )
}