import { useState } from "react";
import { tools } from "@/data";
import { 
  Wrench, 
  Zap, 
  Battery, 
  Cable,
  Filter,
  Search
} from "lucide-react";

type Category = "全部" | "钻孔类" | "拧紧类" | "切割类" | "打磨类" | "焊接类" | "加热类";
type CordFilter = "全部" | "无绳" | "有绳";

const ToolsPage = () => {
  const [selectedCategory, setSelectedCategory] = useState<Category>("全部");
  const [selectedCord, setSelectedCord] = useState<CordFilter>("全部");

  const categories: Category[] = ["全部", "钻孔类", "拧紧类", "切割类", "打磨类", "焊接类", "加热类"];

  const filteredTools = tools.filter(tool => {
    const categoryMatch = selectedCategory === "全部" || tool.category === selectedCategory;
    const cordMatch = selectedCord === "全部" || 
      (selectedCord === "无绳" && tool.isCordless) || 
      (selectedCord === "有绳" && !tool.isCordless);
    return categoryMatch && cordMatch;
  });

  const cordlessCount = tools.filter(t => t.isCordless).length;
  const cordedCount = tools.filter(t => !t.isCordless).length;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 p-8">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="text-center mb-8">
          <h1 className="text-5xl font-bold text-white mb-4">
            电动工具分类展示
          </h1>
          <p className="text-xl text-slate-400">
            重点关注无绳工具的应用场景与技术特点
          </p>
        </div>

        {/* Highlight section */}
        <div className="mb-8 grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="bg-gradient-to-br from-emerald-500/20 to-teal-500/20 rounded-3xl p-8 border border-emerald-500/30">
            <div className="flex items-center gap-4 mb-4">
              <div className="p-3 bg-emerald-500/30 rounded-2xl">
                <Battery className="w-8 h-8 text-emerald-400" />
              </div>
              <div>
                <h3 className="text-2xl font-bold text-emerald-400">无绳工具</h3>
                <p className="text-emerald-200/80">发展重点与趋势</p>
              </div>
            </div>
            <div className="flex items-baseline gap-4">
              <span className="text-5xl font-bold text-white">{cordlessCount}</span>
              <span className="text-slate-400">款重点产品</span>
            </div>
            <p className="mt-4 text-slate-300 leading-relaxed">
              锂电池驱动，便携高效，适合现场作业与灵活场景
            </p>
          </div>
          <div className="bg-gradient-to-br from-blue-500/20 to-cyan-500/20 rounded-3xl p-8 border border-blue-500/30">
            <div className="flex items-center gap-4 mb-4">
              <div className="p-3 bg-blue-500/30 rounded-2xl">
                <Cable className="w-8 h-8 text-blue-400" />
              </div>
              <div>
                <h3 className="text-2xl font-bold text-blue-400">有绳工具</h3>
                <p className="text-blue-200/80">传统与固定场景</p>
              </div>
            </div>
            <div className="flex items-baseline gap-4">
              <span className="text-5xl font-bold text-white">{cordedCount}</span>
              <span className="text-slate-400">款传统产品</span>
            </div>
            <p className="mt-4 text-slate-300 leading-relaxed">
              持续大功率输出，适合固定工位的重型作业
            </p>
          </div>
        </div>

        {/* Filters */}
        <div className="bg-white/5 rounded-2xl p-6 mb-8 border border-white/10">
          <div className="flex flex-col md:flex-row gap-6 items-start md:items-center">
            <div className="flex items-center gap-2">
              <Filter className="w-5 h-5 text-slate-400" />
              <span className="text-slate-300 font-medium">分类筛选:</span>
            </div>
            <div className="flex flex-wrap gap-2">
              {categories.map((category) => (
                <button
                  key={category}
                  onClick={() => setSelectedCategory(category)}
                  className={`
                    px-4 py-2 rounded-xl text-sm font-medium transition-all
                    ${selectedCategory === category 
                      ? 'bg-gradient-to-r from-blue-500 to-emerald-500 text-white shadow-lg' 
                      : 'bg-white/5 text-slate-300 hover:bg-white/10 border border-white/10'
                    }
                  `}
                >
                  {category}
                </button>
              ))}
            </div>
            <div className="flex gap-2 ml-auto">
              <button
                onClick={() => setSelectedCord("全部")}
                className={`
                  px-4 py-2 rounded-xl text-sm font-medium transition-all
                  ${selectedCord === "全部" 
                    ? 'bg-white/20 text-white border border-white/30' 
                    : 'bg-white/5 text-slate-400 hover:bg-white/10 border border-white/10'
                  }
                `}
              >
                全部
              </button>
              <button
                onClick={() => setSelectedCord("无绳")}
                className={`
                  px-4 py-2 rounded-xl text-sm font-medium transition-all flex items-center gap-2
                  ${selectedCord === "无绳" 
                    ? 'bg-emerald-500/30 text-emerald-300 border border-emerald-500/40' 
                    : 'bg-white/5 text-slate-400 hover:bg-white/10 border border-white/10'
                  }
                `}
              >
                <Battery className="w-4 h-4" />
                无绳
              </button>
              <button
                onClick={() => setSelectedCord("有绳")}
                className={`
                  px-4 py-2 rounded-xl text-sm font-medium transition-all flex items-center gap-2
                  ${selectedCord === "有绳" 
                    ? 'bg-blue-500/30 text-blue-300 border border-blue-500/40' 
                    : 'bg-white/5 text-slate-400 hover:bg-white/10 border border-white/10'
                  }
                `}
              >
                <Cable className="w-4 h-4" />
                有绳
              </button>
            </div>
          </div>
        </div>

        {/* Tools grid */}
        {filteredTools.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {filteredTools.map((tool, index) => (
              <div
                key={tool.id}
                className={`
                  group bg-white/5 backdrop-blur-sm rounded-3xl overflow-hidden border-2 transition-all duration-300 hover:scale-105 hover:shadow-2xl
                  ${tool.isCordless 
                    ? 'border-emerald-500/20 hover:border-emerald-500/50' 
                    : 'border-blue-500/20 hover:border-blue-500/50'
                  }
                `}
                style={{ animationDelay: `${index * 0.1}s` }}
              >
                {/* Image */}
                <div className="relative h-48 overflow-hidden">
                  <img
                    src={tool.image}
                    alt={tool.name}
                    className="w-full h-full object-cover group-hover:scale-110 transition-transform duration-500"
                  />
                  <div className={`
                    absolute top-4 right-4 px-3 py-1 rounded-full text-xs font-bold uppercase tracking-wider
                    ${tool.isCordless 
                      ? 'bg-emerald-500/90 text-white' 
                      : 'bg-blue-500/90 text-white'
                    }
                  `}>
                    {tool.isCordless ? '无绳' : '有绳'}
                  </div>
                  <div className="absolute inset-0 bg-gradient-to-t from-slate-900 via-transparent to-transparent"></div>
                </div>

                {/* Content */}
                <div className="p-6">
                  <div className="flex items-start justify-between mb-3">
                    <h3 className="text-xl font-bold text-white group-hover:text-blue-400 transition-colors">
                      {tool.name}
                    </h3>
                    <span className="px-2 py-1 bg-white/10 rounded-lg text-xs text-slate-400">
                      {tool.category}
                    </span>
                  </div>

                  <p className="text-slate-400 text-sm mb-4 leading-relaxed">
                    {tool.description}
                  </p>

                  {/* Applications */}
                  <div className="space-y-2">
                    <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider flex items-center gap-2">
                      <Zap className="w-3 h-3" />
                      应用场景
                    </h4>
                    <div className="flex flex-wrap gap-2">
                      {tool.applications.map((app, idx) => (
                        <span
                          key={idx}
                          className="px-2 py-1 bg-white/5 rounded text-xs text-slate-300 border border-white/5"
                        >
                          {app}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center py-20">
            <Search className="w-16 h-16 text-slate-600 mx-auto mb-4" />
            <p className="text-slate-400 text-lg">没有找到匹配的工具</p>
          </div>
        )}
      </div>
    </div>
  );
};

export default ToolsPage;
