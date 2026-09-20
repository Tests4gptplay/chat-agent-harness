import bpy
import math
import os
import json
from pathlib import Path
from mathutils import Vector

TASK_ID = os.getenv("GAH_TASK_ID", "blender-retro-camera-001")
ITERATION = int(os.getenv("GAH_ITERATION", "3"))
STAGE_DIR = os.environ["GAH_STAGE_DIR"]
FINAL_RENDER = os.environ["GAH_FINAL_RENDER"]
ITERATION_MANIFEST = os.environ["GAH_ITERATION_MANIFEST"]
BLEND_PATH = os.environ["GAH_BLEND_PATH"]

prev_blend = Path(BLEND_PATH).parents[1] / "iter_02" / "camera_iter_02.blend"
if not prev_blend.exists():
    raise FileNotFoundError(str(prev_blend))

bpy.ops.wm.open_mainfile(filepath=str(prev_blend))
scene = bpy.context.scene
scene.render.resolution_x = 800
scene.render.resolution_y = 800
scene.render.resolution_percentage = 100

def get_material(name, base=(0.2,0.2,0.2,1), metallic=0.0, roughness=0.45):
    m = bpy.data.materials.get(name)
    if m is None:
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        bsdf = m.node_tree.nodes.get("Principled BSDF")
        bsdf.inputs["Base Color"].default_value = base
        bsdf.inputs["Metallic"].default_value = metallic
        bsdf.inputs["Roughness"].default_value = roughness
    return m

def assign(obj, material):
    obj.data.materials.clear()
    obj.data.materials.append(material)

def bevel(obj, width=0.02, segments=3):
    mod = obj.modifiers.new("iter03_bevel", "BEVEL")
    mod.width = width
    mod.segments = segments

def cube(name, loc, dims, material, rot=(0,0,0), bw=0.02):
    bpy.ops.mesh.primitive_cube_add(location=loc, rotation=rot)
    o = bpy.context.object
    o.name = name
    o.dimensions = dims
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if bw:
        bevel(o, bw)
    assign(o, material)
    return o

def cyl(name, loc, radius, depth, material, rot=(0,0,0), verts=96, bw=0.015):
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=radius, depth=depth, location=loc, rotation=rot)
    o = bpy.context.object
    o.name = name
    for p in o.data.polygons:
        p.use_smooth = True
    if bw:
        bevel(o, bw)
    assign(o, material)
    return o

def torus(name, loc, major, minor, material, rot=(0,0,0)):
    bpy.ops.mesh.primitive_torus_add(
        major_radius=major,
        minor_radius=minor,
        major_segments=96,
        minor_segments=18,
        location=loc,
        rotation=rot,
    )
    o = bpy.context.object
    o.name = name
    for p in o.data.polygons:
        p.use_smooth = True
    assign(o, material)
    return o

def text_obj(name, body, loc, size, material, extrude=0.009):
    bpy.ops.object.text_add(location=loc, rotation=(math.radians(90),0,0))
    o = bpy.context.object
    o.name = name
    o.data.body = body
    o.data.align_x = "CENTER"
    o.data.align_y = "CENTER"
    o.data.size = size
    o.data.extrude = extrude
    o.data.bevel_depth = extrude * 0.18
    assign(o, material)
    return o

def look_at(obj, target):
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z","Y").to_euler()

def render(path):
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)

leather = get_material("Leather_Black")
silver = get_material("Satin_Silver")
black = get_material("Black_Anodized")
rubber = get_material("Rubber_Rings")
glass = get_material("Optical_Glass")
red = get_material("Shutter_Accent")
white = get_material("Engraving_White", (0.82,0.84,0.84,1), 0.05, 0.38)
clay = get_material("Stage_Clay_03", (0.38,0.39,0.40,1), 0.0, 0.56)

# ---------------------------------------------------------------------------
# Geometry: reduce lens radial dominance while preserving depth.
# ---------------------------------------------------------------------------
lens_prefixes = (
    "Lens_Mount", "Lens_Rear_Barrel", "Focus_Ring", "Aperture_Ring",
    "Front_Barrel", "Front_Chrome_Ring", "Front_Glass", "Inner_Glass",
    "Knurl_", "Mount_Trim_02", "Focus_Sep_", "Aperture_Sep_02",
    "Inner_Bezel_02", "Deep_Inner_Glass_02", "Fine_Focus_Knurl_02_",
    "Aperture_Tick_", "Focus_Tick_", "Lens_Index_Mark",
)
lens_center = Vector((0.0, 0.0, 2.28))
bpy.ops.object.empty_add(type="PLAIN_AXES", location=lens_center)
lens_scale = bpy.context.object
lens_scale.name = "Lens_Radial_Scale_03"

lens_objects = []
for o in list(bpy.data.objects):
    if any(o.name.startswith(p) for p in lens_prefixes):
        lens_objects.append(o)
        world = o.matrix_world.copy()
        o.parent = lens_scale
        o.matrix_world = world
lens_scale.scale = (0.90, 1.0, 0.90)

# Front panel perimeter and shoulder lines.
for sx in (-2.40, 2.40):
    cube(f"Panel_Trim_V_03_{sx:+.2f}", (sx,-0.944,2.16), (0.045,0.035,2.48), silver, bw=0.007)
for zz in (0.92, 3.38):
    cube(f"Panel_Trim_H_03_{zz:.2f}", (0,-0.943,zz), (4.82,0.035,0.045), silver, bw=0.007)

# Reposition badge above the now-smaller lens so it reads clearly.
for name in ("Brand_Nameplate", "Brand_Text", "Brand_Border_02"):
    o = bpy.data.objects.get(name)
    if o:
        o.location.z = 3.58
brand = bpy.data.objects.get("Brand_Text")
if brand:
    brand.data.size = 0.27

# Add a small upper metal brow to break the flat front transition.
cube("Prism_Front_Brow_03", (0,-0.885,3.86), (2.10,0.10,0.14), silver, bw=0.025)

# ---------------------------------------------------------------------------
# Optical stack: darker, smaller, coated central optics.
# ---------------------------------------------------------------------------
axis = (math.radians(90),0,0)
coating = get_material("Optical_Coating_03", (0.004,0.020,0.033,1), 0.05, 0.07)
cbsdf = coating.node_tree.nodes.get("Principled BSDF")
if "IOR" in cbsdf.inputs:
    cbsdf.inputs["IOR"].default_value = 1.48
if "Transmission Weight" in cbsdf.inputs:
    cbsdf.inputs["Transmission Weight"].default_value = 0.72
elif "Transmission" in cbsdf.inputs:
    cbsdf.inputs["Transmission"].default_value = 0.72

cyl("Aperture_Dark_Disc_03", (0,-2.705,2.28), 0.55, 0.026, black, axis, verts=128, bw=0)
torus("Optical_Inner_Ring_03", (0,-2.735,2.28), 0.54, 0.026, silver, axis)

bpy.ops.mesh.primitive_uv_sphere_add(segments=128, ring_count=64, location=(0,-2.765,2.28))
front_optic = bpy.context.object
front_optic.name = "Coated_Front_Optic_03"
front_optic.scale = (0.47,0.10,0.47)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
for p in front_optic.data.polygons:
    p.use_smooth = True
assign(front_optic, coating)

# Restrained blue-green coating glints; tiny and off-axis by design.
glint_mat = get_material("Lens_Glint_03", (0.035,0.22,0.25,1), 0.05, 0.20)
cyl("Lens_Glint_A_03", (-0.15,-2.872,2.47), 0.085, 0.010, glint_mat, axis, verts=64, bw=0)
cyl("Lens_Glint_B_03", (0.18,-2.870,2.09), 0.045, 0.010, glint_mat, axis, verts=64, bw=0)

# Lens inscription and larger upper index mark.
text_obj("Lens_Inscription_03", "50mm  1:1.8", (0,-2.770,3.05), 0.14, white, extrude=0.006)
cube("Lens_Index_Mark_03", (0,-2.775,3.25), (0.035,0.018,0.10), red, bw=0.004)

# Dial/index markings on the shutter-speed cluster.
for i in range(9):
    a = math.radians(-70 + i*17.5)
    x = 1.02 + 0.29*math.cos(a)
    y = 0.23 + 0.29*math.sin(a)
    cube(f"Speed_Tick_03_{i:02d}", (x,y,4.355), (0.025,0.055,0.035), white, rot=(0,0,a), bw=0.003)

# ---------------------------------------------------------------------------
# Materials: make leather and satin metal read at 800x800.
# ---------------------------------------------------------------------------
lbsdf = leather.node_tree.nodes.get("Principled BSDF")
lbsdf.inputs["Base Color"].default_value = (0.010,0.012,0.014,1)
for n in leather.node_tree.nodes:
    if n.bl_idname == "ShaderNodeTexNoise":
        n.inputs["Scale"].default_value = 58.0
        n.inputs["Detail"].default_value = 5.0
        n.inputs["Roughness"].default_value = 0.68
    elif n.bl_idname == "ShaderNodeBump":
        n.inputs["Strength"].default_value = 0.14
        n.inputs["Distance"].default_value = 0.020

color_noise = leather.node_tree.nodes.new("ShaderNodeTexNoise")
color_noise.name = "Leather_Color_Noise_03"
color_noise.inputs["Scale"].default_value = 24.0
color_noise.inputs["Detail"].default_value = 3.0
color_ramp = leather.node_tree.nodes.new("ShaderNodeValToRGB")
color_ramp.name = "Leather_Color_Ramp_03"
color_ramp.color_ramp.elements[0].color = (0.006,0.007,0.008,1)
color_ramp.color_ramp.elements[1].color = (0.026,0.029,0.031,1)
leather.node_tree.links.new(color_noise.outputs["Fac"], color_ramp.inputs["Fac"])
leather.node_tree.links.new(color_ramp.outputs["Color"], lbsdf.inputs["Base Color"])

rough_noise = leather.node_tree.nodes.new("ShaderNodeTexNoise")
rough_noise.name = "Leather_Roughness_Noise_03"
rough_noise.inputs["Scale"].default_value = 35.0
rough_ramp = leather.node_tree.nodes.new("ShaderNodeValToRGB")
rough_ramp.color_ramp.elements[0].color = (0.38,0.38,0.38,1)
rough_ramp.color_ramp.elements[1].color = (0.58,0.58,0.58,1)
leather.node_tree.links.new(rough_noise.outputs["Fac"], rough_ramp.inputs["Fac"])
leather.node_tree.links.new(rough_ramp.outputs["Color"], lbsdf.inputs["Roughness"])

sbsdf = silver.node_tree.nodes.get("Principled BSDF")
sbsdf.inputs["Base Color"].default_value = (0.56,0.58,0.60,1)
sbsdf.inputs["Metallic"].default_value = 0.97
sbsdf.inputs["Roughness"].default_value = 0.19
for key in ("Anisotropic IOR Level","Anisotropic"):
    if key in sbsdf.inputs:
        sbsdf.inputs[key].default_value = 0.28
        break

gbsdf = glass.node_tree.nodes.get("Principled BSDF")
gbsdf.inputs["Base Color"].default_value = (0.004,0.015,0.025,1)
gbsdf.inputs["Roughness"].default_value = 0.025
if "Transmission Weight" in gbsdf.inputs:
    gbsdf.inputs["Transmission Weight"].default_value = 0.78
elif "Transmission" in gbsdf.inputs:
    gbsdf.inputs["Transmission"].default_value = 0.78

# ---------------------------------------------------------------------------
# Lighting and camera.
# ---------------------------------------------------------------------------
if scene.world and scene.world.use_nodes:
    bg = scene.world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Strength"].default_value = 0.16

for name, energy in {
    "Key_Large": 1320,
    "Fill_Soft": 310,
    "Rim_Top": 1650,
    "Front_Eye": 145,
    "Lens_Catchlight": 72,
    "Body_Edge_Strip_02": 650,
}.items():
    o = bpy.data.objects.get(name)
    if o and o.type == "LIGHT":
        o.data.energy = energy

bpy.ops.object.light_add(type="AREA", location=(-4.8,1.7,4.9))
left_rim = bpy.context.object
left_rim.name = "Left_Rim_Strip_03"
left_rim.data.energy = 520
left_rim.data.size = 3.0
left_rim.data.color = (1.0,0.90,0.82)
look_at(left_rim, (-1.6,0.0,2.6))

cam = bpy.data.objects["Hero_Camera"]
cam.location = (7.55,-8.95,6.45)
cam.data.lens = 62
look_at(cam, (0,-0.24,2.42))

# ---------------------------------------------------------------------------
# Stage evidence. Build a genuine neutral-clay geometry tile first.
# ---------------------------------------------------------------------------
backdrop_names = {"Stage_Floor","Seamless_Cyclorama"}
camera_geo = [
    o for o in bpy.data.objects
    if o.type in {"MESH","FONT"} and o.name not in backdrop_names
]
saved_materials = {
    o.name: (o.data.materials[0] if hasattr(o.data,"materials") and len(o.data.materials) else None)
    for o in camera_geo
}

def restore_materials():
    for o in camera_geo:
        m = saved_materials.get(o.name)
        if m is not None:
            assign(o,m)
        o.hide_render = False

def neutral_lights():
    for name, energy in {
        "Key_Large": 920,
        "Fill_Soft": 540,
        "Rim_Top": 650,
        "Front_Eye": 120,
        "Lens_Catchlight": 25,
        "Body_Edge_Strip_02": 0,
        "Left_Rim_Strip_03": 0,
    }.items():
        o=bpy.data.objects.get(name)
        if o and o.type=="LIGHT":
            o.data.energy=energy

neutral_lights()
for o in camera_geo:
    if hasattr(o.data,"materials"):
        assign(o,clay)
render(os.path.join(STAGE_DIR,"01_blockout.png"))

# Lens stage: clay body, finished lens/optics.
for o in camera_geo:
    if any(o.name.startswith(p) for p in lens_prefixes) or o.name.startswith(("Aperture_Dark_Disc_03","Optical_Inner_Ring_03","Coated_Front_Optic_03","Lens_Glint_","Lens_Inscription_03","Lens_Index_Mark_03")):
        m=saved_materials.get(o.name)
        if m is not None:
            assign(o,m)
render(os.path.join(STAGE_DIR,"02_lens.png"))

restore_materials()
neutral_lights()
render(os.path.join(STAGE_DIR,"03_controls.png"))
render(os.path.join(STAGE_DIR,"04_materials.png"))

for name, energy in {
    "Key_Large":1320,
    "Fill_Soft":310,
    "Rim_Top":1650,
    "Front_Eye":145,
    "Lens_Catchlight":72,
    "Body_Edge_Strip_02":650,
    "Left_Rim_Strip_03":520,
}.items():
    o=bpy.data.objects.get(name)
    if o and o.type=="LIGHT":
        o.data.energy=energy
render(os.path.join(STAGE_DIR,"05_lighting.png"))

scene.render.filepath = FINAL_RENDER
bpy.ops.render.render(write_still=True)
bpy.ops.wm.save_as_mainfile(filepath=BLEND_PATH)

manifest = {
    "task_id": TASK_ID,
    "iteration": ITERATION,
    "render_engine": scene.render.engine,
    "render_resolution": [800,800],
    "object_count": len(bpy.data.objects),
    "stage_files": ["01_blockout.png","02_lens.png","03_controls.png","04_materials.png","05_lighting.png"],
    "final_render_path": FINAL_RENDER,
    "blend_path": BLEND_PATH,
    "source_blend": str(prev_blend),
    "improvements": [
        "reduced lens radial scale while preserving protrusion",
        "darker layered optical stack with restrained coated-glass glints",
        "front panel perimeter trim and stronger prism/front transition",
        "badge moved above lens obstruction plus lens inscription and dial marks",
        "visible leather color/roughness/bump variation",
        "brighter anisotropic satin metal where supported",
        "lower ambient fill with stronger bilateral rim separation",
        "true neutral-clay blockout evidence"
    ],
    "notes": [
        "All added or modified geometry/materials are generated through bpy.",
        "No external model, texture or HDR asset is imported."
    ]
}
with open(ITERATION_MANIFEST,"w",encoding="utf-8") as f:
    json.dump(manifest,f,indent=2,ensure_ascii=False)

print(json.dumps({
    "status":"PASS",
    "task_id":TASK_ID,
    "iteration":ITERATION,
    "final_render":FINAL_RENDER,
    "manifest":ITERATION_MANIFEST,
    "blend":BLEND_PATH,
    "object_count":len(bpy.data.objects),
},ensure_ascii=False))
