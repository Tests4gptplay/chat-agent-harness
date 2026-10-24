# NTE UE5.6 UI authoring

Identify the target widget/controller, mount path, dependencies and intended input behavior in the current project.

## Implement

- Build the requested layout with UMG widgets and appropriate brush/font/material assets.
- Separate visual design from input, visibility, persistence and lifecycle state.
- Choose a global tool mount only for a genuinely global tool; mount character outfit menus on their owning character.
- Resolve input focus and the actual key values. For generated FKey defaults, read back the saved parsed value.
- Persist settings at the requested user action and reload after reconstruction.
- Reuse current UI art when repairing a specific functional defect.

## Cook and troubleshoot

Cook all changed widgets, textures, Blueprint dependencies and configuration entries using the compatible project.

Inspect asset paths, class names and references when the widget does not appear. A Texture2D serial-size mismatch requires comparison of property schema and export boundaries with the target's layout.

Check the expected inputs, visibility and saved state with the real mount path. Keep exact package entry names and deliver the coherent pak/ucas/utoc set.
