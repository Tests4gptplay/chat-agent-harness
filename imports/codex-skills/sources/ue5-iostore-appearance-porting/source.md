# UE5 IoStore appearance porting

## Scope and identity

Identify source appearance, target slot and requested scope: mesh, material, animation, VFX, vehicle extension or UI preview. Keep the target's gameplay identity and required package names.

Inspect the actual import/export graph and dependencies. Resolve the USkeletalMesh and related material, animation, cue and UI references from data rather than filenames alone.

## Choose the transplant

- Full export-payload transfer: use when source and target serialization/dependencies are compatible.
- Selective property/map transfer: use when only a subset of behavior should move.
- Import-slot repointing: use when an existing reference should select a compatible source asset.

## Carry dependencies

Migrate import chains and NameMap/FName indices coherently. Preserve class, outer and package relationships.

Check animation and preview references separately. For cinematic/ultimate effects, inspect sequence and animation dependencies. For ornaments controlled by GameplayCues, trace the cue and load path.

Keep the requested dye/material policy. Inspect actual layer/parameter data for emissive or awaken behavior.

## Package and inspect

Cook or patch the affected assets using the matching serialization layout. Validate export boundaries, imports and required dependencies. Package the target identity and inspect the changed appearance, relevant UI and affected action.

Reuse accepted geometry and cooked outputs for an identity-only remap; focus checks on the changed references and attachment surface.
