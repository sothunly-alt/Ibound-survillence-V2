export type StationBay = {
  id: string;
  name: string;
  type: string;
  roi: number[];
};

export function nextAvailableBayName(bays: { name?: string }[]): string {
  const used = new Set<number>();
  for (const bay of bays) {
    const match = String(bay.name || "").match(/Bay\s*(\d+)/i);
    if (match) used.add(Number.parseInt(match[1], 10));
  }
  let num = 1;
  while (used.has(num)) num += 1;
  return `Bay ${num}`;
}

export function engineBayToStation(row: Record<string, unknown>): StationBay | null {
  const id = String(row.bay_id || row.id || "").trim();
  if (!id) return null;
  const roiRaw = Array.isArray(row.roi) ? row.roi.map(Number) : [0.3, 0.2, 0.2, 0.3];
  return {
    id,
    name: String(row.name || id),
    type: row.type === "tool_area" ? "tool_area" : "vehicle_bay",
    roi: roiRaw.length === 4 ? roiRaw : [0.3, 0.2, 0.2, 0.3],
  };
}
