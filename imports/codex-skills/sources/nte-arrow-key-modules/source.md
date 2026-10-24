# Arrow-key visibility modules

1. Identify independent requested parts in Blender. Keep their triangles associated with stable material sections after joining/export.
2. Record the section indices on the imported SkeletalMesh rather than deriving them from object-name order.
3. Create the character's module-control AnimBlueprint or equivalent supported per-character route. Resolve the owning skeletal mesh.
4. Store visibility state per module. Map the requested keys to those states and apply the section show/hide operations to that mesh.
5. Keep the post-process/animation chain and native character appendages intact.
6. With generated Blueprint FKey defaults, use the text accepted by FKey::ImportTextItem. Read the saved key back and verify the same input path.
7. Cook the changed Blueprint/mesh and needed dependencies in the compatible project. Preserve the target asset identity and package names.
8. Check each requested part independently and confirm input plus persistence behavior in the intended character context.
