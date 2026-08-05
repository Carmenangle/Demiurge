import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  AssistantMessage,
  PromptApprovalCard,
  RouteChoiceCard,
  UserMessage,
  userMessageRichContent,
  userMessagePlainText,
} from "./ChatMessages";

describe("PromptApprovalCard", () => {
  it("isolates the prompt and exposes the three requested actions", () => {
    const html = renderToStaticMarkup(
      <PromptApprovalCard
        approval={{
          id: "approval-1",
          messageId: "message-1",
          kind: "image",
          originalPrompt: "原稿",
          prompt: "单独隔离的候选提示词",
          status: "pending",
        }}
        onAction={async () => {}}
      />,
    );

    expect(html).toContain("prompt-approval-code");
    expect(html).toContain("单独隔离的候选提示词");
    expect(html).toContain("确认提交");
    expect(html).toContain("更改");
    expect(html).toContain("取消");
    expect(html).not.toContain("确认无误请回复");
  });

  it("failed generation also exposes the same three actions", () => {
    const html = renderToStaticMarkup(
      <PromptApprovalCard
        approval={{
          id: "approval-failed",
          messageId: "message-failed",
          kind: "image",
          originalPrompt: "原稿",
          prompt: "失败的提示词",
          status: "failed",
          stage: "rewrite_consent",
        }}
        onAction={async () => {}}
      />,
    );

    expect(html).toContain("确认提交");
    expect(html).toContain("更改");
    expect(html).toContain("取消");
  });

  it("labels an unknown upstream delivery as a manual retry", () => {
    const html = renderToStaticMarkup(
      <PromptApprovalCard
        approval={{
          id: "approval-unknown",
          messageId: "message-unknown",
          kind: "img2img",
          originalPrompt: "原稿",
          prompt: "已提交但状态未知的提示词",
          status: "failed",
          stage: "delivery_unknown",
        }}
        onAction={async () => {}}
      />,
    );

    expect(html).toContain("上游交付状态未知");
    expect(html).toContain("确认重新提交");
    expect(html).toContain("更改");
    expect(html).toContain("取消");
  });

  it("labels a connection failure as not sent", () => {
    const html = renderToStaticMarkup(
      <PromptApprovalCard
        approval={{
          id: "approval-not-sent",
          messageId: "message-not-sent",
          kind: "image",
          originalPrompt: "原稿",
          prompt: "未发送的提示词",
          status: "failed",
          stage: "request_failed",
        }}
        onAction={async () => {}}
      />,
    );

    expect(html).toContain("请求未发送到上游");
    expect(html).toContain("确认重新提交");
  });
});

describe("RouteChoiceCard", () => {
  it("renders only the candidates supplied by the supervisor", () => {
    const html = renderToStaticMarkup(
      <RouteChoiceCard
        choice={{
          id: "route-1",
          messageId: "message-1",
          userMessageId: "user-1",
          status: "pending",
          options: [
            { route: "answer", label: "继续对话" },
            { route: "img2img", label: "参考图生图" },
            { route: "analyze", label: "反推提示词" },
          ],
        }}
        onSelect={async () => {}}
      />,
    );

    expect(html).toContain("继续对话");
    expect(html).toContain("参考图生图");
    expect(html).toContain("反推提示词");
    expect(html).not.toContain("生成视频");
    expect(html).not.toContain("调用工具");
  });

  it("keeps the selected route in the pressed visual state", () => {
    const html = renderToStaticMarkup(
      <RouteChoiceCard
        choice={{
          id: "route-selected",
          messageId: "message-selected",
          userMessageId: "user-selected",
          status: "selected",
          selectedRoute: "img2img",
          options: [
            { route: "answer", label: "继续对话" },
            { route: "img2img", label: "参考图生图" },
          ],
        }}
        onSelect={async () => {}}
      />,
    );

    expect(html).toContain('class="btn primary is-selected"');
  });
});

describe("generated image actions", () => {
  const requiredProps = {
    onSendImage: () => {},
    onMaskImage: () => {},
  };

  it("replaces copy link with an enabled regenerate action for bound results", () => {
    const html = renderToStaticMarkup(
      <AssistantMessage
        {...requiredProps}
        msg={{
          id: "image-1",
          role: "assistant",
          text: "",
          image: "result.png",
          regeneration: {
            kind: "ai-image",
            prompt: "固定提示词",
            images: [],
            size: "1024x1024",
            quality: "high",
            model: { baseUrl: "https://images.example", modelName: "image-v1" },
          },
        }}
        onRegenerate={() => {}}
      />,
    );

    expect(html).toContain("重新生图");
    expect(html).toContain("蒙化修改");
    expect(html).not.toContain("识图微调");
    expect(html).not.toContain("复制链接");
    expect(html).not.toContain("disabled=\"\"");
  });

  it("disables regeneration for legacy results without an exact snapshot", () => {
    const html = renderToStaticMarkup(
      <AssistantMessage
        {...requiredProps}
        msg={{ id: "legacy-image", role: "assistant", text: "", image: "legacy.png" }}
        onRegenerate={() => {}}
      />,
    );

    expect(html).toContain("重新生图");
    expect(html).toContain("disabled=\"\"");
    expect(html).toContain("旧结果未保存完整生成参数");
  });

});

describe("user message actions", () => {
  it("gives short and long messages the same collapsed action row", () => {
    for (const text of ["短文本", "很长的用户消息".repeat(80)]) {
      const html = renderToStaticMarkup(
        <UserMessage msg={{ id: text, role: "user", text }} />,
      );

      expect(html).toContain("user-message-text");
      expect(html).toContain("user-message-actions");
      expect(html).toContain('aria-label="展开操作"');
      expect(html).not.toContain("复制纯文本");
    }
  });

  it("copies only text from a mixed image and text message", () => {
    const html = renderToStaticMarkup(
      <UserMessage
        msg={{
          id: "mixed",
          role: "user",
          text: "",
          parts: [
            { type: "image", url: "data:image/png;base64,SHOULD_NOT_COPY" },
            { type: "text", text: "第一段" },
            { type: "text", text: "第二段" },
          ],
        }}
      />,
    );

    expect(html).toContain('aria-label="展开操作"');
    expect(userMessagePlainText({
      id: "mixed",
      role: "user",
      text: "",
      parts: [
        { type: "image", url: "data:image/png;base64,SHOULD_NOT_COPY" },
        { type: "text", text: "第一段" },
        { type: "text", text: "第二段" },
      ],
    })).toBe("第一段第二段");
  });

  it("restores text and every image when editing a historical user message", () => {
    const message = {
      id: "editable",
      role: "user" as const,
      text: "完整要求",
      parts: [
        { type: "image" as const, url: "image-1.png" },
        { type: "image" as const, url: "image-2.png" },
        { type: "text" as const, text: "完整要求" },
      ],
    };
    const html = renderToStaticMarkup(<UserMessage msg={message} onEdit={() => {}} />);

    expect(html).toContain('aria-label="展开操作"');
    expect(userMessageRichContent(message)).toEqual({
      text: "完整要求",
      images: ["image-1.png", "image-2.png"],
      parts: [
        { type: "image", url: "image-1.png" },
        { type: "image", url: "image-2.png" },
        { type: "text", text: "完整要求" },
      ],
    });
  });

  it("restores a masked image as one bound editor attachment", () => {
    const message = {
      id: "masked-editable",
      role: "user" as const,
      text: "只修改选区",
      parts: [
        {
          type: "masked-image" as const,
          url: "preview.png",
          image: "original.png",
          mask: "mask.png",
        },
        { type: "text" as const, text: "只修改选区" },
      ],
    };

    expect(userMessageRichContent(message)).toMatchObject({
      text: "只修改选区",
      images: [],
      maskedImage: {
        image: "original.png",
        mask: "mask.png",
        preview: "preview.png",
      },
    });
  });
});
