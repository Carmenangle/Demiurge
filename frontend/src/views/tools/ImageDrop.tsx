import { useRef, useState } from "react";
import { Upload } from "lucide-react";
import { fileToDataUri } from "../../api/tools";

// 三个工具共用的选图口：点选 + 拖入 + 粘贴。拿到的一律是 data URI。
export function ImageDrop({
  accept, hint, onPick,
}: {
  accept: string;
  hint: string;
  onPick: (dataUri: string, name: string) => void;
}) {
  const [over, setOver] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const take = async (file: File | undefined | null) => {
    if (!file) return;
    onPick(await fileToDataUri(file), file.name);
  };

  return (
    <div
      className={`tool-drop${over ? " tool-drop-over" : ""}`}
      onClick={() => inputRef.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        void take(e.dataTransfer.files?.[0]);
      }}
      onPaste={(e) => {
        const f = Array.from(e.clipboardData.files)[0];
        if (f) void take(f);
      }}
      tabIndex={0}
      role="button"
      aria-label={hint}
    >
      <Upload size={22} />
      <span>{hint}</span>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        style={{ display: "none" }}
        onChange={(e) => { void take(e.target.files?.[0]); e.target.value = ""; }}
      />
    </div>
  );
}
