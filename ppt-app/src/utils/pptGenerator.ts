import PptxGenJS from "pptxgenjs";
import { industries, tools } from "@/data";

export const generatePPT = async () => {
  const pptx = new PptxGenJS();
  pptx.author = "新能源行业分析报告";
  pptx.title = "新能源行业电动工具使用分析报告";
  pptx.subject = "新能源行业电动工具分析";
  pptx.company = "行业研究报告";

  // 设置幻灯片尺寸为16:9
  pptx.layout = "LAYOUT_16x9";

  // 颜色方案 - 与网页完全一致
  const colors = {
    blue: "3B82F6",       // 蓝色-500
    green: "10B981",      // 绿色-500
    yellow: "F59E0B",     // 黄色-500
    purple: "A855F7",     // 紫色-500
    cyan: "06B6D4",       // 青色-500
    text: "FFFFFF",       // 白色文字
    lightText: "9CA3AF",  // 浅灰文字
    darkBg: "0F172A",     // 深蓝背景
    cardBg: "1E293B",     // 卡片背景
    border: "374151"      // 边框颜色
  };

  // ========== 第1页：封面 ==========
  const slide1 = pptx.addSlide();
  slide1.background = { color: colors.darkBg };

  // 装饰圆圈
  slide1.addShape(pptx.shapes.OVAL, {
    x: -1.5, y: -1.5, w: 5, h: 5,
    fill: { color: colors.blue, transparency: 85 }
  });
  slide1.addShape(pptx.shapes.OVAL, {
    x: 6, y: 2, w: 6, h: 6,
    fill: { color: colors.green, transparency: 90 }
  });

  // 标题行 - 4个图标
  slide1.addText("☀️", { x: 2.5, y: 0.8, w: 1, h: 0.8, fontSize: 32, align: "center" });
  slide1.addText("🚗", { x: 3.7, y: 0.8, w: 1, h: 0.8, fontSize: 32, align: "center" });
  slide1.addText("🌬️", { x: 4.9, y: 0.8, w: 1, h: 0.8, fontSize: 32, align: "center" });
  slide1.addText("🔋", { x: 6.1, y: 0.8, w: 1, h: 0.8, fontSize: 32, align: "center" });

  // 主要标题
  slide1.addText("新能源行业", {
    x: 0.5, y: 1.7, w: 9, h: 1.2,
    fontSize: 64, bold: true, color: colors.green,
    align: "center"
  });
  slide1.addText("电动工具使用分析报告", {
    x: 0.5, y: 3, w: 9, h: 0.9,
    fontSize: 44, color: colors.text,
    align: "center"
  });

  // 副标题
  slide1.addText("深入分析光伏、新能源汽车、风电、储能等行业的生产工序与电动工具应用", {
    x: 0.5, y: 4.2, w: 9, h: 0.8,
    fontSize: 18, color: colors.lightText,
    align: "center"
  });

  // 底部数据亮点
  const highlights = [
    { num: "4+", label: "重点行业" },
    { num: "15+", label: "核心工序" },
    { num: "12+", label: "工具类型" },
    { num: "75%", label: "无绳工具" }
  ];
  highlights.forEach((item, index) => {
    const xPos = 1 + index * 2.2;
    slide1.addShape(pptx.shapes.ROUNDED_RECTANGLE, {
      x: xPos, y: 5.4, w: 2, h: 0.9,
      fill: { color: colors.cardBg, transparency: 20 },
      line: { color: colors.border, transparency: 70 }
    });
    slide1.addText(item.num, {
      x: xPos, y: 5.45, w: 2, h: 0.5,
      fontSize: 26, bold: true, color: colors.green,
      align: "center", valign: "middle"
    });
    slide1.addText(item.label, {
      x: xPos, y: 5.95, w: 2, h: 0.3,
      fontSize: 12, color: colors.lightText,
      align: "center"
    });
  });

  // 日期
  slide1.addText(`${new Date().getFullYear()}年 · 行业研究报告`, {
    x: 0.5, y: 6.6, w: 9, h: 0.4,
    fontSize: 14, color: colors.lightText,
    align: "center"
  });

  // ========== 第2页：行业分类 ==========
  const slide2 = pptx.addSlide();
  slide2.background = { color: colors.darkBg };

  slide2.addText("行业分类概述", {
    x: 0.5, y: 0.4, w: 9, h: 0.8,
    fontSize: 40, bold: true, color: colors.blue,
    align: "center"
  });
  slide2.addText("涵盖四大核心新能源领域的生产场景分析", {
    x: 0.5, y: 1.1, w: 9, h: 0.4,
    fontSize: 16, color: colors.lightText,
    align: "center"
  });

  const industryCards = [
    { name: "光伏行业", icon: "☀️", color: colors.yellow, 
      desc: "太阳能光伏组件制造、安装及维护，是新能源产业的重要组成部分",
      processes: ["硅片切割", "电池片制造", "组件封装", "现场安装"] },
    { name: "新能源汽车", icon: "🚗", color: colors.blue,
      desc: "电动汽车整车及核心零部件（电池、电机、电控）的研发与生产",
      processes: ["车身冲压", "焊接工艺", "涂装工艺", "总装工艺", "电池包制造"] },
    { name: "风电行业", icon: "🌬️", color: colors.cyan,
      desc: "风力发电机组的研发、制造、安装与运维",
      processes: ["叶片制造", "机舱装配", "现场安装"] },
    { name: "储能行业", icon: "🔋", color: colors.emerald || colors.green,
      desc: "电化学储能、物理储能等系统的制造与集成",
      processes: ["电池模组生产", "储能系统集成"] }
  ];

  industryCards.forEach((item, index) => {
    const col = index % 2;
    const row = Math.floor(index / 2);
    const xPos = 0.7 + col * 4.6;
    const yPos = 1.8 + row * 2.4;
    
    // 主卡片
    slide2.addShape(pptx.shapes.ROUNDED_RECTANGLE, {
      x: xPos, y: yPos, w: 4.3, h: 2.2,
      fill: { color: colors.cardBg },
      line: { color: item.color, transparency: 60 }
    });
    
    // 图标和标题
    slide2.addText(item.icon, {
      x: xPos + 0.2, y: yPos + 0.15, w: 0.8, h: 0.8,
      fontSize: 36, align: "center", valign: "middle"
    });
    slide2.addText(item.name, {
      x: xPos + 1.1, y: yPos + 0.2, w: 3, h: 0.5,
      fontSize: 22, bold: true, color: item.color
    });
    
    // 描述
    slide2.addText(item.desc, {
      x: xPos + 0.2, y: yPos + 0.8, w: 3.9, h: 0.7,
      fontSize: 11.5, color: colors.lightText
    });
    
    // 工序标签
    const tags = item.processes.slice(0, 4).map(p => `• ${p}`).join("  ");
    slide2.addText(tags, {
      x: xPos + 0.2, y: yPos + 1.6, w: 3.9, h: 0.5,
      fontSize: 10, color: colors.lightText
    });
  });

  // ========== 第3-6页：各行业工序 ==========
  industries.forEach((industry, indIndex) => {
    const slide = pptx.addSlide();
    slide.background = { color: colors.darkBg };

    // 标题栏
    slide.addShape(pptx.shapes.RECTANGLE, {
      x: 0, y: 0, w: 10, h: 1,
      fill: { color: colors.cardBg }
    });
    
    const industryColor = [colors.yellow, colors.blue, colors.cyan, colors.green][indIndex];
    const industryIcon = ["☀️", "🚗", "🌬️", "🔋"][indIndex];
    
    slide.addText(`${industryIcon} ${industry.name}`, {
      x: 0.5, y: 0.2, w: 9, h: 0.6,
      fontSize: 34, bold: true, color: industryColor,
      valign: "middle"
    });

    // 工序时间线
    industry.processes.forEach((process, pIndex) => {
      const yPos = 1.2 + pIndex * 1.4;
      if (yPos < 5.8) {
        // 时间线圆点和连线
        slide.addShape(pptx.shapes.OVAL, {
          x: 1.1, y: yPos + 0.15, w: 0.6, h: 0.6,
          fill: { color: industryColor }
        });
        if (pIndex < industry.processes.length - 1) {
          slide.addShape(pptx.shapes.RECTANGLE, {
            x: 1.37, y: yPos + 0.75, w: 0.06, h: 0.8,
            fill: { color: industryColor, transparency: 50 }
          });
        }

        // 工序卡片
        slide.addShape(pptx.shapes.ROUNDED_RECTANGLE, {
          x: 2, y: yPos, w: 7.5, h: 1.3,
          fill: { color: colors.cardBg },
          line: { color: colors.border, transparency: 60 }
        });

        // 序号
        slide.addText(`${pIndex + 1}`, {
          x: 1.1, y: yPos + 0.15, w: 0.6, h: 0.6,
          fontSize: 20, bold: true, color: colors.text,
          align: "center", valign: "middle"
        });

        // 工序名称
        slide.addText(process.name, {
          x: 2.2, y: yPos + 0.1, w: 7, h: 0.5,
          fontSize: 20, bold: true, color: colors.text
        });

        // 工序描述
        slide.addText(process.description, {
          x: 2.2, y: yPos + 0.6, w: 7, h: 0.5,
          fontSize: 12, color: colors.lightText
        });

        // 工具标签
        const toolLabels = process.tools.map(tool => 
          tool.includes("无绳") ? `🔋${tool}` : tool
        ).join("   ");
        slide.addText(`使用工具: ${toolLabels}`, {
          x: 2.2, y: yPos + 1.0, w: 7, h: 0.25,
          fontSize: 10, color: industryColor
        });
      }
    });
  });

  // ========== 第7页：工具分类 ==========
  const slideTools = pptx.addSlide();
  slideTools.background = { color: colors.darkBg };

  slideTools.addText("电动工具分类展示", {
    x: 0.5, y: 0.3, w: 9, h: 0.7,
    fontSize: 38, bold: true, color: colors.blue,
    align: "center"
  });
  slideTools.addText("重点关注无绳工具的应用场景与技术特点", {
    x: 0.5, y: 0.95, w: 9, h: 0.35,
    fontSize: 16, color: colors.lightText,
    align: "center"
  });

  // 无绳 vs 有绳对比卡
  const cordlessCount = tools.filter(t => t.isCordless).length;
  
  slideTools.addShape(pptx.shapes.ROUNDED_RECTANGLE, {
    x: 0.7, y: 1.5, w: 4.3, h: 1.1,
    fill: { color: colors.cardBg },
    line: { color: colors.green, transparency: 50 }
  });
  slideTools.addText(`🔋 无绳工具`, {
    x: 0.9, y: 1.55, w: 3.9, h: 0.5,
    fontSize: 22, bold: true, color: colors.green
  });
  slideTools.addText(`${cordlessCount}款重点产品 · 锂电池驱动 · 便携高效`, {
    x: 0.9, y: 2.0, w: 3.9, h: 0.5,
    fontSize: 13, color: colors.lightText
  });

  slideTools.addShape(pptx.shapes.ROUNDED_RECTANGLE, {
    x: 5, y: 1.5, w: 4.3, h: 1.1,
    fill: { color: colors.cardBg },
    line: { color: colors.blue, transparency: 50 }
  });
  slideTools.addText(`🔌 有绳工具`, {
    x: 5.2, y: 1.55, w: 3.9, h: 0.5,
    fontSize: 22, bold: true, color: colors.blue
  });
  slideTools.addText(`${tools.length - cordlessCount}款传统产品 · 持续大功率 · 固定工位`, {
    x: 5.2, y: 2.0, w: 3.9, h: 0.5,
    fontSize: 13, color: colors.lightText
  });

  // 工具展示
  const toolDisplay = tools.slice(0, 6);
  let toolRow = 0;
  let toolCol = 0;
  
  toolDisplay.forEach((tool, index) => {
    const xPos = 0.7 + toolCol * 4.6;
    const yPos = 2.9 + toolRow * 1.8;
    
    const toolColor = tool.isCordless ? colors.green : colors.blue;
    
    // 工具卡片
    slideTools.addShape(pptx.shapes.ROUNDED_RECTANGLE, {
      x: xPos, y: yPos, w: 4.3, h: 1.7,
      fill: { color: colors.cardBg },
      line: { color: toolColor, transparency: 60 }
    });

    // 无绳标签
    if (tool.isCordless) {
      slideTools.addShape(pptx.shapes.ROUNDED_RECTANGLE, {
        x: xPos + 3.2, y: yPos + 0.1, w: 0.9, h: 0.3,
        fill: { color: colors.green }
      });
      slideTools.addText("无绳", {
        x: xPos + 3.2, y: yPos + 0.1, w: 0.9, h: 0.3,
        fontSize: 11, bold: true, color: colors.text,
        align: "center", valign: "middle"
      });
    }

    // 工具名称
    slideTools.addText(tool.name, {
      x: xPos + 0.15, y: yPos + 0.1, w: 3, h: 0.45,
      fontSize: 18, bold: true, color: colors.text
    });

    // 分类
    slideTools.addText(tool.category, {
      x: xPos + 0.15, y: yPos + 0.55, w: 3, h: 0.3,
      fontSize: 11, color: colors.lightText
    });

    // 描述
    slideTools.addText(tool.description, {
      x: xPos + 0.15, y: yPos + 0.85, w: 4, h: 0.4,
      fontSize: 11.5, color: colors.lightText
    });

    // 应用场景
    const apps = tool.applications.slice(0, 3).join(" · ");
    slideTools.addText(`应用: ${apps}`, {
      x: xPos + 0.15, y: yPos + 1.25, w: 4, h: 0.35,
      fontSize: 10, color: toolColor
    });

    toolCol++;
    if (toolCol > 1) {
      toolCol = 0;
      toolRow++;
    }
  });

  // ========== 第8页：总结与展望 ==========
  const slideSummary = pptx.addSlide();
  slideSummary.background = { color: colors.darkBg };

  slideSummary.addText("总结与展望", {
    x: 0.5, y: 0.3, w: 9, h: 0.75,
    fontSize: 40, bold: true, color: colors.green,
    align: "center"
  });

  // 核心数据
  const keyData = [
    { value: "75%", label: "无绳工具占比", color: colors.green },
    { value: "40%", label: "年复合增长率", color: colors.blue },
    { value: "500亿", label: "市场规模（元）", color: colors.yellow },
    { value: "12+", label: "重点品类", color: colors.purple }
  ];

  keyData.forEach((item, index) => {
    const xPos = 0.7 + index * 2.2;
    slideSummary.addShape(pptx.shapes.ROUNDED_RECTANGLE, {
      x: xPos, y: 1.2, w: 2, h: 1,
      fill: { color: colors.cardBg },
      line: { color: item.color, transparency: 50 }
    });
    slideSummary.addText(item.value, {
      x: xPos, y: 1.25, w: 2, h: 0.5,
      fontSize: 26, bold: true, color: item.color,
      align: "center", valign: "middle"
    });
    slideSummary.addText(item.label, {
      x: xPos, y: 1.75, w: 2, h: 0.4,
      fontSize: 11.5, color: colors.lightText,
      align: "center"
    });
  });

  // 核心发现
  const insights = [
    { title: "无绳化是必然趋势", desc: "锂电池技术进步驱动，便携性需求持续增长", color: colors.green },
    { title: "智能化成为标配", desc: "扭矩控制、数据联网、状态监测功能普及", color: colors.blue },
    { title: "场景细分化", desc: "针对不同行业开发专用工具，专业化程度提升", color: colors.purple },
    { title: "安全标准升级", desc: "电池安全、电磁兼容等要求日益严格", color: colors.yellow }
  ];

  insights.forEach((item, index) => {
    const col = index % 2;
    const row = Math.floor(index / 2);
    const xPos = 0.7 + col * 4.6;
    const yPos = 2.5 + row * 1.7;
    
    slideSummary.addShape(pptx.shapes.ROUNDED_RECTANGLE, {
      x: xPos, y: yPos, w: 4.3, h: 1.55,
      fill: { color: colors.cardBg },
      line: { color: item.color, transparency: 50 }
    });
    slideSummary.addText(`→ ${item.title}`, {
      x: xPos + 0.2, y: yPos + 0.15, w: 3.9, h: 0.45,
      fontSize: 18, bold: true, color: item.color
    });
    slideSummary.addText(item.desc, {
      x: xPos + 0.2, y: yPos + 0.65, w: 3.9, h: 0.8,
      fontSize: 12, color: colors.lightText
    });
  });

  // 战略建议
  slideSummary.addShape(pptx.shapes.RECTANGLE, {
    x: 0, y: 5.9, w: 10, h: 1.3,
    fill: { color: colors.cardBg }
  });

  const suggestions = [
    { icon: "🔋", title: "产品策略", text: "加大无绳产品线 · 开发专用工具 · 智能化升级" },
    { icon: "⚡", title: "技术创新", text: "电池技术 · 电机效率 · IoT互联互通" },
    { icon: "🎯", title: "市场拓展", text: "深耕新能源行业 · 建立解决方案 · 完善服务" }
  ];

  suggestions.forEach((s, i) => {
    const xPos = 1 + i * 3;
    slideSummary.addText(`${s.icon} ${s.title}`, {
      x: xPos, y: 5.95, w: 2.8, h: 0.4,
      fontSize: 15, bold: true, color: colors.cyan
    });
    slideSummary.addText(s.text, {
      x: xPos, y: 6.35, w: 2.8, h: 0.7,
      fontSize: 11, color: colors.lightText
    });
  });

  // ========== 第9页：结束页 ==========
  const slideEnd = pptx.addSlide();
  slideEnd.background = { color: colors.darkBg };

  slideEnd.addText("谢谢观看", {
    x: 0.5, y: 2, w: 9, h: 1.2,
    fontSize: 60, bold: true, color: colors.green,
    align: "center"
  });

  slideEnd.addText("新能源行业电动工具使用分析报告", {
    x: 0.5, y: 3.5, w: 9, h: 0.6,
    fontSize: 20, color: colors.text,
    align: "center"
  });

  slideEnd.addText(`${new Date().getFullYear()}年`, {
    x: 0.5, y: 4.5, w: 9, h: 0.4,
    fontSize: 14, color: colors.lightText,
    align: "center"
  });

  // 保存文件
  const fileName = `新能源行业电动工具分析报告_${new Date().getFullYear()}.pptx`;
  await pptx.writeFile({ fileName });
  
  return fileName;
};
