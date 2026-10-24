# Clothing pose diagnostics

1. Preserve the accepted Blender source and make a working copy. Identify the exact garment, body, Armature modifier, shape keys and current pose.
2. Evaluate the deformed meshes through the dependency graph. Compare garment and body in the same coordinate space.
3. Sample the affected pose and a small set of relevant controlled poses. Separate static placement defects from animation-driven compression, weight errors and a disconnected seam.
4. Use body BVH nearest-point and signed-distance measurements with the local surface normal. Record the actual selection, minimum clearance and penetration distribution. Inspect the rendered views to distinguish intended contact from visible clipping.
5. Find skin correspondences on the same anatomical side. A nearest vertex on an opposing limb is not a valid weight donor.
6. Transfer weights only to the affected cloth region, normalize them, and retain protected boundary weights. Preserve existing rigid insertion blocks and connectors.
7. For shape-key meshes, apply a compatible local displacement to Basis and affected keys, retaining their relative deltas.
8. At a separate cloth/ribbon join, inspect both pieces' evaluated boundary positions and weights. Repair the join under the relevant pose rather than hiding the seam with unrelated geometry.
9. Solve the observed cause locally. Reuse accepted geometry outside the affected region.
10. Save a new file and reopen it. Compare the same selections and poses used to identify the problem.
