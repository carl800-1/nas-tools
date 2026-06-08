import { Zap, Sun, Car, Wind, Battery, ArrowRight } from "lucide-react";

const CoverPage = () => {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-blue-900 to-slate-900 flex flex-col items-center justify-center p-8 relative overflow-hidden">
      {/* Background decorations */}
      <div className="absolute inset-0 overflow-hidden">
        <div className="absolute top-20 left-20 w-64 h-64 bg-blue-500/10 rounded-full blur-3xl"></div>
        <div className="absolute bottom-20 right-20 w-80 h-80 bg-emerald-500/10 rounded-full blur-3xl"></div>
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-gradient-to-r from-blue-500/5 to-emerald-500/5 rounded-full blur-3xl"></div>
      </div>

      {/* Grid pattern */}
      <div className="absolute inset-0 opacity-5" 
           style={{
             backgroundImage: 'linear-gradient(#fff 1px, transparent 1px), linear-gradient(90deg, #fff 1px, transparent 1px)',
             backgroundSize: '50px 50px'
           }}></div>

      <div className="relative z-10 max-w-5xl w-full text-center">
        {/* Icon row */}
        <div className="flex justify-center gap-6 mb-12 flex-wrap">
          <Sun className="w-12 h-12 text-yellow-400 animate-pulse" />
          <Car className="w-12 h-12 text-blue-400 animate-pulse" style={{ animationDelay: '0.2s' }} />
          <Wind className="w-12 h-12 text-teal-400 animate-pulse" style={{ animationDelay: '0.4s' }} />
          <Battery className="w-12 h-12 text-emerald-400 animate-pulse" style={{ animationDelay: '0.6s' }} />
        </div>

        {/* Main title */}
        <div className="mb-8">
          <h1 className="text-6xl md:text-7xl font-bold text-white mb-4 leading-tight">
            <span className="bg-gradient-to-r from-blue-400 to-emerald-400 bg-clip-text text-transparent">
              新能源行业
            </span>
          </h1>
          <h2 className="text-4xl md:text-5xl font-semibold text-white/90">
            电动工具使用分析报告
          </h2>
        </div>

        {/* Subtitle */}
        <p className="text-xl md:text-2xl text-slate-300 mb-12 max-w-3xl mx-auto leading-relaxed">
          深入分析光伏、新能源汽车、风电、储能等行业的<br />
          <span className="text-emerald-400 font-semibold">生产工序</span>与
          <span className="text-blue-400 font-semibold">电动工具</span>应用，
          重点关注无绳工具的发展趋势
        </p>

        {/* Key highlights */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mb-12">
          <div className="bg-white/5 backdrop-blur-sm rounded-2xl p-6 border border-white/10 hover:border-white/20 transition-all">
            <div className="text-3xl font-bold text-emerald-400 mb-2">4+</div>
            <div className="text-slate-300 text-sm">重点行业</div>
          </div>
          <div className="bg-white/5 backdrop-blur-sm rounded-2xl p-6 border border-white/10 hover:border-white/20 transition-all">
            <div className="text-3xl font-bold text-blue-400 mb-2">15+</div>
            <div className="text-slate-300 text-sm">生产工序</div>
          </div>
          <div className="bg-white/5 backdrop-blur-sm rounded-2xl p-6 border border-white/10 hover:border-white/20 transition-all">
            <div className="text-3xl font-bold text-yellow-400 mb-2">12+</div>
            <div className="text-slate-300 text-sm">工具类型</div>
          </div>
          <div className="bg-white/5 backdrop-blur-sm rounded-2xl p-6 border border-white/10 hover:border-white/20 transition-all">
            <div className="text-3xl font-bold text-teal-400 mb-2">75%</div>
            <div className="text-slate-300 text-sm">无绳工具</div>
          </div>
        </div>

        {/* Date info */}
        <div className="flex items-center justify-center gap-4 text-slate-400 text-lg">
          <Zap className="w-5 h-5" />
          <span>{new Date().getFullYear()}年 行业研究报告</span>
        </div>

        {/* Next page hint */}
        <div className="mt-16 animate-bounce">
          <div className="flex flex-col items-center gap-2 text-slate-400">
            <span className="text-sm">点击下一页继续</span>
            <ArrowRight className="w-6 h-6" />
          </div>
        </div>
      </div>
    </div>
  );
};

export default CoverPage;
