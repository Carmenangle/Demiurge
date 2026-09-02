import { useCallback, useEffect, useRef, useState } from "react";
import { PageShell, EmptyState, StateHint } from "../components/layout/PageShell";
import { NEWCOMER_GUIDE_SECTIONS, type GuideSection, type GuideStep } from "../lib/newcomerGuide";
import {
  buildGuideHash, docAssetUrl, guideStepAnchorId, parseGuideHash, resolveDocLink,
  splitGuideLinks, stripLeadingDocTitle, type GuideLink,
} from "../lib/guideLinks";
import { fetchGuideDoc, type GuideDoc } from "../api/docs";
import { renderMarkdown } from "../lib/renderMarkdown";
import type { NavSection } from "../lib/viewRouting";

// 新人引导页：内容单一真源在 lib/newcomerGuide.ts（文案 + 插图路径）。
// 左栏子项=章节（lib/viewRouting.ts 的 SECTION_SUBNAV.guide），本组件只渲染当前选中的一节；
// 正文可写三类链接（语法见 lib/guideLinks.ts）：
//   [文案](guide:章节id[#步骤序号])  切到别的章节（并可定位到该章第 N 步）
//   [文案](doc:docs/xx.md)           打开仓库里的独立教学文档（本页内阅读，带返回）
//   [文案](https://...)              外链，新标签页打开
// 文档态 hash = #/guide/<章节id>/doc/<文档路径>，刷新可停留。

function resolveImageSrc(image: string): string {
  if (!image) return "";
  if (image.startsWith("http") || image.startsWith("/")) return image;
  return `/${image}`;
}

function StepImage({ image, alt }: { image: string; alt: string }) {
  const src = resolveImageSrc(image);
  if (src) {
    return (
      <img
        src={src}
        alt={alt}
        style={{
          maxWidth: "100%", borderRadius: 8, border: "1px solid var(--border, rgba(128,128,128,.25))",
          display: "block", margin: "8px 0",
        }}
      />
    );
  }
  return (
    <div
      style={{
        border: "1px dashed var(--border, rgba(128,128,128,.4))", borderRadius: 8,
        padding: "28px 16px", textAlign: "center", color: "var(--text-muted)",
        fontSize: 13, margin: "8px 0",
      }}
    >
      插图待补充（放 frontend/public/onboarding/ 后在 lib/newcomerGuide.ts 填路径）
    </div>
  );
}

function GuideLinkChip({ link, onActivate }: { link: GuideLink; onActivate: (link: GuideLink) => void }) {
  if (link.kind === "external") {
    return (
      <a className="guide-link" href={link.target} target="_blank" rel="noreferrer">
        {link.label} ↗
      </a>
    );
  }
  return (
    <button type="button" className="guide-link" onClick={() => onActivate(link)}>
      {link.label}{link.kind === "doc" ? " ›" : " →"}
    </button>
  );
}

/** 正文按空行分段，段内解析出行内链接（纯文本片段原样渲染）。 */
function StepText({ text, onActivate }: { text: string; onActivate: (link: GuideLink) => void }) {
  const paragraphs = (text || "").split(/\n\s*\n/).filter(Boolean);
  return (
    <>
      {paragraphs.map((p, i) => (
        <p key={i} style={{ margin: "6px 0", fontSize: 20, lineHeight: 1.8, color: "var(--text)" }}>
          {splitGuideLinks(p).map((seg, j) =>
            seg.link
              ? <GuideLinkChip key={j} link={seg.link} onActivate={onActivate} />
              : <span key={j}>{seg.text}</span>,
          )}
        </p>
      ))}
    </>
  );
}

function Step({
  step, sectionId, stepNumber, onActivate,
}: {
  step: GuideStep; sectionId: string; stepNumber: number; onActivate: (link: GuideLink) => void;
}) {
  return (
    <section id={guideStepAnchorId(sectionId, stepNumber)} style={{ marginBottom: 24, scrollMarginTop: 12 }}>
      <h3 style={{ margin: "0 0 8px", fontSize: 22, fontWeight: 600 }}>{step.title}</h3>
      <StepText text={step.text} onActivate={onActivate} />
      {step.image !== undefined && <StepImage image={step.image} alt={step.title} />}
    </section>
  );
}

function SectionCard({
  section, onActivate,
}: { section: GuideSection; onActivate: (link: GuideLink) => void }) {
  return (
    <section
      id={`guide-${section.id}`}
      style={{
        border: "1px solid var(--border, rgba(128,128,128,.25))", borderRadius: 10,
        padding: "16px 20px", marginBottom: 16,
      }}
    >
      <h2 style={{ margin: "0 0 6px", fontSize: 25, fontWeight: 600 }}>{section.title}</h2>
      {section.summary && (
        <p style={{ margin: "0 0 14px", fontSize: 19, color: "var(--text-muted)" }}>{section.summary}</p>
      )}
      {section.steps.length === 0 ? (
        <EmptyState>本节内容待补充。</EmptyState>
      ) : (
        section.steps.map((step, i) => (
          <Step key={i} step={step} sectionId={section.id} stepNumber={i + 1} onActivate={onActivate} />
        ))
      )}
    </section>
  );
}

export function NewcomerGuideView({
  activeId, onGoSection,
}: {
  activeId?: string | null;
  /** 切章节（guide: 链接用）；App 侧会同步 subView 与 hash */
  onGoSection: (section: NavSection, subView: string) => void;
}) {
  // 左栏选中的章节；无/未知 id 回退第一节（快速开始）
  const active = NEWCOMER_GUIDE_SECTIONS.find((s) => s.id === activeId) ?? NEWCOMER_GUIDE_SECTIONS[0];
  const [docPath, setDocPath] = useState<string | null>(null);
  const [doc, setDoc] = useState<GuideDoc | null>(null);
  const [docError, setDocError] = useState<string | null>(null);
  const [docLoading, setDocLoading] = useState(false);
  const [docAttempt, setDocAttempt] = useState(0);   // 「重试」用的自增计数
  const pendingStepRef = useRef<number | null>(null);
  const docBodyRef = useRef<HTMLDivElement | null>(null);

  // hash → 文档态：刷新停留，且左栏切章（hash 只剩两段）时自动收起文档。
  useEffect(() => {
    const sync = () => setDocPath(parseGuideHash(window.location.hash).docPath);
    sync();
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  // 取文档：docPath 变化或点「重试」时重新拉。切文档/卸载时丢弃迟到的响应。
  useEffect(() => {
    if (!docPath) {
      setDoc(null);
      setDocError(null);
      setDocLoading(false);
      return;
    }
    let cancelled = false;
    setDocLoading(true);
    setDocError(null);
    fetchGuideDoc(docPath)
      .then((d) => { if (!cancelled) { setDoc(d); setDocLoading(false); } })
      .catch((e: unknown) => {
        if (cancelled) return;
        setDoc(null);
        setDocError(e instanceof Error ? e.message : String(e));
        setDocLoading(false);
      });
    return () => { cancelled = true; };
  }, [docPath, docAttempt]);

  // 文档正文里的相对图片（../assets/...）在浏览器里会按页面 URL 解析而 404，
  // 渲染后统一改写成 docs/assets 的静态挂载地址（纯函数 docAssetUrl 决定改哪些）。
  useEffect(() => {
    const root = docBodyRef.current;
    if (!root || !doc) return;
    root.querySelectorAll("img").forEach((img) => {
      const src = img.getAttribute("src") || "";
      const next = docAssetUrl(doc.path, src);
      if (next) img.setAttribute("src", next);
    });
  }, [doc]);

  // 跨章跳转落位：切章后 DOM 更新完再滚到目标步骤（pendingStepRef 由链接点击写入）。
  useEffect(() => {
    const stepNumber = pendingStepRef.current;
    if (stepNumber == null) return;
    pendingStepRef.current = null;
    document.getElementById(guideStepAnchorId(active.id, stepNumber))
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [active.id]);

  const scrollToStep = useCallback((sectionId: string, stepNumber: number) => {
    requestAnimationFrame(() => {
      document.getElementById(guideStepAnchorId(sectionId, stepNumber))
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, []);

  const activateLink = useCallback((link: GuideLink) => {
    if (link.kind === "guide") {
      if (link.target === active.id) {
        if (link.stepNumber) scrollToStep(active.id, link.stepNumber);
        return;
      }
      pendingStepRef.current = link.stepNumber ?? null;
      onGoSection("guide", link.target);
      return;
    }
    if (link.kind !== "doc") return;
    // 先写 hash（刷新可停留），再置状态；hash 未变化时浏览器不派发 hashchange，故两者都做。
    window.location.hash = buildGuideHash(active.id, link.target);
    setDocPath(link.target);
  }, [active.id, onGoSection, scrollToStep]);

  const closeDoc = useCallback(() => {
    setDocPath(null);
    window.location.hash = buildGuideHash(active.id);
  }, [active.id]);

  // 文档正文里的相对 md 链接：接进阅读态（GitHub 相对链接同款语义），外链/锚点走浏览器默认。
  const onDocClick = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const anchor = (e.target as HTMLElement | null)?.closest?.("a");
    if (!anchor) return;
    const href = anchor.getAttribute("href") || "";
    const target = resolveDocLink(doc?.path || docPath || "", href);
    if (!target) return;
    e.preventDefault();
    window.location.hash = buildGuideHash(active.id, target);
    setDocPath(target);
  }, [active.id, doc?.path, docPath]);

  if (docPath) {
    return (
      <PageShell title={doc?.title || "教学文档"} back={closeDoc}>
        <p className="guide-doc-path">{docPath}</p>
        {docLoading && <StateHint kind="loading">正在读取文档…</StateHint>}
        {docError && (
          <div style={{ textAlign: "center" }}>
            <StateHint kind="error">文档读取失败：{docError}</StateHint>
            <button className="back-btn" onClick={() => setDocAttempt((n) => n + 1)}>重试</button>
          </div>
        )}
        {doc && !docLoading && (
          <div
            ref={docBodyRef}
            className="guide-doc"
            onClick={onDocClick}
            dangerouslySetInnerHTML={{ __html: renderMarkdown(stripLeadingDocTitle(doc.content)) }}
          />
        )}
      </PageShell>
    );
  }

  return (
    <PageShell title="新人引导">
      <SectionCard section={active} onActivate={activateLink} />
    </PageShell>
  );
}
