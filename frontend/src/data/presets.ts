export interface OrderPreset {
  id: "cafe" | "bakery" | "stress";
  title: string;
  subtitle: string;
  text: string;
}

// Each preset is verified against the real fallback parser + HybridSKUMatcher (see the
// backend's test_feature4_api_support / preset probe): the first two resolve every line, the
// stress test deliberately exercises every confidence tone.
export const ORDER_PRESETS: readonly OrderPreset[] = [
  {
    id: "cafe",
    title: "Specialty Café Restock",
    subtitle: "One messy line, 3 vendors",
    text:
      "3x 1kg Ethiopian Yirgacheffe, 2 cases Barista Oat Milk, 1x 58mm calibrated tamper, " +
      "and 2 cases compostable 12oz hot cups + 2 cases 12oz sip lids",
  },
  {
    id: "bakery",
    title: "Artisan Bakery & Tea Wholesale",
    subtitle: "Shorthand units, delivery note",
    text: [
      "Hi team! Bakery + tea bar restock pls:",
      "- 2 x 25kg bread flour",
      "- instant dry yeast 500g x 3",
      "- 4 packs butter sheets 1kg",
      "- 1 box frozen croissants",
      "- vanilla bean paste 1L x2",
      "- 5 tins ceremonial matcha 100g",
      "- earl grey loose leaf 500g - 3",
      "- colombian arabica 1,000g x2",
      "- 2 packs windowed pastry boxes",
      "Note: deliver Tuesday before 7am",
    ].join("\n"),
  },
  {
    id: "stress",
    title: "Edge-Case Stress Test",
    subtitle: "Typos, ambiguity, stock-outs, junk",
    text: [
      "urgent!!",
      "* espreso tampr 58mm x1",
      "* half kg decaf beans x 4",
      "* 2 colombian arabica whole bean coffee",
      "* 1 digital coffee scale",
      "* 3 unicorn saddles",
      "* ???",
    ].join("\n"),
  },
];
