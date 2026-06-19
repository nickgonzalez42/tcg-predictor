import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { RouterProvider } from 'react-router-dom';
import { router } from './app/routes/Routes.tsx';
import { Provider } from 'react-redux';
import { store } from './app/store/store.ts';
import { ToastContainer } from 'react-toastify';
import 'react-toastify/ReactToastify.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Provider store={store}>
      <ToastContainer position='bottom-right' hideProgressBar theme='colored' />
      <RouterProvider router={router} />
    </Provider>
  </StrictMode>,
)
