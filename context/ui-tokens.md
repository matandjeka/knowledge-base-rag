# Advanced Multi-Source Enterprise RAG — UI Tokens

## 1. Purpose
Define reusable visual tokens for a clean enterprise knowledge assistant interface.

## 2. Design Principles
- Professional
- Minimal
- Evidence-first
- Accessible
- Dense enough for enterprise workflows
- Easy to inspect sources and retrieval details

## 3. Color Tokens
Use semantic names rather than hardcoded component-specific names.

```text
color.bg.canvas
color.bg.surface
color.bg.subtle
color.bg.sidebar
color.text.primary
color.text.secondary
color.text.muted
color.border.default
color.border.strong
color.action.primary
color.action.primary_hover
color.status.success
color.status.warning
color.status.error
color.status.info
color.citation.bg
color.citation.border
```

Suggested light-mode values:

```text
canvas          #F7F8FA
surface         #FFFFFF
subtle          #F1F4F8
sidebar         #101828
text.primary    #101828
text.secondary  #344054
text.muted      #667085
border.default  #D0D5DD
border.strong   #98A2B3
action.primary  #155EEF
success         #079455
warning         #DC6803
error           #D92D20
info            #1570EF
citation.bg     #EFF8FF
citation.border #84CAFF
```

## 4. Typography Tokens

```text
font.family.ui      = Inter, system-ui, sans-serif
font.family.mono    = ui-monospace, SFMono-Regular, Menlo, monospace
font.size.xs        = 12px
font.size.sm        = 14px
font.size.md        = 16px
font.size.lg        = 18px
font.size.xl        = 24px
font.size.2xl       = 32px
font.weight.regular = 400
font.weight.medium  = 500
font.weight.semibold= 600
font.weight.bold    = 700
line.height.tight   = 1.25
line.height.normal  = 1.5
line.height.relaxed = 1.7
```

## 5. Spacing Tokens

```text
space.1 = 4px
space.2 = 8px
space.3 = 12px
space.4 = 16px
space.5 = 20px
space.6 = 24px
space.8 = 32px
space.10 = 40px
space.12 = 48px
space.16 = 64px
```

## 6. Radius Tokens

```text
radius.sm = 6px
radius.md = 10px
radius.lg = 14px
radius.xl = 20px
radius.full = 999px
```

## 7. Shadow Tokens

```text
shadow.sm = subtle input/card elevation
shadow.md = modal/dropdown elevation
shadow.lg = floating inspector panel elevation
```

## 8. Layout Tokens

```text
layout.sidebar.width = 280px
layout.content.max = 1280px
layout.chat.max = 900px
layout.inspector.width = 420px
layout.header.height = 64px
```

## 9. Component State Tokens

```text
state.hover.opacity = 0.92
state.disabled.opacity = 0.50
state.focus.ring = 2px
state.focus.offset = 2px
```

## 10. Citation Tokens

```text
citation.badge.radius = radius.full
citation.badge.font = font.size.xs
citation.panel.bg = color.citation.bg
citation.panel.border = color.citation.border
```

Citation labels:
- `[PDF-1 p.12]`
- `[WEB-2]`
- `[CSV-3 row 44]`
- `[DB-4 orders#10291]`

## 11. Retrieval Debug Tokens
Use monospaced type and compact badges for:
- VECTOR
- WINDOW
- GRAPH
- BM25
- SQL
- FUSED
- RERANKED

## 12. Responsive Breakpoints

```text
sm = 640px
md = 768px
lg = 1024px
xl = 1280px
2xl = 1536px
```

On mobile:
- Sidebar becomes drawer
- Source inspector becomes full-screen sheet
- Chat input remains fixed at bottom
