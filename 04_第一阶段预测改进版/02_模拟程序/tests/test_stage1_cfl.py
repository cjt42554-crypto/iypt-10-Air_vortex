import unittest, numpy as np
from airvortex.stage1.transport3d import donor_cell_outflow_rate_s_inv, advective_cfl_face_flux
class Grid:
    shape=(1,1,1); face_shape_x=(2,1,1); face_shape_y=(1,2,1); face_shape_z=(1,1,2); active_mask=np.ones(shape,dtype=bool); open_x=np.ones(face_shape_x,dtype=bool); open_y=np.ones(face_shape_y,dtype=bool); open_z=np.ones(face_shape_z,dtype=bool); dx=dy=dz=1.0
    def require_face_velocity(self,a,b,c): pass
class TestCFL(unittest.TestCase):
    def test_opposing_faces_do_not_cancel(self):
        g=Grid(); ux=np.array([[[-2.]],[[3.]]]); uy=np.zeros(g.face_shape_y); uz=np.zeros(g.face_shape_z); self.assertAlmostEqual(float(donor_cell_outflow_rate_s_inv(ux,uy,uz,g)[0,0,0]),5.0); self.assertAlmostEqual(advective_cfl_face_flux(ux,uy,uz,g,0.1),0.5)
if __name__=="__main__": unittest.main()
