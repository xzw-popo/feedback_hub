function escapeHtml(input: string): string {
  return input
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function renderInline(input: string): string {
  return escapeHtml(input)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
}

function flushParagraph(out: string[], paragraph: string[]) {
  if (paragraph.length === 0) return
  out.push(`<p>${paragraph.map(renderInline).join('<br>')}</p>`)
  paragraph.length = 0
}

function flushList(out: string[], list: string[], ordered: boolean) {
  if (list.length === 0) return
  const tag = ordered ? 'ol' : 'ul'
  out.push(`<${tag}>${list.map(item => `<li>${renderInline(item)}</li>`).join('')}</${tag}>`)
  list.length = 0
}

function isTableRow(line: string): boolean {
  return line.trim().startsWith('|') && line.trim().endsWith('|')
}

function isTableDivider(line: string): boolean {
  return isTableRow(line) && splitTableRow(line).every(cell => /^:?-{3,}:?$/.test(cell.trim()))
}

function splitTableRow(line: string): string[] {
  const trimmed = line.trim()
  return trimmed.slice(1, -1).split('|').map(cell => cell.trim())
}

function renderTable(rows: string[]): string {
  const headers = splitTableRow(rows[0])
  const bodyRows = rows.slice(2).map(splitTableRow)
  const thead = `<thead><tr>${headers.map(cell => `<th>${renderInline(cell)}</th>`).join('')}</tr></thead>`
  const tbody = bodyRows.length
    ? `<tbody>${bodyRows.map(row => `<tr>${row.map(cell => `<td>${renderInline(cell)}</td>`).join('')}</tr>`).join('')}</tbody>`
    : ''
  return `<table>${thead}${tbody}</table>`
}

export function renderMarkdown(markdown: string): string {
  const out: string[] = []
  const paragraph: string[] = []
  const list: string[] = []
  const code: string[] = []
  let inCode = false
  let orderedList = false
  const lines = markdown.split(/\r?\n/)

  for (let i = 0; i < lines.length; i++) {
    const rawLine = lines[i]
    const line = rawLine.trimEnd()

    if (line.trim().startsWith('```')) {
      if (inCode) {
        out.push(`<pre><code>${escapeHtml(code.join('\n'))}</code></pre>`)
        code.length = 0
        inCode = false
      } else {
        flushParagraph(out, paragraph)
        flushList(out, list, orderedList)
        inCode = true
      }
      continue
    }

    if (inCode) {
      code.push(rawLine)
      continue
    }

    if (!line.trim()) {
      flushParagraph(out, paragraph)
      flushList(out, list, orderedList)
      orderedList = false
      continue
    }

    if (isTableRow(line) && i + 1 < lines.length && isTableDivider(lines[i + 1])) {
      flushParagraph(out, paragraph)
      flushList(out, list, orderedList)
      orderedList = false
      const tableRows = [line, lines[i + 1]]
      i += 2
      while (i < lines.length && isTableRow(lines[i])) {
        tableRows.push(lines[i].trimEnd())
        i += 1
      }
      i -= 1
      out.push(renderTable(tableRows))
      continue
    }

    const heading = /^(#{1,3})\s+(.+)$/.exec(line)
    if (heading) {
      flushParagraph(out, paragraph)
      flushList(out, list, orderedList)
      orderedList = false
      const level = heading[1].length
      out.push(`<h${level}>${renderInline(heading[2])}</h${level}>`)
      continue
    }

    const bullet = /^\s*[-*]\s+(.+)$/.exec(line)
    if (bullet) {
      flushParagraph(out, paragraph)
      if (orderedList) flushList(out, list, true)
      orderedList = false
      list.push(bullet[1])
      continue
    }

    const ordered = /^\s*\d+\.\s+(.+)$/.exec(line)
    if (ordered) {
      flushParagraph(out, paragraph)
      if (!orderedList) flushList(out, list, false)
      orderedList = true
      list.push(ordered[1])
      continue
    }

    flushList(out, list, orderedList)
    orderedList = false
    paragraph.push(line)
  }

  if (inCode) {
    out.push(`<pre><code>${escapeHtml(code.join('\n'))}</code></pre>`)
  }
  flushParagraph(out, paragraph)
  flushList(out, list, orderedList)

  return out.join('\n')
}
