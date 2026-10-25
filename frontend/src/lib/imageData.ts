/**
 * 本地代理图 → dataURI（2026-10-18 从 RepoGallery 抽出共用）。
 *
 * 为什么要转换：资产/素材的 url 是后端本地代理（根相对 `/api/comfyui/...`，只有本机能取），
 * 外部视觉模型访问不到这个地址 ⇒ 必须前端先取回字节、以 dataURI 喂给模型。
 */
export async function imageDataUri(url: string): Promise<string> {
  if (/^data:/i.test(url)) return url;
  const blob = await (await fetch(url)).blob();
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("读图失败"));
    reader.readAsDataURL(blob);
  });
}
