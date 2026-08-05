# WeasyPrint constraints (learned the hard way)

- `FontConfiguration`: one instance shared by every `CSS()` AND
  `write_pdf()`. Anything else = silent DejaVu fallback. Check the PDF
  font table, never eyeball glyphs.
- Supported and used here: CSS custom properties (`var()`),
  `target-counter()` for TOC page numbers, `@page` margin boxes
  (`@top-center`, `@bottom-right`), `break-before/after/inside`.
- Not usable for paginated content: CSS grid and flexbox (they render but
  do not fragment across pages). Use block flow, tables and inline-block.
- Multi-column TOC: `columns: 2` on the `ol` works and fragments fine.
- Percentile bars and other data-driven widths: inline `style="width: X%"`
  is the one sanctioned inline style (data, not design).
- Images: prefer inline SVG (vector, dark-safe). matplotlib SVGs need the
  XML prolog stripped before inlining.
- Long tables: WeasyPrint repeats `thead` automatically; keep row content
  single-line where possible, `break-inside: avoid` on `tr`.
- Dark pages: set `background` on `@page` itself, not only on `body`,
  or margins print white.
- Generation is CPU-bound; it runs via `asyncio.to_thread` in the worker.
  Keep `render_pdf` synchronous and thread-safe (no shared globals).
