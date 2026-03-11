import axios from 'axios';
import { supabase } from './supabaseClient';

export const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:7860').replace(/\/$/, '');

// Pre-configured axios instance with auth + ngrok header
export const api = axios.create({
  baseURL: API_BASE,
  headers: { 'ngrok-skip-browser-warning': 'true' },
});

// Attach Supabase JWT to every request
api.interceptors.request.use(async (config) => {
  try {
    const { data: { session } } = await supabase.auth.getSession();
    if (session?.access_token) {
      config.headers.Authorization = `Bearer ${session.access_token}`;
    }
  } catch {
    // No session — request goes without auth (backend will 401 if needed)
  }
  return config;
});
