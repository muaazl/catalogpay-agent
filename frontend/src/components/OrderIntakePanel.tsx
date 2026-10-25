import { AlertTriangle, Coffee, Croissant, FileAudio, ImageUp, Sparkles, Upload } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";

import type { ParsedOrderPreviewResponse } from "../api/types";
import { ORDER_PRESETS, type OrderPreset } from "../data/presets";
import { Button } from "./ui/Button";
import { Panel } from "./ui/Panel";
import { Pill } from "./ui/Pill";

export const ACCEPTED_MEDIA = [
  "image/png",
  "image/jpeg",
  "image/webp",
  "application/pdf",
  "audio/wav",
  "audio/x-wav",
  "audio/mpeg",
  "audio/mp3",
  "audio/ogg",
  "audio/webm",
];
const MAX_TEXT = 5000;
const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

const PRESET_ICONS: Record<OrderPreset["id"], typeof Coffee> = {
  cafe: Coffee,
  bakery: Croissant,
  stress: AlertTriangle,
};

interface OrderIntakePanelProps {
  text: string;
  busy: boolean;
  locked: boolean;
  parse: ParsedOrderPreviewResponse | null;
  sourceLabel: string | null;
  active?: boolean;
  onTextChange: (text: string) => void;
  onParseText: (text: string, sourceLabel?: string) => void;
  onParseMedia: (file: File) => void;
  onReject?: (message: string) => void;
}

export function OrderIntakePanel({
  text,
  busy,
  locked,
  parse,
  sourceLabel,
  active,
  onTextChange,
  onParseText,
  onParseMedia,
  onReject,
}: OrderIntakePanelProps) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const disabled = busy || locked;

  const loadPreset = (preset: OrderPreset) => {
    onTextChange(preset.text);
    onParseText(preset.text, `Preset · ${preset.title}`); // one click: fill *and* resolve
  };

  const acceptFile = (file: File | undefined) => {
    if (!file) return;
    if (file.size === 0) {
      onReject?.(`"${file.name}" is empty (0 bytes). Choose a real PO image, PDF or voice note.`);
      return;
    }
    if (!ACCEPTED_MEDIA.includes(file.type)) {
      onReject?.(`Unsupported file type "${file.type || "unknown"}". Use PNG/JPG/WEBP/PDF or WAV/MP3/OGG.`);
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      onReject?.("File is larger than the 10 MB upload limit.");
      return;
    }
    onParseMedia(file);
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    if (!disabled) acceptFile(event.dataTransfer.files[0]);
  };

  return (
    <Panel
      step={1}
      title="Drop a messy order"
      subtitle="Text, a photo of a PO, a PDF, or a voice note"
      active={active}
      testId="intake-panel"
      className="h-full"
    >
      <div className="space-y-4">
        <div>
          <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
            <Sparkles className="size-3.5" aria-hidden /> 1-click demo orders
          </div>
          <div className="grid gap-2">
            {ORDER_PRESETS.map((preset) => {
              const Icon = PRESET_ICONS[preset.id];
              return (
                <button
                  key={preset.id}
                  type="button"
                  disabled={disabled}
                  onClick={() => loadPreset(preset)}
                  className="group flex items-center gap-3 rounded-xl border border-slate-200 bg-slate-50/60 px-3 py-2.5 text-left transition hover:border-brand-300 hover:bg-brand-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-white text-brand-600 ring-1 ring-slate-200 group-hover:ring-brand-200">
                    <Icon className="size-4.5" aria-hidden />
                  </span>
                  <span className="min-w-0">
                    <span className="block text-sm font-semibold text-slate-800">{preset.title}</span>
                    <span className="block truncate text-xs text-slate-500">{preset.subtitle}</span>
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        <div>
          <label htmlFor="order-text" className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-500">
            Buyer message
          </label>
          <textarea
            id="order-text"
            value={text}
            disabled={disabled}
            maxLength={MAX_TEXT}
            onChange={(e) => onTextChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) onParseText(text);
            }}
            rows={7}
            placeholder={"e.g. 2x 500g arabica beans, 3 cases oat milk,\nespresso tamper 58mm x1"}
            className="w-full resize-y rounded-xl border border-slate-300 bg-white px-3 py-2.5 font-mono text-[13px] leading-relaxed text-slate-800 shadow-inner placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100 disabled:bg-slate-50"
          />
          <div className="mt-1.5 flex items-center justify-between">
            <span className="text-xs text-slate-400">
              {text.length}/{MAX_TEXT} · Ctrl+Enter to parse
            </span>
            <Button
              onClick={() => onParseText(text)}
              disabled={disabled || !text.trim()}
              loading={busy}
              icon={<Sparkles className="size-4" aria-hidden />}
            >
              Parse & resolve SKUs
            </Button>
          </div>
        </div>

        <div
          role="button"
          tabIndex={0}
          aria-label="Upload a purchase order image, PDF, or voice note"
          aria-disabled={disabled}
          onClick={() => !disabled && fileInput.current?.click()}
          onKeyDown={(e) => {
            if ((e.key === "Enter" || e.key === " ") && !disabled) fileInput.current?.click();
          }}
          onDragOver={(e) => {
            e.preventDefault();
            if (!disabled) setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`flex cursor-pointer flex-col items-center gap-1.5 rounded-xl border-2 border-dashed px-4 py-5 text-center transition ${
            dragging ? "border-brand-500 bg-brand-50" : "border-slate-300 hover:border-brand-300 hover:bg-slate-50"
          } ${disabled ? "pointer-events-none opacity-50" : ""}`}
        >
          <div className="flex items-center gap-2 text-slate-400">
            <ImageUp className="size-5" aria-hidden />
            <FileAudio className="size-5" aria-hidden />
            <Upload className="size-5" aria-hidden />
          </div>
          <p className="text-sm font-medium text-slate-700">Drop a PO photo, PDF or voice note</p>
          <p className="text-xs text-slate-500">PNG · JPG · WEBP · PDF · WAV · MP3 · OGG · up to 10 MB · via Gemini 2.5 Flash</p>
          <input
            ref={fileInput}
            type="file"
            hidden
            data-testid="media-input"
            accept={ACCEPTED_MEDIA.join(",")}
            onChange={(e) => {
              acceptFile(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
        </div>

        <p className="text-center text-xs text-slate-500" data-testid="sample-downloads">
          No PO handy? Try our{" "}
          <a href="/samples/sample_wholesale_po.png" download className="font-medium text-brand-700 hover:underline">
            sample PO image
          </a>{" "}
          or{" "}
          <a href="/samples/sample_order_note.txt" download className="font-medium text-brand-700 hover:underline">
            messy text order
          </a>
          .
        </p>

        {parse && (
          <div className="space-y-2 rounded-xl bg-slate-50 px-3 py-2.5 text-xs text-slate-600" data-testid="parse-summary">
            <div className="flex flex-wrap items-center gap-1.5">
              <Pill tone={parse.parser_mode === "gemini" ? "violet" : "slate"}>
                {parse.parser_mode === "gemini" ? "Gemini 2.5 Flash" : "Deterministic fallback"}
              </Pill>
              <span>
                {parse.extracted_items.length} lines extracted
                {sourceLabel ? ` from ${sourceLabel}` : ""}
              </span>
            </div>
            {parse.customer_note && (
              <p>
                <span className="font-semibold text-slate-700">Buyer note:</span> {parse.customer_note}
              </p>
            )}
            {parse.unrecognized_fragments.length > 0 && (
              <p>
                <span className="font-semibold text-rose-700">Not an item:</span>{" "}
                {parse.unrecognized_fragments.join(" · ")}
              </p>
            )}
          </div>
        )}
      </div>
    </Panel>
  );
}
