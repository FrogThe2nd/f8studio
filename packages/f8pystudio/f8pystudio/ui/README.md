# UI Package

`f8pystudio.ui` contains reusable Studio UI code that is below the app-shell level.

This package exists to keep UI concerns explicit and layered:
- low-level reusable controls live together,
- dialogs and support helpers stay out of domain packages,
- composite UI building blocks stay separate from app-level pages.

## Package layout

### `ui.components`

Reusable low-level widgets and editors.

Typical contents:
- custom input controls,
- state value editors,
- small visual controls,
- reusable widget primitives.

Rules:
- should focus on UI behavior, rendering, and widget state,
- may depend on generic UI utilities and Qt,
- should avoid depending on asset/domain workflows,
- should not own application navigation or page orchestration.

### `ui.dialogs`

Reusable dialogs and modal editors.

Typical contents:
- schema editors,
- inspect/detail dialogs,
- focused editing dialogs for reusable UI workflows.

Rules:
- may compose `ui.components` and `ui.support`,
- may depend on domain models when the dialog edits or presents them,
- should keep orchestration local to the dialog,
- should not become a home for non-modal page containers.

### `ui.support`

UI-only helper code, builders, adapters, and policies.

Typical contents:
- widget construction helpers,
- read-only policies,
- formatting helpers for UI presentation,
- glue code between models and widgets,
- shared UI support utilities used by multiple hosts.

Rules:
- can depend on Qt, `ui.components`, and domain read APIs,
- should not own domain mutations or persistence rules,
- should not contain app-level workflow coordination,
- should stay focused on helping UI code stay small and explicit.

### `ui.widgets`

Reusable composite widgets that are larger than a single control but smaller than an application page.

Typical contents:
- structured panels,
- composite editors,
- reusable multi-section widget groups.

Rules:
- may compose `ui.components`, `ui.dialogs`, and `ui.support`,
- may depend on domain models needed to render/edit the widget,
- should represent reusable UI sections rather than top-level windows,
- should not absorb app-shell responsibilities.

### `ui.mainwin`

Main-window shell code and helpers that exist specifically to support the Studio main window.

Typical contents:
- the main window itself,
- main-window-only dock builders,
- main-window-only menu and toolbar builders,
- main-window-specific preference helpers.

Rules:
- may compose `ui.widgets`, `ui.dialogs`, and `ui.support`,
- may depend on app-level workflows that are specific to the Studio shell,
- should only contain code that is coupled to the main window lifecycle or layout,
- should not become a generic dumping ground for reusable UI pieces that belong in other `ui.*` layers.

## What does not belong here

The `ui` package is not the place for:
- app-level windows and docks,
- runtime/service orchestration,
- persistence and repository logic,
- graph/spec mutation logic,
- non-UI domain helpers.

Those should remain in their own functional packages such as `widgets`, `nodegraph`, `assets`, or other domain-specific modules.

## Relationship with top-level `widgets`

Top-level `f8pystudio.widgets` is reserved for application-facing containers such as:
- the main window,
- sidebars and docks,
- app-level panels,
- feature entry widgets tied to Studio shell behavior.

A good rule of thumb:
- if the code is a reusable UI building block, it likely belongs under `ui`,
- if the code is part of the Studio shell or application composition, it likely belongs under top-level `widgets` or `ui.mainwin`,
- if the code only exists to support the main window, prefer `ui.mainwin`.

## Dependency direction

Preferred dependency flow:
- `ui.components` -> base UI utilities
- `ui.support` -> `ui.components`
- `ui.dialogs` -> `ui.components`, `ui.support`
- `ui.widgets` -> `ui.components`, `ui.support`, `ui.dialogs`
- `ui.mainwin` -> `ui.widgets`, `ui.dialogs`, `ui.support`
- app-level `widgets` -> `ui.*`

Avoid reverse dependencies where lower-level UI layers import higher-level ones.

## Design principles

When adding code under `ui`, prefer the following:
- explicit widget contracts over dynamic patching,
- direct imports over package-level indirection,
- UI code that is easy to search, refactor, and type-check,
- narrow modules with clear ownership,
- domain logic kept outside the UI layer unless it is strictly presentation-related.
