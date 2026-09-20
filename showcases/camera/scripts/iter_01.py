import bpy
import math
import os
import json
from mathutils import Vector

# GAH-managed Blender workload: blender-retro-camera-001 / iteration 1
TASK_ID = os.getenv("GAH_TASK_ID", "blender-retro-camera-001")
ITERATION = int(os.getenv("GAH_ITERATION", "1"))
STAGE_DIR = os.environ["GAH_STAGE_DIR"]
FINAL_RENDER = os.environ["GAH_FINAL_RENDER"]
ITERATION_MANIFEST = os.environ["GAH_ITERATION_MANIFEST"]
BLEND_PATH = os.environ["GAH_BLEND_PATH"]

os.makedirs(STAGE_DIR, exist_ok=True)
os.makedirs(os.path.dirname(FINAL_RENDER), exist_ok=True)
os.makedirs(os.path.dirname(ITERATION_MANIFEST), exist_ok=True)
os.makedirs(os.path.dirname(BLEND_PATH), exist_ok=True)

# ----------------------------
# Scene reset / render settings
# ----------------------------
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
# Blender builds expose Eevee under different identifiers. Prefer the
# current identifier when present, but fall back to the enum actually
# reported by this host (Blender 5.2.0 LTS: BLENDER_EEVEE).
try:
    scene.render.engine = "BLENDER_EEVEE_NEXT"
except TypeError:
    scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x = 800
scene.render.resolution_y = 800
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGBA"
scene.render.film_transparent = False

# Eevee quality options are version-sensitive; set only when available.
if hasattr(scene, "eevee"):
    for attr, value in (
        ("taa_render_samples", 64),
        ("use_gtao", True),
        ("gtao_distance", 3),
        ("gtao_factor", 1.25),
    ):
        if hasattr(scene.eevee, attr):
            setattr(scene.eevee, attr, value)

# Factory-startup/use_empty can leave the scene without a World datablock.
# Create one explicitly so headless Blender builds behave consistently.
if scene.world is None:
    scene.world = bpy.data.worlds.new("GAH_Studio_World")
scene.world.use_nodes = True
world_bg = scene.world.node_tree.nodes.get("Background")
if world_bg is not None:
    world_bg.inputs["Color"].default_value = (0.806, 0.806, 0.806, 1.0)
    world_bg.inputs["Strength"].default_value = 0.35
else:
    scene.world.color = (0.806, 0.806, 0.806)

# ----------------------------
# Helpers
# ----------------------------
def hex_rgb(hex_value):
    h = hex_value.lstrip("#")
    return tuple(int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))

def rgba(hex_value, a=1.0):
    r, g, b = hex_rgb(hex_value)
    return (r, g, b, a)

def principled_material(name, base, metallic=0.0, roughness=0.45):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = base
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness
    return m

def leather_material():
    m = principled_material("Leather_Black", (0.015, 0.018, 0.02, 1.0), metallic=0.0, roughness=0.50)
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 42.0
    noise.inputs["Detail"].default_value = 5.0
    noise.inputs["Roughness"].default_value = 0.72
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.17
    bump.inputs["Distance"].default_value = 0.055
    nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return m

def brushed_metal_material():
    m = principled_material("Satin_Silver", (0.44, 0.47, 0.50, 1.0), metallic=0.94, roughness=0.24)
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 18.0
    noise.inputs["Detail"].default_value = 3.0
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.055
    bump.inputs["Distance"].default_value = 0.02
    nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return m

def glass_material():
    m = principled_material("Optical_Glass", (0.015, 0.035, 0.045, 1.0), metallic=0.0, roughness=0.07)
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    if "IOR" in bsdf.inputs:
        bsdf.inputs["IOR"].default_value = 1.46
    if "Transmission Weight" in bsdf.inputs:
        bsdf.inputs["Transmission Weight"].default_value = 0.92
    elif "Transmission" in bsdf.inputs:
        bsdf.inputs["Transmission"].default_value = 0.92
    return m

def assign_mat(obj, mat):
    obj.data.materials.clear()
    obj.data.materials.append(mat)

def bevel(obj, width=0.08, segments=4):
    mod = obj.modifiers.new("Soft industrial edges", "BEVEL")
    mod.width = width
    mod.segments = segments
    if hasattr(mod, "limit_method"):
        mod.limit_method = "ANGLE"
    return obj

def smooth(obj):
    if hasattr(obj.data, "polygons"):
        for p in obj.data.polygons:
            p.use_smooth = True
    return obj

def add_cube(name, loc, dims, mat=None, bevel_w=0.0, rot=(0,0,0)):
    bpy.ops.mesh.primitive_cube_add(location=loc, rotation=rot)
    o = bpy.context.object
    o.name = name
    o.dimensions = dims
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if bevel_w > 0:
        bevel(o, bevel_w, 4)
    if mat:
        assign_mat(o, mat)
    return o

def add_cyl(name, loc, radius, depth, mat=None, rot=(0,0,0), vertices=96, bevel_w=0.0):
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius, depth=depth, location=loc, rotation=rot)
    o = bpy.context.object
    o.name = name
    smooth(o)
    if bevel_w > 0:
        bevel(o, bevel_w, 3)
    if mat:
        assign_mat(o, mat)
    return o

def add_torus(name, loc, major_radius, minor_radius, mat=None, rot=(0,0,0)):
    bpy.ops.mesh.primitive_torus_add(
        major_radius=major_radius,
        minor_radius=minor_radius,
        major_segments=96,
        minor_segments=20,
        location=loc,
        rotation=rot,
    )
    o = bpy.context.object
    o.name = name
    smooth(o)
    if mat:
        assign_mat(o, mat)
    return o

def add_text(name, body, loc, size, mat, extrude=0.018):
    bpy.ops.object.text_add(location=loc, rotation=(math.radians(90), 0, 0))
    o = bpy.context.object
    o.name = name
    o.data.body = body
    o.data.align_x = "CENTER"
    o.data.align_y = "CENTER"
    o.data.size = size
    o.data.extrude = extrude
    o.data.bevel_depth = extrude * 0.22
    assign_mat(o, mat)
    return o

def look_at(obj, target):
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

def add_area(name, loc, energy, size, color=(1,1,1), target=(0,0,2.2)):
    bpy.ops.object.light_add(type="AREA", location=loc)
    l = bpy.context.object
    l.name = name
    l.data.energy = energy
    l.data.shape = "DISK"
    l.data.size = size
    l.data.color = color
    look_at(l, target)
    return l

def render_to(path):
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)

def clear_lights():
    for o in list(bpy.data.objects):
        if o.type == "LIGHT":
            bpy.data.objects.remove(o, do_unlink=True)

# ----------------------------
# Materials
# ----------------------------
clay = principled_material("Stage_Clay", (0.31, 0.32, 0.33, 1.0), metallic=0.0, roughness=0.52)
leather = leather_material()
silver = brushed_metal_material()
black_metal = principled_material("Black_Anodized", (0.018, 0.022, 0.027, 1.0), metallic=0.48, roughness=0.24)
rubber = principled_material("Rubber_Rings", (0.012, 0.013, 0.014, 1.0), metallic=0.0, roughness=0.58)
glass = glass_material()
red = principled_material("Shutter_Accent", (0.32, 0.018, 0.015, 1.0), metallic=0.15, roughness=0.35)
white_print = principled_material("Engraving_White", (0.80, 0.82, 0.82, 1.0), metallic=0.08, roughness=0.42)
backdrop_mat = principled_material("Studio_Gray_E8", rgba("#E8E8E8"), metallic=0.0, roughness=0.72)

# ----------------------------
# Camera framing + neutral construction light
# ----------------------------
bpy.ops.object.camera_add(location=(7.3, -8.6, 6.35))
cam = bpy.context.object
cam.name = "Hero_Camera"
cam.data.lens = 58
cam.data.sensor_width = 36
look_at(cam, (0, -0.25, 2.45))
scene.camera = cam

# A neutral floor is present from the start so construction renders retain contact cues.
floor = add_cube("Stage_Floor", (0, 0.2, -0.11), (18, 18, 0.18), backdrop_mat, bevel_w=0.05)
clear_lights()
add_area("Build_Key", (4.5, -5.0, 8.0), 950, 5.5, target=(0,0,2.1))
add_area("Build_Fill", (-5.0, -2.5, 5.3), 520, 5.0, target=(0,0,2.2))
add_area("Build_Rim", (1.0, 4.5, 6.8), 650, 4.0, target=(0,0.2,2.7))

# ----------------------------
# Stage 1: blockout / silhouette
# ----------------------------
material_targets = []

body = add_cube("Body_Leather_Core", (0, 0, 2.15), (5.20, 1.92, 3.15), clay, bevel_w=0.22)
material_targets.append((body, leather))

# Slight front shoulder around the mount keeps the face from reading as one flat slab.
shoulder = add_cube("Front_Shoulder", (0, -1.00, 2.25), (3.25, 0.30, 2.36), clay, bevel_w=0.14)
material_targets.append((shoulder, black_metal))

top_plate = add_cube("Top_Plate", (0, -0.02, 3.87), (5.18, 1.88, 0.42), clay, bevel_w=0.11)
material_targets.append((top_plate, silver))

bottom_plate = add_cube("Bottom_Plate", (0, 0.02, 0.50), (5.05, 1.84, 0.24), clay, bevel_w=0.07)
material_targets.append((bottom_plate, silver))

# SLR prism/viewfinder housing: stepped central form, not a simple cube silhouette.
prism_base = add_cube("Prism_Base", (0, 0.02, 4.18), (1.70, 1.45, 0.34), clay, bevel_w=0.12)
material_targets.append((prism_base, silver))
prism_top = add_cube("Prism_Housing", (0, 0.03, 4.50), (1.24, 1.22, 0.50), clay, bevel_w=0.16, rot=(0, math.radians(4), 0))
material_targets.append((prism_top, black_metal))

# Subtle right-hand grip bulge.
grip = add_cube("Right_Grip", (2.43, -0.58, 2.05), (0.55, 0.78, 2.55), clay, bevel_w=0.20)
material_targets.append((grip, leather))

render_to(os.path.join(STAGE_DIR, "01_blockout.png"))

# ----------------------------
# Stage 2: lens assembly
# ----------------------------
# Cylinder axis is rotated from Z to Y.
axis_rot = (math.radians(90), 0, 0)

mount = add_cyl("Lens_Mount", (0, -1.22, 2.28), 1.38, 0.28, clay, rot=axis_rot, bevel_w=0.04)
material_targets.append((mount, silver))

rear_barrel = add_cyl("Lens_Rear_Barrel", (0, -1.48, 2.28), 1.24, 0.55, clay, rot=axis_rot, bevel_w=0.04)
material_targets.append((rear_barrel, black_metal))

focus_ring = add_cyl("Focus_Ring", (0, -1.86, 2.28), 1.29, 0.44, clay, rot=axis_rot, bevel_w=0.035)
material_targets.append((focus_ring, rubber))

aperture_ring = add_cyl("Aperture_Ring", (0, -2.18, 2.28), 1.17, 0.30, clay, rot=axis_rot, bevel_w=0.03)
material_targets.append((aperture_ring, black_metal))

front_barrel = add_cyl("Front_Barrel", (0, -2.43, 2.28), 1.08, 0.34, clay, rot=axis_rot, bevel_w=0.03)
material_targets.append((front_barrel, black_metal))

accent_ring = add_torus("Front_Chrome_Ring", (0, -2.62, 2.28), 0.99, 0.055, clay, rot=axis_rot)
material_targets.append((accent_ring, silver))

front_glass = add_cyl("Front_Glass", (0, -2.67, 2.28), 0.91, 0.055, clay, rot=axis_rot, vertices=96)
material_targets.append((front_glass, glass))

inner_glass = add_cyl("Inner_Glass", (0, -2.60, 2.28), 0.70, 0.035, clay, rot=axis_rot, vertices=96)
material_targets.append((inner_glass, glass))

# Raised knurl blocks: real geometry for clear focus/aperture ring texture.
for ring_idx, (y, rad, count, depth, height) in enumerate([
    (-1.86, 1.34, 40, 0.45, 0.11),
    (-2.18, 1.21, 36, 0.30, 0.085),
]):
    for i in range(count):
        a = (2.0 * math.pi * i) / count
        x = rad * math.cos(a)
        z = 2.28 + rad * math.sin(a)
        ridge = add_cube(
            f"Knurl_{ring_idx}_{i:02d}",
            (x, y, z),
            (0.085, depth * 0.86, height),
            clay,
            bevel_w=0.012,
            rot=(0, a, 0),
        )
        material_targets.append((ridge, rubber if ring_idx == 0 else black_metal))

render_to(os.path.join(STAGE_DIR, "02_lens.png"))

# ----------------------------
# Stage 3: controls / viewfinder / nameplate
# ----------------------------
# Shutter button + red center accent.
shutter_base = add_cyl("Shutter_Button_Base", (1.58, -0.18, 4.22), 0.24, 0.16, clay, vertices=64, bevel_w=0.025)
material_targets.append((shutter_base, silver))
shutter_red = add_cyl("Shutter_Button_Accent", (1.58, -0.18, 4.32), 0.105, 0.05, clay, vertices=64, bevel_w=0.015)
material_targets.append((shutter_red, red))

# Film advance lever and pivot.
advance_pivot = add_cyl("Advance_Pivot", (1.95, 0.19, 4.23), 0.30, 0.18, clay, vertices=64, bevel_w=0.025)
material_targets.append((advance_pivot, silver))
advance = add_cube("Film_Advance_Lever", (2.05, 0.37, 4.37), (1.15, 0.19, 0.12), clay, bevel_w=0.075, rot=(0, 0, math.radians(18)))
material_targets.append((advance, black_metal))

# Rewind knob and small grip ridges.
rewind = add_cyl("Rewind_Knob", (-1.91, 0.10, 4.22), 0.37, 0.22, clay, vertices=72, bevel_w=0.03)
material_targets.append((rewind, silver))
for i in range(12):
    a = 2 * math.pi * i / 12
    x = -1.91 + 0.40 * math.cos(a)
    y = 0.10 + 0.40 * math.sin(a)
    tab = add_cube(f"Rewind_Ridge_{i:02d}", (x, y, 4.23), (0.08, 0.11, 0.17), clay, bevel_w=0.015, rot=(0,0,a))
    material_targets.append((tab, black_metal))

# Hot shoe with two rails behind prism.
shoe_base = add_cube("Hot_Shoe_Base", (0.0, 0.67, 4.40), (1.02, 0.56, 0.10), clay, bevel_w=0.035)
material_targets.append((shoe_base, black_metal))
for sx in (-0.40, 0.40):
    rail = add_cube(f"Hot_Shoe_Rail_{sx:+.2f}", (sx, 0.67, 4.48), (0.10, 0.58, 0.10), clay, bevel_w=0.02)
    material_targets.append((rail, silver))

# Front viewfinder/prism window keeps required optical feature visible from hero angle.
vf_frame = add_cube("Viewfinder_Frame", (0, -0.63, 4.49), (0.82, 0.10, 0.29), clay, bevel_w=0.055)
material_targets.append((vf_frame, silver))
vf_glass = add_cube("Viewfinder_Glass", (0, -0.695, 4.49), (0.62, 0.045, 0.18), clay, bevel_w=0.025)
material_targets.append((vf_glass, glass))

# Nameplate and built-in text geometry.
nameplate = add_cube("Brand_Nameplate", (0, -1.135, 3.34), (1.55, 0.07, 0.32), clay, bevel_w=0.045)
material_targets.append((nameplate, silver))
brand = add_text("Brand_Text", "GAH 77", (0, -1.185, 3.34), 0.23, clay, extrude=0.012)
material_targets.append((brand, black_metal))

# Front-side strap lugs.
for sx in (-2.67, 2.67):
    lug = add_torus(f"Strap_Lug_{sx:+.2f}", (sx, -0.13, 3.05), 0.20, 0.055, clay, rot=(math.radians(90),0,0))
    material_targets.append((lug, silver))

# Four small front plate screws.
for sx in (-2.28, 2.28):
    for z in (1.03, 3.55):
        screw = add_cyl(f"Front_Screw_{sx:+.2f}_{z:.2f}", (sx, -1.015, z), 0.07, 0.045, clay, rot=axis_rot, vertices=48)
        material_targets.append((screw, silver))

render_to(os.path.join(STAGE_DIR, "03_controls.png"))

# ----------------------------
# Stage 4: procedural materials
# ----------------------------
for obj, mat in material_targets:
    assign_mat(obj, mat)

# Add engraved-looking ring marks as tiny silver indicators.
for i, label in enumerate(("16", "8", "4", "2")):
    a = math.radians(-48 + i * 32)
    r = 1.20
    x = r * math.cos(a)
    z = 2.28 + r * math.sin(a)
    tick = add_cube(f"Aperture_Tick_{label}", (x, -2.35, z), (0.035, 0.025, 0.12), silver, bevel_w=0.008, rot=(0, a, 0))

render_to(os.path.join(STAGE_DIR, "04_materials.png"))

# ----------------------------
# Stage 5: studio cyclorama + final lighting
# ----------------------------
# Hide the initial slab floor and replace it with a seamless curved sweep.
floor.hide_render = True

# Build a curved cyclorama strip across X.
profile = [
    (-8.0, 0.0),
    (2.8, 0.0),
    (3.5, 0.12),
    (4.15, 0.55),
    (4.60, 1.25),
    (4.85, 2.15),
    (4.95, 3.35),
    (5.00, 6.8),
]
verts = []
faces = []
half_w = 10.0
for x in (-half_w, half_w):
    for y, z in profile:
        verts.append((x, y, z))
n = len(profile)
for i in range(n - 1):
    a = i
    b = i + 1
    c = n + i + 1
    d = n + i
    faces.append((a, b, c, d))
mesh = bpy.data.meshes.new("CycloramaMesh")
mesh.from_pydata(verts, [], faces)
mesh.update()
cyclo = bpy.data.objects.new("Seamless_Cyclorama", mesh)
bpy.context.collection.objects.link(cyclo)
assign_mat(cyclo, backdrop_mat)
for p in cyclo.data.polygons:
    p.use_smooth = True

clear_lights()
add_area("Key_Large", (4.8, -4.2, 8.5), 1250, 5.5, color=(1.0, 0.91, 0.82), target=(0,-0.1,2.35))
add_area("Fill_Soft", (-5.4, -2.2, 5.2), 730, 6.0, color=(0.82, 0.90, 1.0), target=(0,-0.1,2.15))
add_area("Rim_Top", (-0.8, 4.0, 7.6), 1050, 4.0, color=(1.0, 0.96, 0.90), target=(0,0.2,3.0))
add_area("Front_Eye", (0.0, -5.8, 4.2), 260, 3.0, color=(0.92, 0.95, 1.0), target=(0,-0.7,2.3))

# A low-energy point light makes the optical glass read without flattening the body.
bpy.ops.object.light_add(type="POINT", location=(-1.8, -3.7, 3.3))
eye = bpy.context.object
eye.name = "Lens_Catchlight"
eye.data.energy = 80
eye.data.color = (0.72, 0.86, 1.0)
eye.data.shadow_soft_size = 1.5

# Final micro details: subtle orange film-plane/index mark.
index_mark = add_cube("Lens_Index_Mark", (0.0, -1.64, 3.60), (0.045, 0.035, 0.10), red, bevel_w=0.01)

render_to(os.path.join(STAGE_DIR, "05_lighting.png"))

# Final render duplicates the fully lit stage at required exact dimensions.
scene.render.filepath = FINAL_RENDER
bpy.ops.render.render(write_still=True)

# Save the complete intermediate .blend after the final scene is assembled.
bpy.ops.wm.save_as_mainfile(filepath=BLEND_PATH)

# Manifest is intentionally factual and executor-verifiable.
material_names = sorted({m.name for m in bpy.data.materials})
manifest = {
    "task_id": TASK_ID,
    "iteration": ITERATION,
    "render_engine": scene.render.engine,
    "render_resolution": [scene.render.resolution_x, scene.render.resolution_y],
    "object_count": len(bpy.data.objects),
    "material_names": material_names,
    "camera": {
        "name": cam.name,
        "location": [round(v, 4) for v in cam.location],
        "lens_mm": cam.data.lens,
        "description": "front-upper three-quarter hero view, approximately 35-degree downward read"
    },
    "stage_files": [
        "01_blockout.png",
        "02_lens.png",
        "03_controls.png",
        "04_materials.png",
        "05_lighting.png"
    ],
    "final_render_path": FINAL_RENDER,
    "blend_path": BLEND_PATH,
    "notes": [
        "All geometry created procedurally in bpy; no external models, textures or HDR assets.",
        "Stage 1-3 use neutral clay for construction readability; stage 4 applies procedural materials; stage 5 adds seamless studio sweep and final three-point lighting.",
        "Raised focus/aperture knurling uses explicit generated geometry for a strong manufactured-camera silhouette."
    ]
}
with open(ITERATION_MANIFEST, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2, ensure_ascii=False)

print(json.dumps({
    "status": "PASS",
    "task_id": TASK_ID,
    "iteration": ITERATION,
    "final_render": FINAL_RENDER,
    "manifest": ITERATION_MANIFEST,
    "blend": BLEND_PATH,
    "object_count": len(bpy.data.objects),
}, ensure_ascii=False))
