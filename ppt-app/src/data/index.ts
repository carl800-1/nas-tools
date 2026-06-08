import { Industry, Tool } from "@/types";

export const industries: Industry[] = [
  {
    id: "photovoltaic",
    name: "光伏行业",
    description: "太阳能光伏组件制造、安装及维护，是新能源产业的重要组成部分",
    icon: "sun",
    color: "from-yellow-400 to-orange-500",
    processes: [
      {
        id: "pv-1",
        name: "硅片切割",
        description: "将硅锭切割成超薄硅片，是光伏制造的第一道关键工序",
        tools: ["切割机", "金刚石线锯", "高精度磨床"]
      },
      {
        id: "pv-2",
        name: "电池片制造",
        description: "在硅片上制备PN结、电极等，形成光伏电池片",
        tools: ["丝网印刷机", "高温烧结炉", "等离子刻蚀机"]
      },
      {
        id: "pv-3",
        name: "组件封装",
        description: "将电池片串联、封装成光伏组件，增加机械强度和耐久性",
        tools: ["层压机", "组框机", "测试仪"]
      },
      {
        id: "pv-4",
        name: "现场安装",
        description: "在屋顶或地面安装光伏支架和组件",
        tools: ["电钻", "扳手", "无绳电钻", "切割机"]
      }
    ]
  },
  {
    id: "ev-manufacturing",
    name: "新能源汽车制造",
    description: "电动汽车整车及核心零部件（电池、电机、电控）的研发与生产",
    icon: "car",
    color: "from-blue-500 to-cyan-500",
    processes: [
      {
        id: "ev-1",
        name: "车身冲压",
        description: "将钢板冲压成各种车身零部件",
        tools: ["压力机", "折弯机", "剪板机"]
      },
      {
        id: "ev-2",
        name: "焊接工艺",
        description: "将冲压件焊接成完整车身",
        tools: ["点焊机器人", "弧焊机器人", "无绳焊机"]
      },
      {
        id: "ev-3",
        name: "涂装工艺",
        description: "对车身进行电泳、底漆、面漆涂装",
        tools: ["喷涂机器人", "烘干炉", "打磨机"]
      },
      {
        id: "ev-4",
        name: "总装工艺",
        description: "将各系统装配成整车",
        tools: ["电动扳手", "扭矩枪", "无绳电钻"]
      },
      {
        id: "ev-5",
        name: "电池包制造",
        description: "电芯模组装配与电池包集成",
        tools: ["激光焊接机", "拧紧系统", "检漏仪"]
      }
    ]
  },
  {
    id: "wind-power",
    name: "风电行业",
    description: "风力发电机组的研发、制造、安装与运维",
    icon: "wind",
    color: "from-teal-400 to-green-600",
    processes: [
      {
        id: "wind-1",
        name: "叶片制造",
        description: "复合材料叶片的成型与精加工",
        tools: ["复合材料铺设机", "热压罐", "五轴加工中心"]
      },
      {
        id: "wind-2",
        name: "机舱装配",
        description: "发电机、齿轮箱等主部件的安装",
        tools: ["大型起重机", "扭矩扳手", "无绳工具"]
      },
      {
        id: "wind-3",
        name: "现场安装",
        description: "塔筒、机舱、叶片的现场吊装与调试",
        tools: ["履带吊", "液压扳手", "无绳冲击钻"]
      }
    ]
  },
  {
    id: "energy-storage",
    name: "储能行业",
    description: "电化学储能、物理储能等系统的制造与集成",
    icon: "battery",
    color: "from-emerald-400 to-teal-600",
    processes: [
      {
        id: "es-1",
        name: "电池模组生产",
        description: "电芯分选、检测与模组组装",
        tools: ["电芯分选机", "激光焊接机", "气密测试仪"]
      },
      {
        id: "es-2",
        name: "储能系统集成",
        description: "电池簇、BMS、PCS等系统集成",
        tools: ["电动螺丝刀", "扭矩扳手", "无绳电钻"]
      }
    ]
  }
];

export const tools: Tool[] = [
  {
    id: "tool-1",
    name: "无绳电钻",
    category: "钻孔类",
    isCordless: true,
    description: "锂电池驱动，轻便灵活，适合各种安装场景",
    image: "https://images.unsplash.com/photo-1504148455328-c376907d081c?w=400&h=300&fit=crop",
    applications: ["光伏安装", "汽车总装", "风电维护", "储能集成"]
  },
  {
    id: "tool-2",
    name: "无绳冲击扳手",
    category: "拧紧类",
    isCordless: true,
    description: "高扭矩输出，用于大螺栓拧紧",
    image: "https://images.unsplash.com/photo-1572569511254-d8f925fe2cbb?w=400&h=300&fit=crop",
    applications: ["汽车总装", "风电安装", "设备维护"]
  },
  {
    id: "tool-3",
    name: "无绳角磨机",
    category: "打磨类",
    isCordless: true,
    description: "用于金属切割、打磨和抛光",
    image: "https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=400&h=300&fit=crop",
    applications: ["焊接后处理", "模具修整", "设备维护"]
  },
  {
    id: "tool-4",
    name: "无绳扭矩扳手",
    category: "拧紧类",
    isCordless: true,
    description: "精确扭矩控制，确保连接质量",
    image: "https://images.unsplash.com/photo-1586864387967-d02ef85d93e8?w=400&h=300&fit=crop",
    applications: ["汽车制造", "风电安装", "精密装配"]
  },
  {
    id: "tool-5",
    name: "无绳往复锯",
    category: "切割类",
    isCordless: true,
    description: "用于金属和木材的快速切割",
    image: "https://images.unsplash.com/photo-1503387762-592deb58ef4e?w=400&h=300&fit=crop",
    applications: ["管道安装", "结构拆除", "现场加工"]
  },
  {
    id: "tool-6",
    name: "无绳电动螺丝刀",
    category: "拧紧类",
    isCordless: true,
    description: "精密小螺丝拧紧作业",
    image: "https://images.unsplash.com/photo-1531397960291-c94a606369a7?w=400&h=300&fit=crop",
    applications: ["电子装配", "内饰安装", "设备调试"]
  },
  {
    id: "tool-7",
    name: "有绳电焊机",
    category: "焊接类",
    isCordless: false,
    description: "高功率需求，适合固定工位焊接",
    image: "https://images.unsplash.com/photo-1504917595217-d4dc5ebe6122?w=400&h=300&fit=crop",
    applications: ["车身焊接", "钢结构", "管道焊接"]
  },
  {
    id: "tool-8",
    name: "有绳等离子切割机",
    category: "切割类",
    isCordless: false,
    description: "高精度金属切割",
    image: "https://images.unsplash.com/photo-1558618047-3c8c76ca7d13?w=400&h=300&fit=crop",
    applications: ["板材切割", "下料加工", "原型制作"]
  },
  {
    id: "tool-9",
    name: "无绳热风枪",
    category: "加热类",
    isCordless: true,
    description: "用于热缩管、贴膜等加热作业",
    image: "https://images.unsplash.com/photo-1558618451-a399d9676186?w=400&h=300&fit=crop",
    applications: ["线束加工", "包装加热", "维修作业"]
  },
  {
    id: "tool-10",
    name: "无砂轮机",
    category: "打磨类",
    isCordless: false,
    description: "固定工位的重型打磨作业",
    image: "https://images.unsplash.com/photo-1504148455328-c376907d081c?w=400&h=300&fit=crop",
    applications: ["铸件清理", "模具加工", "焊缝平整"]
  },
  {
    id: "tool-11",
    name: "无绳电锤",
    category: "钻孔类",
    isCordless: true,
    description: "混凝土等硬材质钻孔作业",
    image: "https://images.unsplash.com/photo-1572569511254-d8f925fe2cbb?w=400&h=300&fit=crop",
    applications: ["基础施工", "设备固定", "管道安装"]
  },
  {
    id: "tool-12",
    name: "无绳曲线锯",
    category: "切割类",
    isCordless: true,
    description: "曲线和异型切割作业",
    image: "https://images.unsplash.com/photo-1503387762-592deb58ef4e?w=400&h=300&fit=crop",
    applications: ["内饰加工", "原型制作", "维修作业"]
  }
];
