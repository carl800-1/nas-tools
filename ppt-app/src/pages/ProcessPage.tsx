import { industries } from "@/data";
import { 
  Factory, 
  Wrench, 
  Zap, 
  CheckCircle2,
  Settings,
  Package,
  Truck
} from "lucide-react";

const processIcons = [
  <Factory key="factory" />,
  <Wrench key="wrench" />,
  <Zap key="zap" />,
  <CheckCircle2 key="check" />,
  <Settings key="settings" />,
  <Package key="package" />,
  <Truck key="truck" />
];

const ProcessPage = () => {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 p-8">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="text-center mb-12">
          <h1 className="text-5xl font-bold text-white mb-4">
            核心生产工序分析
          </h1>
          <p className="text-xl text-slate-400">
            各行业关键生产工序的详细解析与工具需求
          </p>
        </div>

        {/* All processes */}
        <div className="space-y-12">
          {industries.map((industry, industryIndex) => (
            <div key={industry.id} className="bg-white/5 rounded-3xl p-8 border border-white/10">
              {/* Industry header */}
              <div className="flex items-center gap-4 mb-8">
                <div className={`p-3 rounded-2xl bg-gradient-to-br ${industry.color}`}>
                  {processIcons[industryIndex % processIcons.length]}
                </div>
                <div>
                  <h2 className="text-3xl font-bold text-white">
                    {industry.name}
                  </h2>
                  <p className="text-slate-400">
                    {industry.processes.length} 道核心工序
                  </p>
                </div>
              </div>

              {/* Processes timeline */}
              <div className="space-y-6">
                {industry.processes.map((process, processIndex) => (
                  <div
                    key={process.id}
                    className="flex gap-6 group"
                    style={{ animationDelay: `${(industryIndex * industry.processes.length + processIndex) * 0.1}s` }}
                  >
                    {/* Timeline line and dot */}
                    <div className="flex flex-col items-center">
                      <div className="w-10 h-10 rounded-full bg-gradient-to-br from-blue-500 to-emerald-500 flex items-center justify-center text-white font-bold shadow-lg shadow-blue-500/25">
                        {processIndex + 1}
                      </div>
                      {processIndex < industry.processes.length - 1 && (
                        <div className="w-0.5 flex-1 bg-gradient-to-b from-blue-500/50 to-transparent"></div>
                      )}
                    </div>

                    {/* Process content */}
                    <div className="flex-1 bg-black/20 rounded-2xl p-6 border border-white/5 group-hover:border-white/15 transition-all">
                      <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-4 mb-4">
                        <div>
                          <h3 className="text-xl font-bold text-white mb-2">
                            {process.name}
                          </h3>
                          <p className="text-slate-300 leading-relaxed">
                            {process.description}
                          </p>
                        </div>
                      </div>

                      {/* Tools */}
                      <div className="mt-4 pt-4 border-t border-white/10">
                        <h4 className="text-sm font-semibold text-slate-400 mb-3 flex items-center gap-2">
                          <Wrench className="w-4 h-4" />
                          使用工具
                        </h4>
                        <div className="flex flex-wrap gap-2">
                          {process.tools.map((tool, toolIndex) => (
                            <span
                              key={toolIndex}
                              className={`
                                px-4 py-2 rounded-xl text-sm font-medium transition-all
                                ${tool.includes('无绳') 
                                  ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30' 
                                  : 'bg-blue-500/10 text-blue-300 border border-blue-500/20'
                                }
                              `}
                            >
                              {tool.includes('无绳') && <span className="mr-1">🔋</span>}
                              {tool}
                            </span>
                          ))}
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Key insights */}
        <div className="mt-12 grid grid-cols-1 md:grid-cols-3 gap-6">
          <div className="bg-gradient-to-br from-blue-500/10 to-cyan-500/10 rounded-2xl p-6 border border-blue-500/20">
            <h3 className="text-lg font-bold text-blue-400 mb-3 flex items-center gap-2">
              <Factory className="w-5 h-5" />
              制造工序
            </h3>
            <p className="text-slate-300 text-sm">
              制造环节以大型固定设备为主，对精度和稳定性要求高
            </p>
          </div>
          <div className="bg-gradient-to-br from-emerald-500/10 to-teal-500/10 rounded-2xl p-6 border border-emerald-500/20">
            <h3 className="text-lg font-bold text-emerald-400 mb-3 flex items-center gap-2">
              <Wrench className="w-5 h-5" />
              安装运维
            </h3>
            <p className="text-slate-300 text-sm">
              现场作业场景占比提升，无绳工具需求快速增长
            </p>
          </div>
          <div className="bg-gradient-to-br from-purple-500/10 to-pink-500/10 rounded-2xl p-6 border border-purple-500/20">
            <h3 className="text-lg font-bold text-purple-400 mb-3 flex items-center gap-2">
              <Zap className="w-5 h-5" />
              智能化趋势
            </h3>
            <p className="text-slate-300 text-sm">
              智能扭矩控制、数据联网等功能成为新需求
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ProcessPage;
