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

  // 定义颜色方案 - 与网页一致
  const colors = {
    primary: "3B82F6",      // 蓝色-500
    secondary: "10B981",    // 绿色-500
    accent: "F59E0B",       // 黄色-500
    text: "FFFFFF",         // 白色文字
    lightText: "9CA3AF",    // 浅灰文字
    darkBg: "0F172A",       // 深蓝背景
    cardBg: "1E293B",       // 卡片背景
    gradient1: "0F172A",    // 渐变起始
    gradient2: "1E3A8A"    // 渐变结束
  };

  // ========== 第1页：封面 ==========
  const slide1 = pptx.addSlide();
  slide1.background = { color: colors.darkBg };

  // 添加装饰圆圈
  slide1.addShape(pptx.shapes.OVAL, {
    x: -1, y: -1, w: 5, h: 5,
    fill: { color: colors.primary, transparency: 85 }
  });
  slide1.addShape(pptx.shapes.OVAL, {
    x: 6, y: 2, w: 6, h: 6,
    fill: { color: colors.secondary, transparency: 90 }
  });

  // 主要标题 - 渐变色文字效果
  slide1.addText("新能源行业", {
    x: 0.5, y: 1.5, w: 9, h: 1.2,
    fontSize: 64, bold: true, color: colors.secondary,
    align: "center"
  });
  slide1.addText("电动工具使用分析报告", {
    x: 0.5, y: 2.8, w: 9, h: 1,
    fontSize: 42, color: colors.text,
    align: "center"
  });

  // 副标题
  slide1.addText("光伏 · 新能源汽车 · 风电 · 储能", {
    x: 0.5, y: 4.2, w: 9, h: 0.6,
    fontSize: 20, color: colors.lightText,
    align: "center"
  });

  // 底部信息
  slide1.addText(`${new Date().getFullYear()}年 · 行业研究报告`, {
    x: 0.5, y: 5.5, w: 9, h: 0.4,
    fontSize: 14, color: colors.lightText,
    align: "center"
  });

  // ========== 第2页：目录 ==========
  const slide2 = pptx.addSlide();
  slide2.background = { color: colors.darkBg };

  slide2.addText("报告目录", {
    x: 0.5, y: 0.5, w: 9, h: 0.8,
    fontSize: 42, bold: true, color: colors.primary,
    align: "center"
  });

  const tocItems = [
    { num: "01", title: "行业分类概述", desc: "4大核心新能源领域" },
    { num: "02", title: "核心工序分析", desc: "15道生产工序详解" },
    { num: "03", title: "电动工具分类", desc: "12款重点产品展示" },
    { num: "04", title: "总结与展望", desc: "趋势分析与建议" }
  ];

  tocItems.forEach((item, index) => {
    const yPos = 1.5 + index * 1.1;
    // 编号
    slide2.addText(item.num, {
      x: 1.5, y: yPos, w: 1, h: 0.8,
      fontSize: 32, bold: true, color: colors.secondary,
      align: "center", valign: "middle"
    });
    // 标题
    slide2.addText(item.title, {
      x: 2.8, y: yPos, w: 5, h: 0.4,
      fontSize: 24, color: colors.text,
      valign: "middle"
    });
    // 描述
    slide2.addText(item.desc, {
      x: 2.8, y: yPos + 0.4, w: 5, h: 0.4,
      fontSize: 14, color: colors.lightText,
      valign: "middle"
    });
  });

  // ========== 第3页：行业分类 ==========
  const slideIndustry = pptx.addSlide();
  slideIndustry.background = { color: colors.darkBg };

  slideIndustry.addText("行业分类概述", {
    x: 0.5, y: 0.4, w: 9, h: 0.8,
    fontSize: 38, bold: true, color: colors.primary,
    align: "center"
  });

  const industryCards = [
    { name: "光伏行业", desc: "太阳能光伏组件制造、安装及维护", color: colors.primary, emoji: "☀️" },
    { name: "新能源汽车", desc: "电动汽车整车及核心零部件研发生产", color: colors.secondary, emoji: "🚗" },
    { name: "风电行业", desc: "风力发电机组的研发、制造、安装运维", color: "06B6D4", emoji: "🌬️" },
    { name: "储能行业", desc: "电化学储能、物理储能等系统的制造集成", color: "A855F7", emoji: "🔋" }
  ];

  industryCards.forEach((item, index) => {
    const col = index % 2;
    const row = Math.floor(index / 2);
    const xPos = 0.8 + col * 4.5;
    const yPos = 1.4 + row * 2;
    
    // 卡片背景
    slideIndustry.addShape(pptx.shapes.RECTANGLE, {
      x: xPos, y: yPos, w: 4.2, h: 1.8,
      fill: { color: colors.cardBg },
      line: { color: colors.secondary, transparency: 70 }
    });
    // 标题
    slideIndustry.addText(`${item.emoji} ${item.name}`, {
      x: xPos + 0.2, y: yPos + 0.2, w: 3.8, h: 0.5,
      fontSize: 20, bold: true, color: item.color
    });
    // 描述
    slideIndustry.addText(item.desc, {
      x: xPos + 0.2, y: yPos + 0.7, w: 3.8, h: 1,
      fontSize: 13, color: colors.lightText
    });
  });

  // ========== 行业工序页面 ==========
  industries.forEach((industry, indIndex) => {
    const slide = pptx.addSlide();
    slide.background = { color: colors.darkBg };

    // 标题
    slide.addShape(pptx.shapes.RECTANGLE, {
      x: 0, y: 0, w: 10, h: 1,
      fill: { color: colors.cardBg }
    });
    slide.addText(industry.name, {
      x: 0.5, y: 0.2, w: 9, h: 0.6,
      fontSize: 32, bold: true, color: colors.secondary,
      valign: "middle"
    });

    // 工序卡片
    let yPos = 1.2;
    industry.processes.forEach((process, pIndex) => {
      if (yPos < 5) {
        // 卡片背景
        slide.addShape(pptx.shapes.ROUNDED_RECTANGLE, {
          x: 0.8, y: yPos, w: 8.4, h: 1.1,
          fill: { color: colors.cardBg },
          line: { color: colors.secondary, transparency: 80 }
        });

        // 序号
        slide.addText(`${pIndex + 1}`, {
          x: 0.9, y: yPos + 0.15, w: 0.8, h: 0.8,
          fontSize: 24, bold: true, color: colors.primary,
          align: "center", valign: "middle"
        });

        // 工序名称
        slide.addText(process.name, {
          x: 1.9, y: yPos + 0.1, w: 6, h: 0.4,
          fontSize: 18, bold: true, color: colors.text
        });

        // 工序描述
        slide.addText(process.description, {
          x: 1.9, y: yPos + 0.55, w: 7, h: 0.5,
          fontSize: 12, color: colors.lightText
        });

        yPos += 1.3;
      }
    });
  });

  // ========== 工具分类页 ==========
  const slideTools = pptx.addSlide();
  slideTools.background = { color: colors.darkBg };

  slideTools.addText("电动工具分类", {
    x: 0.5, y: 0.3, w: 9, h: 0.7,
    fontSize: 36, bold: true, color: colors.primary,
    align: "center"
  });

  // 无绳 vs 有绳统计
  const cordlessCount = tools.filter(t => t.isCordless).length;
  slideTools.addShape(pptx.shapes.RECTANGLE, {
    x: 0.8, y: 1.2, w: 4, h: 1,
    fill: { color: colors.cardBg },
    line: { color: colors.secondary, transparency: 70 }
  });
  slideTools.addText(`🔋 无绳工具`, {
    x: 1, y: 1.3, w: 3.6, h: 0.35,
    fontSize: 18, bold: true, color: colors.secondary
  });
  slideTools.addText(`${cordlessCount}款重点产品`, {
    x: 1, y: 1.7, w: 3.6, h: 0.4,
    fontSize: 14, color: colors.lightText
  });

  slideTools.addShape(pptx.shapes.RECTANGLE, {
    x: 5.2, y: 1.2, w: 4, h: 1,
    fill: { color: colors.cardBg },
    line: { color: colors.primary, transparency: 70 }
  });
  slideTools.addText(`🔌 有绳工具`, {
    x: 5.4, y: 1.3, w: 3.6, h: 0.35,
    fontSize: 18, bold: true, color: colors.primary
  });
  slideTools.addText(`${tools.length - cordlessCount}款传统产品`, {
    x: 5.4, y: 1.7, w: 3.6, h: 0.4,
    fontSize: 14, color: colors.lightText
  });

  // 工具卡片网格
  let toolRow = 0;
  let toolCol = 0;
  tools.slice(0, 6).forEach((tool, index) => {
    const xPos = 0.8 + toolCol * 4.4;
    const yPos = 2.5 + toolRow * 1.7;
    
    // 卡片背景
    slideTools.addShape(pptx.shapes.ROUNDED_RECTANGLE, {
      x: xPos, y: yPos, w: 4.2, h: 1.5,
      fill: { color: colors.cardBg },
      line: { color: tool.isCordless ? colors.secondary : colors.primary, transparency: 70 }
    });

    // 标签
    if (tool.isCordless) {
      slideTools.addShape(pptx.shapes.ROUNDED_RECTANGLE, {
        x: xPos + 3.2, y: yPos + 0.15, w: 0.85, h: 0.25,
        fill: { color: colors.secondary }
      });
      slideTools.addText("无绳", {
        x: xPos + 3.2, y: yPos + 0.15, w: 0.85, h: 0.25,
        fontSize: 10, bold: true, color: colors.text,
        align: "center", valign: "middle"
      });
    }

    // 工具名称
    slideTools.addText(tool.name, {
      x: xPos + 0.15, y: yPos + 0.1, w: 3, h: 0.35,
      fontSize: 16, bold: true, color: colors.text
    });

    // 分类
    slideTools.addText(tool.category, {
      x: xPos + 0.15, y: yPos + 0.48, w: 2.5, h: 0.25,
      fontSize: 10, color: colors.lightText
    });

    // 描述
    slideTools.addText(tool.description, {
      x: xPos + 0.15, y: yPos + 0.75, w: 3.9, h: 0.65,
      fontSize: 11, color: colors.lightText
    });

    toolCol++;
    if (toolCol > 1) {
      toolCol = 0;
      toolRow++;
    }
  });

  // ========== 总结页 ==========
  const slideSummary = pptx.addSlide();
  slideSummary.background = { color: colors.darkBg };

  slideSummary.addText("总结与展望", {
    x: 0.5, y: 0.4, w: 9, h: 0.8,
    fontSize: 40, bold: true, color: colors.secondary,
    align: "center"
  });

  // 核心发现卡片
  const insights = [
    { title: "无绳化是必然趋势", desc: "锂电池技术进步，便携性需求持续增长", color: colors.secondary },
    { title: "智能化成为标配", desc: "扭矩控制、数据联网功能普及", color: colors.primary },
    { title: "场景细分化", desc: "针对不同行业开发专用工具", color: "A855F7" },
    { title: "安全标准升级", desc: "电池安全、电磁兼容要求更严格", color: "F59E0B" }
  ];

  insights.forEach((item, index) => {
    const col = index % 2;
    const row = Math.floor(index / 2);
    const xPos = 0.8 + col * 4.4;
    const yPos = 1.5 + row * 1.7;
    
    slideSummary.addShape(pptx.shapes.ROUNDED_RECTANGLE, {
      x: xPos, y: yPos, w: 4.2, h: 1.5,
      fill: { color: colors.cardBg },
      line: { color: item.color, transparency: 70 }
    });
    slideSummary.addText(item.title, {
      x: xPos + 0.2, y: yPos + 0.2, w: 3.8, h: 0.4,
      fontSize: 18, bold: true, color: item.color
    });
    slideSummary.addText(item.desc, {
      x: xPos + 0.2, y: yPos + 0.65, w: 3.8, h: 0.75,
      fontSize: 13, color: colors.lightText
    });
  });

  // 关键数据
  const dataCards = [
    { value: "75%", label: "无绳工具占比", color: colors.secondary },
    { value: "40%", label: "年复合增长率", color: colors.primary },
    { value: "500亿", label: "市场规模（元）", color: "F59E0B" },
    { value: "12+", label: "重点品类", color: "A855F7" }
  ];

  dataCards.forEach((item, index) => {
    const xPos = 0.8 + index * 2.2;
    slideSummary.addShape(pptx.shapes.RECTANGLE, {
      x: xPos, y: 5.1, w: 2, h: 0.9,
      fill: { color: colors.cardBg },
      line: { color: item.color, transparency: 70 }
    });
    slideSummary.addText(item.value, {
      x: xPos, y: 5.15, w: 2, h: 0.45,
      fontSize: 24, bold: true, color: item.color,
      align: "center", valign: "middle"
    });
    slideSummary.addText(item.label, {
      x: xPos, y: 5.6, w: 2, h: 0.35,
      fontSize: 11, color: colors.lightText,
      align: "center"
    });
  });

  // ========== 最后一页 ==========
  const slideEnd = pptx.addSlide();
  slideEnd.background = { color: colors.darkBg };

  slideEnd.addText("谢谢观看", {
    x: 0.5, y: 2, w: 9, h: 1.2,
    fontSize: 56, bold: true, color: colors.secondary,
    align: "center"
  });

  slideEnd.addText("新能源行业电动工具使用分析报告", {
    x: 0.5, y: 3.5, w: 9, h: 0.5,
    fontSize: 18, color: colors.lightText,
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
