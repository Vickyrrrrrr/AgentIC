import axios, { AxiosError } from 'axios';
import { supabase } from './supabaseClient';
import type { ApiError } from './lib/types';

const base = import.meta.env.VITE_API_BASE_URL || (import.meta.env.DEV ? '/api' : '');
const API_VERSION = 'v1';

const cleanBase = base.replace(/\/$/, '');
export const API_BASE = cleanBase ? `${cleanBase}/api/${API_VERSION}` : `/api/${API_VERSION}`;

export const api = axios.create({
  baseURL: API_BASE,
  headers: { 
    'ngrok-skip-browser-warning': 'true',
    'Content-Type': 'application/json',
  },
});

const AUTH_ENABLED = Boolean(import.meta.env.VITE_SUPABASE_URL);

api.interceptors.request.use(async (config) => {
  if (!AUTH_ENABLED) return config;
  
  try {
    const { data: { session } } = await supabase.auth.getSession();
    if (session?.access_token) {
      config.headers.Authorization = `Bearer ${session.access_token}`;
    }
  } catch {
    // No session — request goes without auth
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ApiError>) => {
    if (error.response) {
      const data = error.response.data;
      if (data?.detail) {
        return Promise.reject(new Error(data.detail));
      }
      if (data?.message) {
        return Promise.reject(new Error(data.message));
      }
    }
    return Promise.reject(error);
  }
);

export const unwrap = async <T,>(
  promise: Promise<{ data: T | null; status: number }>
): Promise<[T | null, Error | null]> => {
  try {
    const response = await promise;
    return [response.data, null];
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error';
    return [null, new Error(message)];
  }
};
