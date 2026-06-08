import { create } from "zustand";

interface AppState {
  currentPage: number;
  selectedIndustry: string | null;
  setCurrentPage: (page: number) => void;
  setSelectedIndustry: (industry: string | null) => void;
  nextPage: () => void;
  prevPage: () => void;
}

export const useAppStore = create<AppState>((set, get) => ({
  currentPage: 0,
  selectedIndustry: null,
  setCurrentPage: (page) => set({ currentPage: page }),
  setSelectedIndustry: (industry) => set({ selectedIndustry: industry }),
  nextPage: () => set((state) => ({ currentPage: Math.min(state.currentPage + 1, 4) })),
  prevPage: () => set((state) => ({ currentPage: Math.max(state.currentPage - 1, 0) })),
}));
