import { Sun, Car, Wind, Battery } from "lucide-react";
import { industries } from "@/data";
import { useAppStore } from "@/store/useAppStore";

const iconMap: Record<string, React.ReactNode> = {
  sun: <Sun className="w-10 h-10" />,
  car: <Car className="w-10 h-10" />,
  wind: <Wind className="w-10 h-10" />,
  battery: <Battery className="w-10 h-10" />,
};

const IndustryPage = () => {
  const { selectedIndustry, setSelectedIndustry } = useAppStore();

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 p-8">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="text-center mb-12">
          <h1 className="text-5xl font-bold text-white mb-4">
            新能源行业分类
          </h1>
          <p className="text-xl text-slate-400">
            涵盖四大核心新能源领域的生产场景分析
          </p>
        </div>

        {/* Industry grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
          {industries.map((industry, index) => (
            <div
              key={industry.id}
              onClick={() => setSelectedIndustry(
                selectedIndustry === industry.id ? null : industry.id
              )}
              className={`
                bg-white/5 backdrop-blur-sm rounded-3xl p-8 border-2 
                transition-all duration-300 cursor-pointer group
                ${selectedIndustry === industry.id 
                  ? 'border-white/30 bg-white/10 shadow-2xl scale-105' 
                  : 'border-white/10 hover:border-white/20 hover:bg-white/8 hover:scale-102'
                }
              `}
              style={{ animationDelay: `${index * 0.1}s` }}
            >
              {/* Icon and title */}
              <div className="flex items-start gap-6 mb-6">
                <div className={`
                  p-4 rounded-2xl bg-gradient-to-br ${industry.color} 
                  text-white shadow-lg
                `}>
                  {iconMap[industry.icon]}
                </div>
                <div>
                  <h3 className="text-2xl font-bold text-white mb-2">
                    {industry.name}
                  </h3>
                  <p className="text-slate-400 text-sm">
                    {industry.processes.length} 道核心工序
                  </p>
                </div>
              </div>

              {/* Description */}
              <p className="text-slate-300 mb-6 leading-relaxed">
                {industry.description}
              </p>

              {/* Processes preview */}
              <div className="space-y-3">
                <h4 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">
                  主要工序
                </h4>
                <div className="flex flex-wrap gap-2">
                  {industry.processes.map((process) => (
                    <span
                      key={process.id}
                      className="px-3 py-1 bg-white/5 rounded-full text-sm text-slate-300 border border-white/10"
                    >
                      {process.name}
                    </span>
                  ))}
                </div>
              </div>

              {/* Expand indicator */}
              {selectedIndustry === industry.id && (
                <div className="mt-6 pt-6 border-t border-white/10">
                  <div className="grid grid-cols-1 gap-4">
                    {industry.processes.map((process) => (
                      <div key={process.id} className="bg-black/20 rounded-xl p-4">
                        <h5 className="font-semibold text-white mb-2">
                          {process.name}
                        </h5>
                        <p className="text-sm text-slate-400 mb-3">
                          {process.description}
                        </p>
                        <div className="flex flex-wrap gap-2">
                          <span className="text-xs text-slate-500">使用工具:</span>
                          {process.tools.map((tool, idx) => (
                            <span
                              key={idx}
                              className="text-xs px-2 py-1 bg-emerald-500/20 text-emerald-300 rounded-full"
                            >
                              {tool}
                            </span>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>

        {/* Stats */}
        <div className="mt-12 grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="text-center p-6 bg-white/5 rounded-2xl border border-white/10">
            <div className="text-3xl font-bold text-yellow-400 mb-1">4</div>
            <div className="text-slate-400 text-sm">重点行业</div>
          </div>
          <div className="text-center p-6 bg-white/5 rounded-2xl border border-white/10">
            <div className="text-3xl font-bold text-blue-400 mb-1">15</div>
            <div className="text-slate-400 text-sm">核心工序</div>
          </div>
          <div className="text-center p-6 bg-white/5 rounded-2xl border border-white/10">
            <div className="text-3xl font-bold text-teal-400 mb-1">50+</div>
            <div className="text-slate-400 text-sm">工具类型</div>
          </div>
          <div className="text-center p-6 bg-white/5 rounded-2xl border border-white/10">
            <div className="text-3xl font-bold text-emerald-400 mb-1">万亿级</div>
            <div className="text-slate-400 text-sm">市场规模</div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default IndustryPage;
