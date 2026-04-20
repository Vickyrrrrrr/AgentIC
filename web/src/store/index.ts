import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export interface JobSummary {
  job_id: string;
  design_name: string;
  status: string;
  current_state: string;
  created_at: number;
  event_count: number;
  human_in_loop: boolean;
}

export interface ProfileSummary {
  id: string;
  email: string;
  plan: string;
  plan_type: string;
  build_limit: number | null;
  successful_builds: number;
}

export interface BuildOption {
  name: string;
  description: string;
}

interface AppState {
  session:unknown | null;
  selectedPage: string;
  theme: 'light' | 'dark';
  designs: BuildOption[];
  selectedDesign: string;
  jobs: JobSummary[];
  profile: ProfileSummary | null;
  showBillingModal: boolean;
  authLoading: boolean;
  
  setSession: (session: unknown | null) => void;
  setSelectedPage: (page: string) => void;
  setTheme: (theme: 'light' | 'dark') => void;
  setDesigns: (designs: BuildOption[]) => void;
  setSelectedDesign: (design: string) => void;
  setJobs: (jobs: JobSummary[]) => void;
  addJob: (job: JobSummary) => void;
  updateJob: (jobId: string, updates: Partial<JobSummary>) => void;
  setProfile: (profile: ProfileSummary | null) => void;
  setShowBillingModal: (show: boolean) => void;
  setAuthLoading: (loading: boolean) => void;
  toggleTheme: () => void;
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      session: null,
      selectedPage: 'Home',
      theme: 'dark',
      designs: [],
      selectedDesign: '',
      jobs: [],
      profile: null,
      showBillingModal: false,
      authLoading: true,

      setSession: (session) => set({ session }),
      setSelectedPage: (selectedPage) => set({ selectedPage }),
      setTheme: (theme) => set({ theme }),
      setDesigns: (designs) => set({ designs }),
      setSelectedDesign: (selectedDesign) => set({ selectedDesign }),
      setJobs: (jobs) => set({ jobs }),
      addJob: (job) => set((state) => ({ jobs: [job, ...state.jobs] })),
      updateJob: (jobId, updates) => set((state) => ({
        jobs: state.jobs.map((j) => j.job_id === jobId ? { ...j, ...updates } : j)
      })),
      setProfile: (profile) => set({ profile }),
      setShowBillingModal: (showBillingModal) => set({ showBillingModal }),
      setAuthLoading: (authLoading) => set({ authLoading }),
      toggleTheme: () => set((state) => ({ theme: state.theme === 'dark' ? 'light' : 'dark' })),
    }),
    {
      name: 'agentic-storage',
      partialize: (state) => ({ 
        theme: state.theme,
        selectedDesign: state.selectedDesign 
      }),
    }
  )
);

interface BuildState {
  currentJobId: string | null;
  events: unknown[];
  jobStatus: string | null;
  result: unknown | null;
  isRunning: boolean;
  
  setCurrentJobId: (jobId: string | null) => void;
  addEvent: (event: unknown) => void;
  setEvents: (events: unknown[]) => void;
  setJobStatus: (status: string | null) => void;
  setResult: (result: unknown | null) => void;
  setIsRunning: (running: boolean) => void;
  resetBuild: () => void;
}

export const useBuildStore = create<BuildState>()((set) => ({
  currentJobId: null,
  events: [],
  jobStatus: null,
  result: null,
  isRunning: false,

  setCurrentJobId: (currentJobId) => set({ currentJobId }),
  addEvent: (event) => set((state) => ({ events: [...state.events, event] })),
  setEvents: (events) => set({ events }),
  setJobStatus: (jobStatus) => set({ jobStatus }),
  setResult: (result) => set({ result }),
  setIsRunning: (isRunning) => set({ isRunning }),
  resetBuild: () => set({ 
    currentJobId: null, 
    events: [], 
    jobStatus: null, 
    result: null, 
    isRunning: false 
  }),
}));