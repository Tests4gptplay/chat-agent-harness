# NTE project-authored cosmetic mods

## Source

The authoring approach follows 天才执行者's [异环MOD制作教程](https://www.bilibili.com/video/BV1yEg76JET2), including extraction, UE project import, Outfit System registration and KawaiiPhysics examples. Adapt the workflow to the actual game, mapping and project versions.

## Extraction and Blender

1. Configure the exporter for GAME_NevernessToEverness with the user's authorized AES configuration and matching usmap. Keep keys out of logs and packages.
2. Locate the actual target SkeletalMesh and export its mesh, skeleton and textures.
3. Reconstruct materials with the correct normal and packed texture channels.
4. Make the requested mesh/texture change while preserving skeleton, weights and material slots.
5. Export with the unit, axis and morph settings supported by the target project.

## Unreal and packaging

1. Use the matching UE project and exact package topology.
2. Import mesh/materials/textures; check skeleton and section assignments.
3. If registering in an outfit system, use the actual current DataTable/struct template, stable IDs, icons and component configuration.
4. For KawaiiPhysics, integrate the required bone chain into the animation blueprint and configure the actual root, axis and constraints.
5. Cook into fresh staging. Package only the required assets and dependencies into the final pak/ucas/utoc set.
6. Check equip/unequip, UI preview and the specific animation/material changes in the relevant game setup.
