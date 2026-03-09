# Service Development

This page is the entry point for developers who want to add a new runtime service to the repo.

## Mental Model

A service in this repo usually has three outward-facing pieces:

1. A launch entry under `services/**/service.yml` or platform-specific `service.win.yml` / `service.linux.yml`
2. A `describe.json` file that captures the service/operator spec used by docs and discovery
3. The actual runtime implementation in the relevant package or native service directory

## What A New Service Must Provide

- Stable `serviceClass`, label, version, and launch command
- Explicit state fields, data ports, and commands where applicable
- A generated or maintained `describe.json` that matches the real runtime contract
- Discovery compatibility so Studio and related tooling can surface the service correctly

## Common Development Flow

1. Implement the runtime service in the appropriate package or native target
2. Define or update the launch entry under `services/**`
3. Register service/operator specs through the runtime registry where applicable
4. Regenerate/update `describe.json`
5. Validate that Studio discovery and docs reflect the new service

## Discovery and Spec Notes

- Studio and tooling rely on explicit specs rather than hidden runtime magic
- Canonical documentation is generated from `service.yml` plus `describe.json`
- If the runtime contract changes, the discovery metadata and generated docs must change with it

## Packaging Expectations

- Service launch entries should point to the correct runtime binary/module for each platform
- Build and release packaging should include any assets, wheels, or native outputs the service needs
- A service is not “done” when it only runs locally from an IDE

## Good Repo Examples

- Pure Python/runtime-oriented services: inspect `f8.pyscript`, `f8.pyexpr`, `f8.pyengine`
- Native/C++ service entries: inspect the services under `services/f8/cvkit`, `services/f8/implayer`, `services/f8/screencap`

## Related Pages

- [Build from Source](build-from-source.md)
- [Docs Tooling](docs-tooling.md)
- [Reference > Modules](../modules/index.md)

