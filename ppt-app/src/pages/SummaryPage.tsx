import { 
  TrendingUp, 
  BarChart3, 
  PieChart, 
  Target,
  Zap,
  CheckCircle2,
  ArrowRight,
  Battery
} from "lucide-react";

const SummaryPage = () => {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 p-8">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="text-center mb-12">
          <h1 className="text-5xl font-bold text-white mb-4">
            总结与趋势展望
          </h1>
          <p className="text-xl text-slate-400">
            新能源行业电动工具的发展方向与市场机遇
          </p>
        </div>

        {/* Key metrics */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mb-12">
          <div className="bg-gradient-to-br from-blue-500/20 to-cyan-500/20 rounded-3xl p-6 border border-blue-500/30 text-center">
            <TrendingUp className="w-10 h-10 text-blue-400 mx-auto mb-3" />
            <div className="text-4xl font-bold text-white mb-1">75%</div>
            <p className="text-slate-400 text-sm">无绳工具占比</p>
          </div>
          <div className="bg-gradient-to-br from-emerald-500/20 to-teal-500/20 rounded-3xl p-6 border border-emerald-500/30 text-center">
            <BarChart3 className="w-10 h-10 text-emerald-400 mx-auto mb-3" />
            <div className="text-4xl font-bold text-white mb-1">40%</div>
            <p className="text-slate-400 text-sm">年复合增长率</p>
          </div>
          <div className="bg-gradient-to-br from-yellow-500/20 to-orange-500/20 rounded-3xl p-6 border border-yellow-500/30 text-center">
            <PieChart className="w-10 h-10 text-yellow-400 mx-auto mb-3" />
            <div className="text-4xl font-bold text-white mb-1">500亿</div>
            <p className="text-slate-400 text-sm">市场规模（元）</p>
          </div>
          <div className="bg-gradient-to-br from-purple-500/20 to-pink-500/20 rounded-3xl p-6 border border-purple-500/30 text-center">
            <Target className="w-10 h-10 text-purple-400 mx-auto mb-3" />
            <div className="text-4xl font-bold text-white mb-1">12+</div>
            <p className="text-slate-400 text-sm">重点品类</p>
          </div>
        </div>

        {/* Main content grid */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 mb-12">
          {/* Key findings */}
          <div className="bg-white/5 backdrop-blur-sm rounded-3xl p-8 border border-white/10">
            <h2 className="text-2xl font-bold text-white mb-6 flex items-center gap-3">
              <CheckCircle2 className="w-8 h-8 text-emerald-400" />
              核心发现
            </h2>
            <div className="space-y-4">
              {[
                {
                  title: "无绳化是必然趋势",
                  desc: "锂电池技术进步驱动，便携性需求持续增长",
                  color: "emerald"
                },
                {
                  title: "智能化成为标配",
                  desc: "扭矩控制、数据联网、状态监测功能普及",
                  color: "blue"
                },
                {
                  title: "场景细分化",
                  desc: "针对不同行业开发专用工具，专业化程度提升",
                  color: "purple"
                },
                {
                  title: "安全标准升级",
                  desc: "电池安全、电磁兼容等要求日益严格",
                  color: "yellow"
                }
              ].map((item, index) => (
                <div
                  key={index}
                  className="flex items-start gap-4 p-4 bg-black/20 rounded-2xl border border-white/5"
                >
                  <div className={`
                    w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0
                    ${item.color === 'emerald' ? 'bg-emerald-500/30 text-emerald-400' : 
                      item.color === 'blue' ? 'bg-blue-500/30 text-blue-400' :
                      item.color === 'purple' ? 'bg-purple-500/30 text-purple-400' :
                      'bg-yellow-500/30 text-yellow-400'
                    }
                  `}>
                    <ArrowRight className="w-4 h-4" />
                  </div>
                  <div>
                    <h3 className="font-semibold text-white mb-1">
                      {item.title}
                    </h3>
                    <p className="text-slate-400 text-sm">
                      {item.desc}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Trend chart */}
          <div className="bg-white/5 backdrop-blur-sm rounded-3xl p-8 border border-white/10">
            <h2 className="text-2xl font-bold text-white mb-6 flex items-center gap-3">
              <TrendingUp className="w-8 h-8 text-blue-400" />
              发展趋势
            </h2>
            
            {/* Simulated chart */}
            <div className="space-y-6">
              {[
                { year: "2024", value: 60, label: "60%" },
                { year: "2025", value: 68, label: "68%" },
                { year: "2026", value: 75, label: "75%" },
                { year: "2027", value: 82, label: "82%" },
                { year: "2028", value: 88, label: "88%" }
              ].map((item, index) => (
                <div key={index} className="flex items-center gap-4">
                  <span className="text-slate-400 w-16 text-sm font-medium">
                    {item.year}
                  </span>
                  <div className="flex-1 h-8 bg-black/30 rounded-xl overflow-hidden">
                    <div
                      className="h-full bg-gradient-to-r from-blue-500 to-emerald-500 rounded-xl flex items-center justify-end pr-3 transition-all duration-1000"
                      style={{ width: `${item.value}%` }}
                    >
                      <span className="text-white text-sm font-bold">
                        {item.label}
                      </span>
                    </div>
                  </div>
                  <span className="text-slate-500 w-16 text-xs">
                    无绳工具占比
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Recommendations */}
        <div className="bg-gradient-to-br from-blue-500/10 via-emerald-500/10 to-cyan-500/10 rounded-3xl p-8 border border-blue-500/20">
          <h2 className="text-2xl font-bold text-white mb-6 flex items-center gap-3">
            <Target className="w-8 h-8 text-emerald-400" />
            战略建议
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div className="bg-black/20 rounded-2xl p-6 border border-white/10">
              <div className="w-12 h-12 bg-blue-500/30 rounded-xl flex items-center justify-center mb-4">
                <Battery className="w-6 h-6 text-blue-400" />
              </div>
              <h3 className="font-semibold text-white mb-2">产品策略</h3>
              <ul className="text-slate-400 text-sm space-y-2">
                <li>• 加大无绳产品线投入</li>
                <li>• 开发专用场景工具</li>
                <li>• 智能化功能升级</li>
              </ul>
            </div>
            <div className="bg-black/20 rounded-2xl p-6 border border-white/10">
              <div className="w-12 h-12 bg-emerald-500/30 rounded-xl flex items-center justify-center mb-4">
                <Zap className="w-6 h-6 text-emerald-400" />
              </div>
              <h3 className="font-semibold text-white mb-2">技术创新</h3>
              <ul className="text-slate-400 text-sm space-y-2">
                <li>• 电池技术研发</li>
                <li>• 电机效率提升</li>
                <li>• IoT互联互通</li>
              </ul>
            </div>
            <div className="bg-black/20 rounded-2xl p-6 border border-white/10">
              <div className="w-12 h-12 bg-purple-500/30 rounded-xl flex items-center justify-center mb-4">
                <Target className="w-6 h-6 text-purple-400" />
              </div>
              <h3 className="font-semibold text-white mb-2">市场拓展</h3>
              <ul className="text-slate-400 text-sm space-y-2">
                <li>• 深耕新能源行业</li>
                <li>• 建立行业解决方案</li>
                <li>• 服务体系完善</li>
              </ul>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="mt-12 text-center">
          <div className="inline-flex items-center gap-4 px-8 py-4 bg-gradient-to-r from-blue-500/20 to-emerald-500/20 rounded-2xl border border-white/10">
            <span className="text-slate-400">报告完成时间</span>
            <span className="text-white font-bold">{new Date().getFullYear()}年</span>
            <span className="text-slate-600">|</span>
            <span className="text-emerald-400 font-semibold">持续更新中...</span>
          </div>
        </div>
      </div>
    </div>
  );
};

export default SummaryPage;
