import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { OrderIntakePanel } from "../components/OrderIntakePanel";
import { ORDER_PRESETS } from "../data/presets";
import { sampleParse } from "./fixtures";

function Harness(props: { onParseText?: (t: string, l?: string) => void; onParseMedia?: (f: File) => void; onReject?: (m: string) => void; locked?: boolean }) {
  const [text, setText] = useState("");
  return (
    <OrderIntakePanel
      text={text}
      busy={false}
      locked={props.locked ?? false}
      parse={null}
      sourceLabel={null}
      onTextChange={setText}
      onParseText={props.onParseText ?? (() => {})}
      onParseMedia={props.onParseMedia ?? (() => {})}
      onReject={props.onReject}
    />
  );
}

describe("OrderIntakePanel presets", () => {
  it("ships the three demo presets", () => {
    expect(ORDER_PRESETS.map((p) => p.title)).toEqual([
      "Specialty Café Restock",
      "Artisan Bakery & Tea Wholesale",
      "Edge-Case Stress Test",
    ]);
    const stress = ORDER_PRESETS[2]!.text;
    expect(stress).toMatch(/espreso tampr/); // typo
    expect(stress).toMatch(/half kg/); // shorthand unit
    expect(stress).toMatch(/unicorn saddles/); // unrecognized item
    expect(ORDER_PRESETS[1]!.text).toContain("1,000g");
  });

  it.each(ORDER_PRESETS.map((p) => [p.title, p] as const))(
    "one click on %s fills the textarea and parses immediately",
    async (_title, preset) => {
      const onParseText = vi.fn();
      render(<Harness onParseText={onParseText} />);
      await userEvent.click(screen.getByRole("button", { name: new RegExp(preset.title) }));
      expect(screen.getByLabelText("Buyer message")).toHaveValue(preset.text);
      expect(onParseText).toHaveBeenCalledWith(preset.text, `Preset · ${preset.title}`);
    },
  );

  it("parses typed text with the button and Ctrl+Enter", async () => {
    const onParseText = vi.fn();
    render(<Harness onParseText={onParseText} />);
    const parseButton = screen.getByRole("button", { name: /Parse & resolve SKUs/ });
    expect(parseButton).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Buyer message"), "2x tamper");
    await userEvent.click(parseButton);
    await userEvent.click(screen.getByLabelText("Buyer message")); // focus back on the textarea
    await userEvent.keyboard("{Control>}{Enter}{/Control}");
    expect(onParseText).toHaveBeenCalledTimes(2);
    expect(onParseText).toHaveBeenLastCalledWith("2x tamper");
  });

  it("accepts allowed media and rejects other types before upload", () => {
    const onParseMedia = vi.fn();
    const onReject = vi.fn();
    render(<Harness onParseMedia={onParseMedia} onReject={onReject} />);
    const input = screen.getByTestId("media-input");

    const png = new File(["png"], "po.png", { type: "image/png" });
    fireEvent.change(input, { target: { files: [png] } });
    expect(onParseMedia).toHaveBeenCalledWith(png);

    const exe = new File(["MZ"], "evil.exe", { type: "application/x-msdownload" });
    fireEvent.change(input, { target: { files: [exe] } });
    expect(onParseMedia).toHaveBeenCalledTimes(1);
    expect(onReject).toHaveBeenCalledWith(expect.stringContaining("Unsupported file type"));
  });

  it("locks intake once a PayPal order exists", () => {
    render(<Harness locked />);
    for (const preset of ORDER_PRESETS) {
      expect(screen.getByRole("button", { name: new RegExp(preset.title) })).toBeDisabled();
    }
    expect(screen.getByLabelText("Buyer message")).toBeDisabled();
  });

  it("summarizes the parse result", () => {
    render(
      <OrderIntakePanel
        text=""
        busy={false}
        locked={false}
        parse={sampleParse()}
        sourceLabel="Preset · Specialty Café Restock"
        onTextChange={() => {}}
        onParseText={() => {}}
        onParseMedia={() => {}}
      />,
    );
    const summary = screen.getByTestId("parse-summary");
    expect(summary).toHaveTextContent("Deterministic fallback");
    expect(summary).toHaveTextContent("3 lines extracted from Preset · Specialty Café Restock");
    expect(summary).toHaveTextContent("Back door delivery");
    expect(summary).toHaveTextContent("???");
  });
});
