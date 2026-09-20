"""Run the five published Blender iterations in a fresh output directory."""
from pathlib import Path
import argparse, os, shutil, subprocess

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--blender',default='blender')
    p.add_argument('--output',type=Path,default=Path('camera-output'))
    a=p.parse_args();exe=shutil.which(a.blender)
    if not exe:p.error('Blender not found; pass --blender with its executable path')
    work=a.output.resolve()
    if work.exists() and any(work.iterdir()):p.error('use a new or empty output directory')
    scripts=Path(__file__).resolve().parent/'scripts'
    for i in range(1,6):
        directory=work/f'iter_{i:02d}';stages=directory/'stages';stages.mkdir(parents=True)
        env=dict(os.environ,GAH_TASK_ID='camera-showcase',GAH_ITERATION=str(i),GAH_STAGE_DIR=str(stages),
                 GAH_FINAL_RENDER=str(directory/'final.png'),GAH_ITERATION_MANIFEST=str(directory/'manifest.json'),
                 GAH_BLEND_PATH=str(directory/f'camera_iter_{i:02d}.blend'))
        subprocess.run([exe,'--background','--factory-startup','--disable-autoexec','--python',str(scripts/f'iter_{i:02d}.py')],env=env,check=True,timeout=1800)
    print('Final asset:',work/'iter_05/camera_iter_05.blend')
    print('Final render:',work/'iter_05/final.png')

if __name__=='__main__':main()
