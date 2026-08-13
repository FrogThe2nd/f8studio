# Build from Source

This page is for advanced users and contributors. Ordinary Windows users should use the prebuilt package from [GitHub Releases](https://github.com/feel8-fun/f8studio/releases).

## Clone and Bootstrap

```bash
git clone --recurse-submodules <your-repo-url>
cd f8studio
```

For an existing checkout, initialize the pinned Unity toolchain before Pixi
resolves the Studio environment:

```bash
git submodule update --init --recursive
```

Use Pixi tasks for common runtime commands:

```bash
pixi run -e default f8pystudio
pixi run -e default runner --help
```

## Python and Runtime Basics

- The workspace is organized through `pixi.toml`
- Runtime services are described by `services/**/service.yml`
- Static discovery metadata is stored in `services/**/describe.json`

Regenerate static describes when needed:

```bash
pixi run -e default update_describes
```

## Native/C++ Services

Bootstrap and build native services:

```bash
pixi run -e cpp cpp_bootstrap
pixi run -e cpp cpp_configure_release
pixi run -e cpp cpp_build_release
```

Refresh locks when dependency versions change:

```bash
pixi lock
pixi run -e cpp cpp_lock_refresh
```

## Packaging

Build a distributable runtime bundle:

```bash
pixi run -e ci dist_ci
```

- Output directory: `build/dist/f8studio-<platform-tag>`
- Optional archive mode: `pixi run -e ci dist_ci --archive`
- Windows bundles include the Unity setup wheel, exporter archives, and a
  SHA-256 asset manifest under `unitymods/`

Validate and package the Unity submodule independently:

```bash
pixi run -e default unitymods_validate
pixi run -e default unitymods_contract
pixi run -e default unitymods_build
pixi run -e default unitymods_package
```

`unitymods_contract` regenerates the golden packet through the production C#
encoder on Windows and compares it byte-for-byte with the Python SDK fixture.

Build a Studio launcher executable:

```bash
pixi run -e ci build_studio_launcher
```

## Windows Notes

- CI uses GitHub-hosted Windows runners plus MSVC environment setup
- Local Windows native builds still need Visual Studio Build Tools

## Related Pages

- [Service Development](service-development.md)
- [Unity Modding Architecture](unity-modding-architecture.md)
- [PyStudio Plugin Development](pystudio-plugin-development.md)
- [Docs Tooling](docs-tooling.md)

