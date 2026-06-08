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

  // 定义颜色方案
  const colors = {
    primary: "1E40AF",      // 深蓝色
    secondary: "10B981",    // 绿色
    accent: "F59E0B",       // 黄色
    text: "1F2937",         // 深灰文字
    lightText: "6B7280",    // 浅灰文字
    white: "FFFFFF",
    lightBg: "F3F4F6",
    gradient1: "1E3A8A",    // 渐变起始
    gradient2: "0F766E"    // 渐变结束
  };

  // ========== 第1页：封面 ==========
  const slide1 = pptx.addSlide();
  slide1.background = { color: colors.gradient1 };

  // 添加装饰元素
  slide1.addShape(pptx.shapes.OVAL, {
    x: -1, y: -1, w: 4, h: 4,
    fill: { color: colors.secondary, transparency: 80 }
  });
  slide1.addShape(pptx.shapes.OVAL, {
    x: 7, y: 3, w: 5, h: 5,
    fill: { color: colors.accent, transparency: 85 }
  });

  // 标题
  slide1.addText("新能源行业", {
    x: 0.5, y: 1.8, w: 9, h: 1,
    fontSize: 48, bold: true, color: colors.white,
    align: "center"
  });
  slide1.addText("电动工具使用分析报告", {
    x: 0.5, y: 2.8, w: 9, h: 0.8,
    fontSize: 36, color: colors.white,
    align: "center"
  });

  // 副标题
  slide1.addText("光伏 | 新能源汽车 | 风电 | 储能", {
    x: 0.5, y: 4, w: 9, h: 0.5,
    fontSize: 18, color: colors.white,
    align: "center", transparency: 30
  });

  // 底部信息
  slide1.addText(`${new Date().getFullYear()}年 行业研究报告`, {
    x: 0.5, y: 5, w: 9, h: 0.3,
    fontSize: 14, color: colors.white,
    align: "center", transparency: 50
  });

  // ========== 第2页：目录 ==========
  const slide2 = pptx.addSlide();
  slide2.background = { color: colors.lightBg };

  slide2.addText("报告目录", {
    x: 0.5, y: 0.4, w: 9, h: 0.8,
    fontSize: 36, bold: true, color: colors.primary,
    align: "center"
  });

  const tocItems = [
    "行业分类概述",
    "核心工序分析",
    "电动工具分类",
    "总结与展望"
  ];

  tocItems.forEach((item, index) => {
    // 序号圆圈
    slide2.addShape(pptx.shapes.OVAL, {
      x: 2, y: 1.5 + index * 1, w: 0.6, h: 0.6,
      fill: { color: colors.primary }
    });
    slide2.addText(String(index + 1), {
      x: 2, y: 1.5 + index * 1, w: 0.6, h: 0.6,
      fontSize: 20, bold: true, color: colors.white,
      align: "center", valign: "middle"
    });
    // 标题文字
    slide2.addText(item, {
      x: 2.8, y: 1.5 + index * 1, w: 5, h: 0.6,
      fontSize: 22, color: colors.text,
      valign: "middle"
    });
  });

  // ========== 第3页：行业分类 ==========
  industries.forEach((industry, indIndex) => {
    const slide = pptx.addSlide();
    slide.background = { color: colors.lightBg };

    // 标题栏
    slide.addShape(pptx.shapes.RECTANGLE, {
      x: 0, y: 0, w: 10, h: 1.1,
      fill: { color: colors.primary }
    });
    slide.addText(`${indIndex + 1}. ${industry.name}`, {
      x: 0.5, y: 0.2, w: 9, h: 0.7,
      fontSize: 28, bold: true, color: colors.white,
      valign: "middle"
    });

    // 行业描述
    slide.addText(industry.description, {
      x: 0.5, y: 1.3, w: 9, h: 0.6,
      fontSize: 16, color: colors.lightText
    });

    // 工序列表
    let yPos = 2.1;
    industry.processes.forEach((process, pIndex) => {
      // 工序卡片背景
      slide.addShape(pptx.shapes.ROUNDED_RECTANGLE, {
        x: 0.5, y: yPos, w: 9, h: 1.1,
        fill: { color: colors.white },
        rectRadius: 0.1
      });

      // 序号
      slide.addShape(pptx.shapes.OVAL, {
        x: 0.7, y: yPos + 0.25, w: 0.5, h: 0.5,
        fill: { color: colors.secondary }
      });
      slide.addText(String(pIndex + 1), {
        x: 0.7, y: yPos + 0.25, w: 0.5, h: 0.5,
        fontSize: 14, bold: true, color: colors.white,
        align: "center", valign: "middle"
      });

      // 工序名称
      slide.addText(process.name, {
        x: 1.4, y: yPos + 0.1, w: 3, h: 0.4,
        fontSize: 16, bold: true, color: colors.text
      });

      // 工序描述
      slide.addText(process.description, {
        x: 1.4, y: yPos + 0.5, w: 7.8, h: 0.5,
        fontSize: 12, color: colors.lightText
      });

      // 使用的工具标签
      const toolText = process.tools.join(" | ");
      slide.addText(`工具: ${toolText}`, {
        x: 4.5, y: yPos + 0.2, w: 4.8, h: 0.3,
        fontSize: 10, color: colors.secondary
      });

      yPos += 1.2;
    });
  });

  // ========== 工具分类页 ==========
  const slideTools = pptx.addSlide();
  slideTools.background = { color: colors.lightBg };

  slideTools.addShape(pptx.shapes.RECTANGLE, {
    x: 0, y: 0, w: 10, h: 1.1,
    fill: { color: colors.secondary }
  });
  slideTools.addText("4. 电动工具分类", {
    x: 0.5, y: 0.2, w: 9, h: 0.7,
    fontSize: 28, bold: true, color: colors.white,
    valign: "middle"
  });

  // 分类统计
  const cordlessTools = tools.filter(t => t.isCordless);
  const cordedTools = tools.filter(t => !t.isCordless);

  slideTools.addText(`无绳工具: ${cordlessTools.length}款`, {
    x: 0.5, y: 1.3, w: 4.5, h: 0.4,
    fontSize: 18, bold: true, color: colors.secondary
  });

  slideTools.addText(`有绳工具: ${cordedTools.length}款`, {
    x: 5, y: 1.3, w: 4.5, h: 0.4,
    fontSize: 18, bold: true, color: colors.primary
  });

  // 工具列表
  let toolY = 1.9;
  tools.forEach((tool, index) => {
    if (index < 6) { // 只显示前6个
      const col = index % 2;
      const row = Math.floor(index / 2);
      const xPos = 0.5 + col * 4.7;
      const yPos = toolY + row * 1.1;

      slideTools.addShape(pptx.shapes.ROUNDED_RECTANGLE, {
        x: xPos, y: yPos, w: 4.4, h: 1,
        fill: { color: colors.white },
        rectRadius: 0.08
      });

      // 无绳标签
      if (tool.isCordless) {
        slideTools.addShape(pptx.shapes.ROUNDED_RECTANGLE, {
          x: xPos + 3.4, y: yPos + 0.1, w: 0.8, h: 0.3,
          fill: { color: colors.secondary },
          rectRadius: 0.05
        });
        slideTools.addText("无绳", {
          x: xPos + 3.4, y: yPos + 0.1, w: 0.8, h: 0.3,
          fontSize: 9, bold: true, color: colors.white,
          align: "center", valign: "middle"
        });
      }

      slideTools.addText(tool.name, {
        x: xPos + 0.15, y: yPos + 0.1, w: 3, h: 0.4,
        fontSize: 14, bold: true, color: colors.text
      });

      slideTools.addText(tool.description, {
        x: xPos + 0.15, y: yPos + 0.5, w: 4.1, h: 0.4,
        fontSize: 10, color: colors.lightText
      });
    }
  });

  // ========== 总结页 ==========
  const slideSummary = pptx.addSlide();
  slideSummary.background = { color: colors.gradient2 };

  slideSummary.addText("总结与展望", {
    x: 0.5, y: 0.4, w: 9, h: 0.8,
    fontSize: 36, bold: true, color: colors.white,
    align: "center"
  });

  const insights = [
    { title: "无绳化趋势", desc: "锂电池技术进步推动无绳工具快速发展" },
    { title: "智能化升级", desc: "扭矩控制、数据联网等功能日益普及" },
    { title: "场景专业化", desc: "针对不同行业开发专用工具" },
    { title: "安全标准提升", desc: "电池安全和电磁兼容要求更严格" }
  ];

  insights.forEach((item, index) => {
    const col = index % 2;
    const row = Math.floor(index / 2);
    const xPos = 0.5 + col * 4.7;
    const yPos = 1.5 + row * 1.5;

    slideSummary.addShape(pptx.shapes.ROUNDED_RECTANGLE, {
      x: xPos, y: yPos, w: 4.4, h: 1.3,
      fill: { color: colors.white, transparency: 10 },
      rectRadius: 0.1
    });

    slideSummary.addText(item.title, {
      x: xPos + 0.2, y: yPos + 0.2, w: 4, h: 0.4,
      fontSize: 18, bold: true, color: colors.white
    });

    slideSummary.addText(item.desc, {
      x: xPos + 0.2, y: yPos + 0.7, w: 4, h: 0.4,
      fontSize: 12, color: colors.white, transparency: 20
    });
  });

  // 底部数据
  slideSummary.addText("75% 无绳工具占比 | 40% 年增长率 | 500亿市场规模", {
    x: 0.5, y: 4.8, w: 9, h: 0.4,
    fontSize: 14, color: colors.white,
    align: "center", transparency: 30
  });

  // ========== 最后一页 ==========
  const slideEnd = pptx.addSlide();
  slideEnd.background = { color: colors.gradient1 };

  slideEnd.addText("谢谢观看", {
    x: 0.5, y: 2, w: 9, h: 1,
    fontSize: 48, bold: true, color: colors.white,
    align: "center"
  });

  slideEnd.addText("新能源行业电动工具使用分析报告", {
    x: 0.5, y: 3.2, w: 9, h: 0.5,
    fontSize: 18, color: colors.white,
    align: "center", transparency: 30
  });

  // 保存文件
  const fileName = `新能源行业电动工具分析报告_${new Date().getFullYear()}.pptx`;
  await pptx.writeFile({ fileName });
  
  return fileName;
};
