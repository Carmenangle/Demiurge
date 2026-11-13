import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  CiArmLedger,
} from "./NarrativeCiPanel";
import {
  NARRATIVE_CI_ARM_LABELS,
  NARRATIVE_CI_ARM_ORDER,
  NARRATIVE_CI_ARM_REASONS,
  NARRATIVE_CI_CODES,
} from "../../api/narrative";
import type { NarrativeCiArmsReport } from "../../api/narrative";

// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { existsSync, readFileSync } from "node:fs";

// 本仓未装 @types/node（应用代码不碰 Node API），但本用例要按 cwd 找后端源码 ⇒ 就地声明最小签名。
declare const process: { cwd(): string };

/** 跨语言契约：台账的臂名与原因码是后端字符串，前端只按白名单渲染 ⇒ 后端改名前端静默不显示。 */
function readBackend(rel: string): string {
  // jsdom 环境下 `import.meta.url` 不是 file: URL（`new URL(...)` 会抛「must be of scheme file」），
  // 故按 vitest 的 cwd 往上试两个位置（frontend/ 或仓库根）。
  const candidates = [`${process.cwd()}/../${rel}`, `${process.cwd()}/${rel}`];
  const hit = candidates.find((p) => existsSync(p));
  if (!hit) throw new Error(`找不到后端源码 ${rel}（试过：${candidates.join(" / ")}）`);
  return readFileSync(hit, "utf-8");
}

const NO_BOOK: NarrativeCiArmsReport = {
  turn: 6,
  arms: {
    world_rule: { participated: false, count: 0, reason: "no_worldbook" },
    term_ratchet_stock: { participated: false, count: 0, reason: "no_worldbook" },
  },
};

const BOOK_WITHOUT_BASE: NarrativeCiArmsReport = {
  turn: 12,
  arms: {
    world_rule: { participated: true, count: 3, reason: "" },
    term_ratchet_stock: { participated: false, count: 0, reason: "no_static_base" },
  },
};

const UNREADABLE: NarrativeCiArmsReport = {
  turn: 1,
  arms: {
    world_rule: { participated: false, count: 0, reason: "snapshot_unreadable" },
    term_ratchet_stock: { participated: false, count: 0, reason: "snapshot_unreadable" },
  },
};

// 清单 #3／#4：世界书缺席 ⇒ 这两条判据整臂不跑。面板必须把「没参与」摆在空列表前面，
// 否则「暂无诊断」会被读成「查过、没问题」——绿是空转出来的。
// 缺陷 4（2026-11-06）新增第三条臂：本轮有没有可核对的 World 裁定，输入不来自世界书。
const BOOK_OK_NO_VERDICT: NarrativeCiArmsReport = {
  turn: 18,
  arms: {
    world_rule: { participated: true, count: 2, reason: "" },
    term_ratchet_stock: { participated: true, count: 4, reason: "" },
    agency_verdict: { participated: false, count: 0, reason: "world_not_consulted" },
  },
};

const VERDICT_NOT_ROLLED: NarrativeCiArmsReport = {
  turn: 19,
  arms: {
    world_rule: { participated: true, count: 2, reason: "" },
    term_ratchet_stock: { participated: true, count: 4, reason: "" },
    agency_verdict: { participated: false, count: 0, reason: "no_rolled_attempt" },
  },
};
describe("诊断面板的判据参与台账", () => {
  it("纯卡作品：两条判据都写明本轮没参与、以及为什么没参与", () => {
    const html = renderToStaticMarkup(<CiArmLedger arms={NO_BOOK} hasItems={false} />);
    expect(html).toContain("第 6 轮判据参与情况");
    expect(html).toContain("世界规则");
    expect(html).toContain("棘轮·设定存量");
    expect((html.match(/本轮未参与/g) ?? []).length).toBe(2);
    expect(html).toContain("这个作品没有世界书，判据没有输入");
    expect(html).toContain("未参与＝没查，不等于查过没问题");
  });

  it("有书但只喂到一条：参与的那臂报条数，没喂到的那臂仍要说清", () => {
    const html = renderToStaticMarkup(<CiArmLedger arms={BOOK_WITHOUT_BASE} hasItems={false} />);
    expect(html).toContain("已评估（输入 3 条）");
    expect(html).toContain("本轮未参与");
    expect(html).toContain("世界书条目里没有静态设定底座");
    expect(html).not.toContain("这个作品没有世界书");
  });

  it("快照读不出来不能报成「没有世界书」", () => {
    const html = renderToStaticMarkup(<CiArmLedger arms={UNREADABLE} hasItems={false} />);
    expect(html).toContain("世界书快照读不出来");
    expect(html).not.toContain("这个作品没有世界书");
  });

  it("第三条臂（World 裁定）没输入时也要单独显示，且不许甩锅世界书", () => {
    const html = renderToStaticMarkup(<CiArmLedger arms={BOOK_OK_NO_VERDICT} hasItems={false} />);
    expect(html).toContain("World 裁定");
    expect((html.match(/本轮未参与/g) ?? []).length).toBe(1);
    expect(html).toContain("已评估（输入 2 条）");
    expect(html).toContain("已评估（输入 4 条）");
    expect(html).toContain("本轮没有可核对的 World 裁定");
    // 这条臂的输入不来自世界书：书是齐的，文案不许把用户引去补书
    expect(html).not.toContain("这个作品没有世界书");
  });

  it("有提案但没掷骰 ≠ 没有提案：两句话要分开", () => {
    const html = renderToStaticMarkup(<CiArmLedger arms={VERDICT_NOT_ROLLED} hasItems={false} />);
    expect(html).toContain("都没掷骰");
    expect(html).not.toContain("World 本轮没有提出任何自主行动");
  });

  it("后端台账的臂名与原因码都在前端登记（改一边就红）", () => {
    const src = readBackend("backend/app/services/narrative_ci.py");
    for (const key of ["world_rule", "term_ratchet_stock", "agency_verdict"]) {
      expect(src).toContain(`"${key}"`);
      expect(NARRATIVE_CI_ARM_ORDER).toContain(key);
    }
    for (const code of ["no_worldbook", "snapshot_unreadable", "empty_worldbook",
      "no_rule_lines", "no_static_base", "world_not_consulted", "no_proposal",
      "no_rolled_attempt"]) {
      expect(src).toContain(`"${code}"`);            // 后端仍在发这个码
      expect(NARRATIVE_CI_ARM_REASONS[code]).toBeTruthy();  // 前端仍有这句话
    }
    // 臂名漏登记 label 时面板会退回显示英文 key ⇒ 三条都必须有中文
    for (const key of NARRATIVE_CI_ARM_ORDER) expect(NARRATIVE_CI_ARM_LABELS[key]).toBeTruthy();
  });

  it("新诊断码 npc_verdict_break 两端同串（后端改名 ⇒ 前端红）", () => {
    const src = readBackend("backend/app/services/narrative_ci.py");
    expect(src).toContain('CODE_NPC_VERDICT_BREAK   = "npc_verdict_break"');
    expect(NARRATIVE_CI_CODES["npc_verdict_break"]).toBeTruthy();
    // 未登记码走 `?? item.code` 退回英文原串（prose_style 那批至今如此）——这是既有降级，
    // 不是「所有码都必须登记」的合同；哪天要收紧，得先把 style_* 一批一起搬进来。
    expect(NARRATIVE_CI_CODES["style_banned_phrase"]).toBeUndefined();
  });

  it("没有台账（老库／从没跑过剧情轮）时不编造判据块，空列表文案回到原句", () => {
    const html = renderToStaticMarkup(<CiArmLedger arms={null} hasItems={false} />);
    expect(html).not.toContain("判据参与情况");
    expect(html.trim()).toBe('<div class="nci-empty">暂无未处置的一致性诊断。</div>');
  });

  it("有未处置诊断时长篇文案让位，但台账照样显示", () => {
    const html = renderToStaticMarkup(<CiArmLedger arms={NO_BOOK} hasItems />);
    expect(html).not.toContain("nci-empty");
    expect(html).toContain("本轮未参与");
  });

  it("后端新增的原因码原样显示，不静默变成空白", () => {
    const html = renderToStaticMarkup(
      <CiArmLedger
        arms={{ turn: 2, arms: { world_rule: { participated: false, count: 0, reason: "brand_new" } } }}
        hasItems={false}
      />,
    );
    expect(html).toContain("brand_new");
  });
});
