---
type: hub
tags: [hub, design, visual]
---

# Wiki Inspi

Captured, classified by a vision model, searchable, turned into moodboards. Filters (color, style, mood, author, technique, use, domain) go in the **Filter** bar of the view below, or pick a grouped view in the view switcher.

## Recent captures

![[_Wiki Inspi.base#Recent]]

## Explore

Full base, one view per axis: [[_Wiki Inspi.base]] (By main color, By style, By mood, By author, By technique, By use, By domain, Palette).

## Moodboards

```dataview
LIST FROM "Moodboards"
SORT file.mtime DESC
```

New moodboard from the terminal:

```
inspi board "query" --name my-board [--color #hex] [--style x] [--mood x]
inspi search "query" [--color #hex] --json
```

## Themes (MOCs)

```dataview
LIST file.link
FROM "MOCs"
SORT file.name ASC
```

## Inbox (waiting to be classified)

```dataview
TABLE captured
FROM "Inbox"
WHERE type
SORT file.ctime DESC
LIMIT 20
```
