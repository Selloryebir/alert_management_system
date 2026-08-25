import { createHash } from "node:crypto";
import {
  mkdtemp,
  mkdir,
  readFile,
  readdir,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  createCanvas,
  DOMMatrix,
  ImageData,
  Path2D,
} from "@napi-rs/canvas";
import mammoth from "mammoth";

globalThis.DOMMatrix = DOMMatrix;
globalThis.ImageData = ImageData;
globalThis.Path2D = Path2D;

const { getDocument, ImageKind, OPS } = await import("pdfjs-dist/legacy/build/pdf.mjs");

const scriptDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(scriptDir, "../..");
const backgroundsDir = join(repoRoot, "docs/backgrounds");
const defaultOutputDir = join(repoRoot, "docs/sources");

const sources = {
  pdf: "报警管理系统V1.0研发项目.pdf",
  archive: "2025年RD1、RD2、RD4、RD5全套备查完整文档.docx",
  rd3Proposal: "2025RD3立项报告书彭磊.docx",
  rd3Closure: "2025RD3结项报告彭磊.docx",
};

const historicalNotice = `> 来源属性：历史材料提取结果。正文中的“已完成”“已通过”“达到指标”等均是源文件陈述，未由当前重建项目验证，不得直接作为当前实现事实或验收证据。\n\n`;

function normalizeText(value) {
  return value
    .replace(/\r\n?/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function pdfPageText(items) {
  let result = "";
  for (const item of items) {
    if (!("str" in item)) continue;
    result += item.str;
    result += item.hasEOL ? "\n" : " ";
  }
  return normalizeText(result);
}

function markdownPages(title, sourceName, pages, pageTexts) {
  const sections = pages.map(
    (page) => `## PDF 第 ${page} 页\n\n${pageTexts.get(page) || "（本页未提取到可检索文字。）"}`,
  );
  return `# ${title}\n\n${historicalNotice}来源文件：\`${sourceName}\`\n\n来源页码：${pages[0]}–${pages.at(-1)}\n\n${sections.join("\n\n")}`;
}

async function fileIdentity(path) {
  const data = await readFile(path);
  return {
    path: relative(repoRoot, path).replaceAll("\\", "/"),
    bytes: data.byteLength,
    sha256: createHash("sha256").update(data).digest("hex"),
  };
}

async function writeUtf8(path, value) {
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, value.endsWith("\n") ? value : `${value}\n`, "utf8");
}

async function writeJson(path, value) {
  await writeUtf8(path, `${JSON.stringify(value, null, 2)}\n`);
}

async function extractLargestPageImage(page, outputPath) {
  const operators = await page.getOperatorList();
  const candidates = [];
  for (let index = 0; index < operators.fnArray.length; index += 1) {
    if (operators.fnArray[index] !== OPS.paintImageXObject) continue;
    const image = page.objs.get(operators.argsArray[index][0]);
    candidates.push(image);
  }
  const image = candidates.sort(
    (left, right) => right.width * right.height - left.width * left.height,
  )[0];
  if (!image) throw new Error(`PDF 第 ${page.pageNumber} 页没有可提取的内嵌图片`);

  const canvas = createCanvas(image.width, image.height);
  const context = canvas.getContext("2d");
  const rgba = context.createImageData(image.width, image.height);
  if (image.kind === ImageKind.RGBA_32BPP) {
    rgba.data.set(image.data);
  } else if (image.kind === ImageKind.RGB_24BPP) {
    for (let sourceIndex = 0, targetIndex = 0; sourceIndex < image.data.length; sourceIndex += 3, targetIndex += 4) {
      rgba.data[targetIndex] = image.data[sourceIndex];
      rgba.data[targetIndex + 1] = image.data[sourceIndex + 1];
      rgba.data[targetIndex + 2] = image.data[sourceIndex + 2];
      rgba.data[targetIndex + 3] = 255;
    }
  } else {
    throw new Error(`PDF 第 ${page.pageNumber} 页图片格式不受支持：${image.kind}`);
  }
  context.putImageData(rgba, 0, 0);
  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, await canvas.encode("png"));
}

async function extractPdf(outputDir) {
  const sourcePath = join(backgroundsDir, sources.pdf);
  const source = await fileIdentity(sourcePath);
  const pdfDir = join(outputDir, "alarm-management-v1-pdf");
  const document = await getDocument({
    data: new Uint8Array(await readFile(sourcePath)),
    disableWorker: true,
    useSystemFonts: true,
  }).promise;

  if (document.numPages !== 131) {
    throw new Error(`PDF 页数发生变化：预期 131，实际 ${document.numPages}`);
  }

  const textPages = [
    ...Array.from({ length: 30 }, (_, index) => index + 1),
    ...Array.from({ length: 13 }, (_, index) => index + 52),
    65,
  ];
  const pageTexts = new Map();
  for (const pageNumber of textPages) {
    const page = await document.getPage(pageNumber);
    const content = await page.getTextContent();
    pageTexts.set(pageNumber, pdfPageText(content.items));
    page.cleanup(true);
  }

  const projectPages = Array.from({ length: 17 }, (_, index) => index + 1);
  const specificationPages = Array.from({ length: 13 }, (_, index) => index + 18);
  const processPages = Array.from({ length: 13 }, (_, index) => index + 52);
  await writeUtf8(
    join(pdfDir, "01-project-records.md"),
    markdownPages("立项与结项历史记录", sources.pdf, projectPages, pageTexts),
  );
  await writeUtf8(
    join(pdfDir, "02-requirements-and-design.md"),
    markdownPages("需求与概要设计历史记录", sources.pdf, specificationPages, pageTexts),
  );
  await writeUtf8(
    join(pdfDir, "04-process-and-test-records.md"),
    markdownPages("中期与测试历史记录", sources.pdf, processPages, pageTexts),
  );

  const manualPages = Array.from({ length: 21 }, (_, index) => index + 31);
  const manualLines = [];
  for (const pageNumber of manualPages) {
    const page = await document.getPage(pageNumber);
    const imageName = `manual-page-${String(pageNumber).padStart(3, "0")}.png`;
    await extractLargestPageImage(page, join(pdfDir, "images", imageName));
    manualLines.push(`## PDF 第 ${pageNumber} 页\n\n![用户手册第 ${pageNumber} 页](images/${imageName})`);
    page.cleanup(true);
  }
  await writeUtf8(
    join(pdfDir, "03-user-manual.md"),
    `# 用户使用手册页面\n\n${historicalNotice}来源文件：\`${sources.pdf}\`\n\n来源页码：31–51。这些页面以图片为主，图片用于还原历史界面与操作流程；图片中的功能仍需由当前实现和测试确认。\n\n${manualLines.join("\n\n")}`,
  );

  await writeUtf8(
    join(pdfDir, "05-software-registration-reference.md"),
    `# 软件著作权材料索引\n\n${historicalNotice}来源文件：\`${sources.pdf}\`\n\n来源页码：65。\n\n分类：\`reference-only\`。本页仅用于追踪历史软件著作权材料，不形成产品功能要求，不证明当前仓库中存在对应源码或安装包。\n\n## 可检索文字\n\n${pageTexts.get(65) || "（本页未提取到可检索文字。）"}`,
  );

  const manifest = {
    schemaVersion: 1,
    source,
    sourceClassification: "related-historical-material",
    currentVerificationStatus: "unverified",
    pageCount: document.numPages,
    segments: [
      {
        pages: "1-17",
        status: "extracted",
        output: "01-project-records.md",
        description: "立项与结项历史记录",
      },
      {
        pages: "18-30",
        status: "extracted",
        output: "02-requirements-and-design.md",
        description: "需求规格与概要设计历史记录",
      },
      {
        pages: "31-51",
        status: "extracted-as-images",
        output: "03-user-manual.md; images/manual-page-031.png through manual-page-051.png",
        description: "图片化用户手册与历史界面",
      },
      {
        pages: "52-64",
        status: "extracted",
        output: "04-process-and-test-records.md",
        description: "中期和测试历史记录；结论未经当前项目复核",
      },
      {
        pages: "65",
        status: "reference-only",
        output: "05-software-registration-reference.md",
        description: "软件著作权材料索引，不作为实现或验收依据",
      },
      {
        pages: "66-131",
        status: "excluded",
        output: null,
        description: "图片化占位代码；用户已确认不具备实际恢复价值，故不提取文字或图片",
      },
    ],
  };
  await writeJson(join(pdfDir, "manifest.json"), manifest);
  await writeUtf8(
    join(pdfDir, "README.md"),
    `# 报警管理系统 V1.0 研发项目 PDF\n\n- 来源：\`${source.path}\`\n- 来源页数：131\n- 分类：与报警管理系统相关的历史材料\n- 当前验证状态：未验证\n\n## 提取结果\n\n- [立项与结项历史记录](01-project-records.md)：第 1–17 页。\n- [需求与概要设计历史记录](02-requirements-and-design.md)：第 18–30 页。\n- [用户使用手册页面](03-user-manual.md)：第 31–51 页，并保存 21 张页面图片。\n- [中期与测试历史记录](04-process-and-test-records.md)：第 52–64 页。\n- [软件著作权材料索引](05-software-registration-reference.md)：第 65 页，仅供参考。\n- 第 66–131 页为图片化占位代码，已排除且未生成图片。\n\n详细页码、状态和排除理由见 [manifest.json](manifest.json)。源文件中的完成情况、测试结果、性能和合规性声明均须由当前重建项目重新验证。`,
  );
}

function cleanMammothMarkdown(value) {
  return normalizeText(
    value
      .replace(/<a id="heading_[^"]+"><\/a>/g, "")
      .replace(/\\([.+])/g, "$1"),
  );
}

async function extractArchiveDocx(outputDir) {
  const sourcePath = join(backgroundsDir, sources.archive);
  const source = await fileIdentity(sourcePath);
  const output = await mammoth.convertToMarkdown({ path: sourcePath });
  const startMarker = "第一套：2025RD1";
  const endMarker = "第二套：2025RD2";
  const start = output.value.indexOf(startMarker);
  const end = output.value.indexOf(endMarker);
  if (start < 0 || end <= start) {
    throw new Error("无法定位混合 DOCX 中的 RD1/RD2 边界");
  }

  const rd1 = cleanMammothMarkdown(output.value.slice(start, end));
  const archiveDir = join(outputDir, "rd1-archive-docx");
  await writeUtf8(
    join(archiveDir, "rd1-alarm-management.md"),
    `# RD1 报警管理系统历史备查材料\n\n${historicalNotice}来源文件：\`${sources.archive}\`\n\n来源章节：从“第一套：2025RD1 报警管理系统V1.0研发项目”起，到“第二套：2025RD2 故障树（FTA）分析软件V1.0”前止。\n\n${rd1}`,
  );
  await writeJson(join(archiveDir, "manifest.json"), {
    schemaVersion: 1,
    source,
    sourceClassification: "mixed-project-archive",
    currentVerificationStatus: "unverified",
    sections: [
      {
        heading: "第一套：2025RD1 报警管理系统V1.0研发项目",
        status: "extracted",
        output: "rd1-alarm-management.md",
        description: "与当前报警管理系统相关的 RD1 归档章节",
      },
      {
        heading: "第二套：2025RD2 故障树（FTA）分析软件V1.0",
        status: "excluded",
        output: null,
        description: "其他项目，与当前报警管理系统无关",
      },
      {
        heading: "第三套：2025RD4 安全要求规格书(SRS)编制系统V1.0",
        status: "excluded",
        output: null,
        description: "其他项目，与当前报警管理系统无关",
      },
      {
        heading: "第三套：2025RD5化学品反应相容性智能矩阵与风险管控系统V1.0",
        status: "excluded",
        output: null,
        description: "源文件将其误标为第三套；属于其他项目，与当前报警管理系统无关",
      },
    ],
    extractionWarnings: output.messages.map((message) => message.message),
  });
  await writeUtf8(
    join(archiveDir, "README.md"),
    `# RD1/RD2/RD4/RD5 混合备查文档\n\n- 来源：\`${source.path}\`\n- 分类：多项目混合历史归档\n- 当前验证状态：未验证\n\n仅 [RD1 报警管理系统章节](rd1-alarm-management.md) 进入提取结果。RD2、RD4、RD5 属于其他项目，已明确排除。源文件中 RD5 的套次标题仍写作“第三套”，这里保留并记录该源内错误，不据此改写来源。\n\nRD1 中存在模板占位语句和历史完成/测试结论；它们仅作为来源陈述保留，不证明当前重建项目已实现或已通过。详细边界见 [manifest.json](manifest.json)。`,
  );
}

async function summarizeUnrelatedDocx(outputDir, key, sourceName, documentKind) {
  const sourcePath = join(backgroundsDir, sourceName);
  const source = await fileIdentity(sourcePath);
  const raw = await mammoth.extractRawText({ path: sourcePath });
  const normalized = normalizeText(raw.value);
  if (!normalized.includes("一种用于处理成分复杂废气的处理设备")) {
    throw new Error(`${sourceName} 的项目名称与预期不符，请人工重新判定`);
  }
  const dir = join(outputDir, key);
  const manifest = {
    schemaVersion: 1,
    source,
    sourceClassification: "unrelated",
    currentVerificationStatus: "not-applicable",
    documentKind,
    declaredProjectId: "2025RD3",
    declaredProjectName: "一种用于处理成分复杂废气的处理设备",
    status: "excluded",
    reason: "该文档描述复杂废气处理实体设备，不属于报警管理系统软件项目",
    bodyExtraction: "not-produced",
  };
  await writeJson(join(dir, "manifest.json"), manifest);
  await writeUtf8(
    join(dir, "README.md"),
    `# 2025RD3 ${documentKind}来源登记\n\n- 来源：\`${source.path}\`\n- 文件声明项目编号：\`2025RD3\`\n- 文件声明项目名称：\`一种用于处理成分复杂废气的处理设备\`\n- 分类：\`unrelated\`\n- 处理状态：\`excluded\`\n\n## 摘要\n\n该材料描述由预处理、药剂吸附吸收、三级冷凝、吸附、智能控制及辅助系统组成的复杂废气处理实体设备研发，不是报警管理系统软件项目。为避免污染后续产品事实与技术决策，仅保留本元数据、摘要和排除原因，不将正文提取为产品输入。`,
  );
}

async function buildIndex(outputDir) {
  const identities = await Promise.all(
    Object.values(sources).map((name) => fileIdentity(join(backgroundsDir, name))),
  );
  await writeJson(join(outputDir, "manifest.json"), {
    schemaVersion: 1,
    purpose: "报警管理系统灾后重建的历史来源索引",
    statementsPolicy: "历史完成、测试、性能和合规性结论均为 unverified，须由当前实现重新验证",
    sources: identities,
  });
  await writeUtf8(
    join(outputDir, "README.md"),
    `# 历史来源提取索引\n\n本目录是灾后重建的**历史来源层**，用于追踪原始材料，不是当前产品事实或已通过验收的证明。原始文件继续保存在 \`docs/backgrounds\`，未移动、未改写。\n\n## 来源与处置\n\n| 原始文件 | 相关性 | 处置 |\n| --- | --- | --- |\n| \`${sources.pdf}\` | 相关 | [按页提取](alarm-management-v1-pdf/README.md)；第 66–131 页占位代码排除 |\n| \`${sources.archive}\` | 混合 | [仅提取 RD1](rd1-archive-docx/README.md)；RD2/RD4/RD5 排除 |\n| \`${sources.rd3Proposal}\` | 不相关 | [仅登记元数据与摘要](rd3-waste-gas-proposal/README.md) |\n| \`${sources.rd3Closure}\` | 不相关 | [仅登记元数据与摘要](rd3-waste-gas-closure/README.md) |\n\n## 使用约束\n\n- 开发智能体不得直接将本目录中的历史陈述视为当前需求、架构决策或验收结果。\n- 后续应在产品事实层对候选需求进行采纳、改写或屏蔽，并保留到本目录页码/章节的追踪关系。\n- “已完成”“测试通过”“准确率”“响应时间”“合规”等声明在当前重建项目中一律视为 \`unverified\`。\n- 机器可读的来源文件身份见 [manifest.json](manifest.json)，各来源的提取边界见其子目录 manifest。`,
  );
}

async function extractAll(outputDir) {
  await rm(outputDir, { recursive: true, force: true });
  await mkdir(outputDir, { recursive: true });
  await extractPdf(outputDir);
  await extractArchiveDocx(outputDir);
  await summarizeUnrelatedDocx(
    outputDir,
    "rd3-waste-gas-proposal",
    sources.rd3Proposal,
    "立项报告",
  );
  await summarizeUnrelatedDocx(
    outputDir,
    "rd3-waste-gas-closure",
    sources.rd3Closure,
    "结项报告",
  );
  await buildIndex(outputDir);
}

async function listFiles(root, current = root) {
  const entries = await readdir(current, { withFileTypes: true });
  const files = [];
  for (const entry of entries.sort((a, b) => a.name.localeCompare(b.name, "en"))) {
    const path = join(current, entry.name);
    if (entry.isDirectory()) files.push(...(await listFiles(root, path)));
    else files.push(relative(root, path).replaceAll("\\", "/"));
  }
  return files;
}

async function compareOutputs(expectedDir, actualDir) {
  const expectedFiles = await listFiles(expectedDir);
  const actualFiles = await listFiles(actualDir);
  if (JSON.stringify(expectedFiles) !== JSON.stringify(actualFiles)) {
    throw new Error("提取文件清单不一致，请重新运行 extract 并审阅差异");
  }
  for (const file of expectedFiles) {
    const [expected, actual] = await Promise.all([
      readFile(join(expectedDir, file)),
      readFile(join(actualDir, file)),
    ]);
    if (!expected.equals(actual)) {
      throw new Error(`提取结果不可重复：${file}`);
    }
  }
  return expectedFiles;
}

async function check() {
  try {
    const outputStats = await stat(defaultOutputDir);
    if (!outputStats.isDirectory()) throw new Error("docs/sources 不是目录");
  } catch {
    throw new Error("docs/sources 尚未生成，请先运行 npm run extract");
  }
  const temporaryRoot = await mkdtemp(join(tmpdir(), "alarm-doc-extraction-"));
  const temporaryOutput = join(temporaryRoot, "sources");
  try {
    await extractAll(temporaryOutput);
    const files = await compareOutputs(defaultOutputDir, temporaryOutput);
    console.log(`提取结果检查通过：${files.length} 个文件逐字节一致。`);
  } finally {
    await rm(temporaryRoot, { recursive: true, force: true });
  }
}

if (process.argv.includes("--check")) {
  await check();
} else {
  await extractAll(defaultOutputDir);
  const files = await listFiles(defaultOutputDir);
  console.log(`提取完成：${files.length} 个文件写入 docs/sources。`);
}
