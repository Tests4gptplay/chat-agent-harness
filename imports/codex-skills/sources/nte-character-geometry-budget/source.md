# NTE character geometry budget

Before substantial densification, compare the mod with the matching official cooked mesh.

Collect triangle count, render vertices, sections/material slots, bone count and available LODs from both assets. Compare the same LOD and include the complete character assembly relevant to runtime.

Distinguish Blender's shared vertices from exported split render vertices. UV boundaries, hard normals, materials and bone influences can split vertices.

Set the budget from the target's real counts and the requested visual change. Prefer local refinement where it adds visible shape. Keep protected silhouettes, weights, shape keys and section identity.

After export and Cook, remeasure the changed mesh. Report the actual count changes and LOD behavior.
