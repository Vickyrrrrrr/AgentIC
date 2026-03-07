import axios from 'axios';

export const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:7860').replace(/\/$/, '');

// Pre-configured axios instance with ngrok header
export const api = axios.create({
  baseURL: API_BASE,
  headers: { 'ngrok-skip-browser-warning': 'true' },
});
