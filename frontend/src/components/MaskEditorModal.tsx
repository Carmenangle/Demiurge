import { useCallback, useEffect, useRef, useState } from "react";
import {
  Brush,
  Check,
  Eraser,
  LassoSelect,
  PaintBucket,
  Scan,
  WandSparkles,
  X,
} from "lucide-react";
import {
  editMaskRgba,
  fillMask,
  hexToRgb,
  magicWandSelection,
  paintMaskLine,
  polygonSelection,
  type MaskBounds,
  type MaskPoint,
} from "../lib/maskEditor";

type MaskTool = "brush" | "eraser" | "lasso" | "wand" | "bucket";
type ImageSource = CanvasImageSource & { width: number; height: number; close?: () => void };

interface Transform {
  scale: number;
  offsetX: number;
  offsetY: number;
}

interface PointerAction {
  kind: "paint" | "lasso" | "pan";
  pointerId: number;
  lastClient: MaskPoint;
  lastImage?: MaskPoint;
  points?: MaskPoint[];
}

export interface MaskEditorResult {
  image: string;
  mask: string;
  preview: string;
}

interface Props {
  imageUrl: string;
  onCancel: () => void;
  onComplete: (result: MaskEditorResult) => void;
}

const TOOLS: { id: MaskTool; label: string; icon: typeof Brush }[] = [
  { id: "brush", label: "画笔", icon: Brush },
  { id: "eraser", label: "橡皮擦", icon: Eraser },
  { id: "lasso", label: "套索工具", icon: LassoSelect },
  { id: "wand", label: "魔棒工具", icon: WandSparkles },
  { id: "bucket", label: "油桶", icon: PaintBucket },
];

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

async function loadImage(url: string): Promise<ImageSource> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`图片加载失败（${response.status}）`);
  const blob = await response.blob();
  if (typeof createImageBitmap === "function") return createImageBitmap(blob) as Promise<ImageSource>;

  const objectUrl = URL.createObjectURL(blob);
  try {
    const image = new Image();
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error("图片解码失败"));
      image.src = objectUrl;
    });
    return image as ImageSource;
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

function fullBounds(width: number, height: number): MaskBounds {
  return { left: 0, top: 0, right: width, bottom: height };
}

export function MaskEditorModal({ imageUrl, onCancel, onComplete }: Props) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const sourceRef = useRef<ImageSource | null>(null);
  const sourcePixelsRef = useRef<ImageData | null>(null);
  const maskRef = useRef<Uint8Array | null>(null);
  const selectionRef = useRef<Uint8Array | null>(null);
  const maskCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const selectionCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const transformRef = useRef<Transform>({ scale: 1, offsetX: 0, offsetY: 0 });
  const actionRef = useRef<PointerAction | null>(null);
  const renderRef = useRef<() => void>(() => {});
  const colorRef = useRef("#22c55e");
  const toolRef = useRef<MaskTool>("brush");
  const brushSizeRef = useRef(48);

  const [tool, setTool] = useState<MaskTool>("brush");
  const [color, setColor] = useState("#22c55e");
  const [brushSize, setBrushSize] = useState(48);
  const [imageSize, setImageSize] = useState({ width: 0, height: 0 });
  const [selectionActive, setSelectionActive] = useState(false);
  const [zoom, setZoom] = useState(100);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  toolRef.current = tool;
  colorRef.current = color;
  brushSizeRef.current = brushSize;

  const imagePoint = useCallback((clientX: number, clientY: number, shouldClamp = false): MaskPoint | null => {
    const canvas = canvasRef.current;
    const source = sourceRef.current;
    if (!canvas || !source) return null;
    const rect = canvas.getBoundingClientRect();
    const transform = transformRef.current;
    let x = (clientX - rect.left - transform.offsetX) / transform.scale;
    let y = (clientY - rect.top - transform.offsetY) / transform.scale;
    if (shouldClamp) {
      x = clamp(x, 0, source.width);
      y = clamp(y, 0, source.height);
    } else if (x < 0 || y < 0 || x >= source.width || y >= source.height) {
      return null;
    }
    return { x, y };
  }, []);

  const updateMaskCanvas = useCallback((bounds: MaskBounds) => {
    const mask = maskRef.current;
    const overlay = maskCanvasRef.current;
    if (!mask || !overlay) return;
    const left = clamp(Math.floor(bounds.left), 0, overlay.width);
    const top = clamp(Math.floor(bounds.top), 0, overlay.height);
    const right = clamp(Math.ceil(bounds.right), left, overlay.width);
    const bottom = clamp(Math.ceil(bounds.bottom), top, overlay.height);
    if (right <= left || bottom <= top) return;
    const [red, green, blue] = hexToRgb(colorRef.current);
    const pixels = new ImageData(right - left, bottom - top);
    for (let y = top; y < bottom; y += 1) {
      for (let x = left; x < right; x += 1) {
        const sourceIndex = y * overlay.width + x;
        const targetIndex = ((y - top) * (right - left) + x - left) * 4;
        pixels.data[targetIndex] = red;
        pixels.data[targetIndex + 1] = green;
        pixels.data[targetIndex + 2] = blue;
        pixels.data[targetIndex + 3] = mask[sourceIndex];
      }
    }
    overlay.getContext("2d")?.putImageData(pixels, left, top);
  }, []);

  const updateSelectionCanvas = useCallback(() => {
    const selection = selectionRef.current;
    const overlay = selectionCanvasRef.current;
    if (!overlay) return;
    const context = overlay.getContext("2d");
    if (!context) return;
    context.clearRect(0, 0, overlay.width, overlay.height);
    if (!selection) return;
    const pixels = context.createImageData(overlay.width, overlay.height);
    const width = overlay.width;
    const height = overlay.height;
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        const index = y * width + x;
        if (!selection[index]) continue;
        const boundary = x === 0 || y === 0 || x === width - 1 || y === height - 1
          || !selection[index - 1] || !selection[index + 1]
          || !selection[index - width] || !selection[index + width];
        if (!boundary) continue;
        const offset = index * 4;
        const light = (x + y) % 8 < 4;
        pixels.data[offset] = light ? 255 : 20;
        pixels.data[offset + 1] = light ? 255 : 20;
        pixels.data[offset + 2] = light ? 255 : 20;
        pixels.data[offset + 3] = 255;
      }
    }
    context.putImageData(pixels, 0, 0);
  }, []);

  const render = useCallback(() => {
    const canvas = canvasRef.current;
    const source = sourceRef.current;
    if (!canvas || !source) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    const pixelWidth = Math.max(1, Math.round(width * ratio));
    const pixelHeight = Math.max(1, Math.round(height * ratio));
    if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
      canvas.width = pixelWidth;
      canvas.height = pixelHeight;
    }
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    const { scale, offsetX, offsetY } = transformRef.current;
    context.imageSmoothingEnabled = true;
    context.drawImage(source, offsetX, offsetY, source.width * scale, source.height * scale);
    if (maskCanvasRef.current) {
      context.globalAlpha = 0.52;
      context.drawImage(maskCanvasRef.current, offsetX, offsetY, source.width * scale, source.height * scale);
      context.globalAlpha = 1;
    }
    if (selectionCanvasRef.current) {
      context.drawImage(selectionCanvasRef.current, offsetX, offsetY, source.width * scale, source.height * scale);
    }
    const points = actionRef.current?.kind === "lasso" ? actionRef.current.points : null;
    if (points && points.length > 1) {
      context.beginPath();
      context.moveTo(offsetX + points[0].x * scale, offsetY + points[0].y * scale);
      for (let index = 1; index < points.length; index += 1) {
        context.lineTo(offsetX + points[index].x * scale, offsetY + points[index].y * scale);
      }
      context.setLineDash([6, 5]);
      context.lineWidth = 1.5;
      context.strokeStyle = "#ffffff";
      context.stroke();
      context.setLineDash([]);
    }
  }, []);
  renderRef.current = render;

  const fitImage = useCallback(() => {
    const stage = stageRef.current;
    const source = sourceRef.current;
    if (!stage || !source) return;
    const scale = Math.min(stage.clientWidth / source.width, stage.clientHeight / source.height) * 0.94;
    transformRef.current = {
      scale,
      offsetX: (stage.clientWidth - source.width * scale) / 2,
      offsetY: (stage.clientHeight - source.height * scale) / 2,
    };
    setZoom(Math.round(scale * 100));
    renderRef.current();
  }, []);

  useEffect(() => {
    let disposed = false;
    sourcePixelsRef.current = null;
    setLoading(true);
    setError("");
    loadImage(imageUrl).then((source) => {
      if (disposed) {
        source.close?.();
        return;
      }
      sourceRef.current = source;
      const maskCanvas = document.createElement("canvas");
      maskCanvas.width = source.width;
      maskCanvas.height = source.height;
      maskCanvasRef.current = maskCanvas;
      const selectionCanvas = document.createElement("canvas");
      selectionCanvas.width = source.width;
      selectionCanvas.height = source.height;
      selectionCanvasRef.current = selectionCanvas;
      maskRef.current = new Uint8Array(source.width * source.height);
      selectionRef.current = null;
      setImageSize({ width: source.width, height: source.height });
      setLoading(false);
      requestAnimationFrame(fitImage);
    }).catch((reason: unknown) => {
      if (!disposed) {
        setError(reason instanceof Error ? reason.message : "图片加载失败");
        setLoading(false);
      }
    });
    return () => {
      disposed = true;
      sourceRef.current?.close?.();
      sourceRef.current = null;
    };
  }, [fitImage, imageUrl]);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const observer = new ResizeObserver(() => renderRef.current());
    observer.observe(stage);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onCancel]);

  useEffect(() => {
    if (!imageSize.width) return;
    updateMaskCanvas(fullBounds(imageSize.width, imageSize.height));
    renderRef.current();
  }, [color, imageSize, updateMaskCanvas]);

  const applySelection = (selection: Uint8Array | null) => {
    const activeSelection = selection?.some((value) => value !== 0) ? selection : null;
    selectionRef.current = activeSelection;
    setSelectionActive(!!activeSelection);
    updateSelectionCanvas();
    renderRef.current();
  };

  const sourcePixels = () => {
    const source = sourceRef.current;
    if (!source) return null;
    if (sourcePixelsRef.current) return sourcePixelsRef.current;
    const canvas = document.createElement("canvas");
    canvas.width = source.width;
    canvas.height = source.height;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) throw new Error("浏览器无法读取图像像素");
    context.drawImage(source, 0, 0);
    sourcePixelsRef.current = context.getImageData(0, 0, source.width, source.height);
    return sourcePixelsRef.current;
  };

  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!sourceRef.current || !maskRef.current) return;
    if (event.button === 1 || event.button === 2) {
      event.preventDefault();
      event.currentTarget.setPointerCapture(event.pointerId);
      actionRef.current = {
        kind: "pan",
        pointerId: event.pointerId,
        lastClient: { x: event.clientX, y: event.clientY },
      };
      return;
    }
    if (event.button !== 0) return;
    const point = imagePoint(event.clientX, event.clientY);
    if (!point) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    const activeTool = toolRef.current;
    if (activeTool === "brush" || activeTool === "eraser") {
      actionRef.current = {
        kind: "paint",
        pointerId: event.pointerId,
        lastClient: { x: event.clientX, y: event.clientY },
        lastImage: point,
      };
      const bounds = paintMaskLine(
        maskRef.current,
        sourceRef.current.width,
        sourceRef.current.height,
        point,
        point,
        brushSizeRef.current / 2,
        activeTool === "brush" ? 255 : 0,
        selectionRef.current,
      );
      if (bounds) updateMaskCanvas(bounds);
      renderRef.current();
      return;
    }
    if (activeTool === "lasso") {
      actionRef.current = {
        kind: "lasso",
        pointerId: event.pointerId,
        lastClient: { x: event.clientX, y: event.clientY },
        points: [point],
      };
      return;
    }
    if (activeTool === "wand") {
      try {
        const pixels = sourcePixels();
        if (pixels) {
          applySelection(magicWandSelection(
            pixels.data,
            sourceRef.current.width,
            sourceRef.current.height,
            point.x,
            point.y,
            32,
          ));
        }
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "魔棒选区创建失败");
      }
      return;
    }
    fillMask(maskRef.current, 255, selectionRef.current);
    updateMaskCanvas(fullBounds(sourceRef.current.width, sourceRef.current.height));
    renderRef.current();
  };

  const onPointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const action = actionRef.current;
    const source = sourceRef.current;
    const mask = maskRef.current;
    if (!action || action.pointerId !== event.pointerId || !source || !mask) return;
    if (action.kind === "pan") {
      const transform = transformRef.current;
      transform.offsetX += event.clientX - action.lastClient.x;
      transform.offsetY += event.clientY - action.lastClient.y;
      action.lastClient = { x: event.clientX, y: event.clientY };
      renderRef.current();
      return;
    }
    const point = imagePoint(event.clientX, event.clientY, true);
    if (!point) return;
    if (action.kind === "paint" && action.lastImage) {
      const bounds = paintMaskLine(
        mask,
        source.width,
        source.height,
        action.lastImage,
        point,
        brushSizeRef.current / 2,
        toolRef.current === "eraser" ? 0 : 255,
        selectionRef.current,
      );
      action.lastImage = point;
      if (bounds) updateMaskCanvas(bounds);
      renderRef.current();
      return;
    }
    if (action.kind === "lasso" && action.points) {
      const previous = action.points[action.points.length - 1];
      if (Math.hypot(point.x - previous.x, point.y - previous.y) >= 2 / transformRef.current.scale) {
        action.points.push(point);
        renderRef.current();
      }
    }
  };

  const finishPointer = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const action = actionRef.current;
    if (!action || action.pointerId !== event.pointerId) return;
    if (action.kind === "lasso" && action.points && sourceRef.current) {
      applySelection(polygonSelection(sourceRef.current.width, sourceRef.current.height, action.points));
    }
    actionRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    renderRef.current();
  };

  const onWheel = (event: React.WheelEvent<HTMLCanvasElement>) => {
    if (!sourceRef.current) return;
    event.preventDefault();
    const rect = event.currentTarget.getBoundingClientRect();
    const mouseX = event.clientX - rect.left;
    const mouseY = event.clientY - rect.top;
    const current = transformRef.current;
    const imageX = (mouseX - current.offsetX) / current.scale;
    const imageY = (mouseY - current.offsetY) / current.scale;
    const nextScale = clamp(current.scale * Math.exp(-event.deltaY * 0.0015), 0.05, 8);
    current.offsetX = mouseX - imageX * nextScale;
    current.offsetY = mouseY - imageY * nextScale;
    current.scale = nextScale;
    setZoom(Math.round(nextScale * 100));
    renderRef.current();
  };

  const finish = () => {
    const source = sourceRef.current;
    const overlay = maskCanvasRef.current;
    const mask = maskRef.current;
    if (!source || !overlay || !mask) return;
    try {
      const preview = document.createElement("canvas");
      preview.width = source.width;
      preview.height = source.height;
      const previewContext = preview.getContext("2d");
      if (!previewContext) throw new Error("浏览器无法导出图像");
      previewContext.drawImage(source, 0, 0);
      previewContext.drawImage(overlay, 0, 0);

      const maskOutput = document.createElement("canvas");
      maskOutput.width = source.width;
      maskOutput.height = source.height;
      const maskContext = maskOutput.getContext("2d");
      if (!maskContext) throw new Error("浏览器无法导出蒙版");
      const maskPixels = maskContext.createImageData(source.width, source.height);
      maskPixels.data.set(editMaskRgba(mask));
      maskContext.putImageData(maskPixels, 0, 0);
      onComplete({
        image: imageUrl,
        mask: maskOutput.toDataURL("image/png"),
        preview: preview.toDataURL("image/png"),
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "蒙版图导出失败");
    }
  };

  return (
    <div className="modal-mask mask-editor-mask" role="presentation">
      <section className="mask-editor-modal" role="dialog" aria-modal="true" aria-label="蒙化修改">
        <header className="mask-editor-toolbar">
          <div className="mask-editor-tools" role="toolbar" aria-label="蒙版工具">
            {TOOLS.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                className={`mask-editor-tool${tool === id ? " active" : ""}`}
                type="button"
                title={label}
                aria-label={label}
                aria-pressed={tool === id}
                onClick={() => setTool(id)}
              >
                <Icon size={19} />
              </button>
            ))}
            <span className="mask-editor-divider" />
            <button
              className="mask-editor-tool"
              type="button"
              title="取消选区"
              aria-label="取消选区"
              disabled={!selectionActive}
              onClick={() => applySelection(null)}
            >
              <Scan size={19} />
            </button>
            <button className="mask-editor-tool" type="button" title="适合窗口" aria-label="适合窗口" onClick={fitImage}>
              <Check size={19} />
            </button>
          </div>
          <span className="mask-editor-zoom">{zoom}%</span>
          <button className="mask-editor-tool" type="button" title="取消" aria-label="取消" onClick={onCancel}>
            <X size={20} />
          </button>
        </header>

        <div className="mask-editor-body">
          <div ref={stageRef} className="mask-editor-stage">
            {loading && <div className="mask-editor-status">正在加载图片…</div>}
            {error && <div className="mask-editor-status error" role="alert">{error}</div>}
            <canvas
              ref={canvasRef}
              className={`mask-editor-canvas tool-${tool}`}
              aria-label="蒙版绘制画布"
              onContextMenu={(event) => event.preventDefault()}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={finishPointer}
              onPointerCancel={finishPointer}
              onWheel={onWheel}
            />
          </div>

          <aside className="mask-editor-side">
            <label className="mask-editor-field">
              <span>蒙版颜色</span>
              <span className="mask-editor-color-row">
                <input type="color" value={color} onChange={(event) => setColor(event.target.value)} />
                <output>{color.toUpperCase()}</output>
              </span>
            </label>
            <label className="mask-editor-field">
              <span>画笔尺寸</span>
              <output>{brushSize}px</output>
              <input
                type="range"
                min="1"
                max="300"
                value={brushSize}
                onChange={(event) => setBrushSize(Number(event.target.value))}
              />
            </label>
            <div className="mask-editor-meta">{imageSize.width} × {imageSize.height}</div>
          </aside>
        </div>

        <footer className="mask-editor-footer">
          <button className="btn" type="button" onClick={onCancel}>取消</button>
          <button className="btn primary" type="button" disabled={loading || !!error} onClick={finish}>
            <Check size={16} /> 绘制完毕
          </button>
        </footer>
      </section>
    </div>
  );
}
