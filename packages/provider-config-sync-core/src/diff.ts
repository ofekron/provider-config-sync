export interface AlignedDiffRow {
  key: string;
  kind: "same" | "changed" | "added" | "removed";
  unifiedLine: number | null;
  specificLine: number | null;
  unifiedText: string;
  specificText: string;
}

export interface DiffHunk {
  key: string;
  rows: AlignedDiffRow[];
}

export function splitLines(content: string): string[] {
  const lines = content.split(/\r?\n/);
  return lines.length > 1 && lines[lines.length - 1] === "" ? lines.slice(0, -1) : lines;
}

function buildDiffOps(unifiedLines: string[], specificLines: string[]): AlignedDiffRow[] {
  const dp = Array.from({ length: unifiedLines.length + 1 }, () => Array(specificLines.length + 1).fill(0) as number[]);
  for (let i = unifiedLines.length - 1; i >= 0; i -= 1) {
    for (let j = specificLines.length - 1; j >= 0; j -= 1) {
      dp[i][j] = unifiedLines[i] === specificLines[j]
        ? dp[i + 1][j + 1] + 1
        : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  const rows: AlignedDiffRow[] = [];
  let i = 0;
  let j = 0;
  while (i < unifiedLines.length || j < specificLines.length) {
    if (i < unifiedLines.length && j < specificLines.length && unifiedLines[i] === specificLines[j]) {
      rows.push({
        key: `same:${i}:${j}`,
        kind: "same",
        unifiedLine: i + 1,
        specificLine: j + 1,
        unifiedText: unifiedLines[i],
        specificText: specificLines[j],
      });
      i += 1;
      j += 1;
      continue;
    }

    if (j >= specificLines.length || (i < unifiedLines.length && dp[i + 1][j] >= dp[i][j + 1])) {
      rows.push({
        key: `removed:${i}:${j}`,
        kind: "removed",
        unifiedLine: i + 1,
        specificLine: null,
        unifiedText: unifiedLines[i],
        specificText: "",
      });
      i += 1;
      continue;
    }

    rows.push({
      key: `added:${i}:${j}`,
      kind: "added",
      unifiedLine: null,
      specificLine: j + 1,
      unifiedText: "",
      specificText: specificLines[j],
    });
    j += 1;
  }
  return rows;
}

function pairChangedRows(rows: AlignedDiffRow[]): AlignedDiffRow[] {
  const paired: AlignedDiffRow[] = [];
  for (let index = 0; index < rows.length; index += 1) {
    const row = rows[index];
    const next = rows[index + 1];
    if (row.kind === "removed" && next?.kind === "added") {
      paired.push({
        key: `changed:${row.unifiedLine}:${next.specificLine}`,
        kind: "changed",
        unifiedLine: row.unifiedLine,
        specificLine: next.specificLine,
        unifiedText: row.unifiedText,
        specificText: next.specificText,
      });
      index += 1;
      continue;
    }
    paired.push(row);
  }
  return paired;
}

export function buildAlignedDiffRows(unifiedContent: string, specificContent: string): AlignedDiffRow[] {
  return pairChangedRows(buildDiffOps(splitLines(unifiedContent), splitLines(specificContent)));
}

export function replaceLine(content: string, lineNumber: number | null, fallbackIndex: number, value: string): string {
  const lines = splitLines(content);
  const nextLines = value.split(/\r?\n/);
  const index = lineNumber === null
    ? Math.min(Math.max(fallbackIndex, 0), lines.length)
    : Math.min(Math.max(lineNumber - 1, 0), lines.length);
  lines.splice(index, lineNumber === null ? 0 : 1, ...nextLines);
  return joinLinesLike(lines, content);
}

function joinLinesLike(lines: string[], original: string): string {
  return lines.join("\n") + (original.endsWith("\n") ? "\n" : "");
}

function insertionIndexFor(
  rows: AlignedDiffRow[],
  rowIndex: number,
  target: "unified" | "specific",
  lineCount: number,
): number {
  const lineKey = target === "unified" ? "unifiedLine" : "specificLine";
  for (let index = rowIndex + 1; index < rows.length; index += 1) {
    const lineNumber = rows[index][lineKey];
    if (lineNumber !== null) return Math.max(0, lineNumber - 1);
  }
  for (let index = rowIndex - 1; index >= 0; index -= 1) {
    const lineNumber = rows[index][lineKey];
    if (lineNumber !== null) return Math.min(lineCount, lineNumber);
  }
  return lineCount;
}

function insertionIndexFromRowKey(row: AlignedDiffRow, target: "unified" | "specific"): number | null {
  const match = /^(added|removed):(\d+):(\d+)$/.exec(row.key);
  if (!match) return null;
  return Number(match[target === "unified" ? 2 : 3]);
}

export function applyRowsToContent(
  content: string,
  rows: AlignedDiffRow[],
  target: "unified" | "specific",
): string {
  const lines = splitLines(content);
  let offset = 0;
  rows.forEach((row, rowIndex) => {
    const targetLine = target === "unified" ? row.unifiedLine : row.specificLine;
    const sourceLine = target === "unified" ? row.specificLine : row.unifiedLine;
    const sourceText = target === "unified" ? row.specificText : row.unifiedText;
    if (targetLine !== null && sourceLine !== null) {
      lines[targetLine - 1 + offset] = sourceText;
      return;
    }
    if (targetLine === null && sourceLine !== null) {
      const baseIndex = insertionIndexFromRowKey(row, target)
        ?? insertionIndexFor(rows, rowIndex, target, lines.length);
      const index = baseIndex + offset;
      lines.splice(Math.min(Math.max(index, 0), lines.length), 0, sourceText);
      offset += 1;
      return;
    }
    if (targetLine !== null && sourceLine === null) {
      lines.splice(targetLine - 1 + offset, 1);
      offset -= 1;
    }
  });
  return joinLinesLike(lines, content);
}

export function buildDiffHunks(rows: AlignedDiffRow[]): DiffHunk[] {
  const hunks: DiffHunk[] = [];
  let current: AlignedDiffRow[] = [];
  for (const row of rows) {
    if (row.kind === "same") {
      if (current.length > 0) {
        hunks.push({ key: current[0].key, rows: current });
        current = [];
      }
      continue;
    }
    current.push(row);
  }
  if (current.length > 0) hunks.push({ key: current[0].key, rows: current });
  return hunks;
}
