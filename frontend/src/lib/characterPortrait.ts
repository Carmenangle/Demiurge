export interface CharacterPortrait {
  name: string;
  avatar?: string;
  expressions: Record<string, string>;
}

const EMOTIONS: { text: RegExp; names: RegExp; weight?: number }[] = [
  { text: /愤怒|生气|恼怒|暴怒|怒视|咬牙|angry|furious/i, names: /愤怒|生气|angry|furious/i },
  { text: /开心|喜悦|微笑|大笑|高兴|happy|smile|joy/i, names: /开心|微笑|happy|smile|joy/i },
  { text: /悲伤|难过|哭泣|泪|sad|cry/i, names: /悲伤|哭泣|sad|cry/i },
  { text: /惊讶|震惊|错愕|surprised|shock/i, names: /惊讶|震惊|surprised|shock/i },
  { text: /害怕|恐惧|惊恐|fear|scared/i, names: /害怕|恐惧|fear|scared/i },
  { text: /害羞|脸红|羞涩|shy|blush/i, names: /害羞|脸红|shy|blush/i },
  { text: /嫌弃|厌恶|鄙夷|轻蔑|不屑|皱眉|不满|disgust|contempt|disdain/i,
    names: /嫌弃|厌恶|鄙夷|轻蔑|不屑|disgust|contempt|disdain/i, weight: 45 },
  { text: /尴尬|窘迫|无奈|苦笑|勉强笑|awkward|embarrassed/i,
    names: /尴尬|窘迫|无奈|苦笑|awkward|embarrassed/i, weight: 50 },
  { text: /受伤|伤口|流血|疼痛|痛苦|虚弱|疲惫|injured|hurt|pain|weak|tired/i,
    names: /受伤|伤口|疼痛|痛苦|虚弱|疲惫|injured|hurt|pain|weak|tired/i, weight: 50 },
  { text: /平静|淡然|冷静|面无表情|neutral|calm/i,
    names: /平静|淡然|冷静|无表情|neutral|calm/i },
  { text: /严肃|认真|凝重|警惕|serious|stern|alert/i,
    names: /严肃|认真|凝重|警惕|serious|stern|alert/i },
  { text: /困惑|疑惑|茫然|不解|confused|puzzled/i,
    names: /困惑|疑惑|茫然|不解|confused|puzzled/i },
  { text: /得意|狡黠|坏笑|戏谑|smug|sly|grin/i,
    names: /得意|狡黠|坏笑|戏谑|smug|sly|grin/i },
  { text: /宠溺|深情|爱意|温柔|affectionate|loving|tender/i,
    names: /宠溺|深情|爱意|温柔|affectionate|loving|tender/i },
];

const SPEECH_CUE = /^(?:[^。！？\n]{0,28})(?:说道|说着|问道|问|回答|答道|喊道|喊|低语|开口|嘟囔|呢喃|冷哼)[：:，,]?[“"'「『‘]?/i;

function actorContext(text: string, cardNames: readonly string[], openingCardName: string) {
  const raw: { name: string; index: number; end: number; speech: boolean }[] = [];
  for (const name of cardNames) {
    let from = 0;
    while (from < text.length) {
      const index = text.indexOf(name, from);
      if (index < 0) break;
      const after = text.slice(index + name.length, index + name.length + 40);
      raw.push({
        name, index, end: index + name.length,
        speech: /^\s*[：:][“"'「『‘]?/.test(after) || SPEECH_CUE.test(after),
      });
      from = index + name.length;
    }
  }
  const candidates: typeof raw = [];
  for (const candidate of raw.sort((a, b) => (b.end - b.index) - (a.end - a.index) || a.index - b.index)) {
    if (candidates.some((other) => candidate.index < other.end && other.index < candidate.end)) continue;
    candidates.push(candidate);
  }
  const pool = candidates.some((item) => item.speech)
    ? candidates.filter((item) => item.speech)
    : candidates;
  const actor = pool.sort((a, b) => b.index - a.index)[0];
  const name = actor?.name || openingCardName || cardNames[0] || "";
  const start = actor ? Math.max(0, actor.index - 24) : Math.max(0, text.length - 320);
  return { name, text: text.slice(start, start + 360) };
}

function cjkBigrams(value: string): Set<string> {
  const out = new Set<string>();
  for (const span of value.toLowerCase().match(/[\u3400-\u9fff]+|[a-z0-9]+/g) || []) {
    if (/^[a-z0-9]+$/.test(span)) out.add(span);
    else if (span.length === 1) out.add(span);
    else for (let index = 0; index < span.length - 1; index += 1) out.add(span.slice(index, index + 2));
  }
  return out;
}

function expressionScore(expression: string, context: string): number {
  const label = expression.toLowerCase();
  const content = context.toLowerCase();
  let score = content.includes(label) ? 120 : 0;
  const labelTokens = cjkBigrams(label);
  const contentTokens = cjkBigrams(content);
  for (const token of labelTokens) if (contentTokens.has(token)) score += 8;
  for (const emotion of EMOTIONS) {
    if (emotion.text.test(content) && emotion.names.test(label)) score += emotion.weight || 40;
  }
  return score;
}

function expressionContext(text: string, actorName: string): string {
  const actorIndex = text.lastIndexOf(actorName);
  if (actorIndex < 0) return text;
  const before = text.slice(0, actorIndex);
  const boundary = Math.max(
    before.lastIndexOf("，"), before.lastIndexOf(","), before.lastIndexOf("。"),
    before.lastIndexOf("！"), before.lastIndexOf("？"), before.lastIndexOf("\n"),
  );
  const tail = text.slice(actorIndex);
  const endMatch = /[。！？!?；;\n]/.exec(tail);
  const end = endMatch ? actorIndex + endMatch.index + 1 : Math.min(text.length, actorIndex + 320);
  return text.slice(boundary + 1, end);
}

export function resolveCharacterPortrait(
  text: string,
  cardNames: readonly string[],
  openingCardName: string,
  portraits: Record<string, CharacterPortrait>,
  priorText = "",
): { name: string; url: string } | null {
  const actor = actorContext(`${priorText}\n${text}`, cardNames, openingCardName);
  const name = actor.name;
  const portrait = portraits[name];
  if (!portrait) return null;
  const currentExpressionContext = text.trim() ? expressionContext(text, name) : actor.text;
  const ranked = Object.entries(portrait.expressions)
    .map(([expression, url], order) => ({
      expression, url, order, score: expressionScore(expression, currentExpressionContext),
    }))
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score || a.order - b.order);
  if (ranked[0]) return { name, url: ranked[0].url };
  return portrait.avatar ? { name, url: portrait.avatar } : null;
}
