# First Launch and Sample Graphs

After downloading the Windows prebuilt package, the fastest way to understand F8Studio is to open a predefined graph and run it.

## Step 1: Download a Prebuilt Package

Get the latest Windows package from:

[GitHub Releases](https://github.com/feel8-fun/f8studio/releases){ .md-button .md-button--primary }

## Step 2: Open Studio

Launch the packaged `f8pystudio` application from the extracted release folder.

## Step 3: Pick a Sample Graph

The existing scenario pages already link to downloadable graph JSON files:

1. [Scene 01: CVKit Template Tracking](../scenarios/scene-01-cvkit_template_tracking.md)
2. [Scene 02: GameMod Skeleton](../scenarios/scene-02-gamemod_skeleton.md)
3. [Scene 03: Audio Driven TCode](../scenarios/scene-03-audio_driven.md)
4. [Scene 04: Functional TCode Generation](../scenarios/scene-04-functional_tcode.md)

Each scenario page includes a `Download JSON` link or a direct link to the session file under `docs/scenarios/scripts`.

## Step 4: Load the Graph in Studio

Use one of these actions inside Studio:

- `Ctrl+Shift+O` to open a session file
- `Ctrl+O` to load the last session

After loading, review the node labels and required state fields before starting services.

## Step 5: Run It Carefully

1. Start infrastructure/runtime host services first
2. Start producers such as media or capture services
3. Start downstream processing/output nodes
4. Watch status, logs, and visualization nodes

## What To Read Next

- [Studio Quickstart](../getting-started/studio.md)
- [For Graph Authors](../graph-authors/index.md)

