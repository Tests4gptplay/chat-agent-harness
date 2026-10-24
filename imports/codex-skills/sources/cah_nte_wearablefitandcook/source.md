# NTE wearable fitting and Cook

## Identify and fit

Identify the intended mesh by connected components, topology, material/UV layout and anatomical location. Object names are only an aid. Work from the current accepted mesh.

Build the body reference in the same coordinate space. Use same-side skin correspondences for local fitting and weight transfer. Keep boundary loops and rigid connectors stable.

For a soft strap, fit the free span with surface-aware offsets and a smooth longitudinal path. For a rigid ring, fit its axis, profile and clearance as a unit. Reevaluate relevant poses after weight changes.

On shape-key meshes, preserve topology and copy or apply corresponding changes to the keys. Remove hidden body geometry only in the requested covered region.

## Blender to UE

Measure the source in Blender and the import in UE to establish the scale conversion. Use a disposable export copy when converting meters to centimeters; keep Armature and mesh transforms coherent.

Preserve bone names, hierarchy, weights, material slots and shape-key identity. Choose the import route supported by the installed UE version and inspect the resulting RefSkeleton and MorphTargets.

Keep the material slot order corresponding to sections. Reuse compatible native materials; configure added materials from their actual properties and texture channels.

## Cook and package

Cook current assets into a clean staging directory with the project's compatible settings. Keep exact target package topology and dependencies.

When a game-specific serialization patch is required, inspect the current export layout, operate on a disposable copy, and verify export boundaries, serial sizes, NameMap/FName references and the target class payload.

Create the pak/ucas/utoc set with the matching Zen packaging tool. Preserve native entry names and collect required dependencies. Keep source, cooked staging and final deliverable identities distinct.

Check the modified geometry and package structure, then inspect the relevant game appearance/motion when that runtime is part of the task.
