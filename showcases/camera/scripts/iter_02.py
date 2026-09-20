import bpy, math, os, json
from pathlib import Path
from mathutils import Vector

TASK_ID = os.getenv("GAH_TASK_ID", "blender-retro-camera-001")
ITERATION = int(os.getenv("GAH_ITERATION", "2"))
STAGE_DIR = os.environ["GAH_STAGE_DIR"]
FINAL_RENDER = os.environ["GAH_FINAL_RENDER"]
ITERATION_MANIFEST = os.environ["GAH_ITERATION_MANIFEST"]
BLEND_PATH = os.environ["GAH_BLEND_PATH"]

prev_blend = Path(BLEND_PATH).parents[1] / "iter_01" / "camera_iter_01.blend"
if not prev_blend.exists():
    raise FileNotFoundError(str(prev_blend))
bpy.ops.wm.open_mainfile(filepath=str(prev_blend))
scene = bpy.context.scene
scene.render.resolution_x = 800
scene.render.resolution_y = 800
scene.render.resolution_percentage = 100

def mat(name):
    return bpy.data.materials[name]

def assign(o, m):
    o.data.materials.clear()
    o.data.materials.append(m)

def bevel(o, width=0.03):
    mod = o.modifiers.new("iter02_bevel", "BEVEL")
    mod.width = width
    mod.segments = 3

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

def cyl(name, loc, radius, depth, material, rot=(0,0,0), verts=72, bw=0.02):
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
    bpy.ops.mesh.primitive_torus_add(major_radius=major, minor_radius=minor, major_segments=96, minor_segments=18, location=loc, rotation=rot)
    o = bpy.context.object
    o.name = name
    for p in o.data.polygons:
        p.use_smooth = True
    assign(o, material)
    return o

def render(path):
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)

leather = mat("Leather_Black")
silver = mat("Satin_Silver")
black = mat("Black_Anodized")
rubber = mat("Rubber_Rings")
glass = mat("Optical_Glass")
red = mat("Shutter_Accent")
white = bpy.data.materials.get("Engraving_White")
if white is None:
    white = bpy.data.materials.new("Engraving_White")
    white.use_nodes = True
    wbsdf = white.node_tree.nodes.get("Principled BSDF")
    wbsdf.inputs["Base Color"].default_value = (0.80, 0.82, 0.82, 1.0)
    wbsdf.inputs["Metallic"].default_value = 0.08
    wbsdf.inputs["Roughness"].default_value = 0.42

# 1) Proportion/layering refinement.
body = bpy.data.objects["Body_Leather_Core"]
body.dimensions.y = 1.72
top = bpy.data.objects["Top_Plate"]
top.dimensions.y = 1.70
bottom = bpy.data.objects["Bottom_Plate"]
bottom.dimensions.y = 1.66
grip = bpy.data.objects["Right_Grip"]
grip.dimensions.y = 0.66
grip.location.y = -0.52
for o in (body, top, bottom, grip):
    bpy.context.view_layer.objects.active = o
    o.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    o.select_set(False)

for sx in (-1.55, 1.55):
    cube(f"Front_Leather_Panel_02_{sx:+.2f}", (sx,-0.885,2.13), (1.72,0.085,2.36), leather, bw=0.055)
for sx in (-2.48, 2.48):
    cube(f"Front_Seam_02_{sx:+.2f}", (sx,-0.900,2.18), (0.055,0.075,2.42), black, bw=0.01)
cube("Prism_Cap_02", (0,0.01,4.78), (0.96,0.92,0.15), silver, bw=0.06)
render(os.path.join(STAGE_DIR, "01_blockout.png"))

# 2) Precision-machined lens hierarchy.
axis = (math.radians(90),0,0)
for name,y,r,m,material in [
    ("Mount_Trim_02",-1.28,1.39,0.04,black),
    ("Focus_Sep_Rear_02",-1.61,1.245,0.022,silver),
    ("Focus_Sep_Front_02",-2.055,1.235,0.020,silver),
    ("Aperture_Sep_02",-2.305,1.095,0.018,silver),
    ("Inner_Bezel_02",-2.685,0.785,0.030,black),
]:
    torus(name,(0,y,2.28),r,m,material,axis)
cyl("Deep_Inner_Glass_02",(0,-2.735,2.28),0.58,0.028,glass,axis,verts=128,bw=0)
for i in range(20):
    a=2*math.pi*(i+0.5)/20
    x=1.335*math.cos(a)
    z=2.28+1.335*math.sin(a)
    cube(f"Fine_Focus_Knurl_02_{i:02d}",(x,-1.86,z),(0.04,0.32,0.055),rubber,rot=(0,a,0),bw=0.006)
render(os.path.join(STAGE_DIR, "02_lens.png"))

# 3) Top-deck and front-face mechanical detail.
cyl("Speed_Dial_02",(1.02,0.23,4.22),0.36,0.20,silver,verts=80,bw=0.025)
cyl("Speed_Dial_Cap_02",(1.02,0.23,4.34),0.23,0.055,black,verts=80,bw=0.014)
for i in range(16):
    a=2*math.pi*i/16
    x=1.02+0.385*math.cos(a)
    y=0.23+0.385*math.sin(a)
    cube(f"Speed_Knurl_02_{i:02d}",(x,y,4.22),(0.055,0.09,0.15),black,rot=(0,0,a),bw=0.008)
cube("Speed_Index_02",(1.02,-0.165,4.25),(0.045,0.035,0.13),red,bw=0.006)
cyl("Rewind_Collar_02",(-1.91,0.10,4.10),0.43,0.08,black,verts=80,bw=0.016)

plate=bpy.data.objects["Brand_Nameplate"]
assign(plate,black)
brand=bpy.data.objects["Brand_Text"]
assign(brand,white)
cube("Brand_Border_02",(0,-1.095,3.34),(1.84,0.05,0.44),silver,bw=0.04)
plate.location.y=-1.135
brand.location.y=-1.19
cyl("Lens_Release_02",(1.60,-0.965,2.90),0.14,0.06,silver,axis,verts=64,bw=0.012)
cyl("Timer_Pivot_02",(-1.70,-0.955,2.55),0.13,0.055,silver,axis,verts=64,bw=0.01)
cube("Timer_Lever_02",(-1.61,-1.015,2.39),(0.10,0.07,0.52),black,rot=(0,0,math.radians(-14)),bw=0.02)
render(os.path.join(STAGE_DIR, "03_controls.png"))

# 4) Finer procedural surface response.
lbsdf=leather.node_tree.nodes.get("Principled BSDF")
lbsdf.inputs["Base Color"].default_value=(0.010,0.012,0.014,1)
for n in leather.node_tree.nodes:
    if n.bl_idname=="ShaderNodeTexNoise":
        n.inputs["Scale"].default_value=95.0
        n.inputs["Detail"].default_value=5.5
    elif n.bl_idname=="ShaderNodeBump":
        n.inputs["Strength"].default_value=0.11
        n.inputs["Distance"].default_value=0.024
sbsdf=silver.node_tree.nodes.get("Principled BSDF")
sbsdf.inputs["Base Color"].default_value=(0.50,0.53,0.56,1)
sbsdf.inputs["Metallic"].default_value=0.96
sbsdf.inputs["Roughness"].default_value=0.20
gbsdf=glass.node_tree.nodes.get("Principled BSDF")
gbsdf.inputs["Base Color"].default_value=(0.006,0.020,0.038,1)
gbsdf.inputs["Roughness"].default_value=0.035
render(os.path.join(STAGE_DIR, "04_materials.png"))

# 5) Stronger controlled rim separation.
if scene.world and scene.world.use_nodes:
    bg=scene.world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Strength"].default_value=0.24
for name,energy in {"Key_Large":1380,"Fill_Soft":500,"Rim_Top":1380,"Front_Eye":190,"Lens_Catchlight":105}.items():
    o=bpy.data.objects.get(name)
    if o and o.type=="LIGHT":
        o.data.energy=energy

bpy.ops.object.light_add(type="AREA", location=(5.6,0.8,4.8))
edge=bpy.context.object
edge.name="Body_Edge_Strip_02"
edge.data.energy=430
edge.data.size=2.6
edge.data.color=(0.88,0.93,1.0)
direction=Vector((1.6,0.0,2.5))-edge.location
edge.rotation_euler=direction.to_track_quat("-Z","Y").to_euler()

cam=bpy.data.objects["Hero_Camera"]
cam.location=(7.55,-8.95,6.45)
cam.data.lens=62
direction=Vector((0,-0.28,2.40))-cam.location
cam.rotation_euler=direction.to_track_quat("-Z","Y").to_euler()
render(os.path.join(STAGE_DIR, "05_lighting.png"))

scene.render.filepath=FINAL_RENDER
bpy.ops.render.render(write_still=True)
bpy.ops.wm.save_as_mainfile(filepath=BLEND_PATH)

manifest={
  "task_id":TASK_ID,
  "iteration":ITERATION,
  "render_engine":scene.render.engine,
  "render_resolution":[800,800],
  "object_count":len(bpy.data.objects),
  "stage_files":["01_blockout.png","02_lens.png","03_controls.png","04_materials.png","05_lighting.png"],
  "final_render_path":FINAL_RENDER,
  "blend_path":BLEND_PATH,
  "source_blend":str(prev_blend),
  "improvements":[
    "slimmer body depth and inset leather front panels",
    "prism cap and stronger top-deck hierarchy",
    "shutter-speed dial, rewind collar, lens-release and timer hardware",
    "lens separator rings, finer knurl accents and deeper optical stack",
    "stronger badge hierarchy",
    "finer leather/satin/glass response and stronger rim separation"
  ],
  "notes":["All added/modified geometry is created with bpy; no external model, texture or HDR asset is imported."]
}
with open(ITERATION_MANIFEST,"w",encoding="utf-8") as f:
    json.dump(manifest,f,indent=2,ensure_ascii=False)
print(json.dumps({"status":"PASS","task_id":TASK_ID,"iteration":ITERATION,"final_render":FINAL_RENDER,"manifest":ITERATION_MANIFEST,"blend":BLEND_PATH},ensure_ascii=False))
