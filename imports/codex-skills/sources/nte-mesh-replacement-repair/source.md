# NTE mesh replacement and local repair

## Preserve the target

Record the current model, target package identity, skeleton, section/material order, UVs, vertex colors, shape keys and native appendages. Work on a copy and reuse accepted parts.

Identify components by topology, spatial placement and material/UV structure. A prior backup is a donor only where correspondence is established.

## Fit and reconstruct

- Use anatomical landmarks and same-side body surfaces for clothing, sleeve, shoe and accessory fitting.
- Keep protected boundary loops fixed during local shape transfer. Map the donor's interior into the target frame using corresponding anchors.
- For exact rollback, copy Basis and matching shape-key coordinates only for the identified submesh.
- For shape transfer, preserve current placement while reproducing the donor's relative local form.
- Retain ordered source polygon corners for preserved triangles. An unordered face signature is for matching, not reconstruction.
- Carry UVs, corner colors, normals, weights and shape-key deltas through the source mapping. Interpolate newly created vertices from their actual source face.

## Shading and materials

Separate triangle winding, tangent XYZ/W, normal XYZ, vertex colors, UVs and material behavior. Compare matched vertices/faces against the corresponding accepted or native reference.

Repair the identified channel in the affected scope. For new materials, preserve the required parent and serialized property schema.

## Export and diagnosis

Measure units and preserve the RefSkeleton, section mapping and morph identity through export/import.

For a crash, inspect the current dump and exact package/export type and serial-size mismatch. Compare the cooked property layout and export boundaries with the matching target asset.

Cook into clean staging, package required dependencies, and confirm the actual output. Apply runtime checks to the specific changed appearance or motion when available.
