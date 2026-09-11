import type { ModelType } from "../../api/models";

// HuggingFace 常用模型预设清单（对齐 ComfyUI 官方推荐）。
// 每项存 HF 直链(resolve) + 落盘类型。用户勾选后批量下载。
// 注：只收录地址稳定、社区公认的核心模型；用户要别的可用「链接下载」tab 手动贴。
export interface HFPreset {
  name: string;
  url: string;        // huggingface.co resolve 直链
  type: ModelType;
}
export interface HFGroup {
  label: string;
  items: HFPreset[];
}

const HF = "https://huggingface.co";

export const HF_PRESETS: HFGroup[] = [
  {
    label: "Base Models（基础大模型）",
    items: [
      { name: "Stable Diffusion 1.5", type: "checkpoint",
        url: `${HF}/stable-diffusion-v1-5/stable-diffusion-v1-5/resolve/main/v1-5-pruned-emaonly.safetensors` },
      { name: "Stable Diffusion XL Base 1.0", type: "checkpoint",
        url: `${HF}/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors` },
      { name: "Stable Diffusion XL Refiner 1.0", type: "checkpoint",
        url: `${HF}/stabilityai/stable-diffusion-xl-refiner-1.0/resolve/main/sd_xl_refiner_1.0.safetensors` },
      { name: "SDXL Turbo", type: "checkpoint",
        url: `${HF}/stabilityai/sdxl-turbo/resolve/main/sd_xl_turbo_1.0_fp16.safetensors` },
    ],
  },
  {
    label: "VAE",
    items: [
      { name: "SDXL VAE (fp16 fix)", type: "vae",
        url: `${HF}/madebyollin/sdxl-vae-fp16-fix/resolve/main/sdxl_vae.safetensors` },
      { name: "SD 1.5 VAE (mse)", type: "vae",
        url: `${HF}/stabilityai/sd-vae-ft-mse-original/resolve/main/vae-ft-mse-840000-ema-pruned.safetensors` },
    ],
  },
  {
    label: "CLIP Vision（IPAdapter 等用）",
    items: [
      { name: "CLIP Vision H (ViT-H)", type: "clip_vision",
        url: `${HF}/h94/IP-Adapter/resolve/main/models/image_encoder/model.safetensors` },
      { name: "CLIP Vision G (ViT-bigG)", type: "clip_vision",
        url: `${HF}/comfyanonymous/clip_vision_g/resolve/main/clip_vision_g.safetensors` },
    ],
  },
  {
    label: "Upscale（放大模型）",
    items: [
      { name: "4x-UltraSharp", type: "upscale",
        url: `${HF}/Kim2091/UltraSharp/resolve/main/4x-UltraSharp.pth` },
      { name: "RealESRGAN x4plus", type: "upscale",
        url: `${HF}/ai-forever/Real-ESRGAN/resolve/main/RealESRGAN_x4.pth` },
    ],
  },
];
