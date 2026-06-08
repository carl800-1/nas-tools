import { useAppStore } from "@/store/useAppStore";
import CoverPage from "@/pages/CoverPage";
import IndustryPage from "@/pages/IndustryPage";
import ProcessPage from "@/pages/ProcessPage";
import ToolsPage from "@/pages/ToolsPage";
import SummaryPage from "@/pages/SummaryPage";
import { ChevronLeft, ChevronRight, Home, Sun, Factory, Wrench, Target } from "lucide-react";

const pages = [
  { id: 0, name: "封面", icon: Home, component: CoverPage },
  { id: 1, name: "行业分类", icon: Sun, component: IndustryPage },
  { id: 2, name: "工序分析", icon: Factory, component: ProcessPage },
  { id: 3, name: "工具展示", icon: Wrench, component: ToolsPage },
  { id: 4, name: "总结展望", icon: Target, component: SummaryPage }
];

export default function App() {
  const { currentPage, nextPage, prevPage, setCurrentPage } = useAppStore();
  const CurrentPageComponent = pages[currentPage].component;

  return (
    <div className="min-h-screen bg-slate-950 relative">
      {/* Navigation sidebar */}
      <div className="fixed left-4 top-1/2 -translate-y-1/2 z-50 flex flex-col gap-2">
        {pages.map((page) => {
          const Icon = page.icon;
          return (
            <button
              key={page.id}
              onClick={() => setCurrentPage(page.id)}
              className={`
                p-3 rounded-xl transition-all duration-300 flex flex-col items-center gap-1
                ${currentPage === page.id 
                  ? 'bg-gradient-to-br from-blue-500 to-emerald-500 text-white shadow-lg scale-110' 
                  : 'bg-white/5 text-slate-400 hover:bg-white/10 hover:text-white'
                }
              `}
              title={page.name}
            >
              <Icon className="w-5 h-5" />
              <span className="text-xs font-medium hidden md:block">{page.name}</span>
            </button>
          );
        })}
      </div>

      {/* Main content */}
      <div className="transition-all duration-500">
        <CurrentPageComponent />
      </div>

      {/* Bottom navigation */}
      <div className="fixed bottom-8 left-1/2 -translate-x-1/2 z-50 flex items-center gap-4 bg-black/40 backdrop-blur-lg rounded-2xl p-4 border border-white/10">
        <button
          onClick={prevPage}
          disabled={currentPage === 0}
          className={`
            p-3 rounded-xl transition-all duration-300 flex items-center gap-2
            ${currentPage === 0 
              ? 'opacity-30 cursor-not-allowed' 
              : 'bg-white/10 hover:bg-white/20 text-white'
            }
          `}
        >
          <ChevronLeft className="w-5 h-5" />
          <span className="hidden md:inline text-sm font-medium">上一页</span>
        </button>

        <div className="flex items-center gap-2 px-4">
          {pages.map((page) => (
            <div
              key={page.id}
              onClick={() => setCurrentPage(page.id)}
              className={`
                w-2 h-2 rounded-full transition-all duration-300 cursor-pointer
                ${currentPage === page.id 
                  ? 'w-8 bg-gradient-to-r from-blue-500 to-emerald-500' 
                  : 'bg-white/30 hover:bg-white/50'
                }
              `}
            />
          ))}
        </div>

        <button
          onClick={nextPage}
          disabled={currentPage === pages.length - 1}
          className={`
            p-3 rounded-xl transition-all duration-300 flex items-center gap-2
            ${currentPage === pages.length - 1 
              ? 'opacity-30 cursor-not-allowed' 
              : 'bg-white/10 hover:bg-white/20 text-white'
            }
          `}
        >
          <span className="hidden md:inline text-sm font-medium">下一页</span>
          <ChevronRight className="w-5 h-5" />
        </button>
      </div>

      {/* Page indicator */}
      <div className="fixed top-8 right-8 z-50 bg-black/40 backdrop-blur-lg rounded-xl px-4 py-2 border border-white/10">
        <span className="text-white font-mono text-sm">
          {currentPage + 1} / {pages.length}
        </span>
      </div>
    </div>
  );
}
