# Docs Tooling

F8Studio docs mix generated reference pages with hand-written guides.

## What Is Generated

- `docs/modules/services/*.md` from service metadata
- `docs/modules/index.md`
- generated inventory pages in `docs/node-atlas/*.md`

## Main Commands

Generate docs:

```bash
pixi run -e doc doc_gen
```

Validate generated output, nav, and links:

```bash
pixi run -e doc doc_check
```

Build the site in strict mode:

```bash
pixi run -e doc doc_build
```

## Authoring Rules

- Generated spec pages remain the canonical source for exact fields, ports, and commands
- Human guidance belongs in the manual fragments and audience-oriented guides
- Keep navigation and local links valid before shipping doc changes

## Cloudflare Pages Notes

Recommended build settings remain:

- build command validating nav/links and then building the site
- build output directory: `site`
- repository root as the Pages root directory

