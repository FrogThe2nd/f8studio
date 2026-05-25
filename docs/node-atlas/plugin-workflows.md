# Plugin Workflows

Some Studio capabilities come from renderer or plugin workflows rather than standalone service pages.

## Template Match Capture (`template_match_capture`)

This repo-local plugin adds a capture workflow for `CVKit Template Match` sessions.

- Source of truth: `packages/f8pystudio_ext_template_match/f8pystudio_ext_template_match/plugin.py`
- What it solves: quicker template-region acquisition directly from Studio, without external preprocessing
- Best pairing: `f8.cvkit.templatematch` plus `f8.viz.track`
- Operational advice: use it to lock a clean initial template, then keep the tracking and visualization nodes in the graph for monitoring

See also:

- [CVKit Template Match service page](../modules/services/f8-cvkit-templatematch.md)
- [CVKit Template Tracking](../scenarios/cvkit-template-tracking.md)

## Plugin Loading Model

PyStudio loads local plugins through the `f8studio.pystudio.plugins` entrypoint group. In practice this means:

- editor features can appear without new `services/**/describe.json` entries
- missing plugins may leave sessions with placeholder or missing-node fallbacks
- release builds should verify that required plugin packages are present alongside Studio

