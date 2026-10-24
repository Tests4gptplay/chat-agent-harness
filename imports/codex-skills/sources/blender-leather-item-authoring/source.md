# Detailed leather items

## Shape and construction

1. Decompose the design into silhouette masses, panels, seams, pockets, straps, hardware and embossed details.
2. Establish coordinates, scale and section orientation. Use a few meaningful curvature intervals before adding subdivisions.
3. Loft ring sections to control a bag's broad volume and bulge. Tune the front, side and back profiles independently where the reference requires it.
4. Give pockets and pads real depth, attachment and edge transitions. Add thickness and rounded edges at a scale appropriate to the item.
5. Organize parts by independent editing and visibility boundaries.

## Straps, hardware and stitches

A handle is a bent band with a cross-section. Establish its width axis in front, side and top views, then sweep a controlled rounded-rectangle section along the path.

Orient zipper sliders using the local outward normal and zipper tangent. Their axes must follow the surface and travel direction.

Place stitches on the evaluated surface, not an approximate analytic plane. Segment stitch lengths by arc length, maintaining inset and spacing through bends. Inspect both close-up detail and the whole item.

## Surface detail

Use evaluated mesh/BVH correspondence for curved relief. Record the surface normal and relief distance; preserve that offset when moving decorations. Avoid large nonplanar triangles through curved ornament regions.

For leather materials, balance base color, roughness variation and micro-normal scale against actual lighting. Inspect node inputs in the installed Blender version before assigning them.

## Local revision

Keep accepted shapes and patterns intact. Define the changed region and protected boundary, apply only that revision, and compare matching views. Save a versioned blend plus the requested portable export. Check that the output contains the needed materials and textures.
