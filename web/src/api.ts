import axios from 'axios';
import { supabase } from './supabaseClient';

const base = import.meta.env.VITE_API_BASE_URL || (import.meta.env.DEV ? '/api' : '');
export const API_BASE = base.replace(/\/$/, '');

// Pre-configured axios instance with auth + ngrok header
export const api = axios.create({
  baseURL: API_BASE,
  headers: { 'ngrok-skip-browser-warning': 'true' },
});

const AUTH_ENABLED = Boolean(import.meta.env.VITE_SUPABASE_URL);

// Attach Supabase JWT and BYOK Key to every request
api.interceptors.request.use(async (config) => {
  try {
    const byokKey = localStorage.getItem('agentic_byok_key');
    if (byokKey) {
      config.headers['X-LLM-API-Key'] = byokKey;
    }

    if (!AUTH_ENABLED) return config;

    const { data: { session } } = await supabase.auth.getSession();
    if (session?.access_token) {
      config.headers.Authorization = `Bearer ${session.access_token}`;
    }
  } catch {
    // No session — request goes without auth
  }
  return config;
});
