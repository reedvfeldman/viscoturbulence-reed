"""
Viscoturbulence. 2D hydro + Oldroyd B

Usage:
    viscoturb.py [--mesh=<mesh>] <config_file>

Options:
    --mesh=<mesh>              processor mesh (you're in charge of making this consistent with nproc) [default: None]

"""
import sys
import os
import time
import logging
import pathlib
import numpy as np

import dedalus.public as de
from dedalus.tools  import post
from dedalus.extras import flow_tools

from configparser import ConfigParser
from docopt import docopt

args = docopt(__doc__)
mesh = args['--mesh']
if mesh == 'None':
    mesh = None
else:
    mesh = [int(i) for i in mesh.split(',')]

logger = logging.getLogger(__name__)

runconfig = ConfigParser()
config_file = pathlib.Path(sys.argv[-1])
runconfig.read(str(config_file))
logger.info("Using config file {}".format(config_file))

# parameters
params = runconfig['params']
nx = params.getint('nx')
ny = params.getint('ny')
Re = params.getfloat('Re')
Wi = params.getfloat('Wi')
eta = params.getfloat('eta')

# Always on 2pi domain
x = de.Fourier('x',nx)
y = de.Fourier('y',ny)

domain = de.Domain([x,y], grid_dtype='float', mesh=mesh)

variables = ['u', 'v', 'p',  'lU11', 'U12', 'lU22']

problem = de.IVP(domain, variables=variables)
problem.parameters['η'] = eta
problem.parameters['Re'] = Re
problem.parameters['Wi'] = Wi
problem.substitutions['U11'] = 'exp(lU11)'
problem.substitutions['U22'] = 'exp(lU22)'
problem.substitutions['σ11'] = 'U11*U11'
problem.substitutions['σ12'] = 'U11*U12'
problem.substitutions['σ22'] = 'U22*U22'
problem.substitutions['Lap(A)'] = "dx(dx(A)) + dy(dy(A))"
problem.substitutions['Div_σ_x'] = "dx(σ11) + dy(σ12)"
problem.substitutions['Div_σ_y'] = "dx(σ12) + dy(σ22)"

# Navier-Stokes
problem.add_equation("dt(u) - Lap(u)/(Re*(1+η)) + dx(p) = 2*η*Div_σ_x/(Wi*Re*(1+η)) - u*dx(u) - v*dy(u) - cos(y)/Re")
problem.add_equation("dt(v) - Lap(u)/(Re*(1+η)) + dy(p) = 2*η*Div_σ_y/(Wi*Re*(1+η)) - u*dx(v) - v*dy(v)")

#incompressibility
problem.add_equation("dx(u) + dy(v) = 0", "nx != 0 or ny != 0")
problem.add_equation("p = 0", "nx == 0 and ny == 0")

# conformation tensor evolution
# use Cholsky Decomposition
problem.add_equation("dt(lU11) - dx(u) = -u*dx(lU11) - v*dy(lU11) + U12*dy(u)*exp(-lU11) - (1 - exp(-2*lU11))/Wi")
problem.add_equation("dt( U12) - dy(v) = -u*dx( U12) - v*dy( U12) + exp(-lU11)*(exp(2*lU22) - U12**2)*dy(u) + exp(-lU11)*dx(v) + U12*dy(v) - U12*(1 + exp(-2*lU11))/Wi")
problem.add_equation("dt(lU22) - dy(v) = -u*dx(lU22) - v*dy(lU22) + exp(lU11 - 2*lU22)*U12*dx(v) - (1 - exp(-2*lU22))/Wi")

# Build solver
solver = problem.build_solver(de.timesteppers.MCNAB2)
logger.info('Solver built')

run_opts = runconfig['run']
if run_opts.getfloat('stop_wall_time'):
    solver.stop_wall_time = run_opts.getfloat('stop_wall_time')
else:
    solver.stop_wall_time = np.inf

if run_opts.getint('stop_iteration'):
    solver.stop_iteration = run_opts.getint('stop_iteration')
else:
    solver.stop_iteration = np.inf

if run_opts.getfloat('stop_sim_time'):
    solver.stop_sim_time = run_opts.getfloat('stop_sim_time')
else:
    solver.stop_sim_time = np.inf

# Analysis
analysis_tasks = []
check = solver.evaluator.add_file_handler(os.path.join(data_dir,'checkpoints'), wall_dt=3540, max_writes=50)
check.add_system(solver.state)
analysis_tasks.append(check)

snap = solver.evaluator.add_file_handler(os.path.join(data_dir,'snapshots'), sim_dt=1e-2, max_writes=200)
snap.add_task("dx(v) - dy(u)", name='vorticity')
snap.add_task("u")
snap.add_task("v")
snap.add_task("σ11")
snap.add_task("σ12")
snap.add_task("σ22")
snap.add_task("σ11", name='σ11_kspace', layout='c')
snap.add_task("σ12", name='σ12_kspace', layout='c')
snap.add_task("σ22", name='σ22_kspace', layout='c')
analysis_tasks.append(snap)

timeseries = solver.evaluator.add_file_handler(os.path.join(data_dir,'timeseries'), iter=100)
timeseries.add_task("0.5*integ(u**2 + v**2)", name='Ekin')
timeseries.add_task("integ(σ11 + σ22)", name='Σ')

dt = 1e-3
start  = time.time()
while solver.ok:
    if (solver.iteration-1) % 10 == 0:
        logger.info("Step {:d}; Time = {:e}".format(solver.iteration, solver.sim_time))
    solver.step(dt)
stop = time.time()

logger.info("Total Run time: {:5.2f} sec".format(stop-start))
logger.info('beginning join operation')
for task in analysis_tasks:
    logger.info(task.base_path)
    post.merge_analysis(task.base_path)